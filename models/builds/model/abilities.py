"""Per-ability damage models (MECHANICS §6.2.1 + the ability spec research, 0.34.1)."""
import math

from .damage import HitClass, evaluate_hit, bug, registry_multiplier, thorns_flat, thorns_reflect
from .log import fallback

PLAIN_MAX_HITS_PER_S = 2.0
# Specs whose hits can leech (PlayerLeechHelper skips AOE, DOT, REFLECT, JAVELIN, CHARMED, EFFECT and TOTEM hits;
# Necromancy damage comes from the minions). Ice Bolt only as Lucky Bolt (an arrow instead of an AOE hit).
LEECH_SPECS = {"Fireball_Fireshot", "Smite_Base", "Smite_Archon", "Arcane_Rail", "Storm_Arrow_Base", "Fangs_Base", "Fangs_Maw"}
# Leech-capable specs whose hits also roll lucky hits (so Life Steal procs on them).
LUCKY_SPECS = {"Fangs_Base", "Fangs_Maw"}


def cd_s(cfg, d, key="cooldownTicks"):
    return max(0.05, cfg.get(key, 0) * (1.0 - d["cooldown_reduction"]) / 20.0)


def pack_targets(radius, d, ctx):
    r = radius * d["aoe_multiplier"]
    n = ctx.knobs["pack_size"]
    return 1.0 + (n - 1) * min(1.0, (r / ctx.knobs["pack_radius"]) ** 2)


def etch_value(build, name):
    for it in build.items.values():
        e = it.etching
        if e and (e["attribute"] == "the_vault:" + name or e["attribute"] == name):
            return e["value"] if e["value"] is not None else True
    return None


def plain_rate(rate, notes):
    if rate > PLAIN_MAX_HITS_PER_S:
        notes.append(f"repeat hits {rate:.1f}/s are i-frame clipped to ~{PLAIN_MAX_HITS_PER_S:.0f}/s on one target")
        return PLAIN_MAX_HITS_PER_S
    return rate


def lightning_bonus(st, ctx):
    lt = int(st.attr.get("x:talent_tier:Lightning_Damage", 0))
    if lt:
        return ctx.talents["Lightning_Damage"]["tiers"][lt - 1]["percentDamageDealt"]
    return 0.0


class Model:
    def __init__(self, label, fn, scaling, tags=()):
        self.label = label
        self.fn = fn
        self.scaling = scaling
        self.tags = set(tags)

    def compute(self, build, ctx, st, d, cfg, buffs, target_hp, record):
        return self.fn(build, ctx, st, d, cfg, buffs, target_hp, record)


def finish(per_hit, rate, steps, targets, mana_per_s, notes, flags=(), lo=None, hi=None):
    dps = per_hit * rate
    return {"dps": dps, "dps_lo": (lo if lo is not None else per_hit) * rate,
            "dps_hi": (hi if hi is not None else per_hit) * rate, "pack_dps": dps * targets,
            "targets": targets, "hit": per_hit, "rate": rate, "steps": steps, "mana_per_s": mana_per_s,
            "notes": notes, "flags": list(flags)}


def scale(out, m):
    for k in ("dps", "dps_lo", "dps_hi", "pack_dps", "hit"):
        out[k] *= m
    return out


def ap_instant(pct_keys, radius_key=None, flags="AP+AOE", echo=False, mult=1.0, extra_note=""):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = [extra_note] if extra_note else []
        if isinstance(pct_keys, tuple):
            pct = 0.5 * (cfg[pct_keys[0]] + cfg[pct_keys[1]])
        else:
            pct = cfg[pct_keys]
        raw = d["ability_power"] * pct * mult
        hc = HitClass(flags, ap=True, aoe_flag="AOE" in flags, iframe="plain", echo_ok=echo)
        per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        cd = cd_s(cfg, d)
        rate = plain_rate(1.0 / cd, notes)
        targets = pack_targets(cfg.get(radius_key, 0.0), d, ctx) if radius_key else 1.0
        return finish(per, rate, steps, targets, cfg.get("manaCost", 0.0) / cd, notes, lo=lo, hi=hi)
    return fn


