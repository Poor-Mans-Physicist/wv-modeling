"""Per-ability damage models (RECON §6.2.1 + the ability spec research, 0.34.1)."""
import json
import math
import os

from .damage import HitClass, evaluate_hit, bug, registry_multiplier, thorns_flat, thorns_reflect
from .log import fallback
from . import spec_overlay
from . import stages

PLAIN_MAX_HITS_PER_S = 2.0
SPEC_TABLE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "spec_table.json")


def load_spec_table():
    """data/spec_table.json (extract/spec_table.py). Its overlay part must equal model/spec_overlay.py."""
    with open(SPEC_TABLE_PATH, encoding="utf-8") as fh:
        table = json.load(fh)
    want = json.loads(json.dumps({"specs": spec_overlay.SPEC_OVERLAY, "weapon_combos": spec_overlay.WEAPON_COMBOS}))
    have = {sid: {k: v for k, v in rec.items() if k != "catalog"} for sid, rec in table["specs"].items()}
    if list(have.items()) != list(want["specs"].items()) or table["weapon_combos"] != want["weapon_combos"]:
        raise RuntimeError(f"{SPEC_TABLE_PATH} differs from model/spec_overlay.py: run python extract/spec_table.py")
    return table


_TABLE = load_spec_table()
SPEC_TABLE = {sid: {k: v for k, v in rec.items() if k != "catalog"} for sid, rec in _TABLE["specs"].items()}
WEAPON_COMBOS = _TABLE["weapon_combos"]
# Specs whose hits can leech, and the leech-capable specs whose hits also roll lucky hits (so Life Steal procs on
# them). Ice Bolt leeches only as Lucky Bolt (its "lucky_bolt" rule; ice_bolt sets leech_ok per build).
LEECH_SPECS = {sid for sid, rec in SPEC_TABLE.items() if rec["leech_ok"] is True}
LUCKY_SPECS = {sid for sid, rec in SPEC_TABLE.items() if rec["lucky_ok"] is True}


def cooldown_s(ticks, d, reset=False):
    """Cooldown in seconds after cooldown reduction; ability_cooldown_skip (Ability.java:195-212) skips the whole
    cooldown with that chance, so the expected cooldown is x (1 - chance). Arcane Cascade
    (CooldownReductionLuckyHitTalent): every lucky hit takes (int)(max ticks x cooldownDecrease) off every ability on
    cooldown, so at L lucky hits/s a cooldown of `full` ticks (after reduction, before the skip chance) runs at
    1 / (1 + L x floor(full x c) / 20) of its length; L is evaluate's fixed point (d["cascade_l"]). The Lucky Reset
    etching's ability (reset) also loses r = (int)(max ticks x 0.25) per lucky hit (LuckyHitTalent: 25% of the max
    cooldown, not of the remaining one as the description says): its own cast's lucky hits (d["reset_self"] per cast,
    fangs) come off the cooldown directly, max(0, full - m r), and the other lucky hits (d["reset_l"] per second, the
    build's lucky hits without the ability's own) at their rate, a term in the cascade's denominator. Taking the own
    hits per cast keeps a cast whose own lucky hits refund its whole cooldown at the floor instead of a diverging
    fixed point.

    Rework (d["reset_rework"], user ruling 2026-10-19): a Lucky Reset proc removes LUCKY_RESET_REMAINING (10%) of the
    remaining cooldown, and each cast of any ability procs it at most once. d["reset_self"] is then the chance the
    ability's own cast procs it (1 - (1 - lucky)^fang hits), taken when the cooldown starts: R0 = full (1 - 0.1
    reset_self). d["reset_l"] is the other sources' procs per second (once per cast, rotation.per_cast_rates). In
    mean field the remaining cooldown falls at dR/dt = -a - k R, a = 1 + the Arcane Cascade term, k = 0.1 reset_l, so
    it runs out after T = ln(1 + k R0 / a) / k (R0 / a at k = 0). The mean field is exact to first order in k; in the
    proc-dominated limit the exact log step per proc is ln(10/9) = 0.105 instead of 0.1, so T runs up to ~5% long
    there."""
    t = ticks * (1.0 - d["cooldown_reduction"]) * (1.0 - min(1.0, d["cooldown_skip"])) / 20.0
    L = d.get("cascade_l", 0.0)
    if reset and d.get("reset_rework"):
        full = ticks * (1.0 - d["cooldown_reduction"])
        a = 1.0
        if L > 0.0:
            a += L * math.floor(full * d["cascade_c"] + 1e-9) / 20.0
        r0 = full / 20.0 * (1.0 - LUCKY_RESET_REMAINING * d.get("reset_self", 0.0))
        kk = LUCKY_RESET_REMAINING * d.get("reset_l", 0.0)
        T = math.log1p(kk * r0 / a) / kk if kk > 0.0 else r0 / a
        return max(0.05, (1.0 - min(1.0, d["cooldown_skip"])) * T)
    if reset:
        full = ticks * (1.0 - d["cooldown_reduction"])
        r = math.floor(full * LUCKY_RESET_SHARE + 1e-9) / 20.0
        t = (1.0 - min(1.0, d["cooldown_skip"])) * max(0.0, full / 20.0 - d.get("reset_self", 0.0) * r)
        den = 1.0 + d.get("reset_l", 0.0) * r
        if L > 0.0:
            den += L * math.floor(full * d["cascade_c"] + 1e-9) / 20.0
        return max(0.05, t / den)
    if L > 0.0:
        full = ticks * (1.0 - d["cooldown_reduction"])
        t = t / (1.0 + L * math.floor(full * d["cascade_c"] + 1e-9) / 20.0)
    return max(0.05, t)


