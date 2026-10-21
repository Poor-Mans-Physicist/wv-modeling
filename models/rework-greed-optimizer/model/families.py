"""Build families: the primary damage source (melee weapon or ability spec) and how its DPS is computed."""
import math

from .damage import HitClass, evaluate_hit, bug
from . import procs
from . import rotation
from .stats import ability_level_bonus
from .log import fallback
from . import abilities as ab
from .buffs import (buffs_for, empower_toggle, ethereal_chance, fury_for, low_mana_state, mana_steal_pct, mana_supply,
                    nova_pd_rate, pd_curve, rampage_uptime, snap_bar, spectral_ad, spectral_stacks, starved_free,
                    survival_buffs)

MELEE_WEAPONS = {w: {"combo": combo} for w, combo in ab.WEAPON_COMBOS.items()}

_registry = {}


def register(fam):
    _registry[fam.id] = fam
    return fam


def get(fid):
    if not _registry:
        build_registry()
    return _registry[fid]


def all_families():
    if not _registry:
        build_registry()
    return list(_registry.values())


def effective_tier(ctx, st, spec_id, learned):
    a = ctx.abilities[spec_id]
    n = len(a.get("tiers", [a]))
    bonus = ability_level_bonus(st.ability_levels, a["skill"], ctx.ability_groups, "masterful" in st.flags, spec_id)
    return max(1, int(min(n, learned + bonus))), bonus


_tier_cache = {}
JAVA_DEFAULTS = {"percentAttackDamageDealt": 0.0, "splinterChance": 0.0, "splinterDamageMultiplier": 0.5, "splinterRange": 4.0}


def tier_merge(ctx, spec_id, tier, bugged):
    """Merged tier config and the keys the tier itself lacks, without logging (shared with the Rust kernel export)."""
    a = ctx.abilities[spec_id]
    tiers = a.get("tiers", [a])
    merged = {}
    for t in tiers[:tier]:
        merged.update(t)
    missing = set().union(*[set(t) for t in tiers[:tier]]) - set(tiers[tier - 1])
    if missing and bugged:
        for k in missing:
            merged[k] = JAVA_DEFAULTS.get(k, 0.0)
    return merged, missing


def tier_hole_fallback(spec_id, tier, bugged, missing):
    if bugged:
        return (f"tier-hole-{spec_id}-{tier}", f"{spec_id} tier {tier} lacks {sorted(missing)}; bugged mode uses the Java readJson defaults (ICEBOLT-T12-HOLE)")
    return (f"tier-inherit-{spec_id}-{tier}", f"{spec_id} tier {tier} lacks {sorted(missing)}; intended mode inherits lower-tier values")


def tier_cfg(ctx, spec_id, tier, build=None):
    """Tier config with keys missing from a tier inherited from the tiers below it."""
    bugged = bug(build, "ICEBOLT-T12-HOLE") if build is not None else ctx.mode == "bugged"
    key = (spec_id, tier, bugged)
    if key in _tier_cache:
        return _tier_cache[key]
    merged, missing = tier_merge(ctx, spec_id, tier, bugged)
    if missing:
        fallback(*tier_hole_fallback(spec_id, tier, bugged, missing))
    _tier_cache[key] = merged
    return merged


MOD_KEYS = {"cooldown": ("cooldownTicks",), "mana": ("manaCost", "manaCostPerSecond")}


def spec_cfg(ctx, st, spec_id, tier, build):
    """tier_cfg with the build's per-ability cooldown and mana-cost multipliers applied (key = the spec or its skill)."""
    c = tier_cfg(ctx, spec_id, tier, build)
    if not st.ability_mods:
        return c
    skill = ctx.abilities[spec_id]["skill"]
    mult = {}
    for kind, key, amount in st.ability_mods:
        if key == spec_id or key == skill:
            mult[kind] = mult.get(kind, 1.0) * max(0.0, 1.0 + amount)
    if not mult:
        return c
    c = dict(c)
    for kind, m in sorted(mult.items()):
        for k in MOD_KEYS[kind]:
            if k in c:
                c[k] = c[k] * m
    return c


