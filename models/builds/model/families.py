"""Build families: the primary damage source (melee weapon or ability spec) and how its DPS is computed."""
import math

from .damage import HitClass, evaluate_hit, bug
from .stats import ability_level_bonus
from .log import fallback
from . import abilities as ab

MELEE_WEAPONS = {
    "sword": {"combo": [1.0, 1.0, 1.25]},
    "axe": {"combo": [1.0, 1.0]},
    "battlestaff": {"combo": [0.8, 1.0, 1.2, 1.4, 0.8, 0.8]},
    "trident": {"combo": [1.0]},
}

BUFF_SPECS = ["Taunt_Base", "Totem_Player_Damage", "Rampage_Base", "Rampage_Berserker", "Rampage_Bloodlust", "Rampage_Instinct",
              "Battle_Cry_Base", "Battle_Cry_Spectral_Strike", "Battle_Cry_Lucky_Strike", "Colossus_Base",
              "Concentrate_Base", "Totem_Mana_Regen", "Shell_Porcupine", "Nova_Slow"]
BUFF_SKILL = {s: s.split("_")[0] if not s.startswith("Battle_Cry") else "Battle_Cry" for s in BUFF_SPECS}
BUFF_SKILL.update({"Taunt_Base": "Taunt", "Totem_Player_Damage": "Totem", "Totem_Mana_Regen": "Totem", "Colossus_Base": "Colossus",
                   "Concentrate_Base": "Expunge", "Shell_Porcupine": "Shell", "Nova_Slow": "Nova"})

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
    bonus = ability_level_bonus(st.ability_levels, a["skill"], ctx.ability_groups, "masterful" in st.flags)
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
    U = max(1, math.floor(0.25 * max(D, 2.0) + 0.5))
    return 20.0 / (k + U)


def cooldown_s(ticks, d):
    return max(0.05, ticks * (1.0 - d["cooldown_reduction"]) / 20.0)