LUCKY_RESET_SHARE = 0.25
LUCKY_RESET_REMAINING = 0.10


def cd_s(cfg, d, key="cooldownTicks"):
    return cooldown_s(cfg.get(key, 0), d, cfg.get("_reset", False))


def lucky_reset_spec(fam, build, ctx, st, d):
    """The ability the Lucky Reset etching resets: the family's main ability if its pool names it, else the learned
    secondary in the pool with the highest full-rate damage (a non-main Implode runs on leftover mana and is never
    chosen). None without the etching or a cast ability in the pool."""
    v = etch_value(build, "etching_lucky_cooldown_reset")
    if not isinstance(v, dict) or getattr(fam, "main_spec", None) is None:
        return None
    pool = set(reset_pool(v, ctx))
    if fam.main_spec in pool:
        return fam.main_spec
    secs = [x for x in fam.secondaries(build, ctx) if x in pool and x != "Mana_Shield_Implode"]
    if len(secs) <= 1:
        return secs[0] if secs else None
    from .buffs import buffs_for
    from .families import effective_tier, spec_cfg
    from .procs import tie_key
    bf0 = buffs_for(build, ctx, st, d, False)[0]
    best = None
    for spec in secs:
        tier, _ = effective_tier(ctx, st, spec, max(1, build.abilities.get(spec, 0)))
        c = spec_cfg(ctx, st, spec, tier, build)
        dps = MODELS[spec].compute(build, ctx, st, d, c, bf0, 0.0, False)["dps"]
        if best is None or tie_key(dps) > tie_key(best[0]):
            best = (dps, spec)
    return best[1]


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


def reset_pool(value, ctx):
    """Lucky Reset's ability pool (the etching's `strings` list) as spec ids of ctx: a listed skill id covers each of
    its specializations (LuckyHitTalent resolves the skill to its learned specialization)."""
    if not isinstance(value, dict) or not isinstance(value.get("strings"), list):
        return []
    names = {x["value"] for x in value["strings"] if isinstance(x, dict) and "value" in x}
    return [sid for sid, a in ctx.abilities.items() if sid in names or a.get("skill") in names]


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
    def __init__(self, spec_id, fn):
        rec = SPEC_TABLE[spec_id]
        self.spec_id = spec_id
        self.label = rec["label"]
        self.fn = fn
        self.scaling = rec["scaling"]
        self.tags = set(rec["tags"])

    def compute(self, build, ctx, st, d, cfg, buffs, target_hp, record):
        if d.get("reset_spec") != self.spec_id:
            return self.fn(build, ctx, st, d, cfg, buffs, target_hp, record)
        out = self.fn(build, ctx, st, d, dict(cfg, _reset=True), buffs, target_hp, record)
        ticks = cfg.get("cooldownTicks", 0)
        if d.get("reset_rework"):
            note = (f"Lucky Reset: {self.label} cooldown {cooldown_s(ticks, d):.1f} s -> {cooldown_s(ticks, d, True):.1f} s with "
                    f"a {d.get('reset_self', 0.0):.0%} chance its own cast procs it and {d.get('reset_l', 0.0):.2f} procs/s "
                    "from other sources (each proc removes 10% of the remaining cooldown, at most one proc per cast)")
        else:
            note = (f"Lucky Reset: {self.label} cooldown {cooldown_s(ticks, d):.1f} s -> {cooldown_s(ticks, d, True):.1f} s with "
                    f"{d.get('reset_self', 0.0):.1f} own lucky hits per cast and {d.get('reset_l', 0.0):.1f} other lucky hits/s "
                    "(each removes 25% of the max cooldown)")
        out["notes"] = out.get("notes", []) + [note]
        return out


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


def ap_hit(build, ctx, st, d, cfg, buffs, target_hp, record, pct, radius_key, flags, echo, mult, notes, add=0.0,
           radius=None, free=False):
    """One instant AP hit per cooldown: raw = AP x pct x mult x (Spectral Strike for Fireshot / Arcane Rail) + add;
    radius (when given) replaces the config radius for pack targets. free: the hit neither sets nor respects i-frames
    (no clip, no repeat-hit cap)."""
    sp = 1.0 + ctx.knobs["spectral_ap_per_stack"] * buffs.get("spectral_s", 0.0) if ("FIRESHOT" in flags or "ARCANE_RAIL" in flags) else 1.0
    raw = d["ability_power"] * pct * mult * sp + add
    hc = HitClass(flags, ap=True, aoe_flag="AOE" in flags, iframe="free" if free else "plain", echo_ok=echo)
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    rate = 1.0 / cd if free else plain_rate(1.0 / cd, notes)
    if radius is not None:
        targets = pack_targets(radius, d, ctx)
    else:
        targets = pack_targets(cfg.get(radius_key, 0.0), d, ctx) if radius_key else 1.0
    return finish(per, rate, steps, targets, cfg.get("manaCost", 0.0) / cd, notes, lo=lo, hi=hi)


