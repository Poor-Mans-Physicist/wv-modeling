"""Turn the extracted config catalog into option tables the optimizer can pick from."""
import json
import math
import os

from . import stages
from .log import fallback

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "catalog_0.34.1.json")

ARMOR = ["helmet", "chestplate", "leggings", "boots"]
MAINHAND = ["sword", "axe", "battlestaff", "trident", "rang"]
OFFHAND = ["shield", "focus", "wand", "plushie"]
SLOTS = ARMOR + ["mainhand", "offhand", "necklace"]

AFFIX_COUNT = {"OMEGA": {"armor": 6, "other": 5}, "MYTHIC": {"armor": 7, "other": 6}}

ETCH_TYPE = {"sword": "SWORD", "battlestaff": "SWORD", "rang": "SWORD", "axe": "AXE", "trident": "AXE",
             "plushie": "FOCUS", "focus": "FOCUS", "wand": "WAND", "shield": "SHIELD", "helmet": "HELMET",
             "chestplate": "CHESTPLATE", "leggings": "LEGGINGS", "boots": "BOOTS"}

COMBAT_ATTRS = {
    "the_vault:attack_damage", "the_vault:attack_speed", "the_vault:attack_speed_percent",
    "the_vault:ability_power", "the_vault:ability_power_percent", "the_vault:ability_power_percentile",
    "the_vault:damage_increase", "the_vault:lucky_hit_chance", "the_vault:lucky_hit_chance_percentile",
    "the_vault:jester_lucky_hit_chance_percentile", "the_vault:cooldown_reduction",
    "the_vault:cooldown_reduction_percentile", "the_vault:cooldown_reduction_cap", "the_vault:area_of_effect",
    "the_vault:health", "the_vault:health_percentile", "the_vault:armor", "the_vault:armor_percentile",
    "the_vault:resistance", "the_vault:resistance_cap", "the_vault:block", "the_vault:block_cap",
    "the_vault:dodge_percent", "the_vault:mana_additive", "the_vault:mana_additive_percentile",
    "the_vault:mana_regen", "the_vault:echoing_chance", "the_vault:echoing_damage", "the_vault:execution_damage",
    "the_vault:on_hit_chain", "the_vault:on_hit_aoe", "the_vault:double_hit_chance",
    "the_vault:relentless_strike", "the_vault:third_attack", "the_vault:added_ability_level",
    "the_vault:added_talent_level", "the_vault:effect_duration", "the_vault:critical_hit_mitigation",
    "the_vault:ability_cooldown_skip", "the_vault:arcane_nova_on_hit", "the_vault:burning_hit_chance",
    "the_vault:reaving_damage", "the_vault:ability_cooldown_percent", "the_vault:ability_area_of_effect_percent",
    "the_vault:thorns_damage_flat", "the_vault:effect", "the_vault:leech", "the_vault:healing_effectiveness",
    "the_vault:on_kill_heal", "the_vault:ability_mana_cost_percent", "the_vault:lucky_thorns",
    "the_vault:thorns_damage", "the_vault:thorns_scaling_damage", "the_vault:ap_scaling_damage",
    "the_vault:castle_bastion", "the_vault:ability_special_modification", "the_vault:unique_effect",
}
# Boolean gear attributes ({"flag": true}) the model reads as build flags.
FLAG_ATTRS = {"the_vault:lucky_thorns"}
# Attributes whose mere presence is a build flag (any on-kill-heal modifier gives Bloodthirst, GearAttributeEvents:678).
PRESENCE_FLAGS = {"the_vault:on_kill_heal": "bloodthirst"}
# Granted effects read as build flags (unique_effect effectKey -> flag).
EFFECT_FLAGS = {"woldsvaults:safer_space": "safer_space"}
# Ability special modifications the model reads (specialModificationKey -> name); values add up per key.
SPECIAL_MODS = {"the_vault:frost_nova_vulnerability": "frost_nova_vulnerability",
                "the_vault:fireball_special_modification": "fireball_recast"}
# Per-ability multipliers (CooldownHelper / ManaCostHelper): x max(0, 1 + amount) on the named spec or its skill.
ABILITY_MODS = {"the_vault:ability_cooldown_percent": "cooldown", "the_vault:ability_mana_cost_percent": "mana"}

