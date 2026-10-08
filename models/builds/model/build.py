"""Build representation and assembly of a build into Stats."""
import copy

from . import catalog
from .stats import Stats, ability_level_bonus
from .log import fallback

TALENT_GEAR_TYPES = {"gear_attribute", "high_health_gear_attribute"}
STACK_TYPES = {"stack_on_hit_talent", "stacking_gear_attribute"}


class Item:
    def __init__(self, slot, gear_type):
        self.slot = slot
        self.type = gear_type
        self.implicits = {}
        self.prefixes = []
        self.suffixes = []
        self.seal = None
        self.etching = None
        self.unusual = None
        self.unique = None
        self.uaffixes = None
        self.udrop = None
        self.shadow = None

    def affixes(self):
        if self.unique is not None:
            out = [a for i, a in enumerate(self.uaffixes) if i != self.udrop]
            if self.seal:
                out.append(self.seal)
            return out
        out = list(self.implicits.values()) + self.prefixes + self.suffixes
        if self.seal:
            out.append(self.seal)
        if self.unusual:
            out.append(self.unusual)
        return out

    def to_json(self):
        return {
            "slot": self.slot, "type": self.type,
            "implicits": [a.to_json() for a in self.implicits.values()],
            "prefixes": [a.to_json() for a in self.prefixes],
            "suffixes": [a.to_json() for a in self.suffixes],
            "seal": self.seal.to_json() if self.seal else None,
            "unusual": self.unusual.to_json() if self.unusual else None,
            "etching": self.etching, "unique": self.unique, "udrop": self.udrop,
        }


class Build:
    def __init__(self, stage_name, stage, mode, family):
        self.stage_name = stage_name
        self.stage = stage
        self.mode = mode
        self.family = family
        self.items = {}
        self.trinkets = []
        self.charm = {"god": None, "mods": []}
        self.deck = []
        self.talents = {}
        self.abilities = {}
        self.greed = set()
        self.prestige = []
        self.weave = False

    def clone(self):
        b = Build.__new__(Build)
        b.stage_name, b.stage, b.mode, b.family = self.stage_name, self.stage, self.mode, self.family
        b.items = {k: copy.copy(v) for k, v in self.items.items()}
        for it in b.items.values():
            it.implicits = dict(it.implicits)
            it.prefixes = list(it.prefixes)
            it.suffixes = list(it.suffixes)
        b.trinkets = list(self.trinkets)
        b.charm = {"god": self.charm["god"], "mods": list(self.charm["mods"])}
        b.deck = list(self.deck)
        b.talents = dict(self.talents)
        b.abilities = dict(self.abilities)
        b.greed = set(self.greed)
        b.prestige = self.prestige
        b.weave = self.weave
        return b

    def skill_points_total(self, ctx):
        bonus = sum(ctx.greed_by_id[n].get("skillPoints", 0) for n in self.greed if n in ctx.greed_by_id)
        return ctx.knobs["base_skill_points"] + bonus

    def skill_points_spent(self, ctx):
        spent = 0
        for tid, tier in self.talents.items():
            spent += ctx.talent_cost(tid, tier)
        for aid, tier in self.abilities.items():
            spent += ctx.ability_cost(aid, tier)
        return spent


def offhand_counts(build):
    off = build.items.get("offhand")
    main = build.items.get("mainhand")
    if off is None:
        return False
    from .damage import bug
    if not bug(build, "OFFHAND-STAFF") and main is not None and main.type == "battlestaff":
        return False
    return True


