"""Bridge to the Rust search kernel (kernel/): flatten a Context into index tables, run the kernel, map builds back.

The kernel re-implements evaluate() and the annealing search; model/*.py stays the reference implementation and
every build the kernel returns is re-scored here (a mismatch is logged as the kernel-parity fallback).
"""
import hashlib
import json
import os
import subprocess
import sys

from . import catalog, stages, families, procs
from . import buffs as bf
from . import abilities as ab
from .build import Build, Item
from .damage import bug
from .log import fallback, fallback_n
from . import gods as godmod
from . import godfx
from . import evaluate
from .deck_rework import CORE_CODE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "kernel", "target", "release", "wvk.exe" if os.name == "nt" else "wvk")

BUG_IDS = ["ICEBOLT-DI2", "ECHO-FLAGLOSS", "EXEC-GEAR", "ORDER-FLATADD", "FANG-RESIDUAL", "VULN-OFF1",
           "VOLLEY-BOUNCE-EXPLODE", "ICEBOLT-T12-HOLE", "OFFHAND-STAFF", "MANA-CAP", "BOSS-DMG-CAP"]

CFG_KEYS = [
    "cooldownTicks", "manaCost", "percentAbilityPowerDealt", "percentAbilityPowerDealtMin", "percentAbilityPowerDealtMax",
    "radius", "intervalTicks", "manaCostPerSecond", "additionalManaPerBolt", "durationTicks", "maxTargets", "chainRange",
    "boltCount", "fullDamageHitsPerTarget", "repeatHitDamageMultiplier", "cloudDuration", "percentAttackDamageDealt",
    "damagePerBolt", "damagePerShard", "piercing", "numberOfJavelins", "shockCount", "shockIntervalTicks", "summonCap",
    "attackDamagePercentPerDash", "damageMultiplier", "baseDamage", "percentManaDealt", "percentHealthDrained",
    "damagePerHealth", "blockChanceDamageScalar", "thornsDamageScalar", "totemPercentDamagePerInterval",
    "totemDurationTicks", "totemDamageIntervalTicks", "totemEffectRadius", "poisonTicks", "durationSeconds",
    "totemPlayerDamagePercent", "totemManaRegenPercent", "manaRampPerSecond", "damageIncrease", "luckyHitChance",
    "maxStacksTotal", "attackDamagePerStack", "abilityPowerPerStack", "luckyHitChancePerStack", "maxStacksUsedPerHit",
    "amplifier", "additionalResistance", "flatLifeHealed", "additionalThornsDamagePercent", "stackDuration",
    "baseAmplitude", "amplitudeScaleChance", "baseEffectDuration",
    "speedPercentAdded", "totemHealthPerSecond", "healthPoints", "percentageDamageAbsorbed", "manaPerDamageScalar",
    "baseManaDrainPerTick", "manaDrainRampPerSecond", "maxManaDrainPerTick", "attackIntervalTicks", "aoeRadius",
    "aoeDamageMultiplier", "slowDuration",
]

ETCH_QUERIES = [
    "etching_nova_recast", "etching_smite_echo", "etching_arcane_pierce", "etching_lightning_orb_triple_damage",
    "etching_ice_bolt_lucky", "etching_ice_bolt_multicast", "etching_extra_piercing_javelin", "ravenous_fangs",
    "etching_life_tap_extra_damage", "etching_shield_bash_damage", "woldsvaults:fireball_volley_mitosis",
    "etching_totem_mob_damage_ad", "etching_lucky_vulnerable", "etching_totem_player_damage_effect",
    "etching_rampage_lucky_hit", "woldsvaults:additional_stacking_stacks", "woldsvaults:divinity",
    "woldsvaults:sneaky_getaway_ninja", "etching_totem_player_damage_ap", "etching_lightning_strike_delay",
    "etching_storm_arrow_blizzard_effect", "etching_storm_arrow_blizzard_damage", "etching_lucky_frost_nova",
    "etching_lucky_cooldown_reset", "etching_earthquake_recast_delay", "woldsvaults:fireball_greedball",
    "woldsvaults:reverberation", "etching_blocking_mana", "etching_scatter_mana", "etching_poison_mana",
    "woldsvaults:colossus_titan_resistance", "etching_nova_low_mana_damage",
]

TALENT_TYPES = {"gear_attribute": 1, "high_health_gear_attribute": 11, "stack_on_hit_talent": 2,
                "stacking_gear_attribute": 3, "mind_meld": 4, "damage_lucky_hit": 5, "execution_lucky_hit": 6,
                "low_mana_healing_efficiency": 7, "health_leech_lucky_hit": 8, "low_health_resistance": 9,
                "cast_on_hit": 10, "low_health_damage": 12}
AMOD_KINDS = {"cooldown": 0, "mana": 1}
FLAG_KEYS = ["lucky_thorns", "safer_space", "bloodthirst", "javelin_implode", "javelin_multi_hit", "storm_tracking"]
SPECIAL_KEYS = ["frost_nova_vulnerability", "fireball_recast", "frost_nova_on_damage", "javelin_implode_damage"]

# Affix kinds by code (problem.rs AFFIX_KIND_NAMES; build.rs matches on the codes).
AFFIX_KINDS = ["add", "ability_level", "talent_level", "luck", "none", "flag", "ability_mod", "special", "arcane_nova",
               "cloud", "on_damage", "steel_inferno"]
AK = {name: i for i, name in enumerate(AFFIX_KINDS)}

DEFAULT_SCHEDULE = {"kind": "geometric", "t0": 0.6, "t1": 0.004}

SLOT_ORDER = catalog.ARMOR + ["mainhand", "offhand", "necklace"]

