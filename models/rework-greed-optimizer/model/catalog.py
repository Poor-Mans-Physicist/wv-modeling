"""Turn the extracted config catalog into option tables the optimizer can pick from."""
import json
import math
import os

from . import stages
from .log import fallback

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                    "catalog_rework.json" if stages.LEDGER == "rework" else "catalog_0.34.1.json")

ARMOR = ["helmet", "chestplate", "leggings", "boots"]
MAINHAND = ["sword", "axe", "battlestaff", "trident", "rang"]
OFFHAND = ["shield", "focus", "wand", "plushie"]
SLOTS = ARMOR + ["mainhand", "offhand", "necklace"]

AFFIX_COUNT = {"OMEGA": {"armor": 6, "other": 5}, "MYTHIC": {"armor": 7, "other": 6}}

ETCH_TYPE = {"sword": "SWORD", "battlestaff": "SWORD", "rang": "SWORD", "axe": "AXE", "trident": "AXE",
             "plushie": "FOCUS", "focus": "FOCUS", "wand": "WAND", "shield": "SHIELD", "helmet": "HELMET",
             "chestplate": "CHESTPLATE", "leggings": "LEGGINGS", "boots": "BOOTS"}

REWORK_EXTRA_ATTRS = {"the_vault:item_quantity", "the_vault:item_rarity", "the_vault:movement_speed",
                      "the_vault:knockback_resistance", "the_vault:health_boost", "the_vault:javelin_implode",
                      "the_vault:javelin_multi_hit", "the_vault:hexing_chance", "the_vault:effect_cloud",
                      "the_vault:effect_cloud_chance_additive", "the_vault:thorns_damage_flat_percentile",
                      "the_vault:steel_inferno", "the_vault:storm_tracking", "the_vault:splitting_javelins"}
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
    "the_vault:ability_on_damage",
}
# Vaultarang stats (ranged:rang family): throw velocity, piercing and ricochet targets, return-hit damage.
RANG_ATTRS = {"the_vault:range", "the_vault:velocity", "the_vault:piercing", "the_vault:returning_damage",
              "the_vault:ricochet"}
COMBAT_ATTRS |= RANG_ATTRS
# Rework: Unstoppable Greed scales damage with IIQ + IIR and Kinetic Impact with movement speed, Steadfast turns
# knockback resistance over 100% into Armor %, and the addon's health_boost (Ancient Sweetheart) multiplies max health, so
# gear may roll them.
if stages.LEDGER == "rework":
    COMBAT_ATTRS |= REWORK_EXTRA_ATTRS
# Boolean gear attributes ({"flag": true}) the model reads as build flags.
FLAG_ATTRS = {"the_vault:lucky_thorns", "the_vault:javelin_implode", "the_vault:javelin_multi_hit", "the_vault:storm_tracking"}
# Attributes whose mere presence is a build flag (any on-kill-heal modifier gives Bloodthirst, GearAttributeEvents:678).
PRESENCE_FLAGS = {"the_vault:on_kill_heal": "bloodthirst"}
# Granted effects read as build flags (unique_effect effectKey -> flag).
EFFECT_FLAGS = {"woldsvaults:safer_space": "safer_space"}
# Ability special modifications the model reads (specialModificationKey -> name); values add up per key.
SPECIAL_MODS = {"the_vault:frost_nova_vulnerability": "frost_nova_vulnerability",
                "the_vault:fireball_special_modification": "fireball_recast",
                "woldsvaults:imploding_javelin_damage": "javelin_implode_damage"}
# Abilities cast for free when the wearer takes damage (AbilityTriggerOnDamageAttribute, armor only): abilityId -> special
# key carrying the summed chance. The cast uses the player's own learned tier; the gear's "level" is never read.
ON_DAMAGE_SPECIALS = {"Nova_Slow": "frost_nova_on_damage"}
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
        if stages.LEDGER == "rework":
            for eff, over in stages.REWORK_NODE_OVERRIDES.items():
                god = eff.split("_", 1)[0]
                d = _cat["gods"][god]["effects"].get(eff)
                if d is None:
                    fallback(f"node-override-missing-{eff}", f"{eff} is not in the god catalog; override skipped")
                    continue
                d.update(over)
            apply_unique_overrides(_cat)
            for eid, over in stages.REWORK_ETCHING_OVERRIDES.items():
                e = _cat["etchings"]["etchings"].get(eid)
                if e is None:
                    fallback(f"etching-override-missing-{eid}", f"{eid} is not in the etching catalog; override skipped")
                    continue
                e.update(over)
    return _cat