def assemble(build, ctx):
    """Sum every source of the build into a Stats object."""
    st = Stats()
    for slot, it in build.items.items():
        if slot == "offhand" and not offhand_counts(build):
            continue
        for a in it.affixes():
            src = f"{slot} ({it.type}) {a.kind.lower()}"
            if a.attribute == "the_vault:added_ability_level" and isinstance(a.raw, dict):
                st.ability_levels.append((a.raw.get("abilityKey", ""), float(a.raw.get("levelChange", 0))))
                st.sources["ability_level"].append((src, a.label))
                continue
            if a.attribute in catalog.PRESENCE_FLAGS:
                st.flags.add(catalog.PRESENCE_FLAGS[a.attribute])
                st.sources[catalog.PRESENCE_FLAGS[a.attribute]].append((src, a.label))
                continue
            if a.attribute in catalog.FLAG_ATTRS:
                if isinstance(a.raw, dict) and a.raw.get("flag"):
                    st.flags.add(catalog.short(a.attribute))
                    st.sources[catalog.short(a.attribute)].append((src, a.label))
                continue
            if a.attribute == "the_vault:ability_special_modification" and isinstance(a.raw, dict):
                name = catalog.SPECIAL_MODS.get(a.raw.get("specialModificationKey"))
                if name and a.value is not None:
                    st.special[name] = st.special.get(name, 0.0) + a.value
                    st.sources["special"].append((src, f"{name} {a.value:g}"))
                continue
            if a.attribute == "the_vault:unique_effect" and isinstance(a.raw, dict):
                name = catalog.EFFECT_FLAGS.get(a.raw.get("effectKey"))
                if name:
                    st.flags.add(name)
                    st.sources[name].append((src, a.label))
                continue
            if a.attribute in catalog.ABILITY_MODS and isinstance(a.raw, dict) and "abilityKey" in a.raw:
                st.ability_mods.append((catalog.ABILITY_MODS[a.attribute], a.raw["abilityKey"], a.value))
                st.sources["ability_mods"].append((src, a.label))
                continue
            if a.attribute == "the_vault:added_talent_level" and isinstance(a.raw, dict):
                st.talent_levels.append((a.raw.get("talentKey", ""), float(a.raw.get("levelChange", 0))))
                st.sources["talent_level"].append((src, a.label))
                continue
            if a.attribute == "the_vault:effect" and isinstance(a.raw, dict):
                if a.raw.get("effectKey") == "minecraft:luck":
                    st.luck = max(st.luck, int(a.raw.get("amplifier", 0)) + 1)
                    st.sources["luck"].append((src, a.label))
                continue
            if a.value is not None:
                st.add(a.attribute, a.value, src)
        if it.etching:
            raw = it.etching.get("raw")
            if isinstance(raw, dict) and "talentKey" in raw:
                st.talent_levels.append((raw["talentKey"], float(raw.get("levelChange", 0))))
            if it.etching["attribute"].endswith("colossus_titan_resistance") and it.etching.get("value"):
                st.add("the_vault:resistance_cap", 0.0, "etching Colossus Titan (cap only while Colossus is active)")
                st.attr["x:colossus_titan"] = it.etching["value"]
            st.flags.add("etch:" + it.etching["attribute"])
            st.sources["etchings"].append((slot, it.etching["name"]))
    for tid in build.trinkets:
        t = ctx.trinkets_by_id[tid]
        for eff in t["effects"]:
            src = f"trinket {t['name']}"
            if eff[0] == "add":
                st.add(eff[1], eff[2], src)
            elif eff[0] == "vanilla":
                if eff[3] == 2:
                    st.vanilla_total[eff[1]] *= 1.0 + eff[2]
                    st.sources[eff[1]].append((src, f"x{1 + eff[2]:.2f}"))
                else:
                    fallback("trinket-op", f"trinket {tid} uses operation {eff[3]}; treated as additive")
                    st.add(eff[1], eff[2], src)
            elif eff[0] == "ability_level":
                st.ability_levels.append((eff[1], eff[2]))
                st.sources["ability_level"].append((src, f"+{eff[2]:.0f} {eff[1]}"))
            elif eff[0] == "dice":
                st.dice = (eff[1], eff[2])
            elif eff[0] == "luck":
                st.luck = max(st.luck, eff[1])
                st.sources["luck"].append((src, f"Luck {eff[1]}"))
    for m in build.charm["mods"]:
        st.add(m["attribute"], m["value"], f"god charm ({build.charm['god']})")
    deck_sum = {}
    for i, cid in enumerate(build.deck):
        if cid is None:
            continue
        attr, contrib, _, _ = ctx.deck_options[i][cid]
        n, v = deck_sum.get(attr, (0, 0.0))
        deck_sum[attr] = (n + 1, v + contrib)
    for attr, (n, v) in deck_sum.items():
        st.add(attr, v, f"deck ({n} card{'s' if n > 1 else ''})")
    for nid in build.greed:
        node = ctx.greed_by_id.get(nid)
        if not node:
            continue
        for e in node.get("entries", []):
            st.add(e["attribute"], e["value"], f"greed node {node.get('name', nid)}")
    for p in build.prestige:
        if p.get("type") == "gear_attribute_power" and isinstance(p.get("value"), (int, float)):
            st.add(p["attribute"], float(p["value"]), f"prestige {p['id']}")
        if p.get("type") == "masterful_power":
            st.flags.add("masterful")
        if p.get("type") == "berserk_power":
            st.flags.add("berserk_power")
    apply_talents(build, ctx, st)
    return st


def talent_effective_tier(ctx, st, tid, learned):
    t = ctx.talents[tid]
    n = len(t.get("tiers", [t]))
    bonus = 0.0
    for key, ch in st.talent_levels:
        if key == "all_talents" or key == tid:
            bonus += ch
    return int(min(n, learned + bonus)) if learned > 0 else 0


def apply_talents(build, ctx, st):
    for tid, learned in build.talents.items():
        if learned <= 0:
            continue
        t = ctx.talents[tid]
        eff = talent_effective_tier(ctx, st, tid, learned)
        tier = t.get("tiers", [t])[eff - 1]
        ttype = tier.get("type")
        src = f"talent {tid} t{eff}"
        if ttype in TALENT_GEAR_TYPES:
            if tier.get("attribute") == "woldsvaults:additional_stacking_stacks":
                st.attr["x:extra_stacks"] += tier.get("value", 0)
            else:
                st.add(tier["attribute"], tier.get("value", 0.0), src)
        elif ttype in STACK_TYPES:
            st.attr["x:stack_talent:" + tid] = eff
        elif ttype == "mind_meld":
            st.flags.add("mind_meld")
        elif ttype == "low_mana_healing_efficiency":
            st.attr["x:methodical"] = tier["additionalHealingEfficiency"] * ctx.knobs["uptime_low_mana"]
        st.attr["x:talent_tier:" + tid] = eff
    extra = st.attr.get("x:extra_stacks", 0.0)
    for tid, learned in build.talents.items():
        if learned <= 0:
            continue
        key = "x:stack_talent:" + tid
        if key not in st.attr:
            continue
        t = ctx.talents[tid]
        eff = int(st.attr[key])
        tier = t["tiers"][eff - 1]
        stacks = tier.get("maxStacks", 1) + extra
        uptime = 1.0
        if tier["type"] == "stacking_gear_attribute":
            uptime = ctx.knobs["uptime_kill_stacks"]
            st.flags.add("needs_kills:" + tid)
        if tier["type"] == "stack_on_hit_talent" and not ctx.family_hits_normally(build.family):
            uptime = 0.0
        if uptime > 0:
            st.add(tier["attribute"], tier.get("value", 0.0) * stacks * uptime,
                   f"talent {tid} t{eff} ({stacks:.0f} stacks x{uptime:.2f} uptime)")
