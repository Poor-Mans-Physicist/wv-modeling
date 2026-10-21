"""Build representation and assembly of a build into Stats."""
import copy
import math

from . import catalog
from . import gods
from . import godfx
from .stats import Stats, ability_level_bonus
from .log import fallback

TALENT_GEAR_TYPES = {"gear_attribute", "high_health_gear_attribute"}
STACK_TYPES = {"stack_on_hit_talent", "stacking_gear_attribute"}
# Pyramid Scheme (sword etching): extra max stacks for every stacking talent, like Stack Master.
PYRAMID_ATTR = "woldsvaults:additional_stacking_stacks"


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
        # Abilities cast while sneaking (Power Dump rework); "Starforge_Nova" means the nova procs while sneaking.
        self.pd = set()
        self.gods = None
        # Rework deck menus: the chosen design, its core set and the option index in each of its slots.
        self.design = 0
        self.cset = 0
        self.dk = []

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
        b.pd = set(self.pd)
        b.gods = None if self.gods is None else gods.clone_state(self.gods)
        b.design, b.cset, b.dk = self.design, self.cset, list(self.dk)
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


def god_ranks(build, ctx):
    active = gods.GOD_OF_CHARM.get(build.charm["god"]) if build.charm["god"] else None
    return ctx.gods.ranks(build.gods, active)


def add_god_nodes(build, ctx, st):
    """Plain gear-attribute god nodes into the totals (gear_attribute_scaled: values[k] per placement k, the last entry
    repeating, x the carryover scale); custom effects are kept in st.god_effects for the model hooks."""
    st.god_effects = {}
    ranks = god_ranks(build, ctx)
    for e, (n, scale) in ranks.items():
        d = ctx.gods.effect_def(e)
        if d.get("handler") == "gear_attribute_scaled":
            vals = d.get("values") or [0.0]
            v = sum(vals[min(k, len(vals) - 1)] for k in range(n)) * scale
            src = f"god node {e} x{n}" + (f" at {scale:.0%}" if scale < 1 else "")
            st.add(d["attribute"], v, src)
            if d.get("secondAttribute"):
                st.add(d["secondAttribute"], v, src)
        else:
            st.god_effects[e] = (n, scale)
            for attr, v in godfx.adds(e, d):
                st.add(attr, v, f"god node {e}")
            if e == "idona_stack_stack_stack":
                st.attr["x:extra_stacks"] += int(n * (d.get("values") or [1.0])[0] * scale + 1e-9)
    st.gfx = godfx.compute(st.god_effects, {e: ctx.gods.effect_def(e) for e in st.god_effects}, ctx.knobs)
    if st.gfx["utilized"]:
        st.ability_levels.append(("UTILITY", st.gfx["utilized"]))
        st.sources["ability_level"].append(("god node velara_utilized", f"+{st.gfx['utilized']:.0f} UTILITY"))