def fireball_base(build, ctx, st, d, cfg, buffs, target_hp, record):
    out = ap_instant("percentAbilityPowerDealt", "radius")(build, ctx, st, d, cfg, buffs, target_hp, record)
    p = st.special.get("fireball_recast", 0.0)
    if p > 0:
        scale(out, 1.0 + p)
        out["notes"].append(f"Everflame: {p:.0%} chance of a free second fireball 2 s later (x{1 + p:.2f})")
    return out


def nova_base(build, ctx, st, d, cfg, buffs, target_hp, record):
    out = ap_instant("percentAbilityPowerDealt", "radius")(build, ctx, st, d, cfg, buffs, target_hp, record)
    v = etch_value(build, "etching_nova_recast")
    if v:
        m = 1.0 + min(1.0, v / 100.0)
        scale(out, m)
        out["notes"].append(f"Nova Recast etching: {min(100, v):.0f}% chance of a free recast (x{m:.2f})")
    return out


def dot(pct_key, dur_fn, radius_key, label):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = [label + " ticks run under DOT+POISON_NOVA: no damage increase, no multiplier registry"]
        total = d["ability_power"] * cfg[pct_key]
        dur = dur_fn(cfg, d)
        ticks = max(1, int(dur))
        hc = HitClass("DOT", ap=True, aoe_flag=True, iframe="shotgun")
        per, lo, hi, steps = evaluate_hit(total / ticks, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        period = max(cd_s(cfg, d), dur)
        notes.append(f"{ticks} ticks over {dur:.1f}s, recast every {period:.1f}s (an early recast resets the DoT)")
        return finish(per, ticks / period, steps, pack_targets(cfg.get(radius_key, 0.0), d, ctx),
                      cfg.get("manaCost", 0.0) / period, notes, lo=lo, hi=hi)
    return fn


def smite(base_flag):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = []
        pct = 0.5 * (cfg["percentAbilityPowerDealtMin"] + lightning_bonus(st, ctx) + cfg["percentAbilityPowerDealtMax"])
        hc = HitClass("AP+SMITE_BASE" if base_flag else "AP+SMITE", ap=True, iframe="shotgun", echo_ok=base_flag)
        per, lo, hi, steps = evaluate_hit(d["ability_power"] * pct, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        bolts = 20.0 / max(1, cfg["intervalTicks"])
        n = max(1.0, ctx.knobs["smite_targets_in_range"])
        rate = bolts / n
        notes.append(f"{bolts:.1f} bolts/s, each at one random entity in range: the boss gets 1/{n:.0f} of them "
                     f"(knob smite_targets_in_range; adds and summons share the bolts)")
        ev = etch_value(build, "etching_smite_echo")
        if ev:
            rate *= 1.0 + ev
            notes.append(f"Echoing Smite etching: each bolt re-strikes 10 ticks later at x{ev:.2f}")
        mana = cfg.get("manaCostPerSecond", 0.0) + cfg.get("additionalManaPerBolt", 0.0) * bolts
        notes.append("shotgun hits: vanilla i-frames never clip a bolt")
        return finish(per, rate, steps, 1.0, mana, notes, lo=lo, hi=hi)
    return fn


def blast_wave(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    pct = 0.5 * (cfg["percentAbilityPowerDealtMin"] + lightning_bonus(st, ctx) + cfg["percentAbilityPowerDealtMax"])
    hc = HitClass("AP+AOE+SMITE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * pct, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    rate = plain_rate(20.0 / max(1, cfg["intervalTicks"]), notes)
    return finish(per, rate, steps, pack_targets(cfg["radius"], d, ctx), cfg.get("manaCostPerSecond", 0.0), notes, lo=lo, hi=hi)


def arcane_beam(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    hc = HitClass("AP+EFFECT", ap=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * cfg["percentAbilityPowerDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    rate = plain_rate(20.0, notes)
    pierce = etch_value(build, "etching_arcane_pierce")
    targets = 1.0 + (min(ctx.knobs["pack_size"], pierce) - 1) * 0.5 if pierce else 1.0
    return finish(per, rate, steps, targets, cfg.get("manaCostPerSecond", 0.0), notes, lo=lo, hi=hi)


def arcane_prism(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    hc = HitClass("AP+EFFECT+AOE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * cfg["percentAbilityPowerDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    pulses = max(1, cfg["durationTicks"] // max(1, cfg["intervalTicks"]))
    period = max(cd_s(cfg, d), cfg["durationTicks"] / 20.0)
    rate = plain_rate(pulses / period, notes)
    targets = min(cfg.get("maxTargets", 8), pack_targets(cfg["radius"], d, ctx))
    return finish(per, rate, steps, targets, cfg.get("manaCost", 0.0) / period, notes, lo=lo, hi=hi)


def chain_lightning(build, ctx, st, d, cfg, buffs, target_hp, record):
    out = ap_instant(("percentAbilityPowerDealtMin", "percentAbilityPowerDealtMax"), None)(build, ctx, st, d, cfg, buffs, target_hp, record)
    scale(out, 1.0 + lightning_bonus(st, ctx))
    r = cfg.get("chainRange", 0.0) * d["aoe_multiplier"]
    out["targets"] = 1.0 + (ctx.knobs["pack_size"] - 1) * min(1.0, (r / ctx.knobs["pack_radius"]) ** 2)
    out["pack_dps"] = out["dps"] * out["targets"]
    out["notes"].append("hits the target and every mob within chainRange of it at full damage")
    return out


def orbs(build, ctx, st, d, cfg, buffs, target_hp, record):
    out = ap_instant(("percentAbilityPowerDealtMin", "percentAbilityPowerDealtMax"), None)(build, ctx, st, d, cfg, buffs, target_hp, record)
    m = 1.0 + lightning_bonus(st, ctx)
    v = etch_value(build, "etching_lightning_orb_triple_damage")
    if v:
        m *= 3.0 * (1.0 - v)
        out["notes"].append(f"Tristorm Orbs: 3 orbs at x{1 - v:.2f}")
    scale(out, m)
    out["targets"] = 3.0
    out["pack_dps"] = out["dps"] * 3.0
    return out


def charged_bolts(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["assumes half the bolt fan hits a boss-sized target"]
    pct = 0.5 * (cfg["percentAbilityPowerDealtMin"] + cfg["percentAbilityPowerDealtMax"]) * (1.0 + lightning_bonus(st, ctx))
    n = 0.5 * cfg["boltCount"]
    F, R = cfg["fullDamageHitsPerTarget"], cfg["repeatHitDamageMultiplier"]
    eff = min(n, F) + R * max(0.0, n - F)
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="shotgun")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * pct, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    return finish(per, eff / cd, steps, 3.0, cfg["manaCost"] / cd, notes, lo=lo, hi=hi)


def storm_arrow(build, ctx, st, d, cfg, buffs, target_hp, record):
    pct = 0.5 * (cfg["percentAbilityPowerDealtMin"] + lightning_bonus(st, ctx) + cfg["percentAbilityPowerDealtMax"])
    hc = HitClass("AP+SMITE", ap=True, iframe="shotgun")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * pct, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    D = cfg["cloudDuration"] * (1.0 + d["effect_duration"])
    shots = math.ceil((D + 1) / (cfg["intervalTicks"] + 1))
    period = D / 20.0 + cd_s(cfg, d)
    notes = [f"{shots} bolts per {D / 20:.1f}s cloud; the cooldown starts after the cloud"]
    return finish(per, shots / period, steps, 1.0, cfg["manaCost"] / period, notes, lo=lo, hi=hi)


def ice_bolt(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    lucky_bolt = etch_value(build, "etching_ice_bolt_lucky") is not None
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    baked = d["attack_damage"] * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * (1.0 if lucky_bolt else m_reg)
    lucky_works = lucky_bolt and getattr(build, "lucky_bolt_works", False)
    hc = HitClass("arrow (Lucky Bolt)" if lucky_bolt else "AOE", aoe_flag=not lucky_bolt, lucky=lucky_works,
                  di_baked=True, m_baked=not lucky_bolt, iframe="shotgun")
    per, lo, hi, steps = evaluate_hit(cfg["damagePerBolt"], baked, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    casts = 1.0
    v = etch_value(build, "etching_ice_bolt_multicast")
    if v:
        casts += v
        notes.append(f"Frozen Barrage: +{v:.0f} recasts per cast, each pays full mana")
    if lucky_bolt:
        notes.append("Lucky Bolt: lucky hits NOT counted (user reports they fail in game); registry applies at the hurt stage instead of baked; stops Glacial Shatter")
    out = finish(per, casts / cd, steps, 1.0, cfg["manaCost"] * casts / cd, notes, lo=lo, hi=hi)
    out["leech_ok"] = lucky_bolt
    out["lucky_ok"] = lucky_works
    return out


def shard_blizzard(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    baked = d["attack_damage"] * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * m_reg
    hc = HitClass("AOE", aoe_flag=True, di_baked=True, m_baked=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(cfg["damagePerShard"], baked, hc, d, st, build, ctx, target_hp, buffs, record)
    D = cfg["cloudDuration"] * (1.0 + d["effect_duration"])
    shots = math.ceil((D + 1) / (cfg["intervalTicks"] + 1))
    active = D / 20.0
    period = active + cd_s(cfg, d)
    rate = plain_rate(shots / active, notes) * active / period
    return finish(per, rate, steps, pack_targets(cfg["radius"], d, ctx) * 0.5, cfg["manaCost"] / period, notes, lo=lo, hi=hi)


def javelin(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["JAVELIN flag: gets damage increase and the registry, never lucky-hits"]
    hc = HitClass("JAVELIN", iframe="javelin")
    per, lo, hi, steps = evaluate_hit(d["attack_damage"] * cfg["percentAttackDamageDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    targets = 1.0 + min(ctx.knobs["pack_size"] - 1, cfg.get("piercing", 0)) * 0.5
    if cfg.get("numberOfJavelins"):
        targets += cfg["numberOfJavelins"] * 0.3
        notes.append("Scatter only splits on block hits; pack value assumes some splits")
    v = etch_value(build, "etching_extra_piercing_javelin")
    if v and "piercing" in cfg and "numberOfJavelins" not in cfg:
        targets += 2 * v
        notes.append(f"Scatter Pierce: 2 side javelins at x{v:.2f} (pack damage)")
    return finish(per, 1.0 / cd, steps, targets, cfg["manaCost"] / cd, notes, lo=lo, hi=hi)


def earthquake(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["AD-scaled but AP+AOE-flagged: DI and the registry are baked in once (correct, not a bug)"]
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    baked = d["attack_damage"] * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * m_reg
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(0.0, baked, hc, d, st, build, ctx, target_hp, buffs, record)
    shocks = cfg["shockCount"]
    period = shocks * cfg["shockIntervalTicks"] / 20.0 + cd_s(cfg, d)
    return finish(per, shocks / period, steps, pack_targets(cfg["radius"], d, ctx), cfg["manaCost"] / period, notes, lo=lo, hi=hi)


def grenade(build, ctx, st, d, cfg, buffs, target_hp, record):
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    baked = d["attack_damage"] * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * m_reg
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(0.0, baked, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    return finish(per, 1.0 / cd, steps, pack_targets(cfg["radius"], d, ctx), cfg["manaCost"] / cd, [], lo=lo, hi=hi)


def necromancy(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["minions need souls from cursed non-boss kills (bosses give 0); all minions deal equal raw damage, so ~2 hits/s land on one target",
             "assumes a full minion cap summoned before the boss"]
    hc = HitClass("AP+NECRO", ap=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(d["ability_power"] * cfg["percentAbilityPowerDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    rate = min(float(cfg["summonCap"]), PLAIN_MAX_HITS_PER_S)
    return finish(per, rate, steps, 1.5, 0.0, notes, flags=["niche: needs a pre-built minion army from non-boss kills"], lo=lo, hi=hi)


def dash_damage(build, ctx, st, d, cfg, buffs, target_hp, record):
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    hc = HitClass("AOE", aoe_flag=True, m_baked=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(d["attack_damage"] * cfg["attackDamagePercentPerDash"] * m_reg, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    return finish(per, plain_rate(1.0 / cd, []), steps, 2.0, cfg["manaCost"] / cd, ["must dash through the target"], lo=lo, hi=hi)


def fangs(maw):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = ["one wave only (waveCount is dead code in 0.34.1)",
                 "fang hits get damage increase, the registry, Rampage, Battle Cry, flat adds and lucky hits"]
        ad = d["attack_damage"] * buffs.get("empower_ad_mult", 1.0)
        if not maw and etch_value(build, "ravenous_fangs") is not None:
            ad *= 0.25
            notes.append("Ravenous Fangs: x0.25 AD for a doubled execute threshold (bosses are exempt from execute)")
        raw = ad * cfg["damageMultiplier"] + cfg["baseDamage"]
        hc = HitClass("FANG", normal=True, lucky=True, iframe="reset", rampage=True)
        per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        hits = 8.7 if maw else 3.0
        cd = cd_s(cfg, d)
        notes.append(f"~{hits} fang overlaps on a 2-block-wide boss per cast")
        targets = 1.0 if maw else pack_targets(cfg["radius"], d, ctx) / 2.0
        flags = ["niche: fang hits only take lucky hits and Rampage while your last melee swing was a full charge "
                 "(AttackScaleHelper gate), so keep swinging between casts; fang count per cast assumes a ~2-block-wide boss"]
        return finish(per, hits / cd, steps, max(1.0, targets), cfg["manaCost"] / cd, notes, flags=flags, lo=lo, hi=hi)
    return fn


def implode(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["consumes all mana: sustained damage = mana regenerated per cooldown x percentManaDealt"]
    cd = cd_s(cfg, d)
    mana_per_cast = min(d["mana_max"], d["mana_regen"] * cd)
    hc = HitClass("AOE", aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(mana_per_cast * cfg["percentManaDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    return finish(per, 1.0 / cd, steps, pack_targets(cfg["radius"], d, ctx), 0.0, notes, lo=lo, hi=hi)


def life_tap(build, ctx, st, d, cfg, buffs, target_hp, record):
    extra = etch_value(build, "etching_life_tap_extra_damage") or 0.0
    raw = d["health"] * cfg["percentHealthDrained"] * (cfg["damagePerHealth"] + extra)
    hc = HitClass("AOE", aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    return finish(per, 1.0 / cd, steps, pack_targets(cfg["radius"], d, ctx), 0.0,
                  ["spends percentHealthDrained of max HP per cast; assumes full HP each cast"],
                  flags=["niche: spends most of your HP per cast"], lo=lo, hi=hi)


def shield_bash(ram):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = []
        m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
        ad = d["attack_damage"]
        ev = etch_value(build, "etching_shield_bash_damage") or 0.0
        raw = ad * (1.0 + d["block"] * cfg["blockChanceDamageScalar"]) + ad * ev
        if ram:
            raw += thorns_flat(d, buffs) * cfg.get("thornsDamageScalar", 0.0)
        hc = HitClass("AOE", aoe_flag=True, m_baked=True, iframe="plain")
        per, lo, hi, steps = evaluate_hit(raw * m_reg, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        cd = cd_s(cfg, d)
        return finish(per, plain_rate(1.0 / cd, notes), steps, 2.5, cfg["manaCost"] / cd, notes, lo=lo, hi=hi)
    return fn


def volley(build, ctx, st, d, cfg, buffs, target_hp, record):
    """Fireball Volley. WV's Mitosis mixin explodes the fireball on every bounce (with or without the etching) and,
    with Mitosis, spawns N one-shot children per bounce; every explosion then runs at 0.5x AP."""
    notes = []
    k = ctx.knobs
    mit = etch_value(build, "woldsvaults:fireball_volley_mitosis")
    pen = 0.5 if mit else 1.0
    raw = d["ability_power"] * cfg["percentAbilityPowerDealt"] * pen
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    B = k["volley_bounces"]
    bounce = bool(mit) or bug(build, "VOLLEY-BOUNCE-EXPLODE")
    explosions = (B + 1) if bounce else 1
    if mit:
        explosions += float(mit) * B
    single = min(explosions, k["volley_iframe_hits_per_cast"])
    rate = plain_rate(single / cd, notes)
    notes.append(f"{explosions:.0f} explosions per cast; one target takes at most ~{k['volley_iframe_hits_per_cast']:.0f} of them "
                 f"(equal raw damage inside the 10-tick i-frame window is dropped)")
    if bounce and not mit:
        notes.append("explodes on every bounce: WV's Mitosis mixin calls explode() even without the etching (VOLLEY-BOUNCE-EXPLODE)")
    targets = pack_targets(cfg["radius"], d, ctx)
    pack_mult = 1.0
    flags = []
    if mit:
        pack_mult = k["mitosis_multiplier"] / (pen * max(rate * cd, 1e-9))
        notes.append(f"Mitosis: {float(mit):.0f} children per bounce, 0.5x damage on every explosion; pack damage per cast taken as "
                     f"{k['mitosis_multiplier']:.0f}x a plain cast (user's in-game estimate 10-15x, not derived)")
        flags.append("estimate: Mitosis pack damage uses the user's in-game 10-15x per cast, single target is i-frame limited")
    out = finish(per, rate, steps, targets, cfg["manaCost"] / cd, notes, flags=flags, lo=lo, hi=hi)
    out["pack_dps"] *= pack_mult
    return out


def totem_mob(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    ad_etch = etch_value(build, "etching_totem_mob_damage_ad") is not None
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    base = d["ability_power"] + (d["attack_damage"] * m_reg if ad_etch else 0.0)
    raw = base * cfg["totemPercentDamagePerInterval"]
    if ad_etch:
        hc = HitClass("TOTEM", iframe="plain", echo_ok=True)
        notes.append("AD Totem etching: the hit loses the AP flag, so DI and the registry apply on top (ADTOTEM-M2)")
    else:
        hc = HitClass("AP+TOTEM", ap=True, iframe="plain", echo_ok=True)
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    dur = cfg["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    period = dur + cd_s(cfg, d)
    hits = dur / (cfg["totemDamageIntervalTicks"] / 20.0)
    return finish(per, hits / period, steps, pack_targets(cfg["totemEffectRadius"], d, ctx), cfg["manaCost"] / period, notes, lo=lo, hi=hi)


def porcupine_thorns(build, ctx, st, d, cfg, buffs, target_hp, record):
    """Shell (Porcupine) toggled on: every boss hit reflects, blocked or dodged ones included (LivingAttackEvent, HIGH)."""
    b = dict(buffs, porcupine=cfg["additionalThornsDamagePercent"])
    lucky = "lucky_thorns" in st.flags
    hc = HitClass("THORNS", lucky=lucky, iframe="plain", echo_ok=True, double_ok=True)
    per, lo, hi, steps = evaluate_hit(thorns_reflect(d, b), 0.0, hc, d, st, build, ctx, target_hp, b, record)
    rate = 1.0 / ctx.knobs["hit_interval_s"]
    notes = [f"reflects {thorns_reflect(d, b):,.0f} per boss hit (flat thorns x{1 + b['porcupine']:.1f}), one boss hit every "
             f"{ctx.knobs['hit_interval_s']:.1f} s; reflect hits get damage increase, the registry, Vulnerable, double hit and echo, "
             "lucky hits only with Lucky Thorns, never execution"]
    return finish(per, rate, steps, 1.0, cfg["manaCostPerSecond"], notes, lo=lo, hi=hi)


def toxic_duration(c, d):
    return max(1.0, c["poisonTicks"] * (1 + d["effect_duration"]) / 20.0)


def nova_dot_duration(c, d):
    return c["durationSeconds"] * (1 + d["effect_duration"])


MODELS = {
    "Fireball_Base": Model("Fireball", fireball_base, "AP", ["aoe"]),
    "Fireball_Volley": Model("Fireball Volley", volley, "AP", ["aoe"]),
    "Fireball_Fireshot": Model("Fireshot", ap_instant("percentAbilityPowerDealt", None, flags="AP+FIRESHOT", echo=True), "AP", ["single"]),
    "Nova_Base": Model("Nova", nova_base, "AP", ["aoe"]),
    "Nova_Dot": Model("Poison Nova (DoT)", dot("percentAbilityPowerDealt", nova_dot_duration, "radius", "Poison Nova"), "AP", ["aoe"]),
    "Smite_Base": Model("Smite", smite(True), "AP", ["single"]),
    "Smite_Archon": Model("Smite (Archon)", smite(False), "AP", ["single"]),
    "Smite_Blast_Wave": Model("Smite (Blast Wave)", blast_wave, "AP", ["aoe"]),
    "Arcane_Base": Model("Arcane Beam", arcane_beam, "AP", ["single"]),
    "Arcane_Rail": Model("Arcane Rail", ap_instant(("percentAbilityPowerDealtMin", "percentAbilityPowerDealtMax"), None,
                                                   flags="AP+ARCANE_RAIL", echo=True), "AP", ["single"]),
    "Arcane_Prism": Model("Arcane Prism", arcane_prism, "AP", ["aoe"]),
    "Chain_Lightning_Base": Model("Chain Lightning", chain_lightning, "AP", ["aoe"]),
    "Chain_Lightning_Orbs": Model("Lightning Orbs", orbs, "AP", ["aoe"]),
    "Chain_Lightning_Charged_Bolts": Model("Charged Bolts", charged_bolts, "AP", ["aoe"]),
    "Storm_Arrow_Base": Model("Storm Arrow", storm_arrow, "AP", ["single"]),
    "Ice_Bolt_Base": Model("Ice Bolt", ice_bolt, "AD", ["single"]),
    "Shard_Blizzard": Model("Shard Blizzard", shard_blizzard, "AD", ["aoe"]),
    "Javelin_Base": Model("Javelin", javelin, "AD", ["single"]),
    "Javelin_Piercing": Model("Piercing Javelin", javelin, "AD", ["aoe"]),
    "Javelin_Scatter": Model("Scatter Javelin", javelin, "AD", ["aoe"]),
    "Earthquake_Base": Model("Earthquake", earthquake, "AD", ["aoe"]),
    "Earthquake_Singularity": Model("Earthquake (Singularity)", earthquake, "AD", ["aoe"]),
    "Earthquake_Tremor": Model("Earthquake (Tremor)", earthquake, "AD", ["aoe"]),
    "Grenade_Base": Model("Grenade", grenade, "AD", ["aoe"]),
    "Grenade_Sticky": Model("Sticky Grenade", grenade, "AD", ["aoe"]),
    "Toxic_Grenade": Model("Toxic Grenade (DoT)", dot("percentAbilityPowerDealt", toxic_duration, "radius", "Toxic Grenade"), "AP", ["aoe"]),
    "Necromancy_Base": Model("Necromancy (Warriors)", necromancy, "AP", ["single"]),
    "Necromancy_Archer": Model("Necromancy (Archers)", necromancy, "AP", ["single"]),
    "Dash_Damage": Model("Dash Damage", dash_damage, "AD", ["aoe"]),
    "Fangs_Base": Model("Wall of Fangs", fangs(False), "AD", ["aoe"]),
    "Fangs_Maw": Model("Hungry Maw", fangs(True), "AD", ["single"]),
    "Mana_Shield_Implode": Model("Implode", implode, "Mana", ["aoe"]),
    "Implode_Life_Tap": Model("Life Tap", life_tap, "HP", ["aoe"]),
    "Shield_Bash": Model("Shield Bash", shield_bash(False), "AD+Block", ["aoe"]),
    "Shield_Bash_Earthshatter": Model("Shield Bash (Earthshatter)", shield_bash(False), "AD+Block", ["aoe"]),
    "Shield_Bash_Battering_Ram": Model("Shield Bash (Battering Ram)", shield_bash(True), "AD+Thorns", ["aoe"]),
    "Totem_Mob_Damage": Model("Totem (Mob Damage)", totem_mob, "AP", ["aoe"]),
    "Shell_Porcupine": Model("Thorns (Shell Porcupine)", porcupine_thorns, "Thorns", ["single"]),
}