# Spec-table rules as the kernel reads them (problem.rs RULE_*): no, yes, only with the Lucky Bolt etching.
RULE_CODES = {False: 0, True: 1, "lucky_bolt": 2}
# Fang tables and reach rules as the kernel reads them (problem.rs FANG_* / REACH_*).
FANG_CODES = {None: 0, "wall": 1, "maw": 2}
REACH_CODES = {None: 0, "projectile": 1, "self_radius": 2, "zero": 3, "fang_line": 4}
# Cast-debuff class per (procs kind, uptime rule) (problem.rs spec_cls, buffs.rs cast_debuffs).
CAST_DEBUFF_CLASSES = {("MARK", "totem_interval"): 1, ("SLOWNESS", "maw_slowness"): 2, ("CURSE", "always"): 3,
                       ("HYPOTHERMIA", "share"): 4}


# Rework deck card kinds (kernel deck.rs K_*).
DECK_KINDS = {"dead": 0, "greed": 1, "evo": 2, "typeless": 3, "deluxe": 4, "knack": 5, "temporal": 6, "resource": 7,
              "arcane": 8}


def contract_lists(knobs=None):
    """The name lists the kernel's index-coupled tables must match (problem.rs Contract::check asserts each one at load;
    the `contract` op prints the kernel's side). knobs: the exported knob names (the kernel requires its own list to be
    a subset)."""
    return {
        "affix_kinds": AFFIX_KINDS, "flag_keys": FLAG_KEYS, "special_keys": SPECIAL_KEYS,
        "talent_types": TALENT_TYPES, "amod_kinds": AMOD_KINDS, "proc_kinds": procs.KIND_NAMES,
        "cloud_kinds": procs.CLOUD_KINDS, "etch_queries": ETCH_QUERIES, "cfg_keys": CFG_KEYS, "bug_ids": BUG_IDS,
        "rule_codes": {"no": RULE_CODES[False], "yes": RULE_CODES[True], "lucky_bolt": RULE_CODES["lucky_bolt"]},
        "cast_debuff_classes": [[procs.KIND_NAMES[getattr(procs, k)], rule, code] for (k, rule), code in CAST_DEBUFF_CLASSES.items()],
        "knobs": sorted(knobs or []),
    }


def kernel_contract(exe=None):
    """The kernel's own contract lists (the `contract` op)."""
    exe = exe or EXE
    proc = subprocess.run([exe], input=b'{"op":"contract"}', capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"kernel contract op failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[-2000:]}")
    return json.loads(proc.stdout)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)



def _spec_facts(sid):
    """The spec table facts the kernel reads (zeros for specs the table does not list, e.g. baseline skills)."""
    rec = ab.SPEC_TABLE.get(sid)
    if rec is None:
        return {"leech": 0, "lucky": False, "pd_hold": False, "spectral": 0, "cast_debuff": 0, "fang": 0, "reach": 0,
                "reach_key": None, "free_iframe": False}
    cd = rec["cast_debuffs"]
    if len(cd) > 1 or (cd and (cd[0][0], cd[0][1]) not in CAST_DEBUFF_CLASSES):
        raise ValueError(f"{sid}: the kernel supports one cast debuff from {sorted(CAST_DEBUFF_CLASSES)}, got {cd}")
    if not isinstance(rec["lucky_ok"], bool) or rec["leech_ok"] not in RULE_CODES or rec["spectral"] not in RULE_CODES:
        raise ValueError(f"{sid}: leech_ok / lucky_ok / spectral rule the kernel does not know")
    return {"leech": RULE_CODES[rec["leech_ok"]], "lucky": rec["lucky_ok"], "pd_hold": bool(rec["pd_hold"]),
            "spectral": RULE_CODES[rec["spectral"]], "cast_debuff": CAST_DEBUFF_CLASSES[(cd[0][0], cd[0][1])] if cd else 0,
            "fang": FANG_CODES[rec["fang_hits"]], "reach": REACH_CODES[rec["reach"]], "reach_key": rec["reach_key"],
            "free_iframe": ab.free_iframes(sid)}


def _fang_doc():
    t = ab.fang_table()
    return {"step": t["step"], "wall": t["wall"], "maw": t["maw"]}


def _echo_doc(key="v"):
    t = procs.echo_table()
    return {"n_max": t["n_max"], "na": t["na"], "nq": t["nq"], "v": [x for n in (t[key] or []) for row in n for x in row]}

class _Interner:
    def __init__(self):
        self.ids = {}
        self.names = []

    def __call__(self, name):
        if name not in self.ids:
            self.ids[name] = len(self.names)
            self.names.append(name)
        return self.ids[name]