def apply_unique_overrides(cat):
    """Rework: proposed unique balance (stages.REWORK_UNIQUE_OVERRIDES). Each op either sets the value of every open
    tier of the modifier with `identifier`, or appends a new `modifier`."""
    allu = dict(cat["uniques"]["uniques"])
    allu.update(cat.get("ancients", {}))
    for uid, ops in stages.REWORK_UNIQUE_OVERRIDES.items():
        u = allu.get(uid)
        if u is None:
            fallback(f"unique-override-missing-{uid}", f"{uid} is not in the unique catalog; override skipped")
            continue
        for op in ops:
            if op["op"] == "add":
                u["modifiers"].append(json.loads(json.dumps(op["modifier"])))
                continue
            if op["op"] == "name":
                u["name"] = op["name"]
                continue
            idx = [i for i, m in enumerate(u["modifiers"]) if m.get("identifier") == op["identifier"]]
            if not idx:
                fallback(f"unique-override-mod-{uid}-{op['identifier']}", f"{uid} has no modifier {op['identifier']}; op skipped")
                continue
            if op["op"] == "replace":
                for i in idx:
                    u["modifiers"][i] = dict(json.loads(json.dumps(op["modifier"])), kind=u["modifiers"][i].get("kind", op["modifier"]["kind"]))
                continue
            for i in idx:
                for t in u["modifiers"][i].get("open", []):
                    t["value"] = json.loads(json.dumps(op["value"]))


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
    if v is None and mod["attribute"] == "the_vault:ability_on_damage" and isinstance(raw, dict) and "chance" in raw:
        v = roll(raw["chance"], q)
    if v is None and mod["attribute"] == "the_vault:arcane_nova_on_hit" and isinstance(raw, dict) and "percentAbilityPower" in raw:
        v = roll(raw["percentAbilityPower"], q)
        hr = raw["hitsRequired"]
        raw = dict(raw, hitsRolled=float(round(float(hr["max"]) - q * (float(hr["max"]) - float(hr["min"])))))
    if v is None and mod["attribute"] == "the_vault:steel_inferno" and isinstance(raw, dict) and "centreBonus" in raw:
        v = roll(raw["centreBonus"], q)
        raw = dict(raw, radiusRolled=roll(raw["radiusMultiplier"], q))
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
    if affix.attribute == "the_vault:effect_cloud":
        from .procs import cloud_kinds
        return isinstance(affix.raw, dict) and any(e.get("effect") in cloud_kinds() for e in affix.raw.get("additionalEffects", []))
    if affix.attribute == "the_vault:ability_on_damage":
        return isinstance(affix.raw, dict) and affix.raw.get("abilityId") in ON_DAMAGE_SPECIALS
    return True


TIME_IMMUNITY = "the_vault:mod_immunity_time"
MULTIJUMP = "the_vault:potion_effect_multijump"
# Prismatic Feather (MultiJumpTrinket): three air jumps, more than the boots' Multi Jump implicit; either satisfies the
# multi-jump rule (user, 2026-10-15).
FEATHER = "the_vault:chromatic_feather"
# Trinkets whose config carries no stat the shapes above read: Carapace (curiosSlot only; effect negation), Velara's
# Petal (a granted Regeneration level, read by trinket_regen) and the Immortal Seal (an effect granted on hits taken).
TRINKET_SHAPES = {"the_vault:carapace": "plain", "the_vault:velaras_petal": "effect",
                  "the_vault:immortal_seal": "effect_on_hit_taken"}
