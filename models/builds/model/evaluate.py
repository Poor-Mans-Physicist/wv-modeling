"""Score a build: damage-limited and survival-limited hyper cycles, the lower of which is the build's level."""
import math

from . import stages
from .build import assemble
from .stats import derive, ehp, hit_multiplier
from . import families
from . import hyper

C_LO, C_HI = -12.0, 80.0


def boss_grid(ctx):
    """Hyperboss health, damage and the Frenzy player-damage multiplier by cycle, chaos modifiers included (model/hyper.py)."""
    return hyper.grid(ctx.knobs, stages.HYPER)


def _damage_cycle(fam, build, ctx, st, d, record):
    """Damage cycle for one stat state. Boss DPS is scaled by the hyperboss's rune-shield downtime."""
    up = 1.0 - ctx.knobs["boss_shield_downtime"]
    r0 = fam.compute(build, ctx, st, d, 0.0, record=record)
    r1 = fam.compute(build, ctx, st, d, 1e9, record=False)
    dps0 = r0["dps"] * up
    slope = (r1["dps"] - r0["dps"]) * up / 1e9
    T = ctx.knobs["kill_time_s"]
    s_missing = 2.0 * slope

    def ttk(hp):
        if dps0 <= 0:
            return float("inf")
        if s_missing * hp / dps0 < 1e-6:
            return hp / dps0
        return math.log1p(s_missing * hp / dps0) / s_missing

    c = hyper.solve_damage(boss_grid(ctx), dps0, slope, T)
    return c, r0, dps0, slope, s_missing, ttk


def requirements(d, ctx):
    """Baseline every build must meet (user ruling): >= min mana regen per second and >= min cooldown reduction.
    Unmet requirements cost score in proportion to the gap, so the search climbs back to feasibility."""
    k = ctx.knobs
    gap_regen = max(0.0, k["min_mana_regen"] - d["mana_regen"]) / k["min_mana_regen"]
    gap_cdr = max(0.0, k["min_cooldown_reduction"] - d["cooldown_reduction"]) / k["min_cooldown_reduction"]
    met = gap_regen <= 1e-9 and gap_cdr <= 1e-9
    return {"met": met, "mana_regen": d["mana_regen"], "cooldown_reduction": d["cooldown_reduction"],
            "penalty": 0.0 if met else 8.0 + 40.0 * (gap_regen + gap_cdr)}


HEAL_SKILL = "Heal"


def healing(build, ctx, st, d, r0):
    """Healing per second during the fight, after healing effectiveness (PlayerRecoveryHelper).

    Against a VaultBoss, gear leech heals max HP x leech on every qualifying damage instance (PlayerLeechHelper skips
    its damage/maxHP cap for bosses) and Life Steal heals max HP x its percentage on every lucky hit. The Heal ability
    is cast only with mana the build would otherwise not spend, at most once per cooldown. Regeneration is left out
    (vanilla 1 HP per 50 >> amp ticks: 0.4-3.3 HP/s)."""
    H = d["health"]
    he = d["healing_effectiveness"]
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
    total = he * (leech + ls + heal)
    return {"per_s": total, "effectiveness": he, "leech": he * leech, "life_steal": he * ls, "heal": he * heal,
            "heal_casts_per_s": casts, "heal_spec": spec, "bloodthirst": d["bloodthirst"]}


def survival(ctx, d, heal_per_s):
    """Survival cycle (user ruling 2026-10-07): survive `survive_hits` boss hits spaced `hit_interval_s` apart by tanking
    or out-healing them, and never get one-shot by a hit that isn't blocked or dodged. With one-shot protection on, a
    build that heals oneshot_heal_fraction of max HP between hits may go oneshot_extra_cycles past its one-shot cycle."""
    k = ctx.knobs
    g = boss_grid(ctx)
    H = d["health"]
    n = k["survive_hits"]
    hd = heal_per_s * k["hit_interval_s"]
    m_det = hit_multiplier(d)
    _, m_exp = ehp(d)
    cap1 = H / max(m_det, 1e-9)
    cap5 = min(cap1, max(hd, (H + (n - 1.0) * hd) / n) / m_exp)
    c5 = hyper.solve_survival(g, cap5)
    c1 = hyper.solve_survival(g, cap1)
    protected = bool(k["oneshot_protection"]) and hd >= k["oneshot_heal_fraction"] * H - 1e-9
    c = max(c5, c1 + k["oneshot_extra_cycles"]) if protected else c5
    return c, {"cycle_hits": c5, "cycle_oneshot": c1, "protected": protected, "heal_per_hit": hd,
               "hit_mult": m_det, "avg_mult": m_exp, "binding": "one-shot protection" if protected and c > c5 else
               ("one-shot" if cap1 <= cap5 else "5 hits")}


def has_execution(st, d):
    return d.get("execution", 0.0) > 0 or st.attr.get("x:talent_tier:Execution_Strike", 0) > 0


def unique_effects(st, d, ctx):
    """Castle Bastion at its stationary uptime; Safer Spaces replaces block: one guaranteed block, then no block for
    200 x (1 - block) ticks (MixinGearAttributeEvents.blockAttack), i.e. 1 in 1 + ceil(that / hit interval) hits."""
    d["castle"] = d["castle"] * ctx.knobs["castle_bastion_uptime"]
    if "safer_space" in st.flags:
        n = math.ceil(10.0 * (1.0 - d["block"]) / ctx.knobs["hit_interval_s"] - 1e-9)
        d["block"] = 1.0 / (1.0 + max(0, n))


def evaluate(build, ctx, record=False):
    st = assemble(build, ctx)
    from .damage import bug
    d = derive(st, ctx_caps(ctx), mana_cap=bug(build, "MANA-CAP"))
    unique_effects(st, d, ctx)
    fam = families.get(build.family)
    c_full, r0, dps0, slope, s_missing, ttk = _damage_cycle(fam, build, ctx, st, d, record)
    c_dmg = c_full
    exec_info = None
    if has_execution(st, d):
        st.no_exec = True
        c_ne, _, dps_ne, _, _, _ = _damage_cycle(fam, build, ctx, st, d, False)
        st.no_exec = False
        cap = ctx.knobs["execution_cycle_cap"]
        c_dmg = min(c_full, c_ne + cap)
        exec_info = {"cycle_with": c_full, "cycle_without": c_ne, "gain": c_full - c_ne, "capped": c_full > c_ne + cap,
                     "dps_without": dps_ne}
    if r0.get("resist_bonus"):
        d = dict(d)
        d["resistance"] = min(d["resistance"] + r0["resist_bonus"], d["resistance_cap"])
    e, mult = ehp(d)
    heal = healing(build, ctx, st, d, r0)
    g = boss_grid(ctx)
    c_surv, surv = survival(ctx, d, heal["per_s"])
    c_eff = min(c_dmg, c_surv)
    boss_hp = hyper.at(g, "H", c_eff)
    frenzy = hyper.at(g, "M", c_eff)
    dps_at = (dps0 + slope * boss_hp) * frenzy
    req = requirements(d, ctx)
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
    }
    return out


def ctx_caps(ctx):
    from . import catalog
    return catalog.load()["caps"]