class Export:
    """Index tables for one Context. Python objects keep their position so kernel builds map back exactly."""

    def __init__(self, ctx):
        self.ctx = ctx
        attr = _Interner()
        group = _Interner()
        self.attr = attr
        spec_ids = [s for s in dict.fromkeys(list(ab.MODELS) + bf.BUFF_SPECS
                                             + [v[0] for v in stages.BASELINE_ABILITIES.values()]) if s in ctx.abilities]
        self.spec_ids = spec_ids
        self.spec_idx = {s: i for i, s in enumerate(spec_ids)}
        skills = sorted({ctx.abilities[s]["skill"] for s in spec_ids})
        self.skill_idx = {s: i for i, s in enumerate(skills)}
        self.talent_ids = list(ctx.talents)
        self.talent_idx = {t: i for i, t in enumerate(self.talent_ids)}
        lvl_keys, tal_keys, amod_keys = _Interner(), _Interner(), _Interner()

        self.affix_objs = []
        self.affix_idx = {}
        affixes = []

        clouds = []

        def affix(a):
            if id(a) in self.affix_idx:
                return self.affix_idx[id(a)]
            raw = a.raw if isinstance(a.raw, dict) else None
            rec = {"k": AK["none"], "a": 0, "v": 0.0, "g": group(a.group), "key": 0, "lc": 0.0, "amp": 0}
            if a.attribute == "the_vault:added_ability_level" and raw is not None:
                rec.update(k=AK["ability_level"], key=lvl_keys(raw.get("abilityKey", "")), lc=float(raw.get("levelChange", 0)))
            elif a.attribute == "the_vault:added_talent_level" and raw is not None:
                rec.update(k=AK["talent_level"], key=tal_keys(raw.get("talentKey", "")), lc=float(raw.get("levelChange", 0)))
            elif a.attribute == "the_vault:effect" and raw is not None:
                if raw.get("effectKey") == "minecraft:luck":
                    rec.update(k=AK["luck"], amp=int(raw.get("amplifier", 0)) + 1)
            elif a.attribute in catalog.PRESENCE_FLAGS:
                rec.update(k=AK["flag"], key=FLAG_KEYS.index(catalog.PRESENCE_FLAGS[a.attribute]))
            elif a.attribute in catalog.FLAG_ATTRS:
                if raw is not None and raw.get("flag"):
                    rec.update(k=AK["flag"], key=FLAG_KEYS.index(catalog.short(a.attribute)))
            elif a.attribute == "the_vault:unique_effect":
                name = catalog.EFFECT_FLAGS.get(raw.get("effectKey")) if raw is not None else None
                if name:
                    rec.update(k=AK["flag"], key=FLAG_KEYS.index(name))
            elif a.attribute == "the_vault:ability_special_modification":
                name = catalog.SPECIAL_MODS.get(raw.get("specialModificationKey")) if raw is not None else None
                if name and a.value is not None:
                    rec.update(k=AK["special"], key=SPECIAL_KEYS.index(name), v=float(a.value))
            elif a.attribute == "the_vault:effect_cloud":
                ck = procs.cloud_kinds()
                effs = [[ck[e["effect"]], float(e.get("duration", 0))] for e in (raw or {}).get("additionalEffects", [])
                        if e.get("effect") in ck]
                if effs:
                    clouds.append({"chance": float(raw.get("triggerChance", 0.0)), "ticks": float(raw.get("duration", 0)), "effects": effs,
                                   "poison": max([float(e.get("amplifier", 0)) + 1.0 for e in raw.get("additionalEffects", [])
                                                  if e.get("effect") == "minecraft:poison"], default=0.0),
                                   "chill": max([int(e.get("amplifier", 0)) + 1 for e in raw.get("additionalEffects", [])
                                                 if ck.get(e.get("effect")) == procs.CHILLED], default=0)})
                    rec.update(k=AK["cloud"], key=len(clouds) - 1)
            elif a.attribute == "the_vault:arcane_nova_on_hit":
                if raw is not None and a.value is not None:
                    rec.update(k=AK["arcane_nova"], v=float(a.value), lc=float(raw.get("hitsRolled", 0.0)))
            elif a.attribute == "the_vault:steel_inferno":
                if raw is not None and a.value is not None:
                    rec.update(k=AK["steel_inferno"], v=float(a.value), lc=float(raw.get("radiusRolled", 1.0)))
            elif a.attribute == "the_vault:ability_on_damage":
                name = catalog.ON_DAMAGE_SPECIALS.get(raw.get("abilityId")) if raw is not None else None
                if name and a.value is not None:
                    rec.update(k=AK["on_damage"], key=SPECIAL_KEYS.index(name), v=float(a.value))
            elif a.attribute in catalog.ABILITY_MODS and raw is not None and "abilityKey" in raw:
                rec.update(k=AK["ability_mod"], key=amod_keys(raw["abilityKey"]), amp=AMOD_KINDS[catalog.ABILITY_MODS[a.attribute]],
                           v=float(a.value))
            elif a.value is not None:
                rec.update(k=AK["add"], a=attr(a.attribute), v=float(a.value))
            self.affix_idx[id(a)] = len(affixes)
            self.affix_objs.append(a)
            affixes.append(rec)
            return self.affix_idx[id(a)]

        self.etch_objs = []
        self.etch_idx = {}
        etchings = []

        def etching(e):
            if e["id"] in self.etch_idx:
                return self.etch_idx[e["id"]]
            v = e["value"]
            val = True if v is None else v
            if _num(val) or isinstance(val, bool):
                num = float(val)
            elif isinstance(val, dict) and _num(val.get("level")):
                num = float(val["level"])
            elif isinstance(val, dict) and _num(val.get("damageMultiplier")):
                num = float(val["damageMultiplier"])
            else:
                num = None
            dur = float(val["duration"]) if isinstance(val, dict) and _num(val.get("duration")) else None
            raw = e.get("raw")
            tl = None
            if isinstance(raw, dict) and "talentKey" in raw:
                tl = [tal_keys(raw["talentKey"]), float(raw.get("levelChange", 0))]
            q = [i for i, name in enumerate(ETCH_QUERIES) if e["attribute"] in ("the_vault:" + name, name)]
            self.etch_idx[e["id"]] = len(etchings)
            self.etch_objs.append(e)
            etchings.append({"id": e["id"], "num": num, "dur": dur, "truthy": bool(val), "q": q, "tl": tl,
                             "stacking": e["attribute"].split(":")[-1] in _stacking(),
                             "pool": [self.spec_idx[sid] for sid in ab.reset_pool(e["value"], ctx)
                                      if sid in self.spec_idx]})
            return self.etch_idx[e["id"]]

        self.type_names = list(ctx.pools)
        self.type_idx = {t: i for i, t in enumerate(self.type_names)}
        self.implicit_keys = {}
        types = []
        for t in self.type_names:
            pools = ctx.pools[t]
            pre, suf = catalog.affix_counts(t, ctx.rarity if t != "vault_necklace" else "OMEGA")
            groups = catalog.implicit_groups(pools)
            self.implicit_keys[t] = list(groups)
            types.append({
                "name": t, "pre": pre, "suf": suf,
                "prefix": [affix(a) for a in pools["PREFIX"]], "suffix": [affix(a) for a in pools["SUFFIX"]],
                "seal": [affix(a) for a in pools["CORRUPTED_IMPLICIT"]],
                "unusual": [affix(a) for a in pools["UNUSUAL_PREFIX"] + pools["UNUSUAL_SUFFIX"]],
                "implicit_groups": [[affix(a) for a in opts] for opts in groups.values()],
                "etchings": [etching(e) for e in ctx.etchings.get(t, [])],
            })

        self.unique_ids = []
        uniques = []
        for slot_i, slot in enumerate(SLOT_ORDER):
            for u in ctx.unique_slots.get(slot, []):
                self.unique_ids.append(u["id"])
                uniques.append({"id": u["id"], "ty": self.type_idx[u["type"]], "slot": slot_i,
                                "affixes": [affix(a) for a in u["affixes"]], "explicit": list(u["explicit"]),
                                "ancient": bool(u.get("ancient")),
                                "regen": catalog.unique_regen(u["id"]) if ctx.gods is not None else 0})
        self.unique_idx = {u: i for i, u in enumerate(self.unique_ids)}
        unique_seals = [affix(a) for a in ctx.unique_seals]

        if ctx.gods is not None:
            self.utility_key = lvl_keys("UTILITY")
        for d in getattr(ctx, "designs", None) or []:
            for opts in d["opts"]:
                for c in opts:
                    if c.get("grant"):
                        (lvl_keys if c["grant"][0] == "ability" else tal_keys)(c["grant"][1])
        lvl_hits = []
        for k in lvl_keys.names:
            hits = []
            for s in spec_ids:
                if families.ability_level_bonus([(k, 1.0)], ctx.abilities[s]["skill"], ctx.ability_groups, False, s) > 0:
                    hits.append(self.spec_idx[s])
            ok_any = k == "all_abilities" or bool(hits)
            lvl_hits.append({"all": k == "all_abilities", "hits": hits, "name": k, "ok_any": ok_any})
        tal_key_rec = [{"all": k == "all_talents", "talent": self.talent_idx.get(k, -1), "name": k} for k in tal_keys.names]

        self.trinket_ids = [t["id"] for t in ctx.trinkets]
        self.trinket_idx = {t: i for i, t in enumerate(self.trinket_ids)}
        colors = list(ctx.stage["trinket_slots"])
        trinkets = []
        for t in ctx.trinkets:
            effs = []
            for e in t["effects"]:
                if e[0] == "add":
                    effs.append({"t": "add", "a": attr(e[1]), "v": float(e[2])})
                elif e[0] == "vanilla":
                    effs.append({"t": "vanilla", "a": attr(e[1]), "v": float(e[2]), "op": int(e[3])})
                elif e[0] == "ability_level":
                    effs.append({"t": "lvl", "key": lvl_keys(e[1]), "v": float(e[2])})
                elif e[0] == "dice":
                    effs.append({"t": "dice", "lo": float(e[1]), "hi": float(e[2])})
                elif e[0] == "luck":
                    effs.append({"t": "luck", "n": int(e[1])})
            seal = evaluate.immortal_seal([t["id"]])
            trinkets.append({"id": t["id"], "color": colors.index(t["slot"]), "effects": effs,
                             "regen": catalog.trinket_regen(t["id"]) if ctx.gods is not None else 0,
                             "negation": "negation" in t.get("tags", []) or any(e[0] == "plain" and e[1] == "negation" for e in t["effects"]),
                             "seal": list(seal) if seal else None})
        # trinket ability-level keys may have been added after lvl_hits was built
        for k in lvl_keys.names[len(lvl_hits):]:
            hits = [self.spec_idx[s] for s in spec_ids
                    if families.ability_level_bonus([(k, 1.0)], ctx.abilities[s]["skill"], ctx.ability_groups, False, s) > 0]
            lvl_hits.append({"all": k == "all_abilities", "hits": hits, "name": k, "ok_any": k == "all_abilities" or bool(hits)})

        amod_hits = [{"name": k, "hits": [self.spec_idx[s] for s in spec_ids if k == s or k == ctx.abilities[s]["skill"]]}
                     for k in amod_keys.names]

        self.god_names = list(ctx.charms)
        gods = [{"name": g, "mods": [{"a": attr(m["attribute"]), "v": float(m["value"]), "g": group("charm:" + str(m["group"]))}
                                     for m in ctx.charms[g]]} for g in self.god_names]

        self.card_ids = _Interner()
        deck = [[{"card": self.card_ids(cid), "a": attr(o[0]), "c": float(o[1])} for cid, o in opts.items()]
                for opts in ctx.deck_options]
        designs = []
        for d in getattr(ctx, "designs", None) or []:
            opts = []
            for slot in d["opts"]:
                row = []
                for c in slot:
                    gr = c.get("grant")
                    row.append({"card": self.card_ids(c["id"]), "kind": DECK_KINDS[c["kind"]],
                                "a": attr(c["attr"]) if c.get("attr") else -1, "v": float(c["value"]), "g": int(c["g"]),
                                "gk": 0 if not gr else (1 if gr[0] == "ability" else 2),
                                "key": 0 if not gr else (lvl_keys(gr[1]) if gr[0] == "ability" else tal_keys(gr[1])),
                                "lv": 0.0 if not gr else float(gr[2])})
                opts.append(row)
            lim = d["limits"]
            designs.append({
                "slots": [list(p) for p in d["geo"].slots], "t": list(d["types"]), "color": list(d["colors"]),
                "opts": opts,
                "csets": [{"cores": [[CORE_CODE[n], float(v), int(k), int(col)] for (n, v, k, col) in cs["cores"]],
                           "nitwit": float(cs["nitwit"]), "shiny": bool(cs["shiny"]),
                           "nslots": [bool(x) for x in cs["nitwit_slots"]]} for cs in d["csets"]],
                "imps": [[int(k), float(v), int(gm), int(cm), int(pt)] for (k, v, gm, cm, pt) in d["imps"]],
                "greed": float(d["greed"]), "deluxe_base": float(d["deluxe_base"]),
                "limits": [int(lim.get("knack", -1)), int(lim.get("temporal", -1)), int(lim.get("deluxe", -1))],
                "seed": list(d["seed"])})

        talents = []
        for tid in self.talent_ids:
            t = ctx.talents[tid]
            tiers = t.get("tiers", [t])
            cost, acc = [0], 0
            for x in tiers:
                acc += x.get("learnPointCost", 0)
                cost.append(acc)
            recs = []
            for x in tiers:
                a = x.get("attribute")
                recs.append({"type": TALENT_TYPES.get(x.get("type"), 0),
                             "a": attr(a) if isinstance(a, str) else -1,
                             "extra": a == "woldsvaults:additional_stacking_stacks",
                             "v": float(x["value"]) if _num(x.get("value")) else 0.0,
                             "max_stacks": float(x.get("maxStacks", 1)),
                             "di": float(x["damageIncrease"]) if _num(x.get("damageIncrease")) else None,
                             "th": float(x.get("healthThreshold", 0.5)),
                             "dp": float(x["damagePercentage"]) if _num(x.get("damagePercentage")) else None,
                             "pdd": float(x["percentDamageDealt"]) if _num(x.get("percentDamageDealt")) else None,
                             "mhp": float(x["maxHealthPercentage"]) if _num(x.get("maxHealthPercentage")) else None,
                             "ahe": float(x["additionalHealingEfficiency"]) if _num(x.get("additionalHealingEfficiency")) else None,
                             "mmp": float(x["maxManaPercentage"]) if _num(x.get("maxManaPercentage")) else None,
                             "cdd": float(x["cooldownDecrease"]) if _num(x.get("cooldownDecrease")) else None,
                             "ch": float(x["chance"]) if _num(x.get("chance")) else None,
                             "dur": float(x["durationTicks"]) if _num(x.get("durationTicks")) else None,
                             "pr": float(x["probability"]) if _num(x.get("probability")) else None,
                             "nd": bf.blizzard_nova_ticks(ctx, x) if x.get("type") == "cast_on_hit" and tid == "Blizzard" else None,
                             "dr": float(x["damageReduction"]) if _num(x.get("damageReduction")) else None})
            talents.append({"id": tid, "max": ctx.talent_max(tid), "cost": cost, "tiers": recs})

        self.hole_msgs = []
        specs = []
        for s in spec_ids:
            a = ctx.abilities[s]
            tiers = a.get("tiers", [a])
            cost, acc = [0], 0
            for x in tiers:
                acc += x.get("learnPointCost", 0)
                cost.append(acc)
            cfg = {}
            holes = {}
            for variant, bugged in (("bugged", True), ("intended", False)):
                lst, hl = [], []
                for n in range(1, len(tiers) + 1):
                    merged, missing = families.tier_merge(ctx, s, n, bugged)
                    lst.append({k: float(merged[k]) for k in CFG_KEYS if _num(merged.get(k))})
                    if missing:
                        key, msg = families.tier_hole_fallback(s, n, bugged, missing)
                        hl.append([n, len(self.hole_msgs)])
                        self.hole_msgs.append((key, msg))
                cfg[variant] = lst
                holes[variant] = hl
            specs.append({"id": s, "skill": self.skill_idx[a["skill"]], "max": ctx.ability_max(s), "cost": cost,
                          "cfg": cfg, "holes": holes,
                          "baseline_skill": a["skill"] in stages.BASELINE_ABILITIES,
                          "pd_ok": bf.pd_eligible(s, (a.get("tiers") or [a])[0]), **_spec_facts(s)})

        self.greed_ids = [n["id"] for n in ctx.greed_nodes]
        self.greed_idx = {n: i for i, n in enumerate(self.greed_ids)}
        greed = [{"id": n["id"], "parents": [self.greed_idx[p] for p in ctx.greed_parents(n["id"]) if p in self.greed_idx],
                  "free": ctx.greed_free(n["id"]), "sp": float(n.get("skillPoints", 0)),
                  "entries": [[attr(e["attribute"]), float(e["value"])] for e in n.get("entries", [])]}
                 for n in ctx.greed_nodes]

        p_adds, masterful, berserk, swift = [], False, False, 1.0
        for p in ctx.prestige:
            if p.get("type") == "movement_speed_multiplier_power" and _num(p.get("multiplier")):
                swift = float(p["multiplier"])
            if p.get("type") == "gear_attribute_power" and isinstance(p.get("value"), (int, float)):
                p_adds.append([attr(p["attribute"]), float(p["value"])])
            if p.get("type") == "masterful_power":
                masterful = True
            if p.get("type") == "berserk_power":
                berserk = True

        for name in ("the_vault:attack_damage", "minecraft:generic.attack_damage", "minecraft:generic.max_health",
                     "the_vault:generic.mana_max", "the_vault:generic.mana_regen"):
            attr(name)
        godtree = self._god_doc(attr) if ctx.gods is not None else None
        caps = catalog.load()["caps"]
        st = ctx.stage
        self.doc = {
            "attrs": attr.names, "n_groups": len(group.names), "affixes": affixes, "etchings": etchings,
            "lvl_keys": lvl_hits, "tal_keys": tal_key_rec, "types": types,
            "offhand_types": [self.type_idx[t] for t in ctx.offhand_types],
            "necklace_type": self.type_idx["vault_necklace"],
            "armor_types": [self.type_idx[t] for t in catalog.ARMOR],
            "trinkets": trinkets, "trinket_slots": [st["trinket_slots"][c] for c in colors],
            "trinket_fusion": bool(st.get("trinket_fusion")),
            "gods": gods, "charm_prefixes": st["charm_prefixes"], "deck": deck, "designs": designs,
            "talents": talents,
            "gates": [{"t": self.talent_idx[t], "spent": float(g["spent"]),
                       "deps": [self.talent_idx.get(x, -1) for x in g["deps"]],
                       "either": [[self.talent_idx[x] for x in grp if x in self.talent_idx] for grp in g["either"]],
                       "locked": [self.talent_idx[x] for x in g["locked"] if x in self.talent_idx]}
                      for t, g in ctx.gates.items() if t in self.talent_idx],
            "amod_keys": amod_hits, "uniques": uniques, "unique_seals": unique_seals,
            "unique_max": st["unique_max"], "unique_seals_max": st["unique_seals"],
            "heal_specs": [self.spec_idx[s] for s in sorted(spec_ids) if ctx.abilities[s]["skill"] == "Heal"],
            "specs": specs, "weapon_combos": ab.WEAPON_COMBOS, "buff_specs": [self.spec_idx[s] for s in bf.BUFF_SPECS if s in self.spec_idx],
            "sec_specs": [self.spec_idx[s] for s in families.secondary_specs() if s in self.spec_idx],
            "greed": greed, "greed_budget": ctx.greed_budget,
            "prestige": {"adds": p_adds, "masterful": masterful, "berserk": berserk, "swiftness": swift,
                         "regen": catalog.prestige_regen(ctx.prestige) if ctx.gods is not None else 0},
            "knobs": {k: float(v) for k, v in ctx.knobs.items() if _num(v) or isinstance(v, bool)},
            "caps": {"lucky": float(caps.get("luckyHitCap", 0.562)), "aoe": float(caps.get("aoeCap", 0.8))},
            "hyper": {k: float(v) for k, v in stages.HYPER.items()},
            "boss_grid": _grid_doc(ctx),
            "seals": st["seals"], "unusual": st["unusual"], "mode_bugged": ctx.mode == "bugged",
            "talent_names": self.talent_ids, "spec_names": spec_ids,
            "godtree": godtree, "ancient_max": int(getattr(ctx, "ancient_max", 0) or 0),
            "boots_mj": affix(ctx.boots_mj) if getattr(ctx, "boots_mj", None) is not None else -1,
            "feather": self.trinket_idx.get(catalog.FEATHER, -1) if getattr(ctx, "boots_mj", None) is not None else -1,
            "boots_slot": SLOT_ORDER.index("boots"), "clouds": clouds, "echo": _echo_doc(), "echo_reverb": _echo_doc("v_reverb"),
            "fang_table": _fang_doc(), "dice_z": evaluate.dice_z(ctx.knobs["dice_percentile"]),
        }
        self.doc["contract"] = contract_lists(self.doc["knobs"])
        self._bytes = json.dumps(self.doc, separators=(",", ":")).encode()
        self.digest = hashlib.sha1(self._bytes).hexdigest()

    def family_doc(self, fam):
        ctx = self.ctx
        doc = {"id": fam.id, "mainhands": [self.type_idx[t] for t in ctx.mainhands(fam)]}
        if fam.kind == "melee":
            doc.update(kind="melee", weapon=fam.weapon, main=-1)
        elif fam.kind == "ranged":
            doc.update(kind="rang", weapon=fam.weapon, main=-1)
        else:
            doc.update(kind="ability", main=self.spec_idx[fam.main_spec])
        from .search import baseline_specs
        doc["baseline"] = [[self.spec_idx[s], n] for s, n in baseline_specs(ctx, fam).items()]
        return doc

    def build_doc(self, b):
        items = []
        for slot in SLOT_ORDER:
            it = b.items[slot]
            keys = self.implicit_keys[it.type]
            items.append({"type": self.type_idx[it.type],
                          "implicits": [self.affix_idx[id(it.implicits[g])] for g in keys],
                          "prefixes": [self.affix_idx[id(a)] for a in it.prefixes],
                          "suffixes": [self.affix_idx[id(a)] for a in it.suffixes],
                          "seal": self.affix_idx[id(it.seal)] if it.seal else None,
                          "unusual": self.affix_idx[id(it.unusual)] if it.unusual else None,
                          "etching": self.etch_idx[it.etching["id"]] if it.etching else None,
                          "unique": self.unique_idx[it.unique] if it.unique is not None else None,
                          "udrop": it.udrop,
                          "shadow": ([self.affix_idx[id(it.shadow[0])] if it.shadow[0] else None,
                                      self.etch_idx[it.shadow[1]["id"]] if it.shadow[1] else None,
                                      self.affix_idx[id(it.shadow[2])] if it.shadow[2] else None]
                                     if it.unique is not None else None)})
        god = self.god_names.index(b.charm["god"])
        mods = [next(j for j, m in enumerate(self.ctx.charms[b.charm["god"]]) if m is x) for x in b.charm["mods"]]
        mask = 0
        for i, bid in enumerate(BUG_IDS):
            if bug(b, bid):
                mask |= 1 << i
        return {"items": items, "trinkets": [self.trinket_idx[t] for t in b.trinkets], "god": god, "mods": mods,
                "deck": [self.card_ids.ids[c] for c in b.deck],
                "talents": [int(b.talents.get(t, 0)) for t in self.talent_ids],
                "abilities": [int(b.abilities.get(s, 0)) for s in self.spec_ids],
                "greed": sorted(self.greed_idx[g] for g in b.greed), "bugs": mask, "weave": bool(getattr(b, "weave", False)),
                "pd": sorted(self.spec_idx[s] for s in getattr(b, "pd", set()) if s in self.spec_idx),
                "pd_nova": "Starforge_Nova" in getattr(b, "pd", set()),
                "gods": self._god_state_doc(b),
                "design": int(getattr(b, "design", 0)), "cset": int(getattr(b, "cset", 0)),
                "dk": [int(x) for x in getattr(b, "dk", [])]}

    def to_build(self, doc, fam):
        ctx = self.ctx
        b = Build(ctx.stage_name, ctx.stage, ctx.mode, fam.id)
        for slot, d in zip(SLOT_ORDER, doc["items"]):
            t = self.type_names[d["type"]]
            it = Item(slot, t)
            for g, ai in zip(self.implicit_keys[t], d["implicits"]):
                it.implicits[g] = self.affix_objs[ai]
            it.prefixes = [self.affix_objs[i] for i in d["prefixes"]]
            it.suffixes = [self.affix_objs[i] for i in d["suffixes"]]
            it.seal = self.affix_objs[d["seal"]] if d["seal"] is not None else None
            it.unusual = self.affix_objs[d["unusual"]] if d["unusual"] is not None else None
            if d["etching"] is not None:
                it.etching = self._etching(t, d["etching"])
            if d.get("unique") is not None:
                u = ctx.unique_by_id[self.unique_ids[d["unique"]]]
                it.unique, it.uaffixes, it.udrop = u["id"], u["affixes"], d.get("udrop")
                sh = d["shadow"]
                it.shadow = (self.affix_objs[sh[0]] if sh[0] is not None else None,
                             self._etching(t, sh[1]) if sh[1] is not None else None,
                             self.affix_objs[sh[2]] if sh[2] is not None else None)
            b.items[slot] = it
        b.trinkets = [self.trinket_ids[i] for i in doc["trinkets"]]
        god = self.god_names[doc["god"]]
        b.charm = {"god": god, "mods": [ctx.charms[god][j] for j in doc["mods"]]}
        b.deck = [self.card_ids.names[i] for i in doc["deck"]]
        b.design, b.cset, b.dk = int(doc.get("design", 0)), int(doc.get("cset", 0)), list(doc.get("dk", []))
        b.design_scores = doc.get("design_scores")
        b.talents = {t: n for t, n in zip(self.talent_ids, doc["talents"]) if n > 0}
        b.abilities = {s: n for s, n in zip(self.spec_ids, doc["abilities"]) if n > 0}
        b.greed = {self.greed_ids[i] for i in doc["greed"]}
        b.prestige = ctx.prestige
        b.weave = bool(doc.get("weave", False))
        b.pd = {self.spec_ids[i] for i in doc.get("pd", [])} | ({"Starforge_Nova"} if doc.get("pd_nova") else set())
        if ctx.gods is not None:
            gd = doc["gods"]
            gs = godmod.empty_state()
            for gi, g in enumerate(godmod.GODS):
                t = ctx.gods.trees[g]
                gs["levels"][g] = gd["levels"][gi]
                gs["nodes"][g] = set(gd["nodes"][gi])
                gs["mts"][g] = [self.god_effect_ids[e] for e in gd["mts"][gi]]
            b.gods = gs
        return b

    def _god_doc(self, attr):
        """God trees for the kernel: per god its local graph; a flat effect table (custom handlers by name, numeric
        params only); the level / points curve, pool, reputation, rank gate and the time-lock data."""
        G = self.ctx.gods
        self.god_effect_ids, self.god_effect_idx = [], {}
        trees, effects = [], []
        off = 0
        for gi, g in enumerate(godmod.GODS):
            t = G.trees[g]
            for e in t.effects:
                if e in self.god_effect_idx:
                    continue
                d = t.effects[e]
                typ = t.effect_type.get(e)
                if typ is None:
                    continue
                self.god_effect_idx[e] = len(self.god_effect_ids)
                self.god_effect_ids.append(e)
                ga = d.get("handler") == "gear_attribute_scaled"
                effects.append({
                    "god": gi, "ty": ["root", "stat", "minor", "major"].index(typ), "ga": ga,
                    "a": attr(d["attribute"]) if ga and d.get("attribute") else -1,
                    "a2": attr(d["secondAttribute"]) if ga and d.get("secondAttribute") else -1,
                    "values": [float(x) for x in d.get("values") or []],
                    "minor_node": t.minor_node.get(e, -1), "pious": e.endswith("_pious_devotion"),
                    "handler": d.get("handler") or "",
                    "params": {k: float(v) for k, v in d.items() if _num(v) and k not in ("values",)},
                    "adds": [[attr(a), float(v)] for a, v in godfx.adds(e, d)],
                    "node": next(i for i, x in enumerate(t.effect) if x == e),
                })
            trees.append({"off": off, "n": len(t.nodes), "ty": [["root", "stat", "minor", "major"].index(x) for x in t.type],
                          "eff": [self.god_effect_idx.get(e, 0) for e in t.effect], "usable": list(t.usable),
                          "adj": [sorted(a) for a in t.adj], "roots": list(t.roots)})
            off += len(t.nodes)
        ctx = self.ctx
        return {"trees": trees, "effects": effects, "cum": [float(x) for x in G.cum], "max_defined": G.max_defined,
                "past_max": float(G.past_max), "p1": G.p1, "p_to": G.p_to, "p_past": G.p_past,
                "mts_levels": list(G.mts_levels), "rep": G.rep, "rep_per_point": G.rep_per_point, "pool": float(G.pool),
                "rank_gate": G.rank_gate, "max_level": G.max_level,
                "charm_to_god": [godmod.GODS.index(godmod.GOD_OF_CHARM[c]) for c in self.god_names],
                "time_lock_dynamic": bool(ctx.time_lock_dynamic),
                "plushie_type": self.type_idx.get("plushie", -1), "staff_type": self.type_idx.get("battlestaff", -1),
                "tb_effect": self.god_effect_idx.get(godmod.TEMPORAL_BREAKING, -1),
                "names": list(self.god_effect_ids), "fury": [list(map(float, r)) for r in G.fury_table],
                "utility_key": self.utility_key}

    def _god_state_doc(self, b):
        if self.ctx.gods is None or b.gods is None:
            return None
        gs = b.gods
        return {"levels": [gs["levels"][g] for g in godmod.GODS],
                "nodes": [sorted(gs["nodes"][g]) for g in godmod.GODS],
                "mts": [[self.god_effect_idx[e] for e in gs["mts"][g]] for g in godmod.GODS]}

    def _etching(self, gear_type, idx):
        eid = self.etch_objs[idx]["id"]
        return next(e for e in self.ctx.etchings[gear_type] if e["id"] == eid)

    def call(self, fam, payload, exe=None):
        """Run the kernel with this context, a family and a command payload; returns the parsed JSON reply.
        exe: kernel executable to run (default EXE; A/B checks pass another build)."""
        exe = exe or EXE
        if not os.path.exists(exe):
            raise FileNotFoundError(f"Rust kernel not built: {exe} (cargo build --release in kernel/)")
        head = b'{"ctx":' + self._bytes + b',"family":' + json.dumps(self.family_doc(fam)).encode() + b',"cmd":'
        data = head + json.dumps(payload).encode() + b"}"
        proc = subprocess.run([exe], input=data, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"kernel failed ({proc.returncode}): {proc.stderr.decode(errors='replace')[-2000:]}")
        out = json.loads(proc.stdout)
        self._log_fallbacks(out.get("fallbacks", {}))
        return out

    def _log_fallbacks(self, fb):
        for key, rec in fb.items():
            if key.startswith("hole:"):
                k, msg = self.hole_msgs[int(key[5:])]
                fallback_n(k, msg, 1)
            elif key.startswith("unaffordable:"):
                sid = self.spec_ids[int(key[13:])]
                fallback_n(f"cast-unaffordable-{sid}", f"(kernel) {sid} costs more mana than the bar holds; never cast", rec["n"])
            else:
                fallback_n(key, f"(kernel) {rec['msg']}", rec["n"])


