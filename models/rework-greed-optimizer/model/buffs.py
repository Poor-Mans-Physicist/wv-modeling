"""Buff abilities and the mana helpers the families and the rotation share (the kernel mirrors it in buffs.rs).

buffs_for runs one handler per learned buff spec (BUFF_HANDLERS, keyed by spec id). A handler reads the spec's tier
config and writes buff values, boss debuff sources, notes and flags; it returns the mana demand with its own cost
added. BUFF_SPECS is the handler table's key order.
"""
import math

from . import procs
from .abilities import SPEC_TABLE, cooldown_s, etch_value
from .log import fallback


def unaffordable(c, d):
    """Cast guard: an instant cast that costs more than the whole bar can never be cast (toggles pay per tick)."""
    cost = c.get("manaCost", 0.0)
    return cost > d["mana_max"] + 1e-9 * max(1.0, d["mana_max"])


def frost_nova_procs(st, d, ctx):
    """Free Frost Nova casts per second from Frozen Throne (AbilityTriggerOnDamageAttribute on LivingDamageEvent): the
    summed chance per boss hit that lands (d["boss_hits_landed"]: avoided, blocked, dodged or seal-negated hits never
    reach LivingDamageEvent)."""
    ch = min(1.0, st.special.get("frost_nova_on_damage", 0.0))
    if ch <= 0:
        return 0.0
    return ch * d["boss_hits_landed"]


def taunt_amp(amp, ctx):
    """Taunt's Vulnerable amplifier after the optional nerf knob taunt_amp_half_above h (0 = off): one amplifier per
    two levels above h."""
    h = int(ctx.knobs["taunt_amp_half_above"])
    if h > 0 and amp > h:
        return h + (amp - h) // 2
    return amp


def taunt_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Taunt: Vulnerable on the boss while it lasts."""
    dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = min(1.0, dur / cd)
    amp = taunt_amp(int(c["amplifier"]), ctx)
    buffs["taunt_amp"] = amp
    debuffs.append(("Taunt", procs.VULNERABLE, amp, up, False))
    demand += c["manaCost"] / max(cd, dur)
    notes.append(f"Taunt t{tier}: Vulnerable amplifier {amp} on the boss, {up:.0%} uptime"
                 + (f" (config {c['amplifier']}, halved above {int(ctx.knobs['taunt_amp_half_above'])} by the knob)"
                    if amp != int(c["amplifier"]) else ""))
    return demand


def totem_player_damage(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Totem (Player Damage): an addition to the multiplier registry while standing in its radius."""
    dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    buffs["totem_player_damage"] = c["totemPlayerDamagePercent"] * up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Totem (Player Damage) t{tier}: +{c['totemPlayerDamagePercent']:.2f} to the multiplier registry, {up:.0%} uptime (stand in radius)")
    if etch_value(build, "etching_totem_player_damage_ap"):
        buffs["ap_totem_mult"] = 1.0 + up * c["totemPlayerDamagePercent"]
        notes.append(f"AP Totem etching: ability power x{buffs['ap_totem_mult']:.2f} (x{1 + c['totemPlayerDamagePercent']:.2f} "
                     f"at {up:.0%} uptime, TotemPlayerDamageAbility's ABILITY_POWER_MULTIPLIER)")
    return demand


def _rampage_toggle(ctx, c, buffs):
    """Rampage's damage increase and toggle cost (the ramp averaged over one kill); the rotation pays it last."""
    cost = c["manaCostPerSecond"] + 0.5 * c.get("manaRampPerSecond", 0.0) * ctx.knobs["kill_time_s"]
    buffs["rampage"] = c["damageIncrease"]
    buffs["rampage_cost"] = cost
    return cost


def _rampage_note(sid, tier, c, cost, notes):
    notes.append(f"{sid} t{tier}: melee x{1 + c['damageIncrease']:.2f} while toggled ({cost:.1f} mana/s)")


def rampage_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    _rampage_note("Rampage_Base", tier, c, _rampage_toggle(ctx, c, buffs), notes)
    return demand


