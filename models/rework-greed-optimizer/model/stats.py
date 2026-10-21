"""Stat assembly: sum every source into attribute totals, then derive effective stats (RECON §1)."""
import collections
import math

from . import catalog
from .log import fallback

BASE_HEALTH = 20.0
BASE_ATTACK_SPEED = 4.0


def ability_level_bonus(sources, spec_skill, groups, masterful, spec_id=None):
    """Total +levels for one ability from (abilityKey, levelChange) pairs. A key may name the skill, the
    specialization itself or an ability group (SkillBonusTierCache.getBonus walks from the specialization up through
    its parents, so a specialization key counts for that specialization only)."""
    total = 0.0
    for key, change in sources:
        hit = key == "all_abilities" or key == spec_skill or (spec_id is not None and key == spec_id)
        if not hit:
            for gname, members in groups.items():
                if key.upper() == gname and spec_skill in members:
                    hit = True
                    break
        if hit:
            total += change
    if masterful and total > 0:
        total *= 2
    return total


class Stats:
    def __init__(self):
        self.attr = collections.defaultdict(float)
        self.sources = collections.defaultdict(list)
        self.vanilla_total = collections.defaultdict(lambda: 1.0)
        self.ability_levels = []
        self.ability_mods = []
        self.clouds = []
        self.special = {}
        self.talent_levels = []
        self.flags = set()
        self.dice = None
        self.no_exec = False
        self.luck = 0
        self.god_effects = {}
        self.gfx = None

    def add(self, attribute, value, source):
        if value is None:
            return
        self.attr[attribute] += value
        self.sources[attribute].append((source, value))

    def get(self, short_name):
        return self.attr.get("the_vault:" + short_name, 0.0)


MANA_CAP = 4096.0


def derive(st, caps, mana_cap=False):
    """Effective stats from attribute totals, following the util/calc helpers and their caps."""
    g = st.get
    d = {}
    d["attack_damage"] = (1.0 + g("attack_damage")) * st.vanilla_total["minecraft:generic.attack_damage"]
    d["attack_speed"] = max(0.1, (BASE_ATTACK_SPEED + g("attack_speed")) * (1.0 + g("attack_speed_percent")))
    ap = g("ability_power")
    ap *= 1.0 + g("ability_power_percent")
    ap *= 1.0 + g("ability_power_percentile")
    d["ability_power"] = ap
    d["damage_increase"] = g("damage_increase")
    lucky_flat = g("lucky_hit_chance")
    lucky = lucky_flat * (1.0 + g("lucky_hit_chance_percentile") + g("jester_lucky_hit_chance_percentile"))
    if st.luck > 0:
        lucky *= 1.15 ** st.luck
    gx = st.gfx
    if gx and gx["luckiest"]:
        lucky *= gx["luckiest"]
    # The cap (1.0 in 0.34.1, 10.0 in the rework for Overcrit) bounds the attribute; a hit is lucky at most surely.
    d["lucky_hit_chance"] = min(lucky, float(caps.get("luckyHitCap", 0.562)), 1.0)
    d["lucky_hit_uncapped"] = lucky
    d["aoe_multiplier"] = min(1.0 + g("area_of_effect"), 1.0 + float(caps.get("aoeCap", 0.8)))
    d["health"] = ((BASE_HEALTH + g("health")) * (1.0 + g("health_percentile")) * st.vanilla_total["minecraft:generic.max_health"]
                   * (1.0 + g("health_boost")))
    # Steadfast adds its Armor % to the other Armor % sources (deck cards, Velara nodes, greed nodes; proposed).
    sf = st.gfx["steadfast"] if st.gfx else None
    sf_pct = max(0.0, g("knockback_resistance") - sf[0]) * sf[1] if sf else 0.0
    d["armor"] = g("armor") * (1.0 + g("armor_percentile") + sf_pct)
    res_cap = min(0.95, 0.5 + g("resistance_cap"))
    d["resistance"] = min(g("resistance"), res_cap)
    d["resistance_cap"] = res_cap
    blk_cap = min(0.95, 0.6 + g("block_cap"))
    blk = g("block")
    if st.luck > 0:
        blk *= 1.15 ** st.luck
    d["block"] = min(blk, blk_cap)
    dodge = g("dodge_percent")
    if st.luck > 0:
        dodge *= 1.15 ** st.luck
    d["dodge"] = min(dodge, 0.95)
    d["mana_max"] = (100.0 + g("mana_additive")) * (1.0 + g("mana_additive_percentile")) * st.vanilla_total["the_vault:generic.mana_max"]
    d["mana_max_uncapped"] = d["mana_max"]
    if mana_cap:
        d["mana_max"] = min(d["mana_max"], MANA_CAP)
    d["mana_regen"] = 1.0 * (1.0 + g("mana_regen")) * st.vanilla_total["the_vault:generic.mana_regen"]
    d["mana_regen_vt"] = st.vanilla_total["the_vault:generic.mana_regen"]
    cdr = g("cooldown_reduction") * (1.0 + g("cooldown_reduction_percentile"))
    if "mind_meld" in st.flags:
        cdr += math.floor(d["mana_max"] / 50.0 + 1e-9) * 0.01
    cdr_cap = min(0.95, 0.8 + g("cooldown_reduction_cap"))
    d["cooldown_reduction"] = min(cdr, cdr_cap)
    d["cooldown_reduction_uncapped"] = cdr
    d["cooldown_reduction_cap"] = cdr_cap
    d["echo_chance"] = min(g("echoing_chance"), 1.0)
    d["echo_damage"] = g("echoing_damage")
    d["execution"] = g("execution_damage")
    d["reaving"] = g("reaving_damage")
    d["hexing"] = g("hexing_chance")
    d["burning"] = g("burning_hit_chance")
    d["cloud_chance"] = g("effect_cloud_chance_additive")
    d["chain"] = g("on_hit_chain")
    d["on_hit_aoe"] = g("on_hit_aoe")
    d["double_hit"] = min(g("double_hit_chance"), 1.0)
    d["relentless"] = g("relentless_strike")
    d["third_attack"] = g("third_attack")
    d["effect_duration"] = g("effect_duration")
    d["crit_mitigation"] = g("critical_hit_mitigation")
    d["cooldown_skip"] = g("ability_cooldown_skip")
    d["bloodthirst"] = "bloodthirst" in st.flags
    he = 1.0 + g("healing_effectiveness")
    d["healing_effectiveness"] = 0.0 if d["bloodthirst"] else max(0.0, he)
    d["healing_effectiveness_low_mana"] = 0.0 if d["bloodthirst"] else max(0.0, he + st.attr.get("x:methodical", 0.0))
    d["leech"] = g("leech")
    d["thorns_flat"] = g("thorns_damage_flat") * (1.0 + g("thorns_damage_flat_percentile"))
    d["thorns_pct"] = g("thorns_damage")
    d["thorns_scaling"] = g("thorns_scaling_damage")
    d["ap_scaling"] = g("ap_scaling_damage")
    d["ap_base"] = g("ability_power")
    d["ap_flat"] = g("ability_power") * (1.0 + g("ability_power_percent"))
    d["castle"] = min(0.5, 5.0 * g("castle_bastion"))
    d["item_quantity"] = g("item_quantity")
    d["item_rarity"] = g("item_rarity")
    d["empower_speed"] = 0.0
    d["sneaky_dodge"] = 0.0
    d["sneak_share"] = 0.0
    d["u_high"] = 1.0
    d["u_low"] = 0.0
    d["absorb_pool"] = 0.0
    god_derive(st, d, caps)
    d["move_speed"] = stable_move_speed(st, d)
    return d