_cat = None
VANILLA_COMBAT = {"minecraft:generic.max_health", "the_vault:generic.mana_max", "the_vault:generic.mana_regen"}


def load():
    global _cat
    if _cat is None:
        with open(DATA, encoding="utf-8") as f:
            _cat = json.load(f)
        h = _cat["hyper"]
        stages.HYPER.update({
            "ref_health": h["referenceBossHealth"], "ref_damage": h["referenceBossDamage"],
            "stat_factor": h["hyperStatFactor"], "health_percent": h["bossHealthPercent"],
            "damage_percent": h["bossDamagePercent"], "increment": h["bossStatIncrement"],
        })
    return _cat


def roll(value, q=1.0):
    """Value of a {min,max,step} roll at quality q (0 = min, 1 = max), snapped to the roll's step like the game."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "min" in value and "max" in value:
        lo, hi = float(value["min"]), float(value["max"])
        v = lo + q * (hi - lo)
        step = value.get("step")
        if isinstance(step, (int, float)) and step > 0 and hi > lo:
            v = lo + math.floor((v - lo) / step + 0.5) * step
            v = min(max(v, lo), hi)
        return round(v, 6)
    return None


def best_open_tier(mod):
    tiers = [t for t in mod.get("open", []) if t.get("weight", 0) > 0]
    if not tiers:
        return None
    numeric = [t for t in tiers if roll(t["value"]) is not None]
    if numeric:
        return max(numeric, key=lambda t: t["value"]["max"] if isinstance(t["value"], dict) else t["value"])
    return tiers[-1]


class Affix:
    __slots__ = ("attribute", "group", "ident", "value", "raw", "kind", "label")

    def __init__(self, attribute, group, ident, value, raw, kind):
        self.attribute = attribute
        self.group = group
        self.ident = ident
        self.value = value
        self.raw = raw
        self.kind = kind
        self.label = label_for(attribute, raw, value)

    def to_json(self):
        return {"attribute": self.attribute, "group": self.group, "id": self.ident, "value": self.value,
                "raw": self.raw, "kind": self.kind, "label": self.label}


def short(attr):
    return attr.split(":", 1)[-1]


def label_for(attribute, raw, value):
    a = short(attribute)
    if a == "added_ability_level" and isinstance(raw, dict):
        return f"+{raw.get('levelChange')} {raw.get('abilityKey')} level"
    if a == "added_talent_level" and isinstance(raw, dict):
        return f"+{raw.get('levelChange')} {raw.get('talentKey')} talent level"
    if a == "effect" and isinstance(raw, dict):
        if raw.get("effectKey") == "occultism:double_jump":
            return f"Multi Jump {raw.get('amplifier', 0) + 1}"
        return f"{raw.get('effectKey')} {raw.get('amplifier', 0) + 1}"
    if value is None:
        return a
    if abs(value) < 5 and a not in ("attack_damage", "ability_power", "health", "armor", "mana_additive",
                                    "on_hit_chain", "on_hit_aoe", "thorns_damage_flat", "attack_speed"):
        return f"{a} {value * 100:+.1f}%"
    return f"{a} {value:+.1f}"


LOWER_IS_BETTER = {"the_vault:ability_cooldown_percent", "the_vault:ability_mana_cost_percent"}


def affix_from(mod, kind, q):
    tier = best_open_tier(mod)
    if tier is None:
        return None
    raw = tier["value"]
    if mod["attribute"] in LOWER_IS_BETTER and isinstance(raw, dict) and "min" in raw and "max" in raw:
        lo, hi = sorted((float(raw["min"]), float(raw["max"])))
        raw = dict(raw, min=hi, max=lo, step=-abs(raw["step"]) if isinstance(raw.get("step"), (int, float)) else raw.get("step"))
    v = roll(raw, q)
    if v is None and isinstance(raw, dict) and "levelChange" in raw:
        v = float(raw["levelChange"])
    return Affix(mod["attribute"], mod.get("group"), mod.get("identifier"), v, raw, kind)


def is_combat(affix):
    if affix.attribute not in COMBAT_ATTRS:
        return False
    if affix.attribute == "the_vault:effect":
        return isinstance(affix.raw, dict) and affix.raw.get("effectKey") in ("minecraft:luck", "minecraft:resistance",
                                                                              "minecraft:strength")
    if affix.attribute == "the_vault:ability_special_modification":
        return isinstance(affix.raw, dict) and affix.raw.get("specialModificationKey") in SPECIAL_MODS
    if affix.attribute == "the_vault:unique_effect":
        return isinstance(affix.raw, dict) and affix.raw.get("effectKey") in EFFECT_FLAGS
    return True


TIME_IMMUNITY = "the_vault:mod_immunity_time"
MULTIJUMP = "the_vault:potion_effect_multijump"


def implicit_affix(gear_type, rarity, ident):
    """A non-combat implicit by identifier (top open tier), for movement implicits the model locks in."""
    g = load()["gear"].get(gear_type, {}).get(rarity) or load()["gear"].get(gear_type, {}).get("OMEGA", {})
    for m in g.get("IMPLICIT", []):
        if m.get("identifier") == ident:
            tier = best_open_tier(m)
            if tier is None:
                return None
            return Affix(m["attribute"], m.get("group"), m.get("identifier"), None, tier["value"], "IMPLICIT")
    return None


def time_immunity_affix(gear_type, rarity, q):
    """The plushie's time-acceleration immunity implicit (hyper vault requirement while the lock is on)."""
    g = load()["gear"].get(gear_type, {}).get(rarity) or load()["gear"].get(gear_type, {}).get("OMEGA", {})
    for m in g.get("IMPLICIT", []):
        if m.get("identifier") == TIME_IMMUNITY:
            tier = best_open_tier(m)
            if tier is None:
                return None
            return Affix(m["attribute"], m.get("group"), m.get("identifier"), None, tier["value"], "IMPLICIT")
    return None