NEGATION_TRINKETS = {"the_vault:carapace"}


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
            if grp == "CORRUPTED_IMPLICIT" and stages.LEDGER == "rework" and a.ident in stages.REWORK_BANNED_SEALS:
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
    "the_vault:castle_bastion": (True, "+1 stack per second standing still (max 5), each cutting damage taken by its value, at most 50% after armor and resistance; modeled at an uptime falling with stable movement speed (castle_uptime_lo/hi knobs)"),
    "frost_nova_vulnerability": (True, "Frost Nova (Nova Slow) puts Vulnerable at (summed value - 1) on the target for 4x its duration (NovaSpeedAbility:143-166); needs Frost Nova learned. Proposed in the greed ledger: adds its value once to the target's existing Vulnerable instead"),
    "fireball_recast": (True, "chance of a free second Fireball 2 s later"),
    "the_vault:lucky_thorns": (True, "thorns reflects can roll lucky hits"),
    "the_vault:thorns_scaling_damage": (True, "adds flat thorns x this to every normal melee hit"),
    "the_vault:ap_scaling_damage": (True, "adds ability power (flat x (1 + AP%)) x this to every normal melee hit"),
    "safer_space": (True, "one guaranteed block, then no block at all for 10 x (1 - block) s; worse than plain block against a boss that hits every second"),
    "the_vault:health_boost": (True, "x(1 + roll) total max health (addon MixinVaultGearHelper: MULTIPLY_TOTAL on max health)"),
    "the_vault:ability_on_damage": (True, "when a hit gets through (not blocked or dodged), chance to cast your own learned Frost Nova for free (the rolled level is never read; armor only); with Frozen Orb every proc applies its Vulnerable"),
    "the_vault:arcane_nova_on_hit": (True, "every N non-AP main-hand hits, an ability-power nova on the target (AP x share); it lands inside the triggering swing's i-frames, so only the part above that swing's raw damage counts"),
    "the_vault:javelin_implode": (True, "every plain-Javelin hit casts your learned Implode (not Life Tap) for free: current mana x percentManaDealt, plus damage increase; a sneaking Power Dump throw spends the whole bar, so its Implode reads the mana the throw consumed and takes the throw's Power Dump multiplier"),
    "javelin_implode_damage": (True, "proposed: Implodes cast by the javelin deal x(1 + roll)"),
    "the_vault:javelin_multi_hit": (True, "proposed: after a Scatter javelin splits on a block hit, its children home in on the nearest enemy, so every child (numberOfJavelins) hits a lone boss"),
    "the_vault:splitting_javelins": (True, "the first block hit splits a Scatter javelin into this many javelins, each splitting again into numberOfJavelins on its next block hit (MixinVaultThrownJavelin); with the proposed homing children the pre-split javelins home in and hit the boss once each (this many hits per throw, user 2026-10-21), otherwise the children share the parent's hit list and a lone boss is hit once"),
    "the_vault:steel_inferno": (True, "proposed: Fireball, Volley and Mitosis explosions (and Everflame's recast) shrink to the rolled share of their radius and gain a centre bonus B falling off as 1 - (d/R)^2: x(1 + steel_inferno_boss_efficiency x B) on the boss, x(1 + steel_inferno_pack_mean x B) on pack mobs"),
    "the_vault:storm_tracking": (True, "proposed: the Storm Arrow storm locks onto the mob closest to the arrow's hit and follows it (10 blocks/s), so every bolt hits the boss instead of storm_boss_occupancy of them"),
    "the_vault:chaining_damage": (False, "chains never hit the same target, so nothing against a lone boss"),
    "the_vault:second_judgement": (False, "the second bolt is plain lightning credited to the target, not you (MixinThrownTrident sets the cause to the target; looks like a game bug)"),
    "the_vault:trident_channeling": (False, "a raw-attack-damage bolt, swallowed by the boss's i-frames"),
    "the_vault:trident_channeling_chance": (False, "a raw-attack-damage bolt, swallowed by the boss's i-frames"),
    "the_vault:damage_tank": (False, "tank mobs only; no boss is in that group"),
    "the_vault:damage_baby": (False, "baby mobs only"),
    "the_vault:hit_hearts": (False, "heart fragments on kills"),
    "the_vault:phoenix": (False, "one extra life per vault, not per fight"),
    "the_vault:hexing_chance": (True, "normal hits may hex the boss with one of nine effects (luck-affected): Slowness, Poison, Wither, Weakness, Bad Luck, Vulnerable 0 (4 s) and Fear land and count for Sneaky Advantage; Bleed never lands" + ("; Chilled (level 1) lands (proposed)" if stages.LEDGER == "rework" else "; Chilled never lands")),
    "the_vault:shocking_hit_chance": (False, "knocks the boss away (its AoE needs a second mob to collide with); neutral to harmful"),
    "the_vault:broodmother_web": (False, "small attack-damage burst on block, mostly lost to i-frames"),
    "the_vault:block_glacial_prison": (False, "the boss rejects Glacial Shatter"),
    "the_vault:dripping_lava": (False, "1% chance to place lava"),
    "the_vault:effect_cloud": (True, "normal hits (swings) may leave a potion cloud on the boss (trigger chance + the additive cloud chance, no Luck); its poison, wither, weakness or slowness counts for Sneaky Advantage while the cloud and the effect last (the boss is assumed to stay in it); poison deals amplifier + 1 per second through the cloud column of the DoT table (a rotation row of kind dot); wither and healing deal nothing to the boss"),
    "the_vault:block_heal": (False, "2 HP per block"),
    "the_vault:lucky_hit_aoe": (False, "cleaves 60% of the hit to other mobs; the struck target is excluded"),
    "the_vault:quaking_hit": (False, "fires even on a stun-immune boss, but the Earthquake lands inside the melee hit's i-frames (AP-flagged, no reset), so about nothing"),
    "the_vault:crashwave": (False, "needs repeated riptide crashes into walls at speed"),
    "the_vault:radiation_wave": (False, "slowness only; the boss ignores slow"),
    "the_vault:sweeping_hit_damage": (False, "sweep hits other mobs only"),
    "the_vault:on_kill_heal": (True, "gives Bloodthirst, which blocks all other healing (modeled)"),
    "the_vault:reaving_damage": (True, "the first normal melee hit on a target adds reaving x its max health x 0.25 (bosses take a quarter), once per target: modeled as health the fight does not have to take"),
    "the_vault:burning_hit_chance": (True, "normal hits may burn the boss for 10 s: counted as a harmful effect for Sneaky Advantage; its 10% attack damage per second lands inside melee i-frames, so no damage"),
    "the_vault:ability_area_of_effect_percent": (False, "changes one ability's area; against a lone boss that only matters for pack damage"),
    "the_vault:healing_effectiveness": (True, "healing effectiveness"),
}
# Special modifications the model does not count, with the reason (specialModificationKey -> note).
SPECIAL_NOTES = {
    "the_vault:entropic_bind_poison": "Entropic Bind also poisons nearby monsters for (value) flat damage per second: negligible against a boss; it would add one effect for Sneaky Advantage while the aura runs (the aura is not modeled)",
    "the_vault:glacial_blast_hypothermia": "multiplies Glacial Blast's shatter chance; the boss rejects Glacial Shatter" + ("" if stages.LEDGER == "rework" else " and Chilled"),
}