def bc_swings_per_s(attack_speed):
    """Better Combat 1.6.2 swing cadence (client-driven): P = ceil(0.75 D) + round(0.25 max(D, cap)) ticks, D = 20/AS.

    Upswing 0.5 (pack weapon_attributes) x upswing_multiplier 0.5, attack_interval_cap 2: at most 10 swings/s,
    reached only above attack speed 15 (MinecraftClientInject:260-296, PlayerAttackHelper:48).
    """
    if attack_speed <= 0:
        return 0.0
    D = 20.0 / attack_speed
    k = max(1, math.ceil(0.75 * D - 1e-9))
    U = max(1, math.floor(0.25 * max(D, 2.0) + 0.5 + 1e-9))
    return 20.0 / (k + U)


def arcane_nova_dps(build, ctx, st, d, buffs, target_hp, aps, trigger_raw, notes):
    """Starforge (ArcaneNovaOnHitHelper): every hitsRequired-th non-AP main-hand hit casts an ability-power nova on the
    target (AP x percentAbilityPower, full damage at the centre), flagged AP + AOE. It runs inside the triggering hit's
    hurt, so vanilla i-frames keep only the part above that hit's raw damage. Returns (dps, lo, hi)."""
    hits, pct = st.special.get("arcane_nova_hits", 0.0), st.special.get("arcane_nova_pct", 0.0)
    if hits <= 0 or pct <= 0 or aps <= 0:
        return 0.0, 0.0, 0.0
    nova = d["ability_power"] * pct
    raw = max(0.0, nova - trigger_raw)
    if raw <= 0:
        notes.append(f"Starforge nova ({nova:,.0f} AP raw) is below the swing's {trigger_raw:,.0f} raw, so i-frames swallow it")
        return 0.0, 0.0, 0.0
    hc = HitClass("AP+AOE (Starforge nova)", ap=True, aoe_flag=True, iframe="free")
    per, lo, hi, _ = evaluate_hit(raw, 0.0, hc, d, st, build, ctx, target_hp, buffs, False)
    rate = aps / hits
    notes.append(f"Starforge nova: every {hits:.0f} swings, {raw:,.0f} raw above the swing's i-frames -> {per * rate:,.0f} DPS")
    return per * rate, lo * rate, hi * rate


def cooldown_s(ticks, d):
    """abilities.cooldown_s (cooldown reduction, skip chance, Arcane Cascade)."""
    return ab.cooldown_s(ticks, d)


def family_lucky(build):
    return build.family.startswith("melee:") or build.family in ("ability:Fangs_Base", "ability:Fangs_Maw")


def swing_rate(d, ctx):
    """Better Combat swings per second; 0 with Grand Archmage (every direct swing is cancelled)."""
    if d.get("god_archmage"):
        return 0.0
    return min(bc_swings_per_s(d["attack_speed"]), ctx.knobs["max_swings_per_s"])


def counter_dps(build, ctx, st, d, buffs, target_hp, record):
    """Counterstrike: after a block or dodge of a boss attack that reaches the player (d["boss_hits_reaching"]), chance
    to hit back for the attack-damage attribute (an unflagged normal hit). Melee families only: ability builds do not score attack-damage hits. Returns (dps, lo, hi,
    counters per second)."""
    gx = st.gfx
    if not gx or not gx["counter"] or d.get("god_archmage"):
        return 0.0, 0.0, 0.0, 0.0
    p = 1.0 - (1.0 - d["block"]) * (1.0 - d["dodge"])
    rate = gx["counter"] * p * d["boss_hits_reaching"]
    if rate <= 0:
        return 0.0, 0.0, 0.0, 0.0
    hc = HitClass("counter", normal=True, lucky=True, iframe="reset", echo_ok=True)
    v, lo, hi, _ = evaluate_hit(d["attack_damage"], 0.0, hc, d, st, build, ctx, target_hp, buffs)
    return v * rate, lo * rate, hi * rate, rate


def mana_sources(d, buffs, ctx, steal=0.0):
    out = [["Regen (base, gear, talents, trinkets)", d["mana_regen"]]]
    if buffs.get("mana_regen_add"):
        out.append(["Mana Regen Totem (adds to the regen multiplier)", buffs["mana_regen_add"] * d["mana_regen_vt"]])
    if steal:
        out.append([f"Mana Steal (max mana {d['mana_max']:,.0f} x per lucky hit)", steal])
    return out


