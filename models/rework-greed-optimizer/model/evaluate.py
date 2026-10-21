"""Score a build: damage-limited and survival-limited hyper cycles, the lower of which is the build's level."""
import math
import struct

from . import stages
from . import catalog
from .build import assemble, negation_met
from .stats import derive, ehp, hit_multiplier
from . import families
from . import abilities as ab
from . import buffs
from . import hyper
from . import binomial
from . import procs
from .log import fallback

C_LO, C_HI = -40.0, 80.0


def boss_grid(ctx):
    """Hyperboss health, damage and the Frenzy player-damage multiplier by cycle, chaos modifiers included (model/hyper.py)."""
    return hyper.grid(ctx.knobs, stages.HYPER)


DICE_Z = ((0.5, 0.0), (0.8, 0.8416), (0.9, 1.2816), (0.95, 1.6449))


def dice_z(pct):
    """Standard normal quantile at pct from DICE_Z, linear between its rows; outside them the nearest row (logged)."""
    lo, hi = DICE_Z[0], DICE_Z[-1]
    if pct < lo[0] or pct > hi[0]:
        fallback("dice-percentile-range", f"dice_percentile {pct} outside {lo[0]}-{hi[0]}; clamped")
        pct = min(max(pct, lo[0]), hi[0])
    for (p0, z0), (p1, z1) in zip(DICE_Z, DICE_Z[1:]):
        if pct <= p1:
            return z0 + (z1 - z0) * ((pct - p0) / (p1 - p0))
    return hi[1]


def dice_percentile(st, ctx, r0):
    """The Dice at a percentile of the kill (plan 2.2, ruling R15). Each hit rolls U(lo, hi) (mean m, coefficient of
    variation cv = (hi - lo) / sqrt(12) / m). A fight's damage is a sum of hits h_j each times its own roll, so its
    multiplier has CV cv x sqrt(sum h_j^2) / sum h_j = cv / sqrt(n) with n the effective hit count (sum h)^2 / sum h^2,
    which is the plain count when every hit is the same size and counts a weak stream for its share of the damage.
    Per second that is DPS^2 / sum over sources of dps_i^2 / rate_i (r0["hit_sq"]: every rotation source, scored
    swings, counters, the Starforge nova and echo pulses), times kill_time_s x (1 - boss_shield_downtime). At the
    dice_percentile of the kill time the multiplier is m x max(1 / m, 1 - z cv / sqrt(max(n, 1))) (never below 1).
    Every hit carries the Dice once, so the hit chain keeps the mean and the family's damage is scaled by the returned
    ratio. Returns (ratio, record or None)."""
    if not st.dice:
        return 1.0, None
    k = ctx.knobs
    lo, hi = st.dice
    mean = 0.5 * (lo + hi)
    cv = (hi - lo) / math.sqrt(12.0) / mean
    z = dice_z(k["dice_percentile"])
    sq = r0.get("hit_sq", 0.0)
    n = (r0["dps"] * r0["dps"] / sq if sq > 0 else 0.0) * k["kill_time_s"] * (1.0 - k["boss_shield_downtime"])
    ratio = max(1.0 / mean, 1.0 - z * cv / math.sqrt(max(n, 1.0)))
    return ratio, {"n": n, "z": z, "mean": mean, "mult": mean * ratio, "percentile": k["dice_percentile"]}


def _apply_dice(r0, r1, ratio, info):
    """Scale one evaluation's damage figures by the Dice percentile ratio and restate the Dice step."""
    for key in ("dps", "dps_lo", "dps_hi", "pack_dps", "echo_dps", "echo_main"):
        if r0.get(key) is not None:
            r0[key] *= ratio
    if r0.get("hit") is not None:
        r0["hit"] *= ratio
    for row in r0.get("rotation") or []:
        row["dps"] *= ratio
    r1["dps"] *= ratio
    r0["dice"] = info
    steps = r0.get("steps")
    if steps:
        for i, s in enumerate(steps):
            if s[0] == "The Dice":
                steps[i] = ("The Dice", f"x{info['mult']:.3f}", f"x{info['mean']:.3f} mean per hit, {info['n']:,.0f} scored "
                            f"hits per kill: {info['percentile']:.0%} percentile of the kill time x{info['mult']:.3f}")


def _damage_cycle(fam, build, ctx, st, d, record, dice=None):
    """Damage cycle for one stat state. Boss DPS is scaled by the hyperboss's rune-shield downtime and The Dice is
    taken at its percentile (dice_percentile); `dice` = (ratio, info) reuses a previous pass's percentile ratio (the
    no-execute estimate takes the main pass's ratio, as the kernel scales every lane by the lane-0 ratio)."""
    up = 1.0 - ctx.knobs["boss_shield_downtime"]
    r0 = fam.compute(build, ctx, st, d, 0.0, record=record)
    r1 = fam.compute(build, ctx, st, d, 1e9, record=False)
    ratio, info = dice if dice is not None else dice_percentile(st, ctx, r0)
    r0["_dice"] = (ratio, info)
    if info is not None:
        _apply_dice(r0, r1, ratio, info)
    dps0 = r0["dps"] * up
    slope = (r1["dps"] - r0["dps"]) * up / 1e9
    T = ctx.knobs["kill_time_s"]
    s_missing = 2.0 * slope
    theta = execute_threshold(fam, build, ctx, st, d, r0)
    r0["execute_threshold"] = theta
    left = 1.0 - theta

    def ttk(hp):
        if dps0 <= 0:
            return float("inf")
        hp = hp * left
        if s_missing * hp / dps0 < 1e-6:
            return hp / dps0
        return math.log1p(s_missing * hp / dps0) / s_missing

    c = hyper.solve_damage(boss_grid(ctx), dps0 / left, slope, T)
    return c, r0, dps0, slope, s_missing, ttk


