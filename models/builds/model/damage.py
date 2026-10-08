"""Hurt-chain evaluation (MECHANICS §2.2-2.4, bug ids from §6.8) for one hit class against the hyperboss."""
from . import stages
from .log import fallback

BOSS_EXEC_MULT_GEAR = 0.25
BOSS_EXEC_MULT_TALENT = 0.25
DICE_MEAN_DEFAULT = None


class HitClass:
    """How one damage source's hits enter the pipeline."""

    def __init__(self, name, ap=False, aoe_flag=False, normal=False, lucky=False, di_baked=False, m_baked=False,
                 iframe="plain", echo_ok=False, flat_adds_bugged=False, rampage=False, double_ok=False):
        self.name = name
        self.ap = ap
        self.aoe_flag = aoe_flag
        self.normal = normal
        self.lucky = lucky
        self.di_baked = di_baked
        self.m_baked = m_baked
        self.iframe = iframe
        self.echo_ok = echo_ok
        self.flat_adds_bugged = flat_adds_bugged
        self.rampage = rampage
        self.double_ok = double_ok or normal


def bug(build, bug_id):
    """True when the 0.34.1 behaviour of bug_id applies to this build."""
    over = getattr(build, "bug_overrides", None) or {}
    if bug_id in over:
        return over[bug_id] == "bugged"
    return build.mode == "bugged"


def registry_multiplier(d, st, build, buffs, ctx, steps):
    k = ctx.knobs
    add = 0.0
    parts = []
    if buffs.get("totem_player_damage"):
        add += buffs["totem_player_damage"]
        parts.append(("Totem (Player Damage)", buffs["totem_player_damage"]))
    if "berserk_power" in st.flags:
        v = 1.0 * k["uptime_kill_stacks"]
        add += v
        parts.append(("Berserk prestige (20 stacks x uptime)", v))
    if d["relentless"] > 0:
        v = d["relentless"] * 10
        add += v
        parts.append(("Relentless Strike (10 stacks)", v))
    if d["third_attack"] > 0:
        v = d["third_attack"] / 3.0
        add += v
        parts.append(("Third Attack (every 3rd hit)", v))
    t = st.attr.get("x:talent_tier:Berserking", 0)
    if t:
        v = ctx.talents["Berserking"]["tiers"][int(t) - 1]["damageIncrease"] * k["uptime_low_hp"]
        add += v
        parts.append(("Berserking (uptime-weighted)", v))
    t = st.attr.get("x:talent_tier:Depleted", 0)
    if t:
        v = ctx.talents["Depleted"]["tiers"][int(t) - 1]["damageIncrease"] * k["uptime_low_mana"]
        add += v
        parts.append(("Depleted (uptime-weighted)", v))
    return 1.0 + add, parts


def thorns_flat(d, buffs):
    """Flat thorns after Shell Porcupine, which multiplies it by (1 + p) whenever it is read (ShellPorcupineAbility)."""
    return d["thorns_flat"] * (1.0 + buffs.get("porcupine", 0.0))


def thorns_reflect(d, buffs):
    """Damage reflected per boss hit (GearAttributeEvents.thornsReflectDamage): attack damage x 2 x thorns damage
    (ThornsHelper doubles the multiplier) + flat thorns. It does not depend on the hit taken."""
    return d["attack_damage"] * 2.0 * d["thorns_pct"] + thorns_flat(d, buffs)


def normal_flat_adds(d, buffs):
    """Addon flat adds on normal melee hits: AP x AP scaling (Aurora Scissors), flat thorns x thorns scaling (Grass Sword)."""
    return d["ap_flat"] * d["ap_scaling"] + thorns_flat(d, buffs) * d["thorns_scaling"]


def lucky_terms(d, st, ctx):
    """Expected multiplier and additive term from one lucky-hit roll (all unlocked lucky talents fire)."""
    p = d["lucky_hit_chance"]
    mult = 1.0
    names = []
    has_any = False
    for tid in ("Fatal_Strike", "Execution_Strike"):
        t = int(st.attr.get("x:talent_tier:" + tid, 0))
        if not t:
            continue
        tier = ctx.talents[tid]["tiers"][t - 1]
        has_any = True
        if tier["type"] == "damage_lucky_hit":
            mult *= 1.0 + tier["damageIncrease"]
            names.append(f"{tid} x{1 + tier['damageIncrease']:.2f}")
    exec_frac = 0.0
    t = int(st.attr.get("x:talent_tier:Execution_Strike", 0))
    if t and not st.no_exec:
        tier = ctx.talents["Execution_Strike"]["tiers"][t - 1]
        if tier["type"] == "execution_lucky_hit":
            exec_frac = tier["damageIncrease"]
            names.append(f"Execution Strike +{exec_frac:.2f} x missing HP")
    for tid in ("Fanged_Strike", "Arcane_Strike", "Cleave", "Mana_Steal", "Life_Steal"):
        if st.attr.get("x:talent_tier:" + tid, 0):
            has_any = True
    if not has_any:
        mult = 1.5
        names.append("no lucky talents: default x1.5")
    fang = 0.0
    t = int(st.attr.get("x:talent_tier:Fanged_Strike", 0))
    if t:
        fang = ctx.talents["Fanged_Strike"]["tiers"][t - 1]["damageIncrease"]
    return p, mult, exec_frac, fang, names