def assemble(build, ctx):
    """Sum every source of the build into a Stats object."""
    st = Stats()
    plushie_twice = (ctx.gods is not None and build.gods is not None
                     and "wendarr_plushie_lover" in god_ranks(build, ctx))
    for slot, it in build.items.items():
        if slot == "offhand" and not offhand_counts(build):
            continue
        reps = 2 if (plushie_twice and slot == "offhand" and it.type == "plushie") else 1
        for a in [x for x in it.affixes() for _ in range(reps)]:
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
            if a.attribute == "the_vault:effect_cloud" and isinstance(a.raw, dict):
                from .procs import cloud_kinds, CHILLED
                ck = cloud_kinds()
                effs = [(ck[e["effect"]], float(e.get("duration", 0))) for e in a.raw.get("additionalEffects", [])
                        if e.get("effect") in ck]
                if effs:
                    st.clouds.append({"name": a.raw.get("tooltipDisplayName", "cloud"), "chance": float(a.raw.get("triggerChance", 0.0)),
                                      "ticks": float(a.raw.get("duration", 0)), "effects": effs,
                                      "poison": max([float(e.get("amplifier", 0)) + 1.0 for e in a.raw.get("additionalEffects", [])
                                                     if e.get("effect") == "minecraft:poison"], default=0.0),
                                      "chill": max([int(e.get("amplifier", 0)) + 1 for e in a.raw.get("additionalEffects", [])
                                                    if e.get("effect") in ck and ck[e["effect"]] == CHILLED], default=0)})
                    st.sources["special"].append((src, f"effect cloud {a.raw.get('tooltipDisplayName', '')}"))
                continue
            if a.attribute == "the_vault:arcane_nova_on_hit" and isinstance(a.raw, dict):
                if slot == "mainhand" and a.value is not None:
                    st.special["arcane_nova_pct"] = st.special.get("arcane_nova_pct", 0.0) + a.value
                    st.special["arcane_nova_hits"] = a.raw.get("hitsRolled", 0.0)
                    st.sources["special"].append((src, f"arcane nova every {a.raw.get('hitsRolled', 0):.0f} hits, {a.value:.0%} AP"))
                continue
            if a.attribute == "the_vault:steel_inferno" and isinstance(a.raw, dict):
                if a.value is not None:
                    st.special["steel_inferno"] = st.special.get("steel_inferno", 0.0) + a.value
                    st.special["steel_inferno_radius"] = float(a.raw.get("radiusRolled", 1.0))
                    st.sources["special"].append((src, f"Steel Inferno +{a.value:.0%} centre, x{a.raw.get('radiusRolled', 1.0):.2f} radius"))
                continue
            if a.attribute == "the_vault:ability_on_damage" and isinstance(a.raw, dict):
                name = catalog.ON_DAMAGE_SPECIALS.get(a.raw.get("abilityId"))
                if name and a.value is not None and slot in catalog.ARMOR:
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
            if it.etching["attribute"] == PYRAMID_ATTR and it.etching.get("value"):
                st.attr["x:extra_stacks"] += float(it.etching["value"])
                st.sources["extra_stacks"].append((slot, f"Pyramid Scheme +{float(it.etching['value']):.0f} max stacks"))
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
    units = None
    if ctx.gods is not None and build.gods is not None and build.charm["god"]:
        units = ctx.gods.piety_units(build.gods, gods.GOD_OF_CHARM[build.charm["god"]])
    for m in build.charm["mods"]:
        if units is None:
            st.add(m["attribute"], m["value"], f"god charm ({build.charm['god']})")
        else:
            st.add(m["attribute"], m["value"] * (units + 1), f"god charm ({build.charm['god']}, {units} piety units)")
    if ctx.gods is not None and build.gods is not None:
        add_god_nodes(build, ctx, st)
    deck_sum = {}
    for i, cid in enumerate(build.deck):
        if cid is None:
            continue
        attr, contrib, _, _ = ctx.deck_options[i][cid]
        n, v = deck_sum.get(attr, (0, 0.0))
        deck_sum[attr] = (n + 1, v + contrib)
    for attr, (n, v) in deck_sum.items():
        st.add(attr, v, f"deck ({n} card{'s' if n > 1 else ''})")
    if getattr(ctx, "designs", None) and build.dk:
        from . import deck_rework
        d = ctx.designs[build.design]
        cards = deck_rework.design_cards(d, build.dk)
        terms = deck_rework.design_terms(d, build.cset, build.dk)
        tot, cnt = {}, {}
        for term, c in zip(terms, cards):
            if term != 0.0 and c.get("attr"):
                tot[c["attr"]] = tot.get(c["attr"], 0.0) + term * c["value"]
                cnt[c["attr"]] = cnt.get(c["attr"], 0) + 1
        for attr, v in tot.items():
            st.add(attr, v, f"deck ({cnt[attr]} card{'s' if cnt[attr] > 1 else ''}, {d['label']})")
        ab, tal = deck_rework.level_grants(cards)
        for key, lv in ab:
            st.ability_levels.append((key, lv))
            st.sources["ability_level"].append(("deck card", f"+{lv:.0f} {key}"))
        for key, lv in tal:
            st.talent_levels.append((key, lv))
            st.sources["talent_level"].append(("deck card", f"+{lv:.0f} {key}"))
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
        if p.get("type") == "movement_speed_multiplier_power" and isinstance(p.get("multiplier"), (int, float)):
            st.flags.add("swiftness")
            st.attr["x:swiftness"] = float(p["multiplier"])
            st.sources["move_speed"].append((f"prestige {p['id']}", f"x{p['multiplier']:g}"))
    apply_talents(build, ctx, st)
    return st