def melee_lucky(d, buffs, aps):
    """Lucky-hit chance of a melee swing with Rampage (Instinct) and Lucky Strike Battle Cry."""
    lp = d["lucky_hit_chance"]
    if buffs.get("rampage_lucky") or buffs.get("battle_cry_lucky_per_hit"):
        extra = buffs.get("rampage_lucky", 0.0)
        if buffs.get("battle_cry_lucky_per_hit"):
            extra += buffs["battle_cry_lucky_per_hit"] * min(1.0, buffs["battle_cry_lucky_hits_per_s"] / max(aps, 1e-6))
        lp = min(1.0, d["lucky_hit_chance"] + extra)
    return lp


def melee_swings(weapon, build, ctx, st, d, buffs, target_hp, record, notes):
    """Better Combat swings with `weapon`: per-hit damage, swings/s, lucky chance, raw pre-event amount, pack targets."""
    ad = (d["attack_damage"] + spectral_ad(d, buffs, ctx)) * buffs.get("empower_ad_mult", 1.0)
    combo = MELEE_WEAPONS[weapon]["combo"]
    combo_avg = sum(combo) / len(combo)
    aps = min(bc_swings_per_s(d["attack_speed"]), ctx.knobs["max_swings_per_s"])
    notes.append(f"attack speed {d['attack_speed']:.2f} gives {aps:.2f} swings/s under Better Combat's tick cadence (10/s max, needs AS > 15)")
    raw = ad * combo_avg
    bc = buffs.get("battle_cry_ad_stacks_per_s", 0.0) * ad / max(aps, 1e-6)
    dd = d
    if buffs.get("rampage_lucky") or buffs.get("battle_cry_lucky_per_hit"):
        dd = dict(d)
        dd["lucky_hit_chance"] = melee_lucky(d, buffs, aps)
    hc = HitClass("melee", normal=True, lucky=True, iframe="reset", echo_ok=True, rampage=True)
    if st.gfx:
        dm = 1.0
        dparts = []
        if weapon == "battlestaff" and st.gfx["weaponmaster"] != 1.0:
            dm *= st.gfx["weaponmaster"]
            dparts.append(("Weaponmaster", st.gfx["weaponmaster"]))
        if st.gfx["pincushion"]:
            x = 1.0 + st.gfx["pincushion"] * aps * ctx.knobs["kill_time_s"] / 2.0
            dm *= x
            dparts.append(("Pincushion", x))
        buffs["direct_mult"] = dm
        buffs["direct_parts"] = dparts
    per_hit, lo, hi, steps = evaluate_hit(raw + bc, 0.0, hc, dd, st, build, ctx, target_hp, buffs, record=record)
    buffs.pop("direct_mult", None)
    buffs.pop("direct_parts", None)
    if d.get("god_archmage"):
        per_hit = lo = hi = 0.0
        aps = 0.0
        notes.append("Grand Archmage cancels every direct weapon swing and its procs")
    targets = 1.0
    aoe_size = d["on_hit_aoe"]
    if aoe_size > 0:
        targets += 0.6 * min(ctx.knobs["pack_size"] - 1, aoe_size * 1.5)
    chain = d["chain"]
    if chain > 0:
        targets += sum(0.5 ** k for k in range(1, int(chain + 1e-9) + 1))
    if int(st.attr.get("x:talent_tier:Cleave", 0)):
        t = int(st.attr["x:talent_tier:Cleave"])
        targets += dd["lucky_hit_chance"] * ctx.talents["Cleave"]["tiers"][t - 1]["damagePercentage"] * 2
    if weapon in ("battlestaff",):
        targets += 1.5
    elif weapon in ("sword", "axe"):
        targets += 0.8
    return {"per_hit": per_hit, "lo": lo, "hi": hi, "steps": steps, "aps": aps, "lucky": dd["lucky_hit_chance"],
            "raw": raw + bc, "targets": targets, "combo_avg": combo_avg}