def ap_instant(pct_keys, radius_key=None, flags="AP+AOE", echo=False, mult=1.0, extra_note=""):
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = [extra_note] if extra_note else []
        if isinstance(pct_keys, tuple):
            pct = 0.5 * (cfg[pct_keys[0]] + cfg[pct_keys[1]])
        else:
            pct = cfg[pct_keys]
        return ap_hit(build, ctx, st, d, cfg, buffs, target_hp, record, pct, radius_key, flags, echo, mult, notes)
    return fn


GREEDBALL_RADIUS = 3.0


def greedball(build, d, notes):
    """Greedball (addon MixinAbstractFireballAbility, FireballAbility only): the fireball's damage gains (item quantity +
    item rarity + Copiously) x 100 x roll; Copiously is not tracked (0). MixinVaultFireball fixes the explosion radius at
    3 blocks. Returns (flat add, radius) or (0, None) without the etching."""
    v = etch_value(build, "woldsvaults:fireball_greedball")
    if not v:
        return 0.0, None
    add = (d["item_quantity"] + d["item_rarity"]) * 100.0 * float(v)
    notes.append(f"Greedball etching: +{add:,.0f} damage ((IIQ {d['item_quantity']:.0%} + IIR {d['item_rarity']:.0%}) x 100 x "
                 f"{float(v):.2f}; Copiously not counted), explosion radius {GREEDBALL_RADIUS:.0f}")
    return add, GREEDBALL_RADIUS


def free_iframes(spec):
    """Rework (proposed, user 2026-10-21): the spec's hits neither set nor respect i-frames (DamageUtil.shotgunAttack;
    the spec table's "rework" hit variant with iframe "free"): Fireball and Volley explosions, Mitosis children
    included."""
    rec = SPEC_TABLE.get(spec)
    return stages.LEDGER == "rework" and rec is not None and rec["hit_variants"].get("rework", {}).get("iframe") == "free"


def steel_inferno(st, ctx):
    """Steel Inferno (proposed, user 2026-10-21): (boss multiplier 1 + e B, pack multiplier 1 + m B, radius multiplier)
    for the rolled centre bonus B, or None without it. The bonus falls off as B (1 - (d/R)^2); the boss takes it at the
    knob efficiency e (hits measured to the nearest point of its hitbox), pack mobs at the disc mean m of 1 - (d/R)^2."""
    b = st.special.get("steel_inferno", 0.0)
    if b <= 0:
        return None
    k = ctx.knobs
    return (1.0 + k["steel_inferno_boss_efficiency"] * b, 1.0 + k["steel_inferno_pack_mean"] * b,
            st.special.get("steel_inferno_radius", 1.0))


def steel_apply(out, si, record, notes):
    """Steel Inferno's own multiplier step on one explosion stream: boss hits x(1 + e B), pack damage x(1 + m B)."""
    bm, pm, rm = si
    pack0 = out["pack_dps"]
    scale(out, bm)
    out["pack_dps"] = pack0 * pm
    if record:
        out["steps"].append(("Steel Inferno", f"x{bm:.2f}", f"centre bonus x boss efficiency; pack mobs x{pm:.2f}, radius x{rm:.2f}"))
    notes.append(f"Steel Inferno: explosion radius x{rm:.2f}, boss hits x{bm:.2f}, pack hits x{pm:.2f} (centre bonus falling off "
                 "as 1 - (d/R)^2)")
    return out