EXECUTE_THRESHOLD_MAX = 0.95


def execute_threshold(fam, build, ctx, st, d, r0):
    """Greed rework (addon ExecuteEvents): share of the hyperboss's health left when it is executed.

    The gear execution stat executes on every normal melee hit (melee builds and woven swings); an Executing Strike
    lucky hit adds its tier value on top of the gear stat. The hyperboss is a true boss, so it takes half the threshold.
    The kill needs a qualifying hit after the boss crosses the threshold; that wait is ignored (sub-second at these hit
    rates). Execute ends the fight early, so the boss only has to lose (1 - threshold) of its health. The kernel
    mirrors this in hit.rs execute_threshold."""
    if ctx.gods is None:
        return 0.0
    gear = d.get("execution", 0.0)
    melee = fam.kind == "melee" or bool(getattr(build, "weave", False))
    best = gear if melee else 0.0
    t = int(st.attr.get("x:talent_tier:Execution_Strike", 0))
    if t and r0.get("lucky_rate", 0.0) > 0:
        tier = ctx.talents["Execution_Strike"]["tiers"][t - 1]
        if tier["type"] == "execution_lucky_hit":
            best = max(best, gear + tier["damageIncrease"])
    theta = 0.5 * best
    if melee and d.get("reaving", 0.0) > 0:
        # Reaving (addon LivingEntityEvents.reavingDamage): the first normal non-projectile hit on a target adds
        # reaving x max health x 0.25 (a VaultBoss, MaxHealthDamageHelper) once; counted as health the fight need not take.
        theta += d["reaving"] * 0.25
    if theta > EXECUTE_THRESHOLD_MAX:
        fallback("execute-threshold-cap", f"execute threshold {theta:.2f} clamped to {EXECUTE_THRESHOLD_MAX}")
        theta = EXECUTE_THRESHOLD_MAX
    return theta


def requirements(d, ctx, r0=None):
    """Baseline every build must meet (user ruling): >= min mana regen per second and >= min cooldown reduction, and
    in the rework >= min_move_speed stable movement speed. Unmet requirements cost score in proportion to the gap, so
    the search climbs back to feasibility: 8 + 40 x gap for the hard minimums, and 40 x gap with no step for the
    main-damage share (the end of the search enforces it by dropping secondaries, search.prune_secondaries)."""
    k = ctx.knobs
    gap_regen = max(0.0, k["min_mana_regen"] - d["mana_regen"]) / k["min_mana_regen"]
    gap_cdr = max(0.0, k["min_cooldown_reduction"] - d["cooldown_reduction"]) / k["min_cooldown_reduction"]
    gap_move = max(0.0, k["min_move_speed"] - d["move_speed"]) / k["min_move_speed"] if ctx.gods is not None else 0.0
    gap_neg = 0.0 if d.get("effect_negation_met", True) else 1.0
    share = (r0 or {}).get("main_share", 1.0)
    ms = k["main_damage_share_min"]
    gap_main = max(0.0, ms - share) / ms if ms > 0 else 0.0
    hard = gap_regen <= 1e-9 and gap_cdr <= 1e-9 and gap_move <= 1e-9 and gap_neg <= 1e-9
    met = hard and gap_main <= 1e-9
    return {"met": met, "mana_regen": d["mana_regen"], "cooldown_reduction": d["cooldown_reduction"],
            "move_speed": d["move_speed"], "move_met": gap_move <= 1e-9, "main_share": share,
            "effect_negation_met": gap_neg <= 1e-9,
            "penalty": (0.0 if hard else 8.0 + 40.0 * (gap_regen + gap_cdr + gap_move + gap_neg)) + 40.0 * gap_main}


HEAL_SKILL = "Heal"


def regen_levels(build, ctx, st):
    """Summed Regeneration levels (rework only)."""
    if ctx.gods is None:
        return 0
    n = st.gfx["regen_levels"] if st.gfx else 0
    n += catalog.prestige_regen(ctx.prestige)
    n += sum(catalog.trinket_regen(t) for t in build.trinkets)
    n += sum(catalog.unique_regen(it.unique) for it in build.items.values() if it.unique)
    return n