def power_note(attribute, raw):
    if attribute == "the_vault:ability_special_modification" and isinstance(raw, dict):
        key = SPECIAL_MODS.get(raw.get("specialModificationKey"))
        if key:
            return POWER_NOTES[key]
        return (False, SPECIAL_NOTES.get(raw.get("specialModificationKey"), "special modification the boss ignores or that does not affect damage"))
    if attribute == "the_vault:unique_effect" and isinstance(raw, dict) and raw.get("effectKey") in EFFECT_FLAGS:
        return POWER_NOTES[EFFECT_FLAGS[raw["effectKey"]]]
    if attribute in POWER_NOTES:
        return POWER_NOTES[attribute]
    if attribute in COMBAT_ATTRS:
        return (True, "counted as a stat")
    fallback(f"power-note-{attribute}", f"unique power {attribute} has no audited note; shown as not counted")
    return (False, "not modeled (no audited note yet)")


def unique_options(q, seal_q):
    """Equippable uniques with their fixed modifiers rolled at quality q in the top open bracket, and the unique
    seal pool (gear_modifiers/unique.json CORRUPTED_IMPLICIT) at the stage seal quality.

    `powers` are the modifiers no normal gear can roll (the unique's special power); `modeled` says whether the
    model reads them."""
    cat = load()["uniques"]
    reg = regular_attrs()
    out = {}
    # Rework: ancient uniques ride along (id "ancient:<unique>", single level-independent tier, same seal pool;
    # user ruling 2026-10-11: they can be sealed like regular uniques).
    allu = dict(cat["uniques"])
    allu.update(load().get("ancients", {}))
    for uid, u in sorted(allu.items()):
        affixes, powers = [], []
        for m in u["modifiers"]:
            a = affix_from(m, m["kind"], q)
            if a is None:
                continue
            combat = is_combat(a)
            if combat:
                affixes.append(a)
            if m["attribute"] not in reg or m["attribute"] in POWER_NOTES or m["attribute"] == "the_vault:ability_special_modification":
                modeled, note = power_note(m["attribute"], a.raw)
                powers.append({"attribute": m["attribute"], "label": a.label, "modeled": modeled and combat, "note": note})
        out[uid] = {"id": uid, "name": u["name"], "type": u["type"], "affixes": affixes, "powers": powers,
                    "ancient": bool(u.get("ancient")),
                    "explicit": [i for i, a in enumerate(affixes) if a.kind in ("PREFIX", "SUFFIX")]}
    seals = []
    for m in cat["seals"]:
        a = affix_from(m, "CORRUPTED_IMPLICIT", seal_q)
        if a is not None and is_combat(a) and not (stages.LEDGER == "rework" and a.ident in stages.REWORK_BANNED_SEALS):
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