class MeleeFamily:
    kind = "melee"

    def __init__(self, weapon):
        self.weapon = weapon
        self.id = "melee:" + weapon
        self.label = f"Melee - {weapon.title()}"
        self.mainhands = [weapon]
        self.main_spec = None

    def compute(self, build, ctx, st, d, target_hp, record=False):
        buffs, demand, notes, flags = buffs_for(build, ctx, st, d, record)
        eth = ethereal_chance(st, ctx)
        demand *= 1.0 - eth
        if "rampage_cost" in buffs:
            buffs["rampage_cost"] *= 1.0 - eth
        aps0 = swing_rate(d, ctx)
        if buffs.get("spectral"):
            buffs["spectral_s"] = spectral_stacks(buffs["spectral"], aps0)
        lp = melee_lucky(d, buffs, aps0)
        steal = aps0 * lp * d["mana_max"] * mana_steal_pct(st, ctx)
        supply = mana_supply(d, buffs, ctx) + steal
        ramp_cost = buffs.get("rampage_cost", 0.0)
        fury = fury_for(st, ctx, aps0)
        base_free = empower_toggle(buffs, max(0.0, supply - demand), eth, notes)
        free = starved_free(st, d, base_free, ramp_cost if "rampage" in buffs else 0.0)
        up = rampage_uptime(buffs, free, notes, fury)
        drain = up * ramp_cost if "rampage" in buffs else 0.0
        left = max(0.0, base_free - drain)
        nova_rate = nova_pd_rate(build, st, aps0)
        buffs["u_low_mana"] = low_mana_state(ctx, d, False, nova_rate, supply - demand - drain - buffs.get("empower_paid", 0.0))
        procs.finalize(build, ctx, st, d, buffs, procs.events(d, ctx, aps0, aps0 * lp), notes)
        buffs["_echo_probe"] = None
        m = melee_swings(self.weapon, build, ctx, st, d, buffs, target_hp, record, notes)
        echo_src = []
        if buffs["_echo_probe"]:
            echo_src.append(("swings", m["aps"]) + buffs["_echo_probe"] + (m["targets"], False))
        buffs["_echo_probe"] = None
        per_hit, lo, hi, steps, aps, targets = m["per_hit"], m["lo"], m["hi"], m["steps"], m["aps"], m["targets"]
        combo_avg = m["combo_avg"]
        dps = per_hit * aps
        cd, clo, chi, crate = counter_dps(build, ctx, st, d, buffs, target_hp, record)
        sq = (dps * dps / aps if aps > 0 else 0.0) + (cd * cd / crate if crate > 0 else 0.0)
        if buffs["_echo_probe"]:
            echo_src.append(("Counterstrike", crate) + buffs["_echo_probe"] + (1.0, False))
        buffs.pop("_echo_probe")
        er = {}
        ed, elo, ehi, epack, _ = procs.echo(echo_src, d, st, notes, er)
        cd += ed
        dps += cd
        lo, hi = lo * aps + clo + elo, hi * aps + chi + ehi
        nd, nlo, nhi = arcane_nova_dps(build, ctx, st, d, buffs, target_hp, aps, m["raw"], notes)
        if nova_rate > 0:
            # Sneaking the whole fight (no sneak windows to pay, avoidance halved): every nova is a free cast that dumps
            # the bar at its proc rate, and so is every instant buff cast, so each nova spends the spare mana that came in
            # since the last dump (at most the bar).
            buffs["sneak_share"] = 1.0
            bar = min(d["mana_max"], left / (nova_rate + buffs.get("_inst_buff_rate", 0.0)))
            buffs["pd_bar"] = bar
            if nd:
                pm = pd_curve(st, ctx, d, snap_bar(bar, d))
                nd, nlo, nhi = nd * pm, nlo * pm, nhi * pm
                notes.append(f"Starforge nova under Power Dump: {nova_rate:.2f} dumps/s, x{pm:.2f}")
        dps += nd
        if nd > 0:
            sq += nd * nd / (aps / st.special["arcane_nova_hits"])
        lo, hi = lo + nlo, hi + nhi
        aps_l = 1.0
        dd = {"lucky_hit_chance": m["lucky"]}
        res = {
            "dps": dps, "dps_lo": lo * aps_l, "dps_hi": hi * aps_l, "pack_dps": (dps - cd - nd) * targets + cd - ed + epack + nd,
            "targets": targets, "hit": per_hit, "rate": aps, "steps": steps if record else None,
            "notes": notes + [f"combo avg x{combo_avg:.2f} ({self.weapon})",
                              "melee is never i-frame clipped (Better Combat resets invulnerability)"],
            "flags": flags, "mana_supply": supply,
            "mana_demand": demand + (ramp_cost if "rampage" in buffs else 0.0) + buffs.get("empower_paid", 0.0),
            "source": f"{self.weapon} melee", "resist_bonus": buffs.get("colossus_resist", 0.0),
            "leech_rate": aps, "lucky_rate": aps * dd["lucky_hit_chance"], "lucky_hits": aps * dd["lucky_hit_chance"],
            "lucky_hits_ext": aps * dd["lucky_hit_chance"],
            "mana_sources": mana_sources(d, buffs, ctx, steal),
            "mana_drains": buffs["_drains"] + ([[f"Rampage ({up:.0%} uptime)", ramp_cost]] if "rampage" in buffs else [])
            + ([["Empower", buffs["empower_paid"]]] if "empower" in buffs else []),
            "fury_inc": fury[2] if fury else 1.0, "u_low_mana": buffs["u_low_mana"],
            "debuffed_uptime": buffs.get("debuffed_uptime", 0.0), "hit_sq": sq + er.get("sq", 0.0),
            "pd_bar": buffs.get("pd_bar", 0.0), "debuffs": buffs.get("debuffs", []),
            "pd_mode": "always" if nova_rate > 0 else None, "buff_casts": buffs.get("_buff_casts", []),
        }
        res.update(survival_buffs(buffs, d, ctx))
        cdot = buffs.get("cloud_dot")
        if cdot:
            res["dps"] += cdot[0]
            res["dps_lo"] += cdot[1]
            res["dps_hi"] += cdot[2]
            res["pack_dps"] += cdot[0]
            res["rotation"] = [{"spec": "cloud poison", "kind": "dot", "tier": 0, "share": 1.0, "rate": cdot[3], "dps": cdot[0],
                                "pmult": 1.0, "mana": 0.0, "leftover": False, "notes": [], "flags": []}]
        return res