def rampage_berserker(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Rampage (Berserker): refreshed on cooldown at a fixed uptime once regen and cooldown reduction meet the knobs."""
    cost = _rampage_toggle(ctx, c, buffs)
    k = ctx.knobs
    if d["mana_regen"] >= k["rampage_refresh_regen"] - 1e-9 and d["cooldown_reduction"] >= k["rampage_refresh_cdr"] - 1e-9:
        buffs["rampage_fixed_uptime"] = k["rampage_refresh_uptime"]
        buffs["rampage_cost"] = c["manaCostPerSecond"]
        notes.append(f"Rampage Berserker refreshed on cooldown: {d['mana_regen']:.1f} mana/s regen and {d['cooldown_reduction']:.0%} CDR "
                     f"meet the {k['rampage_refresh_regen']:.0f}/s and {k['rampage_refresh_cdr']:.0%} bar, so {k['rampage_refresh_uptime']:.0%} uptime")
    _rampage_note("Rampage_Berserker", tier, c, cost, notes)
    return demand


def rampage_bloodlust(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    _rampage_note("Rampage_Bloodlust", tier, c, _rampage_toggle(ctx, c, buffs), notes)
    return demand


def rampage_instinct(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Rampage (Instinct): lucky-hit chance while toggled."""
    cost = _rampage_toggle(ctx, c, buffs)
    buffs["rampage_lucky"] = c.get("luckyHitChance", 0.0)
    _rampage_note("Rampage_Instinct", tier, c, cost, notes)
    return demand


def _battle_cry(c, d):
    """Battle Cry cooldown and stacks per cast (30 from a boss)."""
    return cooldown_s(c["cooldownTicks"], d), min(c["maxStacksTotal"], 30)


def battle_cry_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    cd, per_cast_stacks = _battle_cry(c, d)
    buffs["battle_cry_ad_stacks_per_s"] = c["attackDamagePerStack"] * per_cast_stacks / cd
    notes.append(f"Battle Cry t{tier}: {per_cast_stacks} stacks x {c['attackDamagePerStack']:.2f} AD-multiples per cast every {cd:.1f}s")
    return demand + c["manaCost"] / cd


def battle_cry_spectral_strike(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Battle Cry (Spectral Strike, proposed rework): stacks that qualifying hits spend for base AP (spectral_stacks)."""
    cd, per_cast_stacks = _battle_cry(c, d)
    dur = math.floor(c["stackDuration"] * (1.0 + d["effect_duration"]) + 1e-9) / 20.0
    buffs["spectral"] = (per_cast_stacks, c["maxStacksUsedPerHit"], cd, dur)
    notes.append(f"Battle Cry (Spectral) t{tier}: {per_cast_stacks} stacks per cast every {cd:.1f}s, up to "
                 f"{c['maxStacksUsedPerHit']:.0f} per hit, each +{ctx.knobs['spectral_ap_per_stack']:.2f} x base AP (proposed rework)")
    return demand + c["manaCost"] / cd


def battle_cry_lucky_strike(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    cd, per_cast_stacks = _battle_cry(c, d)
    buffs["battle_cry_lucky_per_hit"] = c["luckyHitChancePerStack"] * c["maxStacksUsedPerHit"]
    buffs["battle_cry_lucky_hits_per_s"] = (per_cast_stacks / c["maxStacksUsedPerHit"]) / cd
    notes.append(f"Battle Cry (Lucky) t{tier}: +{buffs['battle_cry_lucky_per_hit']:.0%} lucky on ~{per_cast_stacks / c['maxStacksUsedPerHit']:.1f} hits per cast")
    return demand + c["manaCost"] / cd


def colossus_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    buffs["colossus_resist"] = c["additionalResistance"] * up
    buffs["colossus_up"] = up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Colossus t{tier}: +{c['additionalResistance']:.0%} resistance at {up:.0%} uptime")
    return demand


def concentrate_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Concentrate: every cast strips concentrate_effects_stripped effects from nearby adds, each one Empower with
    chance 1/5; the Empower chain (godfx.concentrate_ladder) gives attack damage x(1 + 0.1 (amp + 1)) while active,
    time-averaged. Cast on cooldown with the cost after mana-cost reduction (the cast bug modeled as fixed)."""
    from .godfx import concentrate_ladder, concentrate_steps
    cd = cooldown_s(c["cooldownTicks"], d)
    k = 0.2 * ctx.knobs["concentrate_effects_stripped"]
    dur = c["baseEffectDuration"] * (1.0 + d["effect_duration"])
    steps = concentrate_steps(tier, ctx.gods is not None)
    amp, up = concentrate_ladder(c["baseAmplitude"], c["amplitudeScaleChance"], dur, cd * 20.0, k, steps)
    buffs["empower_ad_mult"] = 1.0 + 0.1 * (amp + 1.0) * up
    buffs["concentrate_amp"] = amp
    buffs["concentrate_uptime"] = up
    demand += c["manaCost"] / cd
    flags.append(f"concentrate: amp {amp:.1f} at {up:.0%} uptime")
    notes.append(f"Concentrate t{tier}: {k:.1f} Empower procs per cast every {cd:.1f}s, at most {steps} per chain, Empower amp {amp:.1f} on average at "
                 f"{up:.0%} uptime -> attack damage x{buffs['empower_ad_mult']:.2f}")
    return demand


def totem_mana_regen(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    buffs["mana_regen_add"] = c["totemManaRegenPercent"] * up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Totem (Mana Regen) t{tier}: +{c['totemManaRegenPercent']:.0%} to the mana regen multiplier (summed with gear "
                 f"mana regen, both MULTIPLY_BASE on generic.mana_regen) at {up:.0%} uptime")
    return demand


def shell_porcupine(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    buffs["porcupine"] = c["additionalThornsDamagePercent"]
    demand += c["manaCostPerSecond"]
    notes.append(f"Shell (Porcupine) t{tier}: flat thorns x{1 + c['additionalThornsDamagePercent']:.1f} while toggled ({c['manaCostPerSecond']:.0f} mana/s)")
    return demand


def nova_slow(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Frost Nova cast on cooldown as a maintenance buff: Hypothermia on the boss, plus the Frozen Orb's Vulnerable if
    worn."""
    lvl = st.special.get("frost_nova_vulnerability", 0.0)
    cd = cooldown_s(c["cooldownTicks"], d)
    debuffs.append(("Frost Nova", procs.HYPOTHERMIA, 0, 1.0, False))
    if ctx.gods is not None:
        dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
        up = procs.proc_up(frost_nova_procs(st, d, ctx), dur, procs.periodic_up(dur, cd))
        debuffs.append(("Frost Nova (Chilled)", procs.CHILLED, int(c["amplifier"]) + 1, up, False))
    demand += c["manaCost"] / cd
    if lvl > 0:
        dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
        throne = frost_nova_procs(st, d, ctx)
        up = procs.proc_up(throne, 4.0 * dur, procs.periodic_up(4.0 * dur, cd))
        debuffs.append(("Frozen Orb (Frost Nova)", procs.VULNERABLE, int(lvl), up, True))
        notes.append(f"Frost Nova t{tier}: Vulnerable from the Frozen Orb modification ({int(lvl)}) for {4 * dur:.0f}s, cast every "
                     f"{cd:.1f}s" + (f" plus {throne:.2f} free casts/s from Frozen Throne" if throne else "") + f" ({up:.0%} uptime)")
    return demand


def empower_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Empower: a toggle (manaCostPerSecond) adding speedPercentAdded to movement speed in the gear bucket
    (EmpowerAbility, MULTIPLY_BASE). It runs on the mana the damage abilities leave, before Rampage (empower_toggle),
    and its speed counts as stable speed only at full uptime."""
    buffs["empower"] = (c["speedPercentAdded"], c["manaCostPerSecond"])
    return demand


def empower_slowness_aura(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Empower (Slowness Aura): a toggle (manaCostPerSecond) slowing mobs in its radius: a Slowness source on the boss
    at uptime 1 for a melee-range build and 0 for a ranged one (d["ranged"], the survival model's positioning)."""
    up = 0.0 if d.get("ranged", False) else 1.0
    debuffs.append(("Slowness Aura", procs.SLOWNESS, 0, up, False))
    buffs["slowness_aura"] = up
    demand += c["manaCostPerSecond"]
    notes.append(f"Slowness Aura t{tier}: Slowness on the boss at {up:.0%} uptime ({c['manaCostPerSecond']:.1f} mana/s)")
    return demand


def sneaky_getaway(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Sneaky Getaway (addon SneakyGetawayAbility): an instant cast whose effect lasts durationTicks x (1 + effect
    duration), the cooldown starting when it ends. While it lasts the Ninja etching adds its roll to the dodge chance
    (sneaky_dodge, inside the 0.95 cap) and speedPercentAdded is added to movement speed (sneaky_speed, not stable)."""
    dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    ninja = etch_value(build, "woldsvaults:sneaky_getaway_ninja")
    buffs["sneaky_dodge"] = float(ninja) * up if ninja else 0.0
    buffs["sneaky_speed"] = c["speedPercentAdded"] * up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Sneaky Getaway t{tier}: up {up:.0%}" + (f", +{float(ninja):.0%} dodge (Ninja etching) while up" if ninja else ""))
    return demand


def totem_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Totem (Rejuvenation): heals totemHealthPerSecond HP per second (player.heal) while standing in its radius; the
    cooldown starts when the totem ends, so it is up dur / (dur + cd)."""
    dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    buffs["totem_heal"] = c["totemHealthPerSecond"] * up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Totem (Rejuvenation) t{tier}: {c['totemHealthPerSecond']:.1f} HP/s at {up:.0%} uptime")
    return demand


def mana_shield_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Mana Shield: an absorption pool of healthPoints for durationTicks (ManaShieldAbility applies the raw duration,
    no effect duration), the cooldown starting when it ends; the survival window gets healthPoints x uptime."""
    dur = c["durationTicks"] / 20.0
    cd = cooldown_s(c["cooldownTicks"], d)
    up = dur / (dur + cd)
    buffs["mana_shield_pool"] = c["healthPoints"] * up
    demand += c["manaCost"] / (dur + cd)
    notes.append(f"Mana Shield t{tier}: {c['healthPoints']:.0f} HP pool at {up:.0%} uptime")
    return demand


def ultimate_shield_base(build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains):
    """Ultimate Shield (addon UltimateShieldAbility): a toggle draining min(baseManaDrainPerTick + manaDrainRampPerSecond
    x seconds on, maxManaDrainPerTick) mana per tick (the config fields wired, ruling 12.10), averaged over the fight at
    kill_time_s / 2 seconds on and paid with the other toggles. While on, a landed hit has percentageDamageAbsorbed of
    it absorbed at manaPerDamageScalar x ultimate_shield_scalar_mult mana per point from the bar (survival_buffs)."""
    k = ctx.knobs
    tick = min(c["baseManaDrainPerTick"] + c["manaDrainRampPerSecond"] * 0.5 * k["kill_time_s"], c["maxManaDrainPerTick"])
    drain = 20.0 * tick
    scalar = max(c["manaPerDamageScalar"] * k["ultimate_shield_scalar_mult"], 1e-5)
    buffs["us"] = (drain, tick, min(max(c["percentageDamageAbsorbed"], 0.0), 1.0), scalar)
    demand += drain
    notes.append(f"Ultimate Shield t{tier}: drain {drain:.1f} mana/s at {0.5 * k['kill_time_s']:.0f} s on, "
                 f"{scalar:.2f} mana per absorbed point")
    return demand


BUFF_HANDLERS = {
    "Taunt_Base": taunt_base,
    "Totem_Player_Damage": totem_player_damage,
    "Rampage_Base": rampage_base,
    "Rampage_Berserker": rampage_berserker,
    "Rampage_Bloodlust": rampage_bloodlust,
    "Rampage_Instinct": rampage_instinct,
    "Battle_Cry_Base": battle_cry_base,
    "Battle_Cry_Spectral_Strike": battle_cry_spectral_strike,
    "Battle_Cry_Lucky_Strike": battle_cry_lucky_strike,
    "Colossus_Base": colossus_base,
    "Concentrate_Base": concentrate_base,
    "Totem_Mana_Regen": totem_mana_regen,
    "Shell_Porcupine": shell_porcupine,
    "Nova_Slow": nova_slow,
    "Empower_Base": empower_base,
    "Empower_Slowness_Aura": empower_slowness_aura,
    "Sneaky_Getaway": sneaky_getaway,
    "Totem_Base": totem_base,
    "Mana_Shield_Base": mana_shield_base,
    "UltimateShield_Base": ultimate_shield_base,
}

if set(BUFF_HANDLERS) != {sid for sid, rec in SPEC_TABLE.items() if rec["buff"] is not None}:
    raise RuntimeError("spec table buff specs and BUFF_HANDLERS differ: "
                       f"{sorted(set(BUFF_HANDLERS) ^ {sid for sid, rec in SPEC_TABLE.items() if rec['buff'] is not None})}")
BUFF_SPECS = list(BUFF_HANDLERS)
BUFF_SKILL = {s: s.split("_")[0] if not s.startswith("Battle_Cry") else "Battle_Cry" for s in BUFF_SPECS}
BUFF_SKILL.update({"Taunt_Base": "Taunt", "Totem_Player_Damage": "Totem", "Totem_Mana_Regen": "Totem", "Colossus_Base": "Colossus",
                   "Concentrate_Base": "Expunge", "Shell_Porcupine": "Shell", "Nova_Slow": "Nova",
                   "Sneaky_Getaway": "Colossus", "Totem_Base": "Totem", "Mana_Shield_Base": "Mana_Shield",
                   "UltimateShield_Base": "UltimateShield"})


def buffs_for(build, ctx, st, d, record):
    """Active buff abilities: returns (buff values, mana/sec demand, notes, flags). Learned buff specs run in the
    build's ability order (so the demand sums in that order); the main ability of an ability family is skipped."""
    from .families import effective_tier, spec_cfg
    buffs = {}
    demand = 0.0
    notes = []
    flags = []
    debuffs = []
    drains = []
    casts = []
    inst_rate = 0.0
    for sid, learned in build.abilities.items():
        if sid not in BUFF_HANDLERS or learned <= 0 or build.family == "ability:" + sid:
            continue
        tier, bonus = effective_tier(ctx, st, sid, learned)
        c = spec_cfg(ctx, st, sid, tier, build)
        if unaffordable(c, d):
            fallback(f"cast-unaffordable-{sid}", f"{sid} costs {c['manaCost']:.0f} mana, more than the bar holds; never cast")
            continue
        d0 = demand
        demand = BUFF_HANDLERS[sid](build, ctx, st, d, tier, c, buffs, demand, notes, flags, debuffs, drains)
        toggle = bool(c.get("manaCostPerSecond"))
        rate = None
        if demand > d0:
            drains.append([f"{sid.replace('_', ' ')} t{tier}", demand - d0])
            if c.get("manaCost", 0.0) > 0 and not toggle:
                rate = (demand - d0) / c["manaCost"]
                inst_rate += rate
        dur = next((c[k] for k in ("durationTicks", "totemDurationTicks", "cloudDuration") if c.get(k)), None)
        casts.append({"spec": sid, "tier": tier, "toggle": toggle, "rate": rate, "mana": demand - d0, "cost": c.get("manaCost", 0.0),
                      "dur": dur * (1.0 + d["effect_duration"]) / 20.0 if dur else None})
    buffs["_drains"] = drains
    buffs["_inst_buff_rate"] = inst_rate
    buffs["_buff_casts"] = casts
    lv = etch_value(build, "etching_lucky_vulnerable")
    if lv:
        buffs["_lucky_vuln"] = (int(round(lv["level"])), float(lv["duration"]))
    tv = etch_value(build, "etching_totem_player_damage_effect")
    if tv and "totem_player_damage" in buffs:
        lvl = int(round(tv["level"]))
        tc = spec_cfg(ctx, st, "Totem_Player_Damage", effective_tier(ctx, st, "Totem_Player_Damage", build.abilities["Totem_Player_Damage"])[0], build)
        dur = tc["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
        period = dur + cooldown_s(tc["cooldownTicks"], d)
        # Applied on every hit inside the totem; each application lasts the etching's duration after the totem ends.
        debuffs.append(("Vulnerable Totem etching", procs.VULNERABLE, lvl - 1, procs.periodic_up(dur + float(tv["duration"]), period), False))
    mana_etchings(build, ctx, st, d, buffs, notes)
    bv = etch_value(build, "etching_storm_arrow_blizzard_effect")
    if bv:
        buffs["_blizzard_vuln"] = (int(round(bv["level"])), float(bv["duration"]))
    if ctx.gods is not None:
        buffs["_rework"] = True
        mc = etch_value(build, "etching_ice_bolt_multicast")
        buffs["_ice_bolt_casts"] = float(mc) if mc else 0.0
    rl = etch_value(build, "etching_rampage_lucky_hit")
    if rl and "rampage" in buffs:
        buffs["rampage_lucky"] = buffs.get("rampage_lucky", 0.0) + float(rl)
        notes.append(f"Rampaging Lucky Hit etching: +{float(rl):.0%} lucky while Rampage is on")
    if "us" in buffs:
        drain = buffs["us"][0]
        deficit = drain - (mana_supply(d, buffs, ctx) - (demand - drain))
        buffs["us_uptime"] = 1.0 if deficit <= 0 else min(1.0, d["mana_max"] / (deficit * ctx.knobs["kill_time_s"]))
    buffs["_cast_debuffs"] = debuffs
    return buffs, demand, notes, flags


def empower_toggle(buffs, free, eth, notes):
    """Empower on the mana the damage abilities leave (before Rampage, same accounting): uptime min(1, free / cost),
    cost x (1 - Ethereal). buffs["empower_speed"] gets its speed only at full uptime (stable speed, ruling 12.4).
    Returns the mana left for Rampage."""
    e = buffs.get("empower")
    if e is None:
        return free
    speed, cost = e
    cost *= 1.0 - eth
    up = min(1.0, free / cost) if cost > 0 else 1.0
    buffs["empower_paid"] = up * cost
    if up >= 1.0 - 1e-9:
        buffs["empower_speed"] = speed
        notes.append(f"Empower: +{speed:.0%} movement speed, paid in full ({cost:.1f} mana/s)")
    else:
        buffs["empower_speed"] = 0.0
        notes.append(f"Empower runs at {up:.0%} uptime ({cost:.1f} mana/s): its +{speed:.0%} speed is not stable, so it does not count")
    return max(0.0, free - up * cost)


US_REGEN_TOL = 1e-9
SURVIVAL_KEYS = ("mana_shield_pool", "sneaky_dodge", "sneaky_speed", "totem_heal", "empower_speed", "sneak_share")


def survival_buffs(buffs, d, ctx):
    """Buff values the survival model reads from the family result. Ultimate Shield: the bar it absorbs from is full
    without sneak-cast Power Dump, else half the refill between sneak casts (pd_bar / 2), and the shield is off for the
    part of each refill below one tick's drain; us_budget = bar / mana per point is what one window can absorb,
    us_absorb that budget per hit of the survive_hits window (survival caps it at pct x the hit). Rework (user ruling
    2026-10-19): the game keeps the shield on while one tick's regeneration covers one tick's drain, so that off share
    applies only when mana_regen / 20 < the tick's drain (equal counts as enough: regeneration and drain are sums of
    rolled values and often tie exactly, so the test takes US_REGEN_TOL relative slack and rounding noise cannot flip
    it)."""
    out = {k: buffs.get(k, 0.0) for k in SURVIVAL_KEYS}
    us = buffs.get("us")
    if us is not None:
        drain, tick, pct, scalar = us
        bar = snap_bar(buffs.get("pd_bar", 0.0), d)
        u = buffs["us_uptime"]
        if bar > 0:
            mana = 0.5 * min(bar, d["mana_max"])
            if not (ctx.gods is not None and d["mana_regen"] / 20.0 >= tick * (1.0 - US_REGEN_TOL)):
                u *= max(0.0, 1.0 - tick / bar)
        else:
            mana = d["mana_max"]
        out.update(us_uptime=u, us_pct=pct, us_budget=mana / scalar, us_drain=drain,
                   us_absorb=mana / scalar / ctx.knobs["survive_hits"])
    return out


def blizzard_nova_ticks(ctx, tier):
    """durationTicks of the Frost Nova a Blizzard (Frozen Impact) tier casts: its `ability` names a Nova_Slow tier id
    (nova_frost_N), which the player's ability tree resolves to that tier's config whether or not Frost Nova is learned."""
    name = str(tier.get("ability", ""))
    tiers = (ctx.abilities.get("Nova_Slow") or {}).get("tiers") or []
    try:
        n = int(name.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        n = 0
    if not 1 <= n <= len(tiers) or "durationTicks" not in tiers[n - 1]:
        fallback(f"blizzard-nova-{name}", f"Blizzard casts {name!r}, which is not a Nova_Slow tier; its Frost Nova is ignored")
        return None
    return float(tiers[n - 1]["durationTicks"])


def frozen_impact(build, ctx, st, d, ev, sources):
    """Blizzard talent (Frozen Impact, CastOnHitTalent): a normal hit on a Hypothermic target casts the tier's Frost
    Nova for free with chance `probability`. Against the boss only the Frozen Orb's Vulnerable counts (the cast never
    refreshes Hypothermia: NovaSpeedAbility schedules it only on targets without it): procs at p x normal hits x
    Hypothermia uptime, each lasting 4 x the nova's duration, merged into the build's other Frozen Orb source."""
    t = int(st.attr.get("x:talent_tier:Blizzard", 0))
    lvl = st.special.get("frost_nova_vulnerability", 0.0)
    if not t or lvl <= 0 or ev.get("normal", 0.0) <= 0:
        return
    tier = ctx.talents["Blizzard"]["tiers"][t - 1]
    ticks = blizzard_nova_ticks(ctx, tier)
    if ticks is None:
        return
    miss = 1.0
    for s in sources:
        if s[1] == procs.HYPOTHERMIA:
            miss *= 1.0 - s[3]
    rate = tier["probability"] * ev["normal"] * (1.0 - miss)
    u = procs.proc_up(rate, 4.0 * ticks * (1.0 + d["effect_duration"]) / 20.0)
    if u <= 0:
        return
    for i, s in enumerate(sources):
        if s[1] == procs.VULNERABLE and s[4]:
            sources[i] = (s[0] + " + Frozen Impact", s[1], s[2], 1.0 - (1.0 - s[3]) * (1.0 - u), True)
            return
    sources.append(("Frozen Orb (Frozen Impact)", procs.VULNERABLE, int(lvl), u, True))
def cloud_trigger(c, d):
    """Effect-cloud trigger chance per qualifying hit (GearAttributeEvents.triggerEffectCloudsActive): min(1, chance x
    (1 + cloud-proc etching, not modeled)) + effect_cloud_chance_additive, streak-broken by Entropy; no Luck."""
    return min(1.0, min(1.0, c["chance"]) + d.get("cloud_chance", 0.0))


def cloud_normal_rate(build, ctx, d):
    """Qualifying hits per second for on-hit clouds: the swings of a melee family or of a weaving build (the event
    rate procs.events gets as "normal")."""
    if build.family.startswith("melee:") or getattr(build, "weave", False):
        from .families import swing_rate
        return swing_rate(d, ctx)
    return 0.0


def mana_etchings(build, ctx, st, d, buffs, notes):
    """Blocking Mana (shield in the offhand: mana per blocked boss attack), the cloud-poison part of Poison Mana (mana per
    cloud poison hit) and the cloud poison stream itself (buffs["_cloud_poison"] = (damage per hit, hits per second),
    scored in procs.finalize). Vanilla poison is PoisonOverrideEffect in vaults: one hit of amplifier + 1 every 20 ticks
    while the cloud keeps it refreshed; u = 1 - e^(-rate x (floor(ticks x (1 + ed)) + 20) / 20) per cloud; amplifiers
    never add, so the strongest one deals every hit and each further active cloud adds a refresh hit per second."""
    from .build import offhand_counts
    bm = etch_value(build, "etching_blocking_mana")
    off = build.items.get("offhand")
    if bm and off is not None and off.type == "shield" and offhand_counts(build):
        reach = d.get("boss_hits_reaching", 1.0 / ctx.knobs["hit_interval_s"])
        m = float(bm) * d["block"] * reach
        buffs["etch_mana"] = buffs.get("etch_mana", 0.0) + m
        notes.append(f"Blocking Mana etching: +{float(bm):.0f} mana per blocked attack -> {m:.1f} mana/s")
    raw, hits = 0.0, 0.0
    normal = None
    for c in st.clouds:
        if c.get("poison", 0.0) <= 0:
            continue
        if normal is None:
            normal = cloud_normal_rate(build, ctx, d)
        dur = (math.floor(c["ticks"] * (1.0 + d["effect_duration"]) + 1e-9) + 20.0) / 20.0
        u = procs.proc_up(cloud_trigger(c, d) * normal, dur)
        if u > 0:
            raw = max(raw, c["poison"])
            hits += u
    if hits > 0:
        buffs["_cloud_poison"] = (raw, hits)
        pm = etch_value(build, "etching_poison_mana")
        if pm:
            buffs["etch_mana"] = buffs.get("etch_mana", 0.0) + float(pm) * hits
            notes.append(f"Poison Mana etching: +{float(pm):.0f} mana per cloud poison hit ({float(pm) * hits:.1f} mana/s)")


def mana_below_uptime(cast, pd_rate, pd_in, mana_max, frac):
    """Share of casts made with the bar below frac of max mana (the mana-state rule of plan WP-R3 at any threshold):
    1 when a damage ability in the rotation is mana-limited (share < 1); with sneak-cast Power Dump (pd_rate dumps per
    second, pd_in mana per second refilling the bar) the share of each refill spent below frac; else 0 (casts come
    off cooldown with the bar full)."""
    if any(x["share"] < 1.0 for x in cast):
        return 1.0
    if pd_rate > 0:
        return min(1.0, pd_rate * frac * mana_max / pd_in) if pd_in > 0 else 1.0
    return 0.0


def apply_buff_stats(buffs, d):
    """Buff-derived stat write-backs that evaluate applies to d before the damage cycle (buffs from a buffs_for pre-pass
    after the Arcane Cascade fixed point): the AP Totem etching's ability power multiplier."""
    if "ap_totem_mult" in buffs:
        d["ability_power"] *= buffs["ap_totem_mult"]
    return d


def colossus_resistance(st, d, buffs, resist_bonus):
    """Colossus Titan etching (addon MixinResistanceHelper): while Colossus is active the resistance limit gains the
    etching's value; resistance = u x min(res, cap + v, 0.95) + (1 - u) x min(res, cap), res = the resistance attribute
    + Colossus's expected bonus, u = Colossus's uptime. The addon adds the roll after its 0.95 clamp (the limit can pass
    100%); by ruling (user, 2026-10-19) the game gets patched and both ledgers keep the raised limit at 0.95. None
    without the etching or Colossus."""
    v = st.attr.get("x:colossus_titan", 0.0)
    u = buffs.get("colossus_up", 0.0)
    if not v or u <= 0:
        return None
    res = st.get("resistance") + resist_bonus
    cap = d["resistance_cap"]
    return u * min(res, cap + v, COLOSSUS_TITAN_MAX) + (1.0 - u) * min(res, cap)


COLOSSUS_TITAN_MAX = 0.95


def _cd(c, d):
    """The cast ability's cooldown (abilities.cd_s semantics: the Lucky Reset marker applies)."""
    return cooldown_s(c["cooldownTicks"], d, c.get("_reset", False))


def _totem_interval(c, d, share):
    """Totem of Hatred marks the boss between damage ticks while the totem stands."""
    dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    i = c["totemDamageIntervalTicks"]
    up = min(1.0, dur * share / (dur + _cd(c, d)))
    return up * i / (i + 1.0)


def _maw_slowness(c, d, share):
    """Hungry Maw's 9 s Slowness per cast."""
    return min(1.0, 9.0 * (1.0 + d["effect_duration"]) * share / _cd(c, d))


CAST_DEBUFF_RULES = {
    "totem_interval": _totem_interval,
    "maw_slowness": _maw_slowness,
    "always": lambda c, d, share: 1.0,
    "share": lambda c, d, share: min(1.0, share),
}
CAST_DEBUFFS = {sid: rec["cast_debuffs"] for sid, rec in SPEC_TABLE.items() if rec["cast_debuffs"]}


def _check_cast_debuffs():
    for sid, rules in CAST_DEBUFFS.items():
        for kind, rule, label in rules:
            if rule not in CAST_DEBUFF_RULES or not hasattr(procs, kind):
                raise RuntimeError(f"spec table: {sid} has an unknown cast debuff {kind} / {rule}")


_check_cast_debuffs()


def cast_debuffs(spec, c, d, share, out, buffs=None):
    """Harmful effects a rotation ability puts on the boss, at its share of the cooldown rate (Sneaky Advantage terms;
    the spec table's cast_debuffs): Totem of Hatred's mark between damage ticks, Hungry Maw's 9 s Slowness, the
    Necromancy curse while summons live, Hypothermia from Shard Blizzard or a Frost Nova main (in proportion to the
    share; full uptime at the full rate; the hyperboss ignores Glacial Shatter, stuns and Bleed, and Chilled except in the
    rework, where Shard Blizzard and Ice Bolt chill it: CHILL_CASTS). With the
    Blizzard Effect etching every shard puts Vulnerable (level - 1, uncapped ledger rule; the game clamps to
    min(8, level) - 1) on the boss for the etching's ticks: up while the cloud rains and that long after it."""
    if share <= 0:
        return
    if d.get("reset_spec") == spec:
        c = dict(c, _reset=True)
    for kind, rule, label in CAST_DEBUFFS.get(spec, ()):
        out.append((label, getattr(procs, kind), 0, CAST_DEBUFF_RULES[rule](c, d, share), False))
    if buffs is not None and buffs.get("_rework") and spec in CHILL_CASTS:
        out.append(CHILL_CASTS[spec](c, d, share, buffs))
    if spec == "Shard_Blizzard" and buffs and buffs.get("_blizzard_vuln"):
        lvl, ticks = buffs["_blizzard_vuln"]
        active = c["cloudDuration"] * (1.0 + d["effect_duration"]) / 20.0
        period = active + _cd(c, d)
        out.append(("Blizzard Effect etching", procs.VULNERABLE, lvl - 1, min(1.0, share * (active + ticks / 20.0) / period), False))


def _chill_blizzard(c, d, share, buffs):
    """Shard Blizzard (rework): every shard chills its target for slowDuration x (1 + effect duration) at the config
    amplifier (VaultBlizzardShard.destroy), so the boss stays Chilled while the cloud rains and that long after it."""
    active = c["cloudDuration"] * (1.0 + d["effect_duration"]) / 20.0
    slow = c["slowDuration"] * (1.0 + d["effect_duration"]) / 20.0
    return ("Shard Blizzard (Chilled)", procs.CHILLED, int(c["amplifier"]) + 1, min(1.0, share * (active + slow) / (active + _cd(c, d))), False)


def _chill_ice_bolt(c, d, share, buffs):
    """Ice Bolt (rework): every bolt chills its target for durationTicks x (1 + effect duration) at the config
    amplifier (IceBoltArrowAbility:118); Frozen Barrage recasts count as bolts."""
    dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    casts = 1.0 + buffs.get("_ice_bolt_casts", 0.0)
    return ("Ice Bolt (Chilled)", procs.CHILLED, int(c["amplifier"]) + 1, min(1.0, share * casts * dur / _cd(c, d)), False)


# Rework: rotation casts that chill the boss (proposed, user 2026-10-21: the hyperboss can be Chilled).
CHILL_CASTS = {"Shard_Blizzard": _chill_blizzard, "Ice_Bolt_Base": _chill_ice_bolt}


def mana_supply(d, buffs, ctx):
    """Sustained mana per second from regeneration (user ruling: the vault is continuous, so the pool is never a source).
    The Mana Regen Totem's MULTIPLY_BASE adds to gear mana regen on the same attribute. Mana Steal comes on top."""
    return d["mana_regen"] + buffs.get("mana_regen_add", 0.0) * d["mana_regen_vt"] + buffs.get("etch_mana", 0.0)


def mana_steal_pct(st, ctx):
    """Mana Steal (ManaLeechLuckyHitTalent): every lucky hit restores max mana x this (twice with Luckiest Hit)."""
    t = int(st.attr.get("x:talent_tier:Mana_Steal", 0))
    pct = ctx.talents["Mana_Steal"]["tiers"][t - 1]["maxManaPercentage"] if t else 0.0
    if st.gfx and st.gfx["luckiest"]:
        pct *= 2.0
    return pct


def fury_for(st, ctx, hits_per_s):
    """Ultra Rampaging steady state at this many player hits per second on the boss: (<M>, <M cbrt>, incoming)."""
    if not (st.gfx and st.gfx["fury"]):
        return None
    from .godfx import fury_at
    return fury_at(ctx.gods.fury_table, hits_per_s)


def starved_free(st, d, free, short):
    """Mana Starved: below half mana, regen rises by up to max_bonus x regen, enough to cover `short` if it can."""
    if st.gfx and st.gfx["mana_starved"] and short > free:
        return free + min(st.gfx["mana_starved"] * d["mana_regen"], short - free)
    return free


def ethereal_chance(st, ctx):
    """Ethereal: each mana payment is free with this chance (ManaCostHelper.rollsEthereal), so costs run at x (1 - p)."""
    t = int(st.attr.get("x:talent_tier:Ethereal", 0))
    return float(ctx.talents["Ethereal"]["tiers"][t - 1].get("chance", 0.0)) if t else 0.0


def rampage_uptime(buffs, free, notes, fury=None):
    """Rampage runs on the mana left after the other buffs; scales buffs["rampage"] by its uptime. With Ultra
    Rampaging (fury = (<M>, <M cbrt(F/6500)>)) the damage increase becomes DI <M> + <M cbrt>."""
    up = 1.0
    if "rampage" in buffs:
        if fury is not None:
            buffs["rampage"] = buffs["rampage"] * fury[0] + fury[1]
        ramp_cost = buffs.get("rampage_cost", 0.0)
        up = min(1.0, free / ramp_cost) if ramp_cost > 0 else 1.0
        if "rampage_fixed_uptime" in buffs:
            up = min(up, buffs["rampage_fixed_uptime"])
        buffs["rampage"] *= up
        notes.append(f"Rampage uptime {up:.0%}")
    return up


# Power Dump rework (user, 2026-10-18): only abilities cast while sneaking use it. Instant casts drain the bar; held
# channels (HoldManaAbility, the spec table's pd_hold) drain pd_hold_drain of the bar per second. Each cast keeps its
# own bonus for its own hits.
PD_HOLD = {sid for sid, rec in SPEC_TABLE.items() if rec["pd_hold"]}
SPECTRAL_SPECS = {sid for sid, rec in SPEC_TABLE.items() if rec["spectral"] is True}
SPECTRAL_RULES = {sid: rec["spectral"] for sid, rec in SPEC_TABLE.items() if rec["spectral"]}


def pd_eligible(spec, c):
    """An instant-mana or held-channel ability (toggles and others never use Power Dump)."""
    return spec in PD_HOLD or (c.get("manaCost", 0.0) > 0 and not c.get("manaCostPerSecond"))


def pd_bmax(st, ctx, d):
    """Largest Power Dump bonus (user, 2026-10-21): per_mana x sqrt(power_dump_sqrt_ref x max mana), reached by a cast
    that spends the whole bar above its cost."""
    return st.gfx["power_dump"] * math.sqrt(ctx.knobs["power_dump_sqrt_ref"] * d["mana_max"])


def pd_curve(st, ctx, d, surplus):
    """Power Dump multiplier of a cast that spends `surplus` mana above its cost: 1 + pd_bmax x surplus / max mana
    (linear in the share of the bar, so the bonus per second does not depend on how often the player dumps)."""
    if d["mana_max"] <= 0:
        return 1.0
    return 1.0 + pd_bmax(st, ctx, d) * min(surplus, d["mana_max"]) / d["mana_max"]


def snap_bar(bar, d):
    """A Power Dump surplus snapped to 0 within rounding noise of an empty bar (the square root is steep there)."""
    return 0.0 if bar <= 1e-9 * max(1.0, d["mana_max"]) else bar


def nova_pd_rate(build, st, aps):
    """Sneaking Starforge novas per second (every hitsRequired-th swing; build.pd holds "Starforge_Nova"): each one
    dumps the bar like a free sneaking cast (balance change, user 2026-10-18)."""
    if not (st.gfx and st.gfx["power_dump"]) or "Starforge_Nova" not in getattr(build, "pd", set()):
        return 0.0
    hits, pct = st.special.get("arcane_nova_hits", 0.0), st.special.get("arcane_nova_pct", 0.0)
    if hits <= 0 or pct <= 0 or aps <= 0:
        return 0.0
    return aps / hits


def low_mana_state(ctx, d, limited, pd_rate, inflow):
    """Share of the fight below 20% mana (Depleted, Methodical): 1 when a damage ability is mana-limited (the bar is
    spent as it fills), else with sneaking Power Dump dumps at pd_rate per second the refill time of the bar's first
    20% after each dump, min(1, pd_rate x 0.2 x mana_max / inflow), inflow = supply minus the buffs' drain; else 0.
    The uptime_low_mana knob when mana_state_heuristic is off."""
    k = ctx.knobs
    if not k["mana_state_heuristic"]:
        return k["uptime_low_mana"]
    if limited:
        return 1.0
    if pd_rate > 0:
        if inflow <= 1e-9 * max(1.0, d["mana_max"]):
            return 1.0
        return min(1.0, pd_rate * 0.2 * d["mana_max"] / inflow)
    return 0.0


def spectral_ok(spec, build):
    """Hits that spend Spectral Strike stacks (the spec table's spectral): fangs, Fireshot, Arcane Rail and Lucky Bolt
    Ice Bolts (swings too)."""
    rule = SPECTRAL_RULES.get(spec, False)
    return rule is True or (rule == "lucky_bolt" and etch_value(build, "etching_ice_bolt_lucky") is not None)


def spectral_stacks(sp, hits):
    """Average stacks spent per qualifying hit: each Battle Cry cast brings `stacks` (30 from a boss), every hit spends
    at most `u`, and stacks last D seconds (recasts every P seconds add to unspent ones)."""
    stacks, u, P, D = sp
    if hits <= 0:
        return 0.0
    return min(stacks, hits * u * min(P, D)) / (P * hits)


def spectral_ad(d, buffs, ctx):
    return ctx.knobs["spectral_ap_per_stack"] * buffs.get("spectral_s", 0.0) * d["ap_base"]