def gear_pools(gear_type, rarity, q, seal_q=None):
    cat = load()
    g = cat["gear"].get(gear_type, {})
    tables = g.get(rarity)
    if tables is None:
        fallback(f"mythic-missing-{gear_type}", f"no {rarity} table for {gear_type}; using OMEGA tables")
        tables = g.get("OMEGA", {})
    pools = {}
    for grp in ("IMPLICIT", "PREFIX", "SUFFIX", "CORRUPTED_IMPLICIT", "UNUSUAL_PREFIX", "UNUSUAL_SUFFIX",
                "BASE_ATTRIBUTES"):
        out = []
        gq = seal_q if grp == "CORRUPTED_IMPLICIT" and seal_q is not None else q
        for m in tables.get(grp, []):
            a = affix_from(m, grp, gq)
            if a is None:
                continue
            if grp != "BASE_ATTRIBUTES" and not is_combat(a):
                continue
            out.append(a)
        pools[grp] = out
    return pools


_regular_attrs = None


def regular_attrs():
    """Every attribute some normal (non-unique) gear piece can roll."""
    global _regular_attrs
    if _regular_attrs is None:
        s = set()
        for t in load()["gear"].values():
            for tables in t.values():
                for lst in tables.values():
                    s.update(m["attribute"] for m in lst)
        _regular_attrs = s
    return _regular_attrs