def fireball_base(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    add, radius = greedball(build, d, notes)
    si = steel_inferno(st, ctx)
    if si is not None:
        radius = (radius if radius is not None else cfg.get("radius", 0.0)) * si[2]
    free = free_iframes("Fireball_Base")
    out = ap_hit(build, ctx, st, d, cfg, buffs, target_hp, record, cfg["percentAbilityPowerDealt"], "radius", "AP+AOE",
                 False, 1.0, notes, add=add, radius=radius, free=free)
    if free:
        out["notes"].append("explosions neither set nor respect i-frames (proposed: DamageUtil.shotgunAttack)")
    if si is not None:
        steel_apply(out, si, record, out["notes"])
    p = st.special.get("fireball_recast", 0.0)
    if p > 0:
        scale(out, 1.0 + p)
        out["notes"].append(f"Everflame: {p:.0%} chance of a free second fireball 2 s later (x{1 + p:.2f})")
    return out


def etch_max(build, name, key):
    """Strongest copy of a dict-valued etching's `key` over the build's items (None without one)."""
    best = None
    for it in build.items.values():
        e = it.etching
        if e and (e["attribute"] == "the_vault:" + name or e["attribute"] == name) and isinstance(e["value"], dict):
            v = e["value"].get(key)
            if isinstance(v, (int, float)) and (best is None or v > best):
                best = float(v)
    return best


def nova_base(build, ctx, st, d, cfg, buffs, target_hp, record):
    out = ap_instant("percentAbilityPowerDealt", "radius")(build, ctx, st, d, cfg, buffs, target_hp, record)
    v = etch_value(build, "etching_nova_recast")
    if v:
        m = 1.0 + min(1.0, v / 100.0)
        scale(out, m)
        out["notes"].append(f"Nova Recast etching: {min(100, v):.0f}% chance of a free recast (x{m:.2f})")
    low = etch_max(build, "etching_nova_low_mana_damage", "damageMultiplier")
    if low:
        u = buffs.get("u_half_mana", 0.0)
        if u > 0:
            scale(out, 1.0 + low * u)
        out["notes"].append(f"Nova Damage Increase etching: x{1 + low:.2f} on casts below 50% mana, {u:.0%} of casts "
                            "(the strongest copy; NovaAbility compounds copies, NOVA-LOWMANA-STACK, modeled as fixed)")
    return out


def dot(pct_key, dur_fn, radius_key, label):
    """Poison Nova / Toxic Grenade (DamageOverTimeHelper): D = floor(20 x duration) ticks, floor(D / 20) hits of
    total / (D / 20) each, one per second under IS_DOT + IS_POISON_NOVA (the dot hit class: no damage increase, no
    multiplier registry, no god global registry, no Sneaky Advantage, no Hexbreaker; Vulnerable, Executioner, the Dice
    and the rework's talent registry apply)."""
    def fn(build, ctx, st, d, cfg, buffs, target_hp, record):
        notes = [label + " ticks run under DOT+POISON_NOVA: no damage increase, no multiplier registries, no Sneaky "
                 "Advantage or Hexbreaker"]
        total = d["ability_power"] * cfg[pct_key]
        dur = dur_fn(cfg, d)
        dticks = math.floor(20.0 * dur + 1e-9)
        hits = math.floor(dticks / 20.0 + 1e-9)
        hc = HitClass("DOT", ap=True, aoe_flag=True, iframe="shotgun", dot=True)
        per, lo, hi, steps = evaluate_hit(total * 20.0 / max(dticks, 1), 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        period = max(cd_s(cfg, d), dur)
        notes.append(f"{hits} hits over {dticks} ticks, recast every {period:.1f}s (any Poison Nova or Toxic Grenade "
                     "recast wipes the DoT on the target)")
        out = finish(per, hits / period, steps, pack_targets(cfg.get(radius_key, 0.0), d, ctx),
                     cfg.get("manaCost", 0.0) / period, notes, lo=lo, hi=hi)
        out["dot"] = (dticks / 20.0, period)
        pm = etch_value(build, "etching_poison_mana")
        if pm:
            out["mana_back"] = float(pm) * out["rate"]
            notes.append(f"Poison Mana etching: +{float(pm):.0f} mana per tick ({out['mana_back']:.1f} mana/s at the full rate)")
        return out
    return fn


def dot_slot(sources):
    """Single DoT slot (DamageOverTimeHelper.invalidateAll on every Poison Nova / Toxic Grenade cast): sources is
    [(spec, share, (D seconds, cast period))] for the DoT casts in the rotation. With both casting, casts of either
    arrive at a = sum(share / P) per second and each one ticks until the next: it keeps min(D, 1 / a) of its D. Returns
    {spec: factor}; a lone DoT keeps 1.0."""
    live = [(s, sh, dp) for s, sh, dp in sources if dp is not None and sh > 0]
    if len(live) < 2:
        return {}
    a = 0.0
    for _, sh, (D, P) in live:
        a += sh / P
    return {s: min(1.0, 1.0 / (a * D)) for s, _, (D, _) in live}


def dot_slot_apply(out, f):
    """Scales one rotation source's output by its dot_slot factor."""
    if f != 1.0:
        scale(out, f)
        out["rate"] *= f
        out["notes"] = out.get("notes", []) + [f"single DoT slot: the other DoT's casts wipe this one, x{f:.2f}"]
    return out


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
        sd = etch_value(build, "etching_lightning_strike_delay")
        if sd:
            rate = bolts / n * (2.0 + (ev or 0.0))
            notes.append(f"Lightning Strike Delay etching: every bolt strikes again {float(sd) / 20:.1f} s later at full damage "
                         "(castExtraBolt: no mana, resets the i-frames, its own echo roll, no Echoing Smite re-strike)")
        mana = cfg.get("manaCostPerSecond", 0.0) + cfg.get("additionalManaPerBolt", 0.0) * bolts
        notes.append("shotgun hits: vanilla i-frames never clip a bolt")
        out = finish(per, rate, steps, 1.0, mana, notes, lo=lo, hi=hi)
        if ev:
            out["echo_extra"] = ev
        if sd:
            out["echo_orig"] = 2.0
        return out
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
    if etch_value(build, "etching_prism_split") is not None:
        notes.append("Prism Split etching: the split pulse runs in the same tick as the primary at half its raw, so vanilla "
                     "i-frames drop it on the boss (the prism is centred on its target); pack damage only, not counted")
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
    sd = etch_value(build, "etching_lightning_strike_delay")
    if sd:
        rate2 = plain_rate(2.0 / cd_s(cfg, d), out["notes"])
        m = rate2 / out["rate"]
        single = out["dps"]
        for k in ("dps", "dps_lo", "dps_hi"):
            out[k] *= m
        out["rate"] = rate2
        out["pack_dps"] = single * out["targets"] + out["dps"] - single
        out["notes"].append(f"Lightning Strike Delay etching: the struck target takes the cast's damage again {float(sd) / 20:.1f} s "
                            "later (a plain AP+AOE hurt, i-frame clippable like the first)")
    else:
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
    shots = math.ceil((D + 1) / (cfg["intervalTicks"] + 1) - 1e-9)
    period = D / 20.0 + cd_s(cfg, d)
    notes = [f"{shots} bolts per {D / 20:.1f}s cloud; the cooldown starts after the cloud"]
    occ = 1.0 if "storm_tracking" in st.flags else ctx.knobs["storm_boss_occupancy"]
    if "storm_tracking" in st.flags:
        notes.append("Stormcrown: the storm follows the boss, so every bolt hits it")
    elif occ != 1.0:
        notes.append(f"the stationary storm holds the boss {occ:.0%} of the time (knob storm_boss_occupancy)")
    return finish(per, shots / period * occ, steps, 1.0, cfg["manaCost"] / period, notes, lo=lo, hi=hi)


def ice_bolt(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = []
    lucky_bolt = etch_value(build, "etching_ice_bolt_lucky") is not None
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    adq = d["attack_damage"] + (ctx.knobs["spectral_ap_per_stack"] * buffs.get("spectral_s", 0.0) * d["ap_base"] if lucky_bolt else 0.0)
    baked = adq * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * (1.0 if lucky_bolt else m_reg)
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
    bd = etch_value(build, "etching_storm_arrow_blizzard_damage")
    if bd:
        extra = max(0.0, d["ability_power"] * float(bd) - (cfg["damagePerShard"] + baked))
        if extra > 0:
            hc2 = HitClass("AP+AOE (Blizzard Damage)", ap=True, aoe_flag=True, iframe="free")
            p2, l2, h2, _ = evaluate_hit(extra, 0.0, hc2, d, st, build, ctx, target_hp, buffs, False)
            per, lo, hi = per + p2, lo + l2, hi + h2
        notes.append(f"Blizzard Damage etching: each shard also hurts for AP x {float(bd):.2f} as an AP+AOE hit in the shard's "
                     f"i-frame window, so only {extra:,.0f} above the shard's raw counts")
    D = cfg["cloudDuration"] * (1.0 + d["effect_duration"])
    shots = math.ceil((D + 1) / (cfg["intervalTicks"] + 1) - 1e-9)
    active = D / 20.0
    period = active + cd_s(cfg, d)
    rate = plain_rate(shots / active, notes) * active / period
    occ = ctx.knobs["storm_boss_occupancy"]
    if occ != 1.0:
        rate *= occ
        notes.append(f"the stationary cloud holds the boss {occ:.0%} of the time (knob storm_boss_occupancy)")
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
    per, lo, hi = javelin_implode(build, ctx, st, d, cfg, buffs, target_hp, record, per, lo, hi, steps, notes)
    hits = 1.0
    if "javelin_multi_hit" in st.flags and cfg.get("numberOfJavelins"):
        split = st.attr.get("the_vault:splitting_javelins", 0.0)
        split = split if split >= 2.0 - 1e-9 else 1.0
        hits = split if split > 1 else cfg["numberOfJavelins"]
        notes.append(f"Homing javelins (Helm of the Warbound): the throw splits on the ground and its children home in on the boss, "
                     + (f"so Splitting Javelins' {split:.0f} pre-split javelins each hit it once (user, 2026-10-21: they home in, so they "
                        f"never split again)" if split > 1 else f"so all {cfg['numberOfJavelins']:.0f} children hit it"))
    out = finish(per, hits / cd, steps, targets, cfg["manaCost"] / cd, notes, lo=lo, hi=hi)
    sm = etch_value(build, "etching_scatter_mana")
    if sm and cfg.get("numberOfJavelins"):
        out["mana_back"] = float(sm) * out["rate"]
        notes.append(f"Scatter Mana etching: +{float(sm):.0f} mana per Scatter javelin hit ({out['mana_back']:.1f} mana/s at the full rate)")
    return out


def javelin_implode(build, ctx, st, d, cfg, buffs, target_hp, record, per, lo, hi, steps, notes):
    """Imploding Javelin (Pocket Penguin; addon MixinJavelinAbility): every plain-Javelin hit casts the player's learned
    Implode (FlexibleImplode) on the target for free: mana x percentManaDealt x (1 + the proposed implode-damage roll),
    an AOE hit that gets damage increase, after the javelin reset the boss's i-frames. Life Tap is not an Implode, so it
    never fires with it. A standing throw reads the bar minus the throw's cost; a sneaking Power Dump throw while always
    sneaking spends the whole bar, so its Implode reads the mana the throw consumed (user, 2026-10-21; the rotation's
    per-cast bar). The rotation then multiplies the throw, Implode included, by its Power Dump bonus."""
    if "javelin_implode" not in st.flags or "piercing" in cfg or "numberOfJavelins" in cfg:
        return per, lo, hi
    learned = build.abilities.get("Mana_Shield_Implode", 0)
    if learned <= 0:
        notes.append("Imploding Javelin: Implode is not learned, so nothing is cast")
        return per, lo, hi
    from .families import effective_tier, spec_cfg
    tier, _ = effective_tier(ctx, st, "Mana_Shield_Implode", learned)
    ic = spec_cfg(ctx, st, "Mana_Shield_Implode", tier, build)
    pd = bool(st.gfx and st.gfx["power_dump"]) and "Javelin_Base" in getattr(build, "pd", set()) and buffs.get("pd_mode") == "always"
    mana = min(d["mana_max"], buffs.get("pd_bar", 0.0)) if pd else max(0.0, d["mana_max"] - cfg["manaCost"])
    if mana <= 0:
        notes.append("Imploding Javelin: no mana in the bar, so the free Implode deals nothing")
        return per, lo, hi
    boost = 1.0 + st.special.get("javelin_implode_damage", 0.0)
    hc = HitClass("AOE (Imploding Javelin)", aoe_flag=True, iframe="free")
    p2, l2, h2, _ = evaluate_hit(mana * ic["percentManaDealt"] * boost, 0.0, hc, d, st, build, ctx, target_hp, buffs, False)
    notes.append(f"Imploding Javelin: free Implode t{tier} per hit, {mana:,.0f} mana x {ic['percentManaDealt']:.1f}"
                 + (f" x{boost:.2f} (implode damage roll)" if boost != 1.0 else "")
                 + (" (the mana the sneaking throw consumed)" if pd else "") + f" -> {p2:,.0f} per hit (javelin {per:,.0f})")
    return per + p2, lo + l2, hi + h2


def earthquake(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["AD-scaled but AP+AOE-flagged: DI and the registry are baked in once (correct, not a bug)"]
    m_reg, _ = registry_multiplier(d, st, build, buffs, ctx, [])
    baked = d["attack_damage"] * (1.0 + d["damage_increase"]) * cfg["percentAttackDamageDealt"] * m_reg
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(0.0, baked, hc, d, st, build, ctx, target_hp, buffs, record)
    shocks = cfg["shockCount"]
    period = shocks * cfg["shockIntervalTicks"] / 20.0 + cd_s(cfg, d)
    rc = etch_value(build, "etching_earthquake_recast_delay")
    if rc:
        hc2 = HitClass("AP+AOE (recast)", ap=True, aoe_flag=True, iframe="reset")
        p2, l2, h2, _ = evaluate_hit(0.0, baked, hc2, d, st, build, ctx, target_hp, buffs, False)
        w = 1.0 / (shocks + 1.0)
        per, lo, hi = (per * shocks + p2) * w, (lo * shocks + l2) * w, (hi * shocks + h2) * w
        notes.append(f"Earthquake Recast Delay etching: one more shock {float(rc) / 20:.1f} s after the first, full damage, not "
                     "i-frame clipped")
        shocks = shocks + 1.0
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


def necromancy_golem(build, ctx, st, d, cfg, buffs, target_hp, record):
    """Necromancy (Golem): the summons merge into one golem of summonCap stacks that swings once per attackIntervalTicks
    for AP x percentAbilityPowerDealt x stacks, plus an aoeDamageMultiplier splash on the mobs within aoeRadius (pack
    damage only). Souls are taken as pre-built, like the other minions."""
    notes = ["the golem needs souls from cursed non-boss kills; assumes a full golem summoned before the boss",
             f"{cfg['summonCap']:.0f} stacks, one swing every {cfg['attackIntervalTicks']:.0f} ticks"]
    hc = HitClass("AP+NECRO", ap=True, iframe="plain")
    raw = d["ability_power"] * cfg["percentAbilityPowerDealt"] * cfg["summonCap"]
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    rate = plain_rate(20.0 / max(1.0, cfg["attackIntervalTicks"]), notes)
    targets = 1.0 + cfg["aoeDamageMultiplier"] * (pack_targets(cfg["aoeRadius"], d, ctx) - 1.0)
    return finish(per, rate, steps, targets, 0.0, notes, flags=["niche: needs a pre-built minion army from non-boss kills"], lo=lo, hi=hi)


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
        ad = (d["attack_damage"] + ctx.knobs["spectral_ap_per_stack"] * buffs.get("spectral_s", 0.0) * d["ap_base"]) * buffs.get("empower_ad_mult", 1.0)
        if not maw and etch_value(build, "ravenous_fangs") is not None:
            ad *= 0.25
            notes.append("Ravenous Fangs: x0.25 AD for a doubled execute threshold (bosses are exempt from execute)")
        raw = ad * cfg["damageMultiplier"] + cfg["baseDamage"]
        hc = HitClass("FANG", normal=True, lucky=True, iframe="reset", rampage=True)
        per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
        hits = fang_hits(SPEC_TABLE["Fangs_Maw" if maw else "Fangs_Base"]["fang_hits"], cfg["radius"] * d["aoe_multiplier"], ctx)
        cd = cd_s(cfg, d)
        notes.append(f"{hits:.2f} fang hits per cast on the boss roster at {ctx.knobs['fang_boss_distance']:.1f} blocks "
                     f"(data/fang_hits.json{', x' + format(ctx.knobs['maw_pull_factor'], '.2f') + ' pull' if maw else ''})")
        targets = 1.0 if maw else pack_targets(cfg["radius"], d, ctx) / 2.0
        flags = ["niche: fang hits (like every non-thorns lucky hit) only take lucky hits and Rampage while the player's last "
                 "melee attack was fully charged (AttackScaleHelper keeps that value, so one full swing keeps it on); fang "
                 "count per cast from the boss-roster geometry (tools/fang_geometry.py)"]
        out = finish(per, hits / cd, steps, max(1.0, targets), cfg["manaCost"] / cd, notes, flags=flags, lo=lo, hi=hi)
        if ctx.gods is not None:
            # Rework (proposed, user 2026-10-17): leech, Mana Steal and Life Steal proc at most once per cast.
            out["per_cast"] = hits
        return out
    return fn


_FANGS = None


def fang_table():
    """data/fang_hits.json (tools/fang_geometry.py): raw fang hits per cast by effective radius, Wall and Maw."""
    global _FANGS
    if _FANGS is None:
        path = os.path.join(os.path.dirname(SPEC_TABLE_PATH), "fang_hits.json")
        with open(path, encoding="utf-8") as fh:
            t = json.load(fh)
        _FANGS = {"step": t["meta"]["grid_step"], "dist": t["meta"]["fang_boss_distance"],
                  "wall": [h for _, h in t["wall"]], "maw": [h for _, h in t["maw"]]}
    return _FANGS


def fang_hits(kind, radius, ctx):
    """Fang hits per cast on the hyperboss for an effective radius (radius x area of effect): linear in the table
    (data/fang_hits.json), Hungry Maw x maw_pull_factor. A radius past the table's end takes its last row (logged)."""
    t = fang_table()
    if abs(t["dist"] - ctx.knobs["fang_boss_distance"]) > 1e-12:
        fallback("fang-table-distance", f"data/fang_hits.json was built for a boss at {t['dist']} blocks, "
                 f"fang_boss_distance is {ctx.knobs['fang_boss_distance']}; rerun tools/fang_geometry.py")
    v = t[kind]
    x = radius / t["step"]
    if x >= len(v) - 1:
        if x > len(v) - 1 + 1e-9:
            fallback("fang-radius-clamp", f"fang radius {radius:.2f} past the fang table; using its last row")
        h = v[-1]
    else:
        i = int(x)
        h = v[i] + (v[i + 1] - v[i]) * (x - i)
    return h * ctx.knobs["maw_pull_factor"] if kind == "maw" else h


def implode_pd(st, ctx, d, mana):
    """Power Dump on every Implode (user, 2026-10-21): with the node, an Implode that consumes `mana` gets the Power Dump
    multiplier for that share of the bar, sneaking or not; 1 without the node."""
    if not (st.gfx and st.gfx["power_dump"]):
        return 1.0
    from .buffs import pd_curve
    return pd_curve(st, ctx, d, mana)


def implode(build, ctx, st, d, cfg, buffs, target_hp, record):
    notes = ["consumes all mana: sustained damage = mana regenerated per cooldown x percentManaDealt"]
    cd = cd_s(cfg, d)
    mana_per_cast = min(d["mana_max"], d["mana_regen"] * cd)
    hc = HitClass("AOE", aoe_flag=True, iframe="plain")
    per, lo, hi, steps = evaluate_hit(mana_per_cast * cfg["percentManaDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    pm = implode_pd(st, ctx, d, mana_per_cast)
    return finish(per * pm, 1.0 / cd, steps, pack_targets(cfg["radius"], d, ctx), 0.0, notes, lo=lo * pm, hi=hi * pm)


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
    free = free_iframes("Fireball_Volley")
    raw = d["ability_power"] * cfg["percentAbilityPowerDealt"] * pen
    hc = HitClass("AP+AOE", ap=True, aoe_flag=True, iframe="free" if free else "plain")
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    cd = cd_s(cfg, d)
    B = k["volley_bounces"]
    bounce = bool(mit) or bug(build, "VOLLEY-BOUNCE-EXPLODE")
    explosions = (B + 1) if bounce else 1
    if mit:
        explosions += float(mit) * B
    if free:
        rate = explosions / cd
        notes.append(f"{explosions:.0f} explosions per cast, every one counted on the boss (proposed: explosions neither set nor "
                     "respect i-frames)")
    else:
        single = min(explosions, k["volley_iframe_hits_per_cast"])
        rate = plain_rate(single / cd, notes)
        notes.append(f"{explosions:.0f} explosions per cast; one target takes at most ~{k['volley_iframe_hits_per_cast']:.0f} of them "
                     f"(equal raw damage inside the 10-tick i-frame window is dropped)")
    if bounce and not mit:
        notes.append("explodes on every bounce: WV's Mitosis mixin calls explode() even without the etching (VOLLEY-BOUNCE-EXPLODE)")
    si = steel_inferno(st, ctx)
    radius = GREEDBALL_RADIUS if etch_value(build, "woldsvaults:fireball_greedball") else cfg["radius"]
    if si is not None:
        radius *= si[2]
    targets = pack_targets(radius, d, ctx)
    pack_mult = 1.0
    flags = []
    if mit and free:
        notes.append(f"Mitosis: {float(mit):.0f} children per bounce, 0.5x damage on every explosion; pack damage = explosions x "
                     f"expected pack mobs in each explosion ({targets:.2f})")
    elif mit:
        pack_mult = k["mitosis_multiplier"] / (pen * max(rate * cd, 1e-9))
        notes.append(f"Mitosis: {float(mit):.0f} children per bounce, 0.5x damage on every explosion; pack damage per cast taken as "
                     f"{k['mitosis_multiplier']:.0f}x a plain cast (user's in-game estimate 10-15x, not derived)")
        flags.append("estimate: Mitosis pack damage uses the user's in-game 10-15x per cast, single target is i-frame limited")
    out = finish(per, rate, steps, targets, cfg["manaCost"] / cd, notes, flags=flags, lo=lo, hi=hi)
    out["pack_dps"] *= pack_mult
    if si is not None:
        steel_apply(out, si, record, notes)
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
    """Shell (Porcupine) toggled on: every boss attack that reaches the player reflects, blocked or dodged ones included
    (LivingAttackEvent, HIGH), at d["boss_hits_reaching"] per second."""
    b = dict(buffs, porcupine=cfg["additionalThornsDamagePercent"])
    lucky = "lucky_thorns" in st.flags
    hc = HitClass("THORNS", lucky=lucky, iframe="plain", echo_ok=True, double_ok=True)
    per, lo, hi, steps = evaluate_hit(thorns_reflect(d, b), 0.0, hc, d, st, build, ctx, target_hp, b, record)
    for k in ("_raw_probe", "_echo_probe", "_echo_clip"):
        if k in b:
            buffs[k] = b[k]
    rate = d["boss_hits_reaching"]
    notes = [f"reflects {thorns_reflect(d, b):,.0f} per boss attack that reaches the player (flat thorns x{1 + b['porcupine']:.1f}), "
             f"{rate:.2f} per second after avoidance; reflect hits get damage increase, the registry, Vulnerable, double hit and echo, "
             "lucky hits only with Lucky Thorns, never execution"]
    return finish(per, rate, steps, 1.0, cfg["manaCostPerSecond"], notes, lo=lo, hi=hi)


def toxic_duration(c, d):
    return max(1.0, c["poisonTicks"] * (1 + d["effect_duration"]) / 20.0)


def nova_dot_duration(c, d):
    return c["durationSeconds"] * (1 + d["effect_duration"])


MODEL_FNS = {
    "Fireball_Base": fireball_base,
    "Fireball_Volley": volley,
    "Fireball_Fireshot": ap_instant("percentAbilityPowerDealt", None, flags="AP+FIRESHOT", echo=True),
    "Nova_Base": nova_base,
    "Nova_Dot": dot("percentAbilityPowerDealt", nova_dot_duration, "radius", "Poison Nova"),
    "Smite_Base": smite(True),
    "Smite_Archon": smite(False),
    "Smite_Blast_Wave": blast_wave,
    "Arcane_Base": arcane_beam,
    "Arcane_Rail": ap_instant(("percentAbilityPowerDealtMin", "percentAbilityPowerDealtMax"), None, flags="AP+ARCANE_RAIL", echo=True),
    "Arcane_Prism": arcane_prism,
    "Chain_Lightning_Base": chain_lightning,
    "Chain_Lightning_Orbs": orbs,
    "Chain_Lightning_Charged_Bolts": charged_bolts,
    "Storm_Arrow_Base": storm_arrow,
    "Ice_Bolt_Base": ice_bolt,
    "Shard_Blizzard": shard_blizzard,
    "Javelin_Base": javelin,
    "Javelin_Piercing": javelin,
    "Javelin_Scatter": javelin,
    "Earthquake_Base": earthquake,
    "Earthquake_Singularity": earthquake,
    "Earthquake_Tremor": earthquake,
    "Grenade_Base": grenade,
    "Grenade_Sticky": grenade,
    "Toxic_Grenade": dot("percentAbilityPowerDealt", toxic_duration, "radius", "Toxic Grenade"),
    "Necromancy_Base": necromancy,
    "Necromancy_Archer": necromancy,
    "Dash_Damage": dash_damage,
    "Fangs_Base": fangs(False),
    "Fangs_Maw": fangs(True),
    "Mana_Shield_Implode": implode,
    "Implode_Life_Tap": life_tap,
    "Shield_Bash": shield_bash(False),
    "Shield_Bash_Earthshatter": shield_bash(False),
    "Shield_Bash_Battering_Ram": shield_bash(True),
    "Totem_Mob_Damage": totem_mob,
    "Shell_Porcupine": porcupine_thorns,
    "Necromancy_Golem": necromancy_golem,
}

_damage_specs = [sid for sid, rec in SPEC_TABLE.items() if rec["hit"] is not None]
if set(_damage_specs) != set(MODEL_FNS):
    raise RuntimeError(f"spec table damage specs and MODEL_FNS differ: {sorted(set(_damage_specs) ^ set(MODEL_FNS))}")
MODELS = {sid: Model(sid, MODEL_FNS[sid]) for sid in _damage_specs}