def healing(build, ctx, st, d, r0):
    """Healing per second during the fight, after healing effectiveness (PlayerRecoveryHelper).

    Against a VaultBoss, gear leech heals max HP x leech on every qualifying damage instance (PlayerLeechHelper skips
    its damage/maxHP cap for bosses) and Life Steal heals max HP x its percentage on every lucky hit. The Heal ability
    is cast only with mana the build would otherwise not spend, at most once per cooldown. Rework: Regeneration levels
    from god nodes, Velara's Petal, regeneration uniques and the prestige power are summed (GrantedEffectEvent adds
    amplifiers) and heal vanilla 1 HP per 50 >> amp ticks. Methodical's effectiveness holds for the family's
    u_low_mana share of the fight (time-weighted, each state clamped on its own)."""
    H = d["health"]
    he = he_lo = d["healing_effectiveness"]
    he_lm = d.get("healing_effectiveness_low_mana", he)
    u = r0.get("u_low_mana", 0.0)
    if he_lm != he:
        he = (1.0 - u) * he_lo + u * he_lm
    leech = r0.get("leech_rate", 0.0) * H * d["leech"]
    ls = 0.0
    t = int(st.attr.get("x:talent_tier:Life_Steal", 0))
    if t:
        ls = r0.get("lucky_rate", 0.0) * H * ctx.talents["Life_Steal"]["tiers"][t - 1]["maxHealthPercentage"]
    heal, casts, spec = 0.0, 0.0, None
    for sid, learned in sorted(build.abilities.items()):
        if learned > 0 and sid in ctx.abilities and ctx.abilities[sid]["skill"] == HEAL_SKILL:
            spec = sid
    if spec is not None:
        tier, _ = families.effective_tier(ctx, st, spec, build.abilities[spec])
        c = families.spec_cfg(ctx, st, spec, tier, build)
        amount = c.get("flatLifeHealed", 0.0)
        cost = c.get("manaCost", 0.0)
        if amount > 0:
            cd = families.cooldown_s(c["cooldownTicks"], d)
            spare = max(0.0, r0.get("mana_supply", 0.0) - r0.get("mana_demand", 0.0))
            casts = 1.0 / cd if cost <= 0 else min(1.0 / cd, spare / cost)
            heal = casts * amount
    if casts > 0 and spec is not None:
        r0.setdefault("mana_drains", []).append([f"{spec.replace('_', ' ')} on spare mana", casts * cost])
    regen = 0.0
    levels = regen_levels(build, ctx, st)
    if levels:
        from .godfx import regen_hps
        regen = regen_hps(levels)
    if st.gfx and st.gfx["luckiest"]:
        ls *= 2.0
    totem = r0.get("totem_heal", 0.0)
    # Rework addon (LivingEntityEvents, commit 8c01e87e): healing effectiveness is clamped to at most 1 while leeching,
    # so it can only reduce gear leech and Life Steal.
    hl = he
    if ctx.gods is not None:
        hl = (1.0 - u) * min(he_lo, 1.0) + u * min(he_lm, 1.0) if he_lm != he_lo else min(he, 1.0)
    totem = r0.get("totem_heal", 0.0)
    total = hl * (leech + ls) + he * (heal + regen)
    if totem:
        total += he * totem
    return {"per_s": total, "effectiveness": he, "leech": hl * leech, "life_steal": hl * ls, "heal": he * heal,
            "regen": he * regen, "totem": he * totem,
            "heal_casts_per_s": casts, "heal_spec": spec, "bloodthirst": d["bloodthirst"]}


def speed_curve(s, k, y_lo, y_hi):
    """y_lo at stable speed speed_lo to y_hi at speed_hi, linear, clamped outside (avoidance and Castle Bastion)."""
    t = min(1.0, max(0.0, (s - k["speed_lo"]) / (k["speed_hi"] - k["speed_lo"])))
    return y_lo + (y_hi - y_lo) * t


def source_reach(spec, ctx, st, build, d):
    """How far from the boss a damage source can be cast (plan 1.4, the spec table's reach rule): projectile and
    targeted abilities reach any distance, self-centred AoEs radius x area of effect (reach_key names the radius),
    Hungry Maw's line 1.5 x that, contact and hits-taken sources 0."""
    rec = families.ab.SPEC_TABLE.get(spec)
    rule = rec["reach"] if rec else None
    if rule == "projectile":
        return math.inf
    if rule == "zero":
        return 0.0
    if rule not in ("self_radius", "fang_line"):
        fallback(f"reach-unknown-{spec}", f"{spec} has no reach rule in the spec table; counted as melee range")
        return 0.0
    tier, _ = families.effective_tier(ctx, st, spec, max(1, build.abilities.get(spec, 0)))
    c = families.spec_cfg(ctx, st, spec, tier, build)
    r = c.get(rec["reach_key"], 0.0) * d["aoe_multiplier"]
    return r * 1.5 if rule == "fang_line" else r


def is_ranged(fam, build, ctx, st, d):
    """Ranged Build Survivability Bonus eligibility (plan 1.4, ruling R29): not a melee family, no weaving, and every
    damage source in the rotation (main and learned secondaries) reaches at least ranged_reach blocks. Counterstrike
    and thorns do not disqualify (they only lose rate). Returns (ranged, shortest reach)."""
    if fam.kind == "melee" or getattr(build, "weave", False):
        return False, 0.0
    if fam.kind != "ability":
        return True, math.inf
    reach = min(source_reach(x, ctx, st, build, d) for x in [fam.main_spec] + fam.secondaries(build, ctx))
    return reach >= ctx.knobs["ranged_reach"] - 1e-9, reach


IMMORTALITY = "the_vault:immortality"


def immortal_seal(trinket_ids):
    """(chance per hit, immunity ticks) of the Immortal Seal trinkets among trinket_ids (addon EffectOnHitTakenEffect, rolled in
    LivingEntityEvents.onPlayerHit at LivingHurtEvent: after block, before dodge, only while not already immune);
    None without one."""
    trinkets = catalog.load()["trinkets"]
    miss, ticks = 1.0, 0.0
    for tid in trinket_ids:
        cfg = trinkets.get(tid, {}).get("config", {})
        if cfg.get("effect") == IMMORTALITY and cfg.get("chance", 0.0) > 0:
            miss *= 1.0 - float(cfg["chance"])
            ticks = max(ticks, float(cfg["duration"]))
    return None if miss >= 1.0 else (1.0 - miss, ticks)