# What the model does with each unique power against the single hyperboss (unique-power research, 2026-10-07).
POWER_NOTES = {
    "the_vault:castle_bastion": (True, "+1 stack per second standing still (max 5), each cutting damage taken by its value, at most 50% after armor and resistance; modeled at the castle_bastion_uptime knob"),
    "frost_nova_vulnerability": (True, "Frost Nova (Nova Slow) puts Vulnerable at (level - 1) on the boss for 4x its duration; needs Nova Slow learned"),
    "fireball_recast": (True, "chance of a free second Fireball 2 s later"),
    "the_vault:lucky_thorns": (True, "thorns reflects can roll lucky hits"),
    "the_vault:thorns_scaling_damage": (True, "adds flat thorns x this to every normal melee hit"),
    "the_vault:ap_scaling_damage": (True, "adds ability power (flat x (1 + AP%)) x this to every normal melee hit"),
    "safer_space": (True, "one guaranteed block, then no block at all for 10 x (1 - block) s; worse than plain block against a boss that hits every second"),
    "the_vault:chaining_damage": (False, "chains never hit the same target, so nothing against a lone boss"),
    "the_vault:second_judgement": (False, "the second bolt is credited to the target, not you"),
    "the_vault:trident_channeling": (False, "a raw-attack-damage bolt, swallowed by the boss's i-frames"),
    "the_vault:trident_channeling_chance": (False, "a raw-attack-damage bolt, swallowed by the boss's i-frames"),
    "the_vault:damage_tank": (False, "tank mobs only; no boss is in that group"),
    "the_vault:hit_hearts": (False, "heart fragments on kills"),
    "the_vault:phoenix": (False, "one extra life per vault, not per fight"),
    "the_vault:ability_on_damage": (False, "casts Frost Nova when hit; Frost Nova itself deals no damage"),
    "the_vault:hexing_chance": (False, "random debuffs: crowd control, a weak poison, Vulnerable at +0%"),
    "the_vault:shocking_hit_chance": (False, "knockback crowd control"),
    "the_vault:broodmother_web": (False, "small attack-damage burst on block, mostly lost to i-frames"),
    "the_vault:block_glacial_prison": (False, "the boss is immune to Glacial Shatter"),
    "the_vault:dripping_lava": (False, "1% chance to place lava"),
    "the_vault:effect_cloud": (False, "potion clouds: a few HP of healing or flat poison"),
    "the_vault:javelin_implode": (False, "free Implode on Javelin hits; not modeled (early-game plushie only)"),
    "the_vault:arcane_nova_on_hit": (False, "every Nth hit casts an ability-power nova; not modeled, the boss's i-frames swallow most of it"),
    "the_vault:block_heal": (False, "2 HP per block"),
    "the_vault:on_kill_heal": (True, "gives Bloodthirst, which blocks all other healing (modeled)"),
    "the_vault:healing_effectiveness": (True, "healing effectiveness"),
}


def power_note(attribute, raw):
    if attribute == "the_vault:ability_special_modification" and isinstance(raw, dict):
        key = SPECIAL_MODS.get(raw.get("specialModificationKey"))
        if key:
            return POWER_NOTES[key]
        return (False, "special modification the boss ignores or that does not affect damage")
    if attribute == "the_vault:unique_effect" and isinstance(raw, dict) and raw.get("effectKey") in EFFECT_FLAGS:
        return POWER_NOTES[EFFECT_FLAGS[raw["effectKey"]]]
    return POWER_NOTES.get(attribute, (False, "no effect on a single boss"))


def unique_options(q, seal_q):
    """Equippable uniques with their fixed modifiers rolled at quality q in the top open bracket, and the unique
    seal pool (gear_modifiers/unique.json CORRUPTED_IMPLICIT) at the stage seal quality.

    `powers` are the modifiers no normal gear can roll (the unique's special power); `modeled` says whether the
    model reads them."""
    cat = load()["uniques"]
    reg = regular_attrs()
    out = {}
    for uid, u in sorted(cat["uniques"].items()):
        affixes, powers = [], []
        for m in u["modifiers"]:
            a = affix_from(m, m["kind"], q)
            if a is None:
                continue
            combat = is_combat(a)
            if combat:
                affixes.append(a)
            if m["attribute"] not in reg or m["attribute"] == "the_vault:ability_special_modification":
                modeled, note = power_note(m["attribute"], a.raw)
                powers.append({"attribute": m["attribute"], "label": a.label, "modeled": modeled and combat, "note": note})
        out[uid] = {"id": uid, "name": u["name"], "type": u["type"], "affixes": affixes, "powers": powers,
                    "explicit": [i for i, a in enumerate(affixes) if a.kind in ("PREFIX", "SUFFIX")]}
    seals = []
    for m in cat["seals"]:
        a = affix_from(m, "CORRUPTED_IMPLICIT", seal_q)
        if a is not None and is_combat(a):
            seals.append(a)
    return out, seals


def implicit_groups(pools):
    groups = {}
    for a in pools["IMPLICIT"]:
        groups.setdefault(a.group or a.ident, []).append(a)
    return groups


def affix_counts(gear_type, rarity):
    if gear_type == "vault_necklace":
        return 0, 1
    n = AFFIX_COUNT[rarity]["armor" if gear_type in ARMOR else "other"]
    return n // 2, n - n // 2