def rang_period(d, st, ctx):
    """Seconds per vaultarang throw (VaultRangItem / VaultRangEntity, ruling R29): out and back over rang_travel_blocks at
    1.5 + velocity x 0.325 blocks per tick, then the catch cooldown of round(20 / attack speed) ticks (the snapshot
    attack speed, the period a swing would have)."""
    speed = 1.5 + st.get("velocity") * 0.325
    cd_ticks = math.floor(20.0 / d["attack_speed"] + 0.5 + 1e-9)
    return 2.0 * ctx.knobs["rang_travel_blocks"] / (speed * 20.0) + cd_ticks / 20.0


def rang_throws(build, ctx, st, d, buffs, target_hp, record, notes):
    """One vaultarang throw per rang_period: the outbound hit is a full normal attack (player.attack: damage increase,
    the registry, Rampage, lucky hits, echo; vanilla i-frames apply), the return hit a plain hurt of attack damage x (1 +
    returning damage) inside the outbound hit's i-frames, so only its raw above the outbound raw counts (no lucky hit,
    no echo). Piercing (+1 target per level) and ricochet (+0.5 per level) only add pack targets."""
    rate = 1.0 / rang_period(d, st, ctx)
    ad = (d["attack_damage"] + spectral_ad(d, buffs, ctx)) * buffs.get("empower_ad_mult", 1.0)
    raw = ad + buffs.get("battle_cry_ad_stacks_per_s", 0.0) * ad / max(rate, 1e-6)
    dd = d
    if buffs.get("rampage_lucky") or buffs.get("battle_cry_lucky_per_hit"):
        dd = dict(d)
        dd["lucky_hit_chance"] = melee_lucky(d, buffs, rate)
    hc = HitClass("rang", normal=True, lucky=True, iframe="plain", echo_ok=True, rampage=True)
    per, lo, hi, steps = evaluate_hit(raw, 0.0, hc, dd, st, build, ctx, target_hp, buffs, record=record)
    ret_raw = max(0.0, d["attack_damage"] * (1.0 + st.get("returning_damage")) - raw)
    rper = rlo = rhi = 0.0
    if ret_raw > 0:
        rper, rlo, rhi, _ = evaluate_hit(ret_raw, 0.0, HitClass("rang return", iframe="free"), d, st, build, ctx,
                                         target_hp, buffs)
    if d.get("god_archmage"):
        per = lo = hi = rper = rlo = rhi = 0.0
        rate = 0.0
        notes.append("Grand Archmage cancels every direct weapon attack, the rang's included")
    targets = 1.0
    if d["on_hit_aoe"] > 0:
        targets += 0.6 * min(ctx.knobs["pack_size"] - 1, d["on_hit_aoe"] * 1.5)
    if d["chain"] > 0:
        targets += sum(0.5 ** k for k in range(1, int(d["chain"] + 1e-9) + 1))
    if int(st.attr.get("x:talent_tier:Cleave", 0)):
        t = int(st.attr["x:talent_tier:Cleave"])
        targets += dd["lucky_hit_chance"] * ctx.talents["Cleave"]["tiers"][t - 1]["damagePercentage"] * 2
    targets += min(ctx.knobs["pack_size"] - 1, st.get("piercing")) + 0.5 * st.get("ricochet")
    notes.append(f"vaultarang: {rate:.2f} throws/s; return hit keeps {ret_raw:,.0f} raw above the outbound {raw:,.0f}")
    return {"per_hit": per, "lo": lo, "hi": hi, "steps": steps, "rate": rate, "lucky": dd["lucky_hit_chance"],
            "raw": raw, "ret": rper, "ret_lo": rlo, "ret_hi": rhi, "ret_hits": rate if ret_raw > 0 else 0.0,
            "targets": targets}