NEGATION_ETCHING = "woldsvaults:divinity"


def negation_met(build):
    """Effect negation (user ruling 2026-10-18): the Carapace trinket or the Blessed etching on any piece."""
    if any(t in catalog.NEGATION_TRINKETS for t in build.trinkets):
        return True
    return any(it.etching and it.etching["attribute"] == NEGATION_ETCHING for it in build.items.values())


def talent_effective_tier(ctx, st, tid, learned):
    t = ctx.talents[tid]
    n = len(t.get("tiers", [t]))
    bonus = 0.0
    for key, ch in st.talent_levels:
        if key == "all_talents" or key == tid:
            bonus += ch
    return int(min(n, learned + bonus)) if learned > 0 else 0


def apply_talents(build, ctx, st):
    """Talents into the totals. Stacking talents share one timer that every new stack resets
    (StackingGearAttributeTalent, StackOnHitAttributeTalent): with T = durationTicks x (1 + effect duration) x Stack
    Hoarder and stack events at `rate` per second (kills_per_s for kill stacks, normal hits for hit stacks), the stacks
    stay at max when rate x T / 20 >= 1 and are otherwise 1 stack at that uptime."""
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
            st.attr["x:methodical"] = tier["additionalHealingEfficiency"]
        st.attr["x:talent_tier:" + tid] = eff
    extra = st.attr.get("x:extra_stacks", 0.0)
    hoard = st.gfx["stack_hoarder"] if st.gfx else 1.0
    ed = st.get("effect_duration")
    hits = normal_hit_rate(build, ctx, st)
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
        if st.gfx and st.gfx["super_stacker"] != 1.0:
            stacks = math.floor(stacks * st.gfx["super_stacker"] + 1e-9)
        if tier["type"] == "stacking_gear_attribute":
            rate = ctx.knobs["kills_per_s"]
            st.flags.add("needs_kills:" + tid)
        else:
            rate = hits
        n = rate * tier["durationTicks"] * (1.0 + ed) * hoard / 20.0
        if n < 1.0 - 1e-9:
            stacks, uptime = 1.0, n
        else:
            uptime = 1.0
        if uptime > 0:
            st.add(tier["attribute"], tier.get("value", 0.0) * stacks * uptime,
                   f"talent {tid} t{eff} ({stacks:.0f} stacks x{uptime:.2f} uptime)")


def normal_hit_rate(build, ctx, st):
    """Normal weapon hits per second before stacking talents (melee families and weaving ability builds swing; Grand
    Archmage cancels every swing), for the hit-stacking talents' refresh test."""
    if st.gfx and st.gfx["archmage"]:
        return 0.0
    from .stats import BASE_ATTACK_SPEED
    aspd = max(0.1, (BASE_ATTACK_SPEED + st.get("attack_speed")) * (1.0 + st.get("attack_speed_percent")))
    if build.family.startswith("ranged:"):
        speed = 1.5 + st.get("velocity") * 0.325
        cd_ticks = math.floor(20.0 / aspd + 0.5 + 1e-9)
        return 1.0 / (2.0 * ctx.knobs["rang_travel_blocks"] / (speed * 20.0) + cd_ticks / 20.0)
    if not (build.family.startswith("melee:") or getattr(build, "weave", False)):
        return 0.0
    from .families import bc_swings_per_s
    return min(bc_swings_per_s(aspd), ctx.knobs["max_swings_per_s"])