# Hit classes vanilla i-frames can clip: plain hurts, and Javelin (zeroes invulnerability only after its own hit).
CLIPPED_IFRAMES = ("plain", "javelin")


def evaluate_hit(flat, baked, hc, d, st, build, ctx, target_hp, buffs, record=False):
    """Expected final damage of one hit. With melee weaving (buffs["_weave"]) a clippable hit lands inside a swing's
    i-frame window with probability f and then deals raw - melee raw (MECHANICS 2.4); a hit at or below the melee raw is
    dropped with no hurt event."""
    w = buffs.get("_weave")
    if not w or hc.iframe not in CLIPPED_IFRAMES or w["f"] <= 0:
        return _hit(flat, baked, hc, d, st, build, ctx, target_hp, buffs, record)
    f = w["f"]
    v, lo, hi, steps = _hit(flat, baked, hc, d, st, build, ctx, target_hp, buffs, record)
    base = flat + baked
    r = max(0.0, 1.0 - w["raw"] / base) if base > 0 else 0.0
    if r > 0:
        cv, clo, chi, _ = _hit(flat * r, baked * r, hc, d, st, build, ctx, target_hp, buffs)
    else:
        cv = clo = chi = 0.0
        w["dropped"] = True
    full = v
    v = (1.0 - f) * v + f * cv
    lo = (1.0 - f) * lo + f * clo
    hi = (1.0 - f) * hi + f * chi
    if record:
        steps.append(("melee i-frame clip", f"x{v / full:.2f}" if full else "x0",
                      f"{f:.0%} of hits land within {ctx.knobs['weave_window_s']:.1f} s of a swing and keep raw - "
                      f"{w['raw']:,.0f} (melee raw)" + ("; at or below it they are dropped, procs included" if r == 0 else
                                                         f" = {r:.0%} of the raw")))
    return v, lo, hi, steps