class RangedFamily:
    """Vaultarang (plan WP-D4): a ranged weapon family; no weaving and no secondaries, like the melee families."""
    kind = "ranged"

    def __init__(self, weapon):
        self.weapon = weapon
        self.id = "ranged:" + weapon
        self.label = "Ranged - Vaultarang"
        self.mainhands = [weapon]
        self.main_spec = None

    def compute(self, build, ctx, st, d, target_hp, record=False):
        buffs, demand, notes, flags = buffs_for(build, ctx, st, d, record)
        eth = ethereal_chance(st, ctx)
        demand *= 1.0 - eth
        if "rampage_cost" in buffs:
            buffs["rampage_cost"] *= 1.0 - eth
        rate0 = 1.0 / rang_period(d, st, ctx) if not d.get("god_archmage") else 0.0
        if buffs.get("spectral"):
            buffs["spectral_s"] = spectral_stacks(buffs["spectral"], rate0)
        lp = melee_lucky(d, buffs, rate0)
        steal = rate0 * lp * d["mana_max"] * mana_steal_pct(st, ctx)
        supply = mana_supply(d, buffs, ctx) + steal
        ramp_cost = buffs.get("rampage_cost", 0.0)
        fury = fury_for(st, ctx, rate0)
        free = starved_free(st, d, empower_toggle(buffs, max(0.0, supply - demand), eth, notes),
                            ramp_cost if "rampage" in buffs else 0.0)
        up = rampage_uptime(buffs, free, notes, fury)
        drain = up * ramp_cost if "rampage" in buffs else 0.0
        buffs["u_low_mana"] = low_mana_state(ctx, d, False, 0.0, supply - demand - drain - buffs.get("empower_paid", 0.0))
        procs.finalize(build, ctx, st, d, buffs, procs.events(d, ctx, rate0, rate0 * lp), notes)
        buffs["_echo_probe"] = None
        m = rang_throws(build, ctx, st, d, buffs, target_hp, record, notes)
        echo_src = []
        if buffs["_echo_probe"]:
            echo_src.append(("rang", m["rate"]) + buffs["_echo_probe"] + (m["targets"], False))
        buffs["_echo_probe"] = None
        rate = m["rate"]
        dps = (m["per_hit"] + m["ret"]) * rate
        cd, clo, chi, crate = counter_dps(build, ctx, st, d, buffs, target_hp, record)
        if buffs["_echo_probe"]:
            echo_src.append(("Counterstrike", crate) + buffs["_echo_probe"] + (1.0, False))
        buffs.pop("_echo_probe")
        out = m["per_hit"] * rate
        sq = out * out / rate if rate > 0 else 0.0
        if m["ret_hits"] > 0:
            rv = m["ret"] * rate
            sq += rv * rv / rate
        if crate > 0:
            sq += cd * cd / crate
        er = {}
        ed, elo, ehi, epack, _ = procs.echo(echo_src, d, st, notes, er)
        res = {
            "dps": dps + cd + ed, "dps_lo": (m["lo"] + m["ret_lo"]) * rate + clo + elo,
            "dps_hi": (m["hi"] + m["ret_hi"]) * rate + chi + ehi,
            "pack_dps": m["per_hit"] * rate * m["targets"] + m["ret"] * rate + cd + epack,
            "targets": m["targets"], "hit": m["per_hit"], "rate": rate, "steps": m["steps"] if record else None,
            "notes": notes + ["the rang is thrown from range (no Better Combat entry, so it never weaves)"],
            "flags": flags, "mana_supply": supply,
            "mana_demand": demand + (ramp_cost if "rampage" in buffs else 0.0) + buffs.get("empower_paid", 0.0),
            "source": "vaultarang", "resist_bonus": buffs.get("colossus_resist", 0.0),
            "leech_rate": rate + m["ret_hits"], "lucky_rate": rate * m["lucky"], "lucky_hits": rate * m["lucky"],
            "lucky_hits_ext": rate * m["lucky"],
            "mana_sources": mana_sources(d, buffs, ctx, steal),
            "mana_drains": buffs["_drains"] + ([[f"Rampage ({up:.0%} uptime)", ramp_cost]] if "rampage" in buffs else [])
            + ([["Empower", buffs["empower_paid"]]] if "empower" in buffs else []),
            "fury_inc": fury[2] if fury else 1.0, "u_low_mana": buffs["u_low_mana"],
            "debuffed_uptime": buffs.get("debuffed_uptime", 0.0), "hit_sq": sq + er.get("sq", 0.0),
            "pd_bar": buffs.get("pd_bar", 0.0), "debuffs": buffs.get("debuffs", []), "buff_casts": buffs.get("_buff_casts", []),
        }
        res.update(survival_buffs(buffs, d, ctx))
        return res