# Etching audit for the report's Etchings view: catalog id -> (modeled, note). An unmodeled etching is tagged
# "nothing against the boss" if it is in ETCHING_NO_BOSS, "planned" if it is in ETCHING_PLANNED, else "not modeled".
# modeled=True wins over both sets, so flipping an entry to True is enough when an etching gets modeled.
ETCHING_NOTES = {
    "the_vault:etching_arcane_pierce": (True, "pierced mobs add pack damage only (half weight each); the boss takes one beam"),
    "the_vault:etching_extra_piercing_javelin": (True, "the 2 side javelins at the rolled share count toward pack damage only"),
    "the_vault:etching_ice_bolt_lucky": (True, "Ice Bolt hits count as arrows: they leech and take Spectral Strike stacks, and the registry applies at the hurt stage; their lucky hits are not counted (user report: they fail in game)"),
    "the_vault:etching_ice_bolt_multicast": (True, "+N recasts per cast, each paying full mana"),
    "the_vault:etching_life_tap_extra_damage": (True, "adds the roll to Life Tap's damage per hit point drained"),
    "the_vault:etching_lightning_orb_size": (False, "the orbs' hit radius grows to 1 + the summed rolls: more pack targets, nothing against the lone boss"),
    "the_vault:etching_lightning_orb_triple_damage": (True, "3 orbs, each at x(1 - roll) damage, all counted on the boss"),
    "the_vault:etching_lucky_vulnerable": (True, "lucky hits put Vulnerable at the rolled level on the boss for the rolled duration"),
    "the_vault:etching_nova_low_mana_damage": (True, "Nova x(1 + roll) on the share of casts made below 50% mana (1 when the rotation is mana-limited, the refill share below half with sneak-cast Power Dump, else 0); the strongest single copy (the game compounds copies: NOVA-LOWMANA-STACK, modeled as fixed)"),
    "the_vault:etching_nova_recast": (True, "counted as a free recast with chance min(1, roll / 100)"),
    "the_vault:etching_rampage_lucky_hit": (True, "+roll lucky hit chance while Rampage is on"),
    "the_vault:etching_shield_bash_damage": (True, "Shield Bash adds roll x attack damage"),
    "the_vault:etching_smite_echo": (True, "each Smite bolt strikes again 10 ticks later at x roll"),
    "the_vault:etching_totem_mob_damage_ad": (True, "Totem of Hatred adds attack damage to its AP base; the hit loses the AP flag, so damage increase and the registry apply on top (ADTOTEM-M2)"),
    "the_vault:etching_totem_player_damage_effect": (True, "Totem of Hatred hits keep Vulnerable at the rolled level on the boss for the totem's duration plus the roll"),
    "woldsvaults:colossus_titan_resistance": (True, "while Colossus is up the resistance limit gains the roll, held at 0.95 (the addon adds it after its 0.95 clamp; patched by ruling): resistance = u x min(res, cap + roll, 0.95) + (1 - u) x min(res, cap) at Colossus's uptime u"),
    "woldsvaults:fireball_volley_mitosis": (True, "every Volley bounce explodes and spawns N one-shot children; every explosion runs at 0.5x AP"),
    "woldsvaults:ingenium": (True, "+2 or +3 to every talent level"),
    "woldsvaults:pyramid_scheme": (True, "the roll adds to every stacking talent's max stacks (with Stack Master and Stack Stack Stack); kill and hit stacks then follow the refresh rule"),
    "woldsvaults:ravenous_fangs": (True, "Wall of Fangs at x0.25 attack damage; the doubled execute threshold is not counted on the boss"),
    "the_vault:etching_smite_stun": (False, "stun; the hyperboss ignores stuns"),
    "the_vault:etching_stone_fall_stun": (False, "No-AI stun on knocked-back mobs; the hyperboss ignores stuns"),
    "the_vault:etching_frost_nova_freeze": (False, "glacial imprison; the boss rejects Glacial Shatter"),
    "the_vault:etching_glacial_shatter_explosion": (False, "needs a shattered frostbitten mob; the boss rejects Glacial Shatter"),
    "the_vault:etching_ice_bolt_effect": (False, "applies Glacial Shatter, which the boss rejects"),
    "the_vault:etching_ice_bolt_chunk_damage": (False, "Glacial Blast chunk damage"),
    "the_vault:etching_chaining_penalty": (False, "chains never hit the same target, so nothing against a lone boss"),
    "the_vault:etching_earthquake_chest_loot": (False, "chest loot only"),
    "the_vault:etching_lightning_orb_kill_cooldown": (False, "needs kills"),
    "the_vault:etching_lucky_vortex": (False, "crowd control, no damage"),
    "the_vault:etching_heal_cleanse": (False, "cleanses the player; no damage"),
    "the_vault:etching_dash_warp_nova": (False, "a Nova when Dash Warp lands"),
    "the_vault:etching_mana_shield_nova": (False, "a Nova when Mana Shield expires"),
    "the_vault:etching_spirit_walk_ability": (False, "a Frost Nova when Spirit Walk expires"),
    "the_vault:etching_lucky_low_health": (False, "needs the player below half health; low-HP bonuses sit at 0% uptime because healing keeps builds topped up"),
    "the_vault:etching_implode_mana_regen": (False, "per mob Implode kills"),
    "the_vault:etching_life_tap_lucky_hit": (False, "per mob Life Tap kills"),
    "woldsvaults:concentrate_drain": (False, "Concentrate drains current health per effect drained"),
    "woldsvaults:conservation_of_momentum": (False, "Momentum Engine keeps its stacks when attacking"),
    "woldsvaults:diffuse_chemical_bomb": (False, "Chaos Grenades thrown at random around you"),
    "woldsvaults:levitation_slow_falling": (False, "movement only"),
    "woldsvaults:prudent_chaos": (False, "a random Brew effect when Prudent triggers"),
    "woldsvaults:purist_common": (False, "Purist also counts Common gear"),
    "woldsvaults:reaving_hemmorage": (False, "Bleed; the hyperboss ignores Bleed"),
    "woldsvaults:sneaky_getaway_ninja": (True, "adds its roll to the capped dodge chance while Sneaky Getaway is active (at the ability's uptime)"),
    "the_vault:etching_piercing_javelin_ricochet": (False, "ricochets sideways when piercing; a lone boss is hit once"),
    "the_vault:etching_totem_player_damage_ap": (True, "the learned Wrath totem multiplies ability power by 1 + uptime x its totemPlayerDamagePercent (every AP read, the Starforge nova included)"),
    "the_vault:etching_lightning_strike_delay": (True, "Smite and Smite (Archon) bolts strike again at full damage (no mana, own echo roll); Chain Lightning hits its struck target again as a plain AP+AOE hurt; Blast Wave has no delayed strike in code"),
    "the_vault:etching_storm_arrow_blizzard_effect": (True, "every Shard Blizzard shard puts Vulnerable (level - 1, uncapped; the game clamps to min(8, level) - 1) on the boss: up while the cloud rains and the etching's ticks after it, at the ability's cast share"),
    "the_vault:etching_storm_arrow_blizzard_damage": (True, "each shard also hurts for AP x roll as an AP+AOE hit inside the shard's i-frame window, so only the part above the shard's raw counts"),
    "the_vault:etching_lucky_frost_nova": (True, "every lucky hit casts Frost Nova at the rolled tier: Hypothermia on the boss (1 - e^(-L x 20 s)) and, with the Frozen Orb modification, its Vulnerable procs at rate L; no damage; the boss is assumed inside the nova's radius"),
    "the_vault:etching_lucky_cooldown_reset": (True, "resets one ability: the family's main ability if the pool names it, else the pooled secondary with the highest full-rate damage. 0.34.1: every lucky hit removes (int)(25% of the max cooldown) (the description's 'remaining' is wrong: the code reads the max). Rework (proposed): helmet only, each proc removes 10% of the remaining cooldown and each cast of any ability procs it at most once; the cooldown runs out at T = ln(1 + k R0) / k in mean field. Both are solved with the lucky-hit rate in the Arcane Cascade fixed point"),
    "the_vault:etching_earthquake_recast_delay": (True, "one more shock per cast at full damage, not i-frame clipped"),
    "the_vault:etching_prism_split": (False, "the half-damage split pulse lands in the same tick as the primary pulse, so vanilla i-frames drop it on the boss (the prism is centred on its target); pack damage only"),
    "woldsvaults:fireball_greedball": (True, "Fireball (FireballAbility only, not Volley or Fireshot) gains (item quantity + item rarity) x 100 x roll damage; Copiously is not tracked; explosion radius 3 for pack targets"),
    "woldsvaults:reverberation": (True, "pulses deal nothing; every echo proc that finds a live echo deals its damage x decay at once (and re-procs it), tabulated by tools/echo_table.py --reverb (v_reverb)"),
    "the_vault:etching_blocking_mana": (True, "roll x block chance x boss attacks reaching the player per second, with a shield in the offhand"),
    "the_vault:etching_poison_mana": (True, "roll per Poison Nova / Toxic Grenade tick (at the cast's full rate, in the rotation's mana split) and per cloud poison hit"),
    "the_vault:etching_scatter_mana": (True, "roll per Scatter Javelin hit on the boss (at the cast's full rate, in the rotation's mana split)"),
    "woldsvaults:divinity": (True, "immunity to most negative effects: meets the effect-negation requirement in place of the Carapace trinket"),
    "the_vault:etching_ice_bolt_pierce": (False, "not audited yet"),
    "the_vault:etching_rail_pierce": (False, "not audited yet"),
    "the_vault:etching_arcane_rail_size": (False, "not audited yet"),
    "the_vault:etching_prism_anchor": (False, "not audited yet"),
    "woldsvaults:imploding_barrier": (False, "not audited yet"),
}
ETCHING_NO_BOSS = {
    "the_vault:etching_smite_stun", "the_vault:etching_stone_fall_stun", "the_vault:etching_frost_nova_freeze",
    "the_vault:etching_glacial_shatter_explosion", "the_vault:etching_ice_bolt_effect", "the_vault:etching_ice_bolt_chunk_damage",
    "the_vault:etching_chaining_penalty", "the_vault:etching_earthquake_chest_loot", "the_vault:etching_lightning_orb_kill_cooldown",
    "the_vault:etching_lucky_vortex", "the_vault:etching_heal_cleanse", "the_vault:etching_dash_warp_nova",
    "the_vault:etching_mana_shield_nova", "the_vault:etching_spirit_walk_ability", "the_vault:etching_lucky_low_health",
    "the_vault:etching_implode_mana_regen", "the_vault:etching_life_tap_lucky_hit", "woldsvaults:concentrate_drain",
    "woldsvaults:conservation_of_momentum", "woldsvaults:diffuse_chemical_bomb", "woldsvaults:levitation_slow_falling",
    "woldsvaults:prudent_chaos", "woldsvaults:purist_common", "woldsvaults:reaving_hemmorage", "woldsvaults:sneaky_getaway_ninja",
    "the_vault:etching_piercing_javelin_ricochet", "the_vault:etching_prism_split", "the_vault:etching_lightning_orb_size",
}
ETCHING_PLANNED = {
    "woldsvaults:divinity",
}


