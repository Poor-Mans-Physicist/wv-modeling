"""Stat assembly: sum every source into attribute totals, then derive effective stats (MECHANICS §1)."""
import collections
import math

from . import catalog
from .log import fallback

BASE_HEALTH = 20.0
BASE_ATTACK_SPEED = 4.0


def ability_level_bonus(sources, spec_skill, groups, masterful):
    """Total +levels for one ability skill id from (abilityKey, levelChange) pairs."""
    total = 0.0
    for key, change in sources:
        hit = key == "all_abilities" or key == spec_skill
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
        self.special = {}
        self.talent_levels = []
        self.flags = set()
        self.dice = None
        self.no_exec = False
        self.luck = 0

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
    d["lucky_hit_chance"] = min(lucky, float(caps.get("luckyHitCap", 0.562)))
    d["lucky_hit_uncapped"] = lucky
    d["aoe_multiplier"] = min(1.0 + g("area_of_effect"), 1.0 + float(caps.get("aoeCap", 0.8)))
    d["health"] = (BASE_HEALTH + g("health")) * (1.0 + g("health_percentile")) * st.vanilla_total["minecraft:generic.max_health"]
    d["armor"] = g("armor") * (1.0 + g("armor_percentile"))
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
    d["chain"] = g("on_hit_chain")
    d["on_hit_aoe"] = g("on_hit_aoe")
    d["double_hit"] = min(g("double_hit_chance"), 1.0)
    d["relentless"] = g("relentless_strike")
    d["third_attack"] = g("third_attack")
    d["effect_duration"] = g("effect_duration")
    d["crit_mitigation"] = g("critical_hit_mitigation")
    d["cooldown_skip"] = g("ability_cooldown_skip")
    d["bloodthirst"] = "bloodthirst" in st.flags
    he = 1.0 + g("healing_effectiveness") + st.attr.get("x:methodical", 0.0)
    d["healing_effectiveness"] = 0.0 if d["bloodthirst"] else max(0.0, he)
    d["leech"] = g("leech")
    d["thorns_flat"] = g("thorns_damage_flat")
    d["thorns_pct"] = g("thorns_damage")
    d["thorns_scaling"] = g("thorns_scaling_damage")
    d["ap_scaling"] = g("ap_scaling_damage")
    d["ap_flat"] = g("ability_power") * (1.0 + g("ability_power_percent"))
    d["castle"] = min(0.5, 5.0 * g("castle_bastion"))
    return d


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