RANGED_WEAPONS = ("rang",)


class AbilityFamily:
    kind = "ability"

    def __init__(self, spec_id, model):
        self.id = "ability:" + spec_id
        self.main_spec = spec_id
        self.model = model
        self.label = model.label
        self.mainhands = ["sword", "axe", "battlestaff", "trident"]

    def secondaries(self, build, ctx):
        """Learned damage abilities other than the main one, in spec order (the rotation casts them too)."""
        return [x for x in secondary_specs() if x != self.main_spec and build.abilities.get(x, 0) > 0 and x in ctx.abilities]

    def compute(self, build, ctx, st, d, target_hp, record=False):
        return rotation.compute(self, self.secondaries(build, ctx), build, ctx, st, d, target_hp, record)


_SECONDARY = None


def secondary_specs():
    """Damage abilities a build may learn next to its main one (the spec table's secondary_eligible: every modeled
    damage spec except the Shell Porcupine thorns toggle, which is a buff; baseline skills are excluded by the learning
    move)."""
    global _SECONDARY
    if _SECONDARY is None:
        _SECONDARY = [x for x in ab.MODELS if ab.SPEC_TABLE[x]["secondary_eligible"]]
    return _SECONDARY


def build_registry():
    for w in MELEE_WEAPONS:
        register(MeleeFamily(w))
    for w in RANGED_WEAPONS:
        register(RangedFamily(w))
    for sid, model in ab.MODELS.items():
        register(AbilityFamily(sid, model))