def etching_status(eid):
    """(tag, note) for one etching: tag is "modeled", "no-boss", "planned" or "not-modeled" (see ETCHING_NOTES)."""
    entry = ETCHING_NOTES.get(eid)
    if entry is None:
        fallback(f"etching-note-{eid}", f"{eid} has no ETCHING_NOTES entry; shown as not modeled")
        return "not-modeled", "not audited yet"
    modeled, note = entry
    if modeled:
        return "modeled", note
    if eid in ETCHING_NO_BOSS:
        return "no-boss", note
    if eid in ETCHING_PLANNED:
        return "planned", note
    return "not-modeled", note


# Trinket shapes kept without a stat effect (the survival and requirement models read them): a curios-slot-only
# trinket by its tag, and the granted effect that a hit taken can open.
PLAIN_TRINKETS = {"the_vault:carapace": "negation"}
_trinkets = None


def trinket_options():
    """Trinkets the model offers: stat adds, vanilla multipliers, Stone of Jordan, The Dice, Lucky Coins (the original
    shapes, in catalog order), then the shapes kept for the survival models, appended so the indices of the first
    ones do not move: "effect" (a granted regeneration level: Velara's Petal), "effect_on_hit_taken" (chance,
    duration in ticks, effect, amplifier: Immortal Seal) and "plain" (curiosSlot only, a tag: Carapace "negation").
    Every other trinket is dropped with a fallback naming its shape."""
    global _trinkets
    if _trinkets is not None:
        return _trinkets
    cat = load()
    out, extra = [], []
    for tid, t in cat["trinkets"].items():
        cfg = t.get("config", {})
        slot = cfg.get("curiosSlot")
        rec = {"id": tid, "name": t.get("name"), "slot": slot, "text": t.get("effectText", "")}
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
        elif cfg.get("effect") == "the_vault:immortality":
            eff.append(("immortal_seal", float(cfg["chance"]), int(cfg["duration"])))
        if tid == FEATHER and stages.KNOBS.get("boots_multijump_lock"):
            out.append(dict(rec, effects=[]))
            continue
        keep = [e for e in eff if not (e[0] == "add" and e[1] not in COMBAT_ATTRS)
                and not (e[0] == "vanilla" and e[1] not in VANILLA_COMBAT)]
        if keep:
            out.append(dict(rec, effects=keep))
            continue
        shape = sorted(k for k in cfg if k != "curiosSlot")
        if "chance" in cfg and "duration" in cfg and "effect" in cfg:
            extra.append(dict(rec, effects=[("effect_on_hit_taken", float(cfg["chance"]), float(cfg["duration"]), cfg["effect"],
                                             int(cfg.get("addedAmplifier", 0)))]))
        elif "effect" in cfg and cfg["effect"] == REGEN:
            extra.append(dict(rec, effects=[("effect", cfg["effect"], int(cfg.get("addedAmplifier", 0)))]))
        elif not shape and tid in PLAIN_TRINKETS:
            extra.append(dict(rec, effects=[("plain", PLAIN_TRINKETS[tid])]))
        else:
            what = ", ".join(e[1] for e in eff) if eff else ("config " + ", ".join(shape) if shape else "curiosSlot only")
            fallback(f"trinket-dropped-{tid}", f"trinket {tid} dropped: no combat effect the model reads ({what})")
    _trinkets = out + extra
    return _trinkets


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


REGEN = "minecraft:regeneration"


def trinket_regen(tid):
    """Regeneration levels a trinket grants (Velara's Petal: addedAmplifier 1)."""
    c = load()["trinkets"].get(tid, {}).get("config", {})
    return int(c.get("addedAmplifier", 0)) if c.get("effect") == REGEN else 0


def unique_regen(uid):
    """Regeneration levels from a unique's (or ancient's) granted-effect modifiers (Baguette: amplifier 1)."""
    cat = load()
    u = cat["ancients"].get(uid) if uid.startswith("ancient:") else cat["uniques"]["uniques"].get(uid)
    n = 0
    for m in (u or {}).get("modifiers", []):
        for o in m.get("open") or []:
            v = o.get("value", o) if isinstance(o, dict) else {}
            if isinstance(v, dict) and v.get("effectKey") == REGEN:
                n = max(n, int(v.get("amplifier", 0)))
    return n


def prestige_regen(powers):
    """Regeneration levels from prestige potion powers (Gift of the Petal: amplifier 0 = level 1)."""
    return sum(int(p.get("amplifier", 0)) + 1 for p in powers if p.get("effectId") == REGEN)


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