def attack_stream(build, ctx, st, d, fam):
    """The boss's attack stream as the player meets it (plan 1.1, 1.2): one attempt every hit_interval_s.

    p_avoid = speed_curve(stable speed, avoid_lo, avoid_hi) (avoided by moving), times 1 - (1 - sneak_avoid_factor) x
    the share of the fight spent sneaking for Power Dump (d["sneak_share"]); p_ranged = ranged_bonus for a ranged
    build (is_ranged); attempts reaching the player: (1 - p_avoid) x p_ranged per interval (thorns reflect, Counterstrike
    and the Immortal Seal see these). Immortal Seal (renewal over one cycle): the seal rolls p on each reaching attempt
    that is not blocked, the triggering hit and the next n_imm = duration / 20 / hit_interval_s attempt intervals are
    immune, and only the share a = (1 - p_avoid) x p_ranged x (1 - block) of those intervals carries an attempt that
    would have been rolled, so p_seal = (1 / p - 1) / (1 / p + n_imm x a) of the would-land hits still land (the plan's
    (1/p - 1) / (1/p + n_imm) is the a = 1 case). Guards: block, then dodge (luck-affected, capped at 0.95, Sneaky
    Getaway's d["sneaky_dodge"] inside the cap). p_land = (1 - p_avoid) x p_ranged x p_seal x (1 - block) x (1 - dodge).
    Writes ranged, p_avoid, p_seal, p_land, boss_hits_landed and boss_hits_reaching (per second) into d."""
    k = ctx.knobs
    ranged, reach_min = is_ranged(fam, build, ctx, st, d)
    p_avoid = speed_curve(d["move_speed"], k, k["avoid_lo"], k["avoid_hi"])
    if d.get("sneak_share", 0.0) > 0:
        p_avoid *= 1.0 - (1.0 - k["sneak_avoid_factor"]) * d["sneak_share"]
    p_ranged = k["ranged_bonus"] if ranged else 1.0
    if d["sneaky_dodge"] > 0:
        d["dodge"] = min((st.get("dodge_percent") + d["sneaky_dodge"]) * procs.luck_mult(st), 0.95)
    reach = (1.0 - p_avoid) * p_ranged
    p_seal = 1.0
    seal = immortal_seal(build.trinkets)
    if seal is not None:
        ch, ticks = seal
        n_imm = ticks / 20.0 / k["hit_interval_s"]
        a = reach * (1.0 - d["block"])
        p_seal = (1.0 / ch - 1.0) / (1.0 / ch + n_imm * a)
    p_land = reach * p_seal * (1.0 - d["block"]) * (1.0 - d["dodge"])
    d["ranged"] = ranged
    d["reach_min"] = reach_min
    d["p_avoid"] = p_avoid
    d["p_seal"] = p_seal
    d["p_land"] = p_land
    d["boss_hits_landed"] = p_land / k["hit_interval_s"]
    d["boss_hits_reaching"] = reach / k["hit_interval_s"]


def window_cap(x, n, absorb):
    """Largest hit (after armour and the other per-hit factors) n landed hits can each be, given x = pool + (n - 1)
    heal per interval: x / n, plus the Mana Shield pool (absorb["mana_shield_pool"]) in x, and Ultimate Shield (uptime
    u, share pct of each hit, budget B points per window) absorbing u x min(n x pct x hit, B), so n hit - u min(n pct
    hit, B) <= x gives min(x / (n (1 - u pct)), (x + u B) / n)."""
    msp = absorb.get("mana_shield_pool", 0.0)
    u = absorb.get("us_uptime", 0.0)
    if not msp and not u:
        return x / n
    x = x + msp
    ua = u * absorb.get("us_pct", 0.0)
    hi = (x + u * absorb.get("us_budget", 0.0)) / n
    return hi if ua >= 1.0 else min(x / (n * (1.0 - ua)), hi)


def last_stand(st, ctx, d):
    """Last Stand (LowHealthResistanceTalent:60-77, LivingDamageEvent LOWEST, after armour, resistance and absorption):
    damage x (1 - damageReduction) below its health threshold, so a landed hit in the window takes x(1 - dr x u_low).
    The one-shot test is taken at full health and ignores it."""
    t = int(st.attr.get("x:talent_tier:Last_Stand", 0))
    if not t or "Last_Stand" not in ctx.talents:
        return 1.0
    return 1.0 - ctx.talents["Last_Stand"]["tiers"][t - 1]["damageReduction"] * d["u_low"]