def _grid_doc(ctx):
    from .evaluate import boss_grid
    g = boss_grid(ctx)
    return {"c0": g["c0"], "step": g["step"], "H": g["H"], "D": g["D"], "M": g["M"], "DF": g["DF"], "DS": g["DS"]}


_STACKING = None


def _stacking():
    global _STACKING
    if _STACKING is None:
        from .search import STACKING_ETCHINGS
        _STACKING = STACKING_ETCHINGS
    return _STACKING


_exports = {}


def export_for(ctx):
    key = id(ctx)
    if key not in _exports:
        _exports[key] = Export(ctx)
    return _exports[key]


def anneal(ctx, fam, iters, seeds, schedule=None, trace=False, legacy=False):
    """Run one kernel anneal per seed; returns [(score, Build, info)] in seed order."""
    ex = export_for(ctx)
    cmd = {"op": "anneal", "iters": int(iters), "seeds": [int(s) for s in seeds],
           "schedule": schedule or DEFAULT_SCHEDULE, "trace": bool(trace), "legacy": bool(legacy)}
    out = ex.call(fam, cmd)
    return [(r["score"], ex.to_build(r["build"], fam), r) for r in out["results"]]


def batch_anneal(jobs, threads=0, verify_delta=False):
    """Run many anneals in one kernel process on a thread pool over all cores.

    jobs: [(ctx, fam, iters, seed, schedule, legacy[, init_doc, force_eot])]. Returns [(score, build_doc, info)] in job
    order; build docs are mapped back with Export.to_build (in this process or any process that rebuilt the same
    Context). init_doc starts the anneal from that build (rework Edge of Time pass) instead of a random one.
    verify_delta re-scores every delta-scored candidate in full (info["delta"]: checked, mismatch, max_err).
    """
    exports, ctx_idx, fam_docs, fam_idx, job_docs = [], {}, [], {}, []
    for job in jobs:
        ctx, fam, iters, seed, schedule, legacy = job[:6]
        init, force = (job[6], job[7]) if len(job) > 6 else (None, False)
        stratum = job[8] if len(job) > 8 else -1
        opts = job[9] if len(job) > 9 else None
        ulock = job[10] if len(job) > 10 else None
        deckjob = job[11] if len(job) > 11 else None
        ex = export_for(ctx)
        if id(ex) not in ctx_idx:
            ctx_idx[id(ex)] = len(exports)
            exports.append(ex)
        key = (id(ex), fam.id)
        if key not in fam_idx:
            fam_idx[key] = len(fam_docs)
            fam_docs.append(ex.family_doc(fam))
        job_docs.append({"ctx": ctx_idx[id(ex)], "family": fam_idx[key], "iters": int(iters), "seed": int(seed),
                         "schedule": schedule or DEFAULT_SCHEDULE, "legacy": bool(legacy)})
        if verify_delta:
            job_docs[-1]["verify_delta"] = True
        if init is not None:
            job_docs[-1].update(init=init, force_eot=bool(force))
        if stratum >= 0:
            job_docs[-1]["stratum"] = int(stratum)
        if opts:
            job_docs[-1]["opts"] = opts
        if ulock is not None:
            slot, uid = ulock
            job_docs[-1]["ulock"] = [SLOT_ORDER.index(slot), ex.unique_idx[uid]]
        if deckjob:
            job_docs[-1].update(design=int(deckjob.get("design", -1)), designs=[int(d) for d in deckjob.get("designs", [])],
                                design_lock=bool(deckjob.get("design_lock", False)))
    if not os.path.exists(EXE):
        raise FileNotFoundError(f"Rust kernel not built: {EXE} (cargo build --release in kernel/)")
    data = (b'{"ctxs":[' + b",".join(ex._bytes for ex in exports) + b'],"families":' + json.dumps(fam_docs).encode()
            + b',"jobs":' + json.dumps(job_docs).encode() + b',"threads":' + str(int(threads)).encode() + b"}")
    proc = subprocess.run([EXE], input=data, stdout=subprocess.PIPE, stderr=None)
    if proc.returncode != 0:
        raise RuntimeError(f"kernel batch failed with exit code {proc.returncode}")
    out = json.loads(proc.stdout)
    for ex, fb in zip(exports, out["fallbacks"]):
        ex._log_fallbacks(fb)
    print(f"[kernel] {len(job_docs)} anneals on {out['threads']} threads in {out['secs']:.1f}s", file=sys.stderr, flush=True)
    return [(r["score"], r["build"], r) for r in out["results"]]


def evaluate_many(ctx, fam, builds, exe=None):
    """Kernel scores for Python builds (parity checks); exe as in Export.call."""
    ex = export_for(ctx)
    out = ex.call(fam, {"op": "eval", "builds": [ex.build_doc(b) for b in builds]}, exe=exe)
    return out["results"]