def _hit(flat, baked, hc, d, st, build, ctx, target_hp, buffs, record=False):
    steps = []
    missing = 0.5 * target_hp
    di = d["damage_increase"]
    e_mult = 1.0 if hc.ap else 1.0 + di
    m_mult, m_parts = registry_multiplier(d, st, build, buffs, ctx, steps)
    m_applies = not (hc.ap or hc.aoe_flag)

    def e_and_m(x_flat, x_baked):
        if hc.di_baked and not bug(build, "ICEBOLT-DI2"):
            v = x_flat * e_mult + x_baked
        else:
            v = (x_flat + x_baked) * e_mult
        if m_applies:
            v *= m_mult
        return v

    base = flat + baked
    if record:
        steps.append(("raw", f"{base:,.1f}", "flat part" if flat else "ability formula (stages baked in where noted)"))
    exec_attr = 0.0 if st.no_exec else d["execution"]
    x_add = normal_flat_adds(d, buffs) if hc.normal else 0.0
    flat_adds_apply = hc.normal and exec_attr > 0
    if flat_adds_apply:
        add = missing * exec_attr
        if bug(build, "EXEC-GEAR"):
            pre = e_and_m((flat + x_add + add) * BOSS_EXEC_MULT_GEAR, baked * BOSS_EXEC_MULT_GEAR)
            post = e_and_m(flat, baked) * BOSS_EXEC_MULT_GEAR + add * BOSS_EXEC_MULT_GEAR + x_add
            if bug(build, "ORDER-FLATADD"):
                v = 0.5 * (pre + post)
                lo, hi = min(pre, post), max(pre, post)
            else:
                v = pre
                lo = hi = v
            if record:
                steps.append(("execution (gear)", f"(hit + {add:,.0f}) x 0.25", "EXEC-GEAR bug: boss penalty scales the whole hit"))
        else:
            v = e_and_m(flat + x_add + add * BOSS_EXEC_MULT_GEAR, baked)
            lo = hi = v
            if record:
                steps.append(("execution (gear)", f"+{add * BOSS_EXEC_MULT_GEAR:,.0f}", "missing HP x attr x 0.25 (boss), added before multipliers"))
    elif x_add > 0:
        pre = e_and_m(flat + x_add, baked)
        if bug(build, "ORDER-FLATADD"):
            post = e_and_m(flat, baked) + x_add
            v = 0.5 * (pre + post)
            lo, hi = min(pre, post), max(pre, post)
        else:
            v = pre
            lo = hi = v
    else:
        v = e_and_m(flat, baked)
        lo = hi = v
    if record:
        if not hc.ap:
            note = "DI applied twice (baked + hurt stage)" if hc.di_baked and bug(build, "ICEBOLT-DI2") else ""
            steps.append(("damage increase", f"x{e_mult:.2f}", note))
        else:
            steps.append(("damage increase", "skipped", "ability-power hit"))
        if m_applies:
            steps.append(("multiplier registry", f"x{m_mult:.2f}", ", ".join(f"{n} +{x:.2f}" for n, x in m_parts) or "empty"))
        else:
            steps.append(("multiplier registry", "skipped", "AP/AoE-flagged hit" + (" (baked into the formula)" if hc.m_baked else "")))
    cond = 1.0
    t = int(st.attr.get("x:talent_tier:Executioner", 0))
    if t:
        tier = ctx.talents["Executioner"]["tiers"][t - 1]
        th, inc = tier.get("healthThreshold", 0.5), tier["damageIncrease"]
        x = 1.0 / ((1.0 - th) + th / (1.0 + inc)) - 1.0
        cond *= 1.0 + x
        if record:
            steps.append(("Executioner", f"x{1 + x:.2f}", f"x{1 + inc:.2f} on every hit once the target is below {th:.0%} HP "
                          "(LivingDamage, LOWEST); averaged over the kill time"))
    t = int(st.attr.get("x:talent_tier:Hexbreaker", 0))
    if t and hc.ap:
        x = ctx.talents["Hexbreaker"]["tiers"][t - 1]["damageIncrease"] * ctx.knobs["uptime_target_debuffed"]
        cond *= 1.0 + x
        if record:
            steps.append(("Hexbreaker", f"x{1 + x:.2f}", "AP hits vs debuffed target (uptime-weighted)"))
    if hc.rampage and buffs.get("rampage"):
        cond *= 1.0 + buffs["rampage"]
        if record:
            steps.append(("Rampage", f"x{1 + buffs['rampage']:.2f}", "direct full-charge melee"))
    v *= cond
    lo *= cond
    hi *= cond
    if hc.lucky:
        p, mult, exec_frac, fang, names = lucky_terms(d, st, ctx)
        lucky_add = p * exec_frac * missing * BOSS_EXEC_MULT_TALENT
        fang_add = 0.0
        if fang > 0:
            fang_hit = v * mult * fang
            if not bug(build, "FANG-RESIDUAL"):
                fang_hit /= cond
            fang_add = p * fang_hit
        factor = 1.0 + p * (mult - 1.0)
        v = v * factor + lucky_add + fang_add
        lo = lo * factor + lucky_add + fang_add
        hi = hi * factor + lucky_add + fang_add
        if record:
            steps.append(("lucky hit (expected)", f"p={p:.2f}, x{factor:.2f}", "; ".join(names)
                          + (f"; fang +{fang_add:,.0f}" if fang_add else "") + (f"; execution +{lucky_add:,.0f}" if lucky_add else "")))
    if buffs.get("vulnerable", 1.0) > 1.0:
        vm = buffs["vulnerable"]
        v *= vm
        lo *= vm
        hi *= vm
        if record:
            steps.append(("Vulnerable (target)", f"x{vm:.2f}", "target-side, every hit"))
    if st.dice:
        dm = 0.5 * (st.dice[0] + st.dice[1])
        v *= dm
        lo *= dm
        hi *= dm
        if record:
            steps.append(("The Dice", f"x{dm:.3f}", "uniform 0.01-3.0, every hit"))
    if record and x_add > 0:
        steps.append(("AP / thorns scaling", f"+{x_add:,.0f}", "flat add on normal melee hits (Aurora Scissors, Grass Sword)"))
    if hc.double_ok and d["double_hit"] > 0:
        dh = 1.0 + d["double_hit"] * (2.0 - 1.0)
        v *= dh
        lo *= dh
        hi *= dh
        if record:
            steps.append(("double hit", f"x{dh:.2f}", "chance x (2 - 1), after the hurt chain"))
    if hc.echo_ok and d["echo_chance"] > 0:
        c = d["echo_chance"]
        stored = base * (1.0 + d["echo_damage"]) * 0.667
        series, term, decay, k = 0.0, 1.0, 1.0, 0
        while decay > 0 and k < 40:
            series += term
            term *= c ** 0.5 * decay
            decay = decay * 0.95 - 0.05
            k += 1
        keep_flags = not bug(build, "ECHO-FLAGLOSS")
        hc_echo = HitClass("echo", ap=hc.ap and keep_flags, aoe_flag=False,
                           normal=hc.normal if keep_flags else True, lucky=False, iframe="shotgun")
        replay, _, _, _ = evaluate_hit(stored, 0.0, hc_echo, d, st, build, ctx, target_hp, buffs)
        ev = c * series * replay
        v += ev
        lo += ev
        hi += ev
        if record:
            steps.append(("echo (expected)", f"+{ev:,.0f}", f"{c:.0%} chance; stores 0.667 x (1+echo dmg) x pre-multiplier hit, "
                          f"re-procs at sqrt(chance) with decay (series x{series:.2f}); replay runs the full pipeline"
                          + (" as a melee hit (ECHO-FLAGLOSS)" if hc.ap and not keep_flags else "")))
    return v, lo, hi, steps