def survival(ctx, d, heal_per_s, dmg_cap=False, gfx=None, fury_inc=1.0, r0=None, st=None):
    """Survival cycle (plan 1.3, rulings R5 and R7): the largest cycle whose boss hit the build survives both tests.

    Hit size: the grid's hits carry hyper_boss_crit_mult (every hit crits); crit mitigation turns the multiplier into
    m_crit = 1 + (crit_mult - 1) x max(0, 1 - crit_mitigation) (EntityEvents:243-247), then armour, resistance and
    Castle Bastion (hit_multiplier) and the god factors. One-shot: an unguarded hit at full health (no avoidance, no
    guards, no absorption) must not kill: D x m_crit x m_det <= H. Window: K ~ Binomial(survive_hits, p_land) landed
    hits, k_q = the smallest k with P(K <= k) >= 1 - survival_risk (model/binomial.py); k_q landed hits are survivable
    up to max(hd, window_cap(H' + (k_q - 1) hd, k_q, r0)) each (or the sustained hd), with hd the healing per interval,
    H' = H, and the Mana Shield pool and Ultimate Shield absorption from the family result (window_cap); k_q = 0
    imposes no window cap. Adaptive Armor averages over k_q hits; Last Stand scales window hits only. With one-shot
    protection (bugged mode) a build that heals oneshot_heal_fraction of max HP between hits may go
    oneshot_extra_cycles past its one-shot cycle.

    Immortal's revive (VelaraImmortal: the lethal hit is cancelled and health set to max, once per 600 s, so once per
    fight): a build that survives m landed hits from full health dies on hit m + 1, revives at full and survives m more,
    so k_q landed hits need m >= floor(k_q / 2) (the window cap at m = floor(k_q / 2) hits from full health; none for
    k_q = 1, the revive takes any single hit). The one-shot test assumes a landed unguarded hit; with the revive it
    passes at any hit size only when at most one hit lands in the whole fight at the same risk, k_fight =
    quantile(Binomial(round(kill_time_s / hit_interval_s), p_land)) <= 1; a second landed hit after the revive would
    kill."""
    k = ctx.knobs
    r0 = r0 or {}
    g = boss_grid(ctx)
    H = d["health"]
    n = int(round(k["survive_hits"]))
    hd = heal_per_s * k["hit_interval_s"]
    cr = k["hyper_boss_crit_mult"]
    m_crit = 1.0 + (cr - 1.0) * max(0.0, 1.0 - d["crit_mitigation"])
    crit = cr / m_crit
    k_q = binomial.quantile(n, d["p_land"], 1.0 - k["survival_risk"])
    m_det = hit_multiplier(d)
    m_base = m_det
    m_win = m_det
    pool = H
    adds = r0.get("mana_shield_pool", 0.0)
    d["absorb_pool"] = adds
    parts = []
    if gfx:
        from .godfx import adaptive_factors
        a1, an = adaptive_factors(gfx["adaptive"][0], gfx["adaptive"][1], max(k_q, 1))
        m_det *= a1 * gfx["shield"] * fury_inc * (gfx["fleeting"] if gfx["fleeting"] else 1.0)
        m_win *= an * gfx["shield"] * fury_inc * (gfx["fleeting"] * 2.0 / 3.0 if gfx["fleeting"] else 1.0)
        for name, x in (("Adaptive Armor (average over the hits)", an), ("Temporal Shielding", gfx["shield"]),
                        ("Ultra Rampaging (damage taken)", fury_inc),
                        ("Fleeting Physicality", gfx["fleeting"] * 2.0 / 3.0 if gfx["fleeting"] else 1.0)):
            if abs(x - 1.0) > 1e-9:
                parts.append((name, 1.0 / x))
        if gfx["revive"]:
            pool = 2.0 * H
            parts.append(("Immortal revive (second life)", 2.0))
    m_hit = m_win
    ls = last_stand(st, ctx, d) if st is not None else 1.0
    if ls != 1.0:
        parts.append(("Last Stand (low-HP share of the window)", 1.0 / ls))
    m_win *= ls
    if m_win <= 1e-9:
        fallback("survival-zero-mult", "window damage-taken multiplier hit ~0; clamped to 1e-9")
        m_win = 1e-9
    cap1 = H / max(m_det, 1e-9) * crit
    revive = bool(gfx and gfx["revive"])
    k_f = m_rev = None
    if revive:
        n_f = int(round(k["kill_time_s"] / k["hit_interval_s"]))
        k_f = binomial.quantile(n_f, d["p_land"], 1.0 - k["survival_risk"])
        if k_f <= 1:
            cap1 = math.inf
        m_rev = k_q // 2
    capw = cap1
    y = 0.0
    if revive:
        if m_rev > 0:
            y = window_cap(H + (m_rev - 1.0) * hd, m_rev, r0)
            capw = min(cap1, max(hd, y) / m_win * crit)
    elif k_q > 0:
        y = window_cap(pool + (k_q - 1.0) * hd, k_q, r0)
        capw = min(cap1, max(hd, y) / m_win * crit)
    cw = hyper.solve_survival(g, capw, dmg_cap)
    c1 = hyper.solve_survival(g, cap1, dmg_cap)
    protected = bool(k["oneshot_protection"]) and hd >= k["oneshot_heal_fraction"] * H - 1e-9
    c = max(cw, c1 + k["oneshot_extra_cycles"]) if protected else cw
    absorb = min(r0.get("us_pct", 0.0) * y, r0.get("us_budget", 0.0) / max(k_q, 1)) if r0.get("us_uptime") else 0.0
    return c, {"cycle_hits": cw, "cycle_oneshot": c1, "protected": protected, "heal_per_hit": hd,
               "k_q": k_q, "k_fight": k_f, "revive_hits": m_rev, "attempts": n, "p_land": d["p_land"], "p_avoid": d["p_avoid"], "p_seal": d["p_seal"],
               "ranged": d["ranged"], "crit_mult": m_crit, "absorb_pool": adds, "us_absorb": absorb,
               "last_stand": ls, "hit_mult_window": m_hit, "crit_scale": 1.0 / crit,
               "ehp_god": (pool + adds) / max(m_win, 1e-12), "ehp_base": H / max(m_base, 1e-12), "ehp_god_parts": parts,
               "hit_mult": m_det, "avg_mult": m_win, "binding": "one-shot protection" if protected and c > cw else
               ("one-shot" if cap1 <= capw else "window")}


HP_HIGH, HP_LOW = 0.8, 0.2