def buffs_for(build, ctx, st, d, record):
    """Active buff abilities: returns (buff values, mana/sec demand, notes, flags)."""
    T = ctx.knobs["kill_time_s"]
    buffs = {}
    demand = 0.0
    notes = []
    flags = []
    vuln_sources = []
    drains = []
    for sid, learned in build.abilities.items():
        if sid not in BUFF_SPECS or learned <= 0 or build.family == "ability:" + sid:
            continue
        tier, bonus = effective_tier(ctx, st, sid, learned)
        c = spec_cfg(ctx, st, sid, tier, build)
        d0 = demand
        if sid == "Totem_Player_Damage":
            dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
            cd = cooldown_s(c["cooldownTicks"], d)
            up = dur / (dur + cd)
            buffs["totem_player_damage"] = c["totemPlayerDamagePercent"] * up
            demand += c["manaCost"] / (dur + cd)
            notes.append(f"Totem (Player Damage) t{tier}: +{c['totemPlayerDamagePercent']:.2f} to the multiplier registry, {up:.0%} uptime (stand in radius)")
        elif sid == "Totem_Mana_Regen":
            dur = c["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
            cd = cooldown_s(c["cooldownTicks"], d)
            up = dur / (dur + cd)
            buffs["mana_regen_add"] = c["totemManaRegenPercent"] * up
            demand += c["manaCost"] / (dur + cd)
            notes.append(f"Totem (Mana Regen) t{tier}: +{c['totemManaRegenPercent']:.0%} to the mana regen multiplier (summed with gear "
                         f"mana regen, both MULTIPLY_BASE on generic.mana_regen) at {up:.0%} uptime")
        elif sid.startswith("Rampage"):
            cost = c["manaCostPerSecond"] + 0.5 * c.get("manaRampPerSecond", 0.0) * T
            buffs["rampage"] = c["damageIncrease"]
            buffs["rampage_cost"] = cost
            k = ctx.knobs
            if (sid == "Rampage_Berserker" and d["mana_regen"] >= k["rampage_refresh_regen"] - 1e-9
                    and d["cooldown_reduction"] >= k["rampage_refresh_cdr"] - 1e-9):
                buffs["rampage_fixed_uptime"] = k["rampage_refresh_uptime"]
                buffs["rampage_cost"] = c["manaCostPerSecond"]
                notes.append(f"Rampage Berserker refreshed on cooldown: {d['mana_regen']:.1f} mana/s regen and {d['cooldown_reduction']:.0%} CDR "
                             f"meet the {k['rampage_refresh_regen']:.0f}/s and {k['rampage_refresh_cdr']:.0%} bar, so {k['rampage_refresh_uptime']:.0%} uptime")
            if sid == "Rampage_Instinct":
                buffs["rampage_lucky"] = c.get("luckyHitChance", 0.0)
            notes.append(f"{sid} t{tier}: melee x{1 + c['damageIncrease']:.2f} while toggled ({cost:.1f} mana/s)")
        elif sid.startswith("Battle_Cry"):
            cd = cooldown_s(c["cooldownTicks"], d)
            per_cast_stacks = min(c["maxStacksTotal"], 30)
            if sid == "Battle_Cry_Base":
                buffs["battle_cry_ad_stacks_per_s"] = c["attackDamagePerStack"] * per_cast_stacks / cd
                notes.append(f"Battle Cry t{tier}: {per_cast_stacks} stacks x {c['attackDamagePerStack']:.2f} AD-multiples per cast every {cd:.1f}s")
            elif sid == "Battle_Cry_Spectral_Strike":
                buffs["battle_cry_ap_stacks_per_s"] = c["abilityPowerPerStack"] * per_cast_stacks / cd
                notes.append(f"Battle Cry (Spectral) t{tier}: {per_cast_stacks} stacks x {c['abilityPowerPerStack']:.2f} AP-multiples per cast every {cd:.1f}s")
            else:
                buffs["battle_cry_lucky_per_hit"] = c["luckyHitChancePerStack"] * c["maxStacksUsedPerHit"]
                buffs["battle_cry_lucky_hits_per_s"] = (per_cast_stacks / c["maxStacksUsedPerHit"]) / cd
                notes.append(f"Battle Cry (Lucky) t{tier}: +{buffs['battle_cry_lucky_per_hit']:.0%} lucky on ~{per_cast_stacks / c['maxStacksUsedPerHit']:.1f} hits per cast")
            demand += c["manaCost"] / cd
        elif sid == "Taunt_Base":
            dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
            cd = cooldown_s(c["cooldownTicks"], d)
            up = min(1.0, dur / cd)
            vuln_sources.append(("Taunt", int(c["amplifier"]), up, True))
            demand += c["manaCost"] / max(cd, dur)
            notes.append(f"Taunt t{tier}: Vulnerable amplifier {c['amplifier']} on the boss, {up:.0%} uptime")
        elif sid == "Colossus_Base":
            dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
            cd = cooldown_s(c["cooldownTicks"], d)
            up = dur / (dur + cd)
            buffs["colossus_resist"] = c["additionalResistance"] * up
            demand += c["manaCost"] / (dur + cd)
            notes.append(f"Colossus t{tier}: +{c['additionalResistance']:.0%} resistance at {up:.0%} uptime")
        elif sid == "Nova_Slow":
            lvl = st.special.get("frost_nova_vulnerability", 0.0)
            if lvl > 0:
                dur = c["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
                cd = cooldown_s(c["cooldownTicks"], d)
                up = min(1.0, 4.0 * dur / cd)
                vuln_sources.append(("Frost Nova (vulnerability modification)", int(lvl) - 1, up, True))
                demand += c["manaCost"] / cd
                notes.append(f"Frost Nova t{tier}: Vulnerable amplifier {int(lvl) - 1} for {4 * dur:.0f}s every {cd:.1f}s ({up:.0%} uptime)")
        elif sid == "Shell_Porcupine":
            buffs["porcupine"] = c["additionalThornsDamagePercent"]
            demand += c["manaCostPerSecond"]
            notes.append(f"Shell (Porcupine) t{tier}: flat thorns x{1 + c['additionalThornsDamagePercent']:.1f} while toggled ({c['manaCostPerSecond']:.0f} mana/s)")
        elif sid == "Concentrate_Base":
            amp = ctx.knobs["concentrate_empower_amp"]
            buffs["empower_ad_mult"] = 1.0 + 0.1 * (amp + 1)
            cd = cooldown_s(c["cooldownTicks"], d)
            demand += c["manaCost"] / cd
            flags.append("gimmick: Concentrate Empower stacking (modeled at a held amp of %d; uncapped in game)" % amp)
            notes.append(f"Concentrate t{tier}: Empower amp {amp} held -> attack damage x{buffs['empower_ad_mult']:.1f}")
        if demand > d0:
            drains.append([f"{sid.replace('_', ' ')} t{tier}", demand - d0])
    buffs["_drains"] = drains
    from .abilities import etch_value
    lv = etch_value(build, "etching_lucky_vulnerable")
    if lv and family_lucky(build):
        lvl = int(round(lv["level"] if isinstance(lv, dict) else lv))
        up = min(1.0, d["lucky_hit_chance"] * 3.0)
        vuln_sources.append(("Lucky Vulnerable etching", lvl - 1, up, False))
    tv = etch_value(build, "etching_totem_player_damage_effect")
    if tv and "totem_player_damage" in buffs:
        lvl = int(round(tv["level"] if isinstance(tv, dict) else tv))
        tc = spec_cfg(ctx, st, "Totem_Player_Damage", effective_tier(ctx, st, "Totem_Player_Damage", build.abilities["Totem_Player_Damage"])[0], build)
        dur = tc["totemDurationTicks"] * (1.0 + d["effect_duration"]) / 20.0
        up = dur / (dur + cooldown_s(tc["cooldownTicks"], d))
        vuln_sources.append(("Vulnerable Totem etching", lvl - 1, up, False))
    rl = etch_value(build, "etching_rampage_lucky_hit")
    if rl and "rampage" in buffs:
        buffs["rampage_lucky"] = buffs.get("rampage_lucky", 0.0) + float(rl)
        notes.append(f"Rampaging Lucky Hit etching: +{float(rl):.0%} lucky while Rampage is on")
    if vuln_sources:
        best = 1.0
        best_src = None
        for name, amp, up, is_amp in vuln_sources:
            capped = min(8, amp if is_amp else amp + 1) - (0 if is_amp else 1)
            if bug(build, "VULN-OFF1"):
                bonus = 0.1 * capped
            else:
                bonus = 0.1 * (capped + 1)
            m = 1.0 + bonus * up
            if m > best:
                best, best_src = m, name
        buffs["vulnerable"] = best
        notes.append(f"Vulnerable on the boss from {best_src}: x{best:.2f} expected (VULN-OFF1 {'bugged' if bug(build, 'VULN-OFF1') else 'fixed'})")
    return buffs, demand, notes, flags


def family_lucky(build):
    return build.family.startswith("melee:") or build.family in ("ability:Fangs_Base", "ability:Fangs_Maw")


def mana_supply(d, buffs, ctx):
    """Sustained mana per second from regeneration (user ruling: the vault is continuous, so the pool is never a source).
    The Mana Regen Totem's MULTIPLY_BASE adds to gear mana regen on the same attribute. Mana Steal comes on top."""
    return d["mana_regen"] + buffs.get("mana_regen_add", 0.0) * d["mana_regen_vt"]


def mana_steal_pct(st, ctx):
    """Mana Steal (ManaLeechLuckyHitTalent): every lucky hit restores max mana x this."""
    t = int(st.attr.get("x:talent_tier:Mana_Steal", 0))
    return ctx.talents["Mana_Steal"]["tiers"][t - 1]["maxManaPercentage"] if t else 0.0


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


def rampage_uptime(buffs, free, notes):
    """Rampage runs on the mana left after the other buffs; scales buffs["rampage"] by its uptime."""
    up = 1.0
    if "rampage" in buffs:
        ramp_cost = buffs.get("rampage_cost", 0.0)
        up = min(1.0, free / ramp_cost) if ramp_cost > 0 else 1.0
        if "rampage_fixed_uptime" in buffs:
            up = min(up, buffs["rampage_fixed_uptime"])
        buffs["rampage"] *= up
        notes.append(f"Rampage uptime {up:.0%}")
    return up


def melee_swings(weapon, build, ctx, st, d, buffs, target_hp, record, notes):
    """Better Combat swings with `weapon`: per-hit damage, swings/s, lucky chance, raw pre-event amount, pack targets."""
    ad = d["attack_damage"] * buffs.get("empower_ad_mult", 1.0)
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
    per_hit, lo, hi, steps = evaluate_hit(raw + bc, 0.0, hc, dd, st, build, ctx, target_hp, buffs, record=record)
    targets = 1.0
    aoe_size = d["on_hit_aoe"]
    if aoe_size > 0:
        targets += 0.6 * min(ctx.knobs["pack_size"] - 1, aoe_size * 1.5)
    chain = d["chain"]
    if chain > 0:
        targets += sum(0.5 ** k for k in range(1, int(chain) + 1))
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
        aps0 = min(bc_swings_per_s(d["attack_speed"]), ctx.knobs["max_swings_per_s"])
        lp = melee_lucky(d, buffs, aps0)
        steal = aps0 * lp * d["mana_max"] * mana_steal_pct(st, ctx)
        supply = mana_supply(d, buffs, ctx) + steal
        ramp_cost = buffs.get("rampage_cost", 0.0)
        up = rampage_uptime(buffs, max(0.0, supply - demand), notes)
        m = melee_swings(self.weapon, build, ctx, st, d, buffs, target_hp, record, notes)
        per_hit, lo, hi, steps, aps, targets = m["per_hit"], m["lo"], m["hi"], m["steps"], m["aps"], m["targets"]
        combo_avg = m["combo_avg"]
        dps = per_hit * aps
        dd = {"lucky_hit_chance": m["lucky"]}
        res = {
            "dps": dps, "dps_lo": lo * aps, "dps_hi": hi * aps, "pack_dps": dps * targets,
            "targets": targets, "hit": per_hit, "rate": aps, "steps": steps if record else None,
            "notes": notes + [f"combo avg x{combo_avg:.2f} ({self.weapon})",
                              "melee is never i-frame clipped (Better Combat resets invulnerability)"],
            "flags": flags, "mana_supply": supply, "mana_demand": demand + (ramp_cost if "rampage" in buffs else 0.0),
            "source": f"{self.weapon} melee", "resist_bonus": buffs.get("colossus_resist", 0.0),
            "leech_rate": aps, "lucky_rate": aps * dd["lucky_hit_chance"],
            "mana_sources": mana_sources(d, buffs, ctx, steal),
            "mana_drains": buffs["_drains"] + ([[f"Rampage ({up:.0%} uptime)", ramp_cost]] if "rampage" in buffs else []),
        }
        return res


class AbilityFamily:
    kind = "ability"

    def __init__(self, spec_id, model):
        self.id = "ability:" + spec_id
        self.main_spec = spec_id
        self.model = model
        self.label = model.label
        self.mainhands = ["sword", "axe", "battlestaff", "trident"]

    def mana_scale(self, out, base, demand, d, st, ctx):
        """Share of the cooldown rate mana allows (closed form with Mana Steal from the ability's own lucky hits)."""
        need = out["mana_per_s"]
        lucky_ok = out.get("leech_ok", self.main_spec in ab.LEECH_SPECS) and out.get("lucky_ok", self.main_spec in ab.LUCKY_SPECS)
        k = out.get("rate", 0.0) * d["lucky_hit_chance"] * d["mana_max"] * mana_steal_pct(st, ctx) if lucky_ok else 0.0
        avail = max(0.0, base - demand)
        scale = 1.0
        if need > 0 and avail + k < need:
            scale = avail / (need - k)
        return scale, k

    def compute(self, build, ctx, st, d, target_hp, record=False):
        buffs, demand, notes, flags = buffs_for(build, ctx, st, d, record)
        learned = build.abilities.get(self.main_spec, 0)
        tier, bonus = effective_tier(ctx, st, self.main_spec, max(1, learned))
        c = spec_cfg(ctx, st, self.main_spec, tier, build)
        weave = bool(getattr(build, "weave", False))
        steal_m = 0.0
        if weave:
            aps0 = min(bc_swings_per_s(d["attack_speed"]), ctx.knobs["max_swings_per_s"])
            steal_m = aps0 * melee_lucky(d, buffs, aps0) * d["mana_max"] * mana_steal_pct(st, ctx)
        base = mana_supply(d, buffs, ctx) + steal_m
        ramp_cost = buffs.get("rampage_cost", 0.0)
        free = 0.0
        if "rampage" in buffs:
            # The main ability has mana priority; Rampage runs on what is left (its cost and rate do not depend on Rampage).
            probe = self.model.compute(build, ctx, st, d, c, buffs, target_hp, False)
            s0, k0 = self.mana_scale(probe, base, demand, d, st, ctx)
            free = max(0.0, base + k0 * s0 - demand - probe["mana_per_s"] * s0)
        up = rampage_uptime(buffs, free, notes)
        m = None
        if weave:
            m = melee_swings(build.items["mainhand"].type, build, ctx, st, d, buffs, target_hp, record, notes)
            buffs["_weave"] = {"raw": m["raw"], "f": min(1.0, ctx.knobs["weave_window_s"] * m["aps"]), "dropped": False}
        out = self.model.compute(build, ctx, st, d, c, buffs, target_hp, record)
        need = out["mana_per_s"]
        scale, k = self.mana_scale(out, base, demand, d, st, ctx)
        if scale < 1.0:
            notes.append(f"mana-limited: casting at {scale:.0%} of the cooldown rate ({max(0.0, base - demand) + k * scale:.1f}/{need:.1f} mana/s)")
        steal = k * scale
        supply = base + steal
        dps = out["dps"] * scale
        res = {
            "dps": dps, "dps_lo": out.get("dps_lo", out["dps"]) * scale, "dps_hi": out.get("dps_hi", out["dps"]) * scale,
            "pack_dps": out["pack_dps"] * scale, "targets": out.get("targets", 1.0), "hit": out.get("hit"),
            "rate": out.get("rate", 0.0) * scale, "steps": out.get("steps") if record else None,
            "notes": [f"{self.main_spec} effective tier {tier} (learned {learned} + {bonus:+.0f} from gear)"] + out.get("notes", []) + notes,
            "flags": flags + out.get("flags", []), "mana_supply": supply,
            "mana_demand": demand + need + (ramp_cost if "rampage" in buffs else 0.0),
            "source": self.label, "resist_bonus": buffs.get("colossus_resist", 0.0),
            "leech_rate": 0.0, "lucky_rate": 0.0,
            "mana_sources": mana_sources(d, buffs, ctx, steal + steal_m),
            "mana_drains": buffs["_drains"] + ([[f"Rampage ({up:.0%} uptime)", ramp_cost]] if "rampage" in buffs else [])
            + [[f"{self.label} at its full cooldown rate" + (f" (mana only covers {scale:.0%} of that)" if scale < 1 else ""), need]],
        }
        if out.get("leech_ok", self.main_spec in ab.LEECH_SPECS):
            res["leech_rate"] = res["rate"]
            if out.get("lucky_ok", self.main_spec in ab.LUCKY_SPECS):
                res["lucky_rate"] = res["rate"] * d["lucky_hit_chance"]
        if weave:
            w = buffs["_weave"]
            if w["dropped"]:
                res["leech_rate"] *= 1.0 - w["f"]
                res["lucky_rate"] *= 1.0 - w["f"]
            # User ruling 2026-10-08: ability builds are AP-centred, so woven melee damage is not scored; the swings
            # only add leech, Life Steal and Mana Steal (and clip plain ability hits).
            mdps = m["per_hit"] * m["aps"]
            res["leech_rate"] += m["aps"]
            res["lucky_rate"] += m["aps"] * m["lucky"]
            res["weave"] = {"weapon": build.items["mainhand"].type, "swings": m["aps"], "melee_dps": mdps, "ability_dps": dps,
                            "melee_raw": m["raw"], "window_share": w["f"], "dropped": w["dropped"], "mana_steal": steal_m,
                            "melee_lucky": m["lucky"]}
            if record:
                b0 = dict(buffs)
                b0.pop("_weave")
                clean = self.model.compute(build, ctx, st, d, c, b0, target_hp, False)
                res["weave"]["clip_loss"] = (clean["dps"] - out["dps"]) * scale
                res["weave"]["melee_steps"] = m["steps"]
            res["notes"].append(f"weaving {build.items['mainhand'].type} swings: {m['aps']:.2f}/s for leech and Mana Steal (their {mdps:,.0f} DPS is not scored); "
                                f"{w['f']:.0%} of plain ability hits land inside a swing's i-frame window")
        return res


def build_registry():
    for w in MELEE_WEAPONS:
        register(MeleeFamily(w))
    for sid, model in ab.MODELS.items():
        register(AbilityFamily(sid, model))