def etching_options(gear_type, greed_tier, q):
    cat = load()
    groups = cat["etchings"]["groups"]
    et = ETCH_TYPE.get(gear_type)
    out = []
    for eid, e in cat["etchings"]["etchings"].items():
        if e.get("minGreedTier", 0) > greed_tier:
            continue
        allowed = set()
        for tg in e.get("typeGroups", []):
            allowed.update(groups.get(tg, [tg]))
        if et not in allowed:
            continue
        attr = e["attributes"][0]
        tier = None
        for t in attr.get("tiers", []):
            lo, hi = t.get("minGreedTier", 0), t.get("maxGreedTier", -1)
            if lo <= greed_tier and (hi == -1 or hi >= greed_tier):
                tier = t
        if tier is None:
            continue
        raw = tier["value"]
        val = roll(raw, q)
        if val is None and isinstance(raw, dict):
            val = {k: (roll(v, q) if roll(v, q) is not None else v) for k, v in raw.items()}
            if raw.get("flag") is True:
                val = True
        out.append({"id": eid, "name": e.get("name"), "attribute": attr["attribute"], "raw": raw,
                    "value": val, "minGreedTier": e.get("minGreedTier", 0)})
    return out


def trinket_options():
    cat = load()
    out = []
    for tid, t in cat["trinkets"].items():
        cfg = t.get("config", {})
        slot = cfg.get("curiosSlot")
        eff = []
        if "key" in cfg:
            eff.append(("add", cfg["key"], float(cfg["value"])))
        elif "keys" in cfg:
            for k, v in zip(cfg["keys"], cfg["values"]):
                eff.append(("add", k, float(v)))
        elif "attribute" in cfg and "modifier" in cfg:
            m = cfg["modifier"]
            eff.append(("vanilla", cfg["attribute"], float(m["amount"]), int(m["operation"])))
        elif tid == "the_vault:stone_of_jordan":
            eff.append(("ability_level", cfg.get("name", "all_abilities"), float(cfg.get("value", 1))))
        elif tid == "the_vault:the_dice":
            eff.append(("dice", cfg["minimumMultiplier"], cfg["maximumMultiplier"]))
        elif tid == "the_vault:lucky_coins":
            eff.append(("luck", int(cfg.get("addedAmplifier", 1))))
        if not eff:
            continue
        keep = []
        for e in eff:
            if e[0] == "add" and e[1] not in COMBAT_ATTRS:
                continue
            if e[0] == "vanilla" and e[1] not in VANILLA_COMBAT:
                continue
            keep.append(e)
        if not keep:
            continue
        eff = keep
        out.append({"id": tid, "name": t.get("name"), "slot": slot, "effects": eff, "text": t.get("effectText", "")})
    return out


def charm_options(rep, q):
    gods = load()["charms"]
    out = {}
    for god, mods in gods.items():
        lst = []
        for m in mods:
            tiers = m.get("tiers", [])
            if not tiers:
                continue
            v = roll(tiers[-1]["value"], q)
            if v is None or m["attribute"] not in COMBAT_ATTRS:
                continue
            lst.append({"attribute": m["attribute"], "group": m.get("group"), "value": v * (rep + 1)})
        out[god] = lst
    return out


def card_stats():
    cat = load()
    return {k: v for k, v in cat["cards"].items() if k in COMBAT_ATTRS}


def visible_talents():
    cat = load()
    vis = set(cat["talent_visible"])
    return {t["id"]: t for t in cat["talents"] if t["id"] in vis}


def abilities():
    cat = load()
    out = {}
    for s in cat["abilities"]:
        for spec in s.get("specializations", [s]):
            out[spec["id"]] = dict(spec, skill=s["id"])
    return out


def ability_groups():
    return load()["ability_groups"]


def greed_nodes():
    return load()["greed_nodes"]


def prestige_powers(greed_tier):
    cat = load()
    vis = set(cat["prestige_visible"])
    out = []
    for p in cat["prestige"]:
        if p["id"] not in vis:
            continue
        t = p["tiers"][0]
        if t.get("requiredGreedTier", 0) <= greed_tier:
            out.append({**{k: v for k, v in t.items()}, "id": p["id"], "name": p.get("name") or p["id"]})
    return out