def hp_state(d, ctx, hit, heal_per_s):
    """Share of the fight above 80% and below 20% HP (plan 2.1, ruling R25): a sawtooth where each landed hit (rate
    lam = p_land / hit_interval_s) takes f = hit / H at the scored cycle and healing refills r = heal_per_s / H per
    second. After a hit at full health the player spends t80 = (f - 0.2) / r (at most the mean gap 1 / lam) below 80%:
    u_high = 1 - min(1, lam t80) (0 when f > 0.2 and nothing heals); below 20%: u_low = min(1, lam (f - 0.8)^+ / r).
    Ignores the healing curve's shape and absorption."""
    H = d["health"]
    lam = d["p_land"] / ctx.knobs["hit_interval_s"]
    if lam <= 0 or H <= 0:
        return 1.0, 0.0
    f = hit / H
    r = heal_per_s / H
    if f <= 1.0 - HP_HIGH:
        u_high = 1.0
    elif r <= 0:
        u_high = 0.0
    else:
        u_high = 1.0 - min(1.0, lam * min((f - (1.0 - HP_HIGH)) / r, 1.0 / lam))
    u_low = min(1.0, lam * max(0.0, f - (1.0 - HP_LOW)) / max(r, 1e-12))
    return u_high, u_low


HP_STATE_TYPES = ("high_health_gear_attribute", "low_health_resistance", "low_health_damage")


def hp_state_talents(build, ctx, st):
    """Learned talents whose effect depends on the HP state (Sorcery, Prime Amplification, Stoneskin, Last Stand,
    Berserking): (talent id, tier config) pairs."""
    out = []
    for tid in sorted(build.talents):
        t = int(st.attr.get("x:talent_tier:" + tid, 0))
        if t and tid in ctx.talents:
            tier = ctx.talents[tid]["tiers"][t - 1]
            if tier.get("type") in HP_STATE_TYPES:
                out.append((tid, tier))
    return out


def hp_state_redo(talents, u_high, u_low):
    """What the HP shares change after the first pass, which assumed u_high = 1 and u_low = 0: "full" (a high-health
    talent below full uptime, or Berserking with a low-HP share: stats and damage move), "survival" (only Last Stand's
    window factor moves) or None (the first pass is already exact)."""
    types = {tier.get("type") for _, tier in talents}
    if ("high_health_gear_attribute" in types and u_high != 1.0) or ("low_health_damage" in types and u_low != 0.0):
        return "full"
    if "low_health_resistance" in types and u_low != 0.0:
        return "survival"
    return None


def apply_high_health(st, talents, u_high):
    """High-health talents (HighHealthGearAttributeTalent: their attribute only above 80% HP) were assembled at full
    value; scale them to u_high for the second pass."""
    for tid, tier in talents:
        if tier.get("type") == "high_health_gear_attribute" and u_high != 1.0:
            st.add(tier["attribute"], tier["value"] * (u_high - 1.0), f"talent {tid} above 80% HP x{u_high:.2f}")


def write_back(build, ctx, st, d, fam, r0):
    """Derived-stat buffs the rotation reports (plan 13): Empower's speed when paid at full uptime (r0["empower_speed"],
    into stable speed and so Castle Bastion's uptime), Sneaky Getaway's dodge (r0["sneaky_dodge"], inside the capped
    dodge roll) and the Power Dump sneaking share (r0["sneak_share"], halving avoidance) re-run the attack stream for the survival test; the family's own boss-hit rates (Counterstrike, thorns,
    Frozen Throne) keep the stream they were computed with. True when anything changed."""
    e, sd, sn = r0.get("empower_speed", 0.0), r0.get("sneaky_dodge", 0.0), r0.get("sneak_share", 0.0)
    if not e and not sd and not sn:
        return False
    k = ctx.knobs
    d["empower_speed"] = e
    d["sneaky_dodge"] = sd
    d["sneak_share"] = sn
    from .stats import stable_move_speed
    d["move_speed"] = stable_move_speed(st, d)
    d["castle"] = d["castle_max"] * speed_curve(d["move_speed"], k, k["castle_uptime_lo"], k["castle_uptime_hi"])
    attack_stream(build, ctx, st, d, fam)
    return True


def has_execution(st, d, ctx):
    """Missing-HP execution, which takes the +5-cycle cap. The greed rework replaced it with an execute threshold."""
    if ctx.gods is not None:
        return False
    return d.get("execution", 0.0) > 0 or st.attr.get("x:talent_tier:Execution_Strike", 0) > 0


def unique_effects(st, d, ctx):
    """Castle Bastion at its uptime for the build's stable speed (castle_uptime_lo to castle_uptime_hi, ruling R19);
    Safer Spaces replaces block: one guaranteed block, then no block for 200 x (1 - block) ticks
    (MixinGearAttributeEvents.blockAttack), i.e. 1 in 1 + ceil(that / hit interval) hits."""
    k = ctx.knobs
    d["castle_max"] = d["castle"]
    d["castle"] = d["castle_max"] * speed_curve(d["move_speed"], k, k["castle_uptime_lo"], k["castle_uptime_hi"])
    if "safer_space" in st.flags:
        n = math.ceil(10.0 * (1.0 - d["block"]) / ctx.knobs["hit_interval_s"] - 1e-9)
        d["block"] = 1.0 / (1.0 + max(0, n))


CASCADE_COMPUTES = 12
CASCADE_TOL = 1e-12
CASCADE_ACCEPT = 1e-5
CASCADE_WIDTH = 4e-6


def cascade_grid(x):
    """x > 0 rounded to 20 mantissa bits (a relative step of 2^-21 to 2^-20; round numbers sit on grid points, not
    on the cell edges; eval.rs cascade_grid)."""
    bits = struct.unpack("<Q", struct.pack("<d", x))[0]
    return struct.unpack("<d", struct.pack("<Q", (bits + 0x80000000) & 0xFFFFFFFF00000000))[0]