def stable_move_speed(st, d):
    """Stable movement speed (execution plan 1.5; vanilla AttributeInstance.calculateValue through VaultGearHelper:256):
    (1 + the MULTIPLY_BASE bucket: gear movement_speed, the Speed talent, the god-tree stat, plus d["empower_speed"]
    when Empower is paid at full uptime) x the MULTIPLY_TOTAL factors: The Stonewall (god speed_mult) and Swiftness
    Amplified (st.attr["x:swiftness"], 1.25 from prestige_powers.json, MovementSpeedMultiplierPrestigePower:79). The
    Zephyr Charm sets a flat speed and is excluded."""
    ms = 1.0 + st.get("movement_speed") + d.get("empower_speed", 0.0)
    if st.gfx:
        ms *= st.gfx["speed_mult"]
    return ms * st.attr.get("x:swiftness", 1.0)


def god_derive(st, d, caps):
    """Greed-rework custom god nodes on the derived stats (model/godfx.py)."""
    gx = st.gfx
    if not gx:
        return
    g = st.get
    d["health"] *= gx["health_mult"]
    d["armor"] *= gx["armor_mult"]
    d["ability_power"] *= gx["ap"]
    if not d["bloodthirst"]:
        for key in ("healing_effectiveness", "healing_effectiveness_low_mana"):
            he = (d[key] + gx["he_flow"] * g("mana_regen")) * gx["he_mult"]
            if gx["malediction"]:
                he = min(he, gx["malediction"])
            d[key] = max(0.0, he)
    tm = 1.0
    if gx["malediction"]:
        tm = (1.0 + g("healing_effectiveness")) ** (1.0 / 3.0)
    d["thorns_flat"] *= gx["thorns_f"] * tm
    d["thorns_pct"] *= gx["thorns_m"] * tm
    glob = gx["global"]
    parts = list(gx["global_parts"])
    if gx["kinetic"]:
        x = 1.0 + gx["kinetic"] * 100.0 * g("movement_speed")
        glob *= x
        parts.append(("Kinetic Impact", x))
    if gx["greed"]:
        x = max(1.0, 1.0 + gx["greed"] * (g("item_quantity") + g("item_rarity")))
        glob *= x
        parts.append(("Unstoppable Greed", x))
    d["god_global"] = glob
    d["god_global_parts"] = parts
    d["god_stack"] = gx["stack"]
    d["god_true_rage"] = gx["true_rage"]
    d["god_archmage"] = gx["archmage"]
    excess = max(0.0, d["lucky_hit_uncapped"] - min(float(caps.get("luckyHitCap", 0.562)), 1.0))
    d["god_overcrit"] = excess * gx["overcrit"]
    d["god_lucky_twice"] = bool(gx["luckiest"])


def armor_multiplier(armor):
    if armor <= 0:
        return 1.0
    return 1600.0 / (1600.0 + armor * armor)


def hit_multiplier(d):
    """Damage taken by a hit that is not blocked or dodged: armor, resistance, then Castle Bastion (LivingDamageEvent)."""
    return armor_multiplier(d["armor"]) * (1.0 - d["resistance"]) * (1.0 - d["castle"])


def ehp(d):
    mult = (1.0 - d["block"]) * (1.0 - d["dodge"]) * hit_multiplier(d)
    if mult <= 1e-9:
        fallback("ehp-zero-mult", "damage-taken multiplier hit ~0; clamped to 1e-9")
        mult = 1e-9
    return d["health"] / mult, mult