def cascade_solve(f, x0, g0, x1, n):
    """Root of g(x) = f(x) - x from a known point (x0, g0) and a first trial x1, n computes already spent.

    Before a sign change: secant steps through the last two points (a plain step x -> f(x) when the secant is
    undefined or points away from the root). Once one point has g > 0 and another g < 0: Illinois regula falsi inside
    that bracket. Every trial point is rounded to cascade_grid, so the trials of a delta-scored build and of its
    full re-score are the same numbers: f is discontinuous (the rotation's mana split jumps with the cooldown rates,
    often right at the fixed point of the piece below), and a trial on rounding noise next to a jump would flip sides
    and jump the score. Stops at |g| <= 1e-12 x (an exact hit), when the next trial is the current one (converged on
    the grid), when the bracket is narrower than 4e-6 x (a jump of f across the diagonal: no fixed point), or after
    CASCADE_COMPUTES computes in all. ok means |g| <= 1e-5 x at the last x. On a linear piece of f the secant lands on
    the root's grid cell in one step. Returns (x, ok) for the last x evaluated; f must leave its state at that x. The
    kernel (eval.rs cascade_solve) runs the same arithmetic."""
    lo = (x0, g0) if g0 > 0 else None
    hi = (x0, g0) if g0 < 0 else None
    xp, gp = x0, g0
    x = cascade_grid(x1)
    side = 0
    while True:
        y = f(x)
        n += 1
        g = y - x
        if abs(g) <= CASCADE_TOL * x:
            return x, True
        ok = abs(g) <= CASCADE_ACCEPT * x
        if n >= CASCADE_COMPUTES:
            return x, ok
        if g > 0:
            if side == 1 and hi is not None:
                hi = (hi[0], hi[1] / 2.0)
            lo, side = (x, g), 1
        else:
            if side == -1 and lo is not None:
                lo = (lo[0], lo[1] / 2.0)
            hi, side = (x, g), -1
        if lo is not None and hi is not None:
            if abs(hi[0] - lo[0]) <= CASCADE_WIDTH * x:
                return x, ok
            nx = lo[0] - lo[1] * (hi[0] - lo[0]) / (hi[1] - lo[1])
        else:
            nx = y
            if g != gp:
                s = x - g * (x - xp) / (g - gp)
                if s > 0.0 and (s > x) == (g > 0):
                    nx = s
        nx = cascade_grid(nx)
        if nx == x:
            return x, ok
        xp, gp = x, g
        x = nx


def cascade_fixed_point(fam, build, ctx, st, d):
    """Arcane Cascade: lucky hits shorten cooldowns, and shorter cooldowns can add lucky hits (fang casts), so the lucky
    hit rate L solves L = f(L), f = lucky hits/s with cooldowns shortened at L. From f(0), cascade_solve finds the
    root of f(L) - L (f is linear in L on each piece: the cast rate is 1 + L x steps / 20 times the base rate until a
    cooldown floor or the mana runs out, so the second compute usually lands on it). A build whose lucky hits do not
    depend on cooldowns stops after two computes. Sets d["cascade_l"] and d["cascade_c"]; eval.rs mirrors this and
    keeps the family output of the last compute. There is no warm start from a neighbour's L: f can cross the
    diagonal more than once (the rotation's mana split jumps), and a start-dependent root would make delta scores
    disagree with full scores."""
    t = int(st.attr.get("x:talent_tier:Arcane_Strike", 0))
    c = float(ctx.talents["Arcane_Strike"]["tiers"][t - 1].get("cooldownDecrease", 0.0)) if t else 0.0
    reset = ab.lucky_reset_spec(fam, build, ctx, st, d)
    if c <= 0.0 and reset is None:
        return
    d["cascade_c"] = c if c > 0.0 else 0.0
    if reset is not None:
        d["reset_spec"] = reset
        rec = ab.SPEC_TABLE.get(reset, {})
        d["reset_self"] = 0.0
        d["reset_rework"] = ctx.gods is not None
        if rec.get("lucky_ok") is True and rec.get("fang_hits"):
            tier, _ = families.effective_tier(ctx, st, reset, max(1, build.abilities.get(reset, 0)))
            cfg = families.spec_cfg(ctx, st, reset, tier, build)
            n = ab.fang_hits(rec["fang_hits"], cfg["radius"] * d["aoe_multiplier"], ctx)
            if d["reset_rework"]:
                d["reset_self"] = 1.0 - (1.0 - d["lucky_hit_chance"]) ** n
            else:
                d["reset_self"] = d["lucky_hit_chance"] * n
        d["reset_l"] = 0.0

    def f(x):
        d["cascade_l"] = x
        r = fam.compute(build, ctx, st, d, 0.0, record=False)
        y = r.get("lucky_hits", 0.0)
        if reset is not None:
            d["reset_l"] = r.get("lucky_hits_ext", y)
        return y
    y0 = f(0.0)
    if y0 <= 0.0:
        d["cascade_l"] = 0.0
        return
    L, ok = cascade_solve(f, 0.0, y0, y0, 1)
    d["cascade_l"] = L
    if not ok:
        fallback("cascade-unconverged", "Arcane Cascade lucky-hit rate has no fixed point within 1e-5 (f jumps across "
                 f"L = f(L), or {CASCADE_COMPUTES} computes ran out); kept the last trial")


def evaluate(build, ctx, record=False):
    """Score a build. One pass derives the stats, the attack stream, the damage and the survival cycles. When the
    build learns an HP-state talent and hp_state_heuristic is on, the HP shares (hp_state) at the first pass's scored
    cycle replace the first pass's u_high = 1, u_low = 0: a second full pass re-derives everything when a talent's
    effect moves stats or damage, only the survival test is redone when Last Stand alone moves (hp_state_redo), and
    the first pass stands when nothing moves. At most two passes."""
    st = assemble(build, ctx)
    from .damage import bug
    k = ctx.knobs
    fam = families.get(build.family)
    g = boss_grid(ctx)
    dmg_cap = bug(build, "BOSS-DMG-CAP")
    hp_tal = hp_state_talents(build, ctx, st) if k["hp_state_heuristic"] else []
    hp = None
    for npass in range(2):
        d = derive(st, ctx_caps(ctx), mana_cap=bug(build, "MANA-CAP"))
        d["effect_negation_met"] = not ctx.knobs["effect_negation_required"] or negation_met(build)
        if hp is not None:
            d["u_high"], d["u_low"] = hp
        elif not k["hp_state_heuristic"]:
            d["u_low"] = k["uptime_low_hp"]
        unique_effects(st, d, ctx)
        attack_stream(build, ctx, st, d, fam)
        cascade_fixed_point(fam, build, ctx, st, d)
        pre = buffs.buffs_for(build, ctx, st, d, False)[0]
        buffs.apply_buff_stats(pre, d)
        c_full, r0, dps0, slope, s_missing, ttk = _damage_cycle(fam, build, ctx, st, d, record)
        c_dmg = c_full
        exec_info = None
        if has_execution(st, d, ctx):
            st.no_exec = True
            c_ne, _, dps_ne, _, _, _ = _damage_cycle(fam, build, ctx, st, d, False, dice=r0["_dice"])
            st.no_exec = False
            cap = k["execution_cycle_cap"]
            c_dmg = min(c_full, c_ne + cap)
            exec_info = {"cycle_with": c_full, "cycle_without": c_ne, "gain": c_full - c_ne, "capped": c_full > c_ne + cap,
                         "dps_without": dps_ne}
        write_back(build, ctx, st, d, fam, r0)
        titan = buffs.colossus_resistance(st, d, pre, r0.get("resist_bonus", 0.0))
        if titan is not None:
            d = dict(d)
            d["resistance"] = titan
        elif r0.get("resist_bonus"):
            d = dict(d)
            d["resistance"] = min(d["resistance"] + r0["resist_bonus"], d["resistance_cap"])
        e, mult = ehp(d)
        heal = healing(build, ctx, st, d, r0)
        c_surv, surv = survival(ctx, d, heal["per_s"], dmg_cap, st.gfx, r0.get("fury_inc", 1.0), r0, st)
        c_eff = min(c_dmg, c_surv)
        if npass == 1 or not hp_tal:
            break
        hit = hyper.at(g, "DS" if dmg_cap else "DF", c_eff) * surv["crit_scale"] * surv["hit_mult_window"]
        hp = hp_state(d, ctx, hit, heal["per_s"])
        redo = hp_state_redo(hp_tal, hp[0], hp[1])
        if redo == "full":
            apply_high_health(st, hp_tal, hp[0])
            continue
        d["u_high"], d["u_low"] = hp
        if redo == "survival":
            c_surv, surv = survival(ctx, d, heal["per_s"], dmg_cap, st.gfx, r0.get("fury_inc", 1.0), r0, st)
            c_eff = min(c_dmg, c_surv)
        break
    if "us_uptime" in r0:
        r0["flags"] = list(r0.get("flags", [])) + [f"ultimate-shield: drain {r0['us_drain']:.1f}/s, absorbs "
                                                   f"{surv['us_absorb']:,.0f} per hit at {r0['us_uptime']:.0%} uptime"]
    if hp is not None:
        surv["hp_state"] = {"u_high": hp[0], "u_low": hp[1], "talents": [t for t, _ in hp_tal], "passes": npass + 1}
    boss_hp = hyper.at(g, "H", c_eff)
    frenzy = hyper.at(g, "M", c_eff)
    dps_at = (dps0 + slope * boss_hp) * frenzy
    req = requirements(d, ctx, r0)
    score = c_eff + 0.002 * max(c_dmg, c_surv) - req["penalty"]
    hp_scaling = s_missing * boss_hp / max(dps0, 1e-9) > 1.0
    out = {
        "score": score, "cycle": c_eff, "cycle_damage": c_dmg, "cycle_survival": c_surv,
        "dps_vs_matched_boss": dps_at, "dps_no_hp_scaling": dps0, "dps_per_boss_hp": slope,
        "dps_raw": r0["dps"], "pack_dps": r0.get("pack_dps", r0["dps"]), "ehp": e, "damage_taken_mult": mult,
        "derived": d, "family_result": r0, "stats": st, "hp_scaling": hp_scaling, "execution": exec_info,
        "requirements": req,
        "ttk_at_cycle": ttk(boss_hp) / frenzy, "boss_hp_at_cycle": boss_hp, "frenzy_at_cycle": frenzy,
        "boss_hit_at_cycle": hyper.at(g, "D", c_eff), "healing": heal, "survival": surv,
        "attack_stream": {"p_avoid": d["p_avoid"], "p_land": d["p_land"], "k_q": surv["k_q"], "ranged": d["ranged"],
                          "reach_min": d["reach_min"], "p_seal": d["p_seal"]},
    }
    return out


def ctx_caps(ctx):
    from . import catalog
    return catalog.load()["caps"]
