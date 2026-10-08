"""Bridge to the Rust search kernel (libs/buildkernel/): flatten a Context into index tables, run the kernel, map builds back.

The kernel re-implements evaluate() and the annealing search; model/*.py stays the reference implementation and
every build the kernel returns is re-scored here (a mismatch is logged as the kernel-parity fallback).
"""
import hashlib
import json
import os
import subprocess
import sys

from . import catalog, stages, families
from . import abilities as ab
from .build import Build, Item
from .damage import bug
from .log import fallback, fallback_n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "..", "..", "libs", "buildkernel", "target", "release", "wvk.exe" if os.name == "nt" else "wvk")

BUG_IDS = ["ICEBOLT-DI2", "ECHO-FLAGLOSS", "EXEC-GEAR", "ORDER-FLATADD", "FANG-RESIDUAL", "VULN-OFF1",
           "VOLLEY-BOUNCE-EXPLODE", "ICEBOLT-T12-HOLE", "OFFHAND-STAFF", "MANA-CAP"]

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
    "amplifier", "additionalResistance", "flatLifeHealed", "additionalThornsDamagePercent",
]

ETCH_QUERIES = [
    "etching_nova_recast", "etching_smite_echo", "etching_arcane_pierce", "etching_lightning_orb_triple_damage",
    "etching_ice_bolt_lucky", "etching_ice_bolt_multicast", "etching_extra_piercing_javelin", "ravenous_fangs",
    "etching_life_tap_extra_damage", "etching_shield_bash_damage", "woldsvaults:fireball_volley_mitosis",
    "etching_totem_mob_damage_ad", "etching_lucky_vulnerable", "etching_totem_player_damage_effect",
    "etching_rampage_lucky_hit",
]

TALENT_TYPES = {"gear_attribute": 1, "high_health_gear_attribute": 1, "stack_on_hit_talent": 2,
                "stacking_gear_attribute": 3, "mind_meld": 4, "damage_lucky_hit": 5, "execution_lucky_hit": 6,
                "low_mana_healing_efficiency": 7, "health_leech_lucky_hit": 8}
AMOD_KINDS = {"cooldown": 0, "mana": 1}
FLAG_KEYS = ["lucky_thorns", "safer_space", "bloodthirst"]
SPECIAL_KEYS = ["frost_nova_vulnerability", "fireball_recast"]

DEFAULT_SCHEDULE = {"kind": "geometric", "t0": 0.6, "t1": 0.004}

SLOT_ORDER = catalog.ARMOR + ["mainhand", "offhand", "necklace"]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
        spec_ids = [s for s in dict.fromkeys(list(ab.MODELS) + families.BUFF_SPECS
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

        def affix(a):
            if id(a) in self.affix_idx:
                return self.affix_idx[id(a)]
            raw = a.raw if isinstance(a.raw, dict) else None
            rec = {"k": 4, "a": 0, "v": 0.0, "g": group(a.group), "key": 0, "lc": 0.0, "amp": 0}
            if a.attribute == "the_vault:added_ability_level" and raw is not None:
                rec.update(k=1, key=lvl_keys(raw.get("abilityKey", "")), lc=float(raw.get("levelChange", 0)))
            elif a.attribute == "the_vault:added_talent_level" and raw is not None:
                rec.update(k=2, key=tal_keys(raw.get("talentKey", "")), lc=float(raw.get("levelChange", 0)))
            elif a.attribute == "the_vault:effect" and raw is not None:
                if raw.get("effectKey") == "minecraft:luck":
                    rec.update(k=3, amp=int(raw.get("amplifier", 0)) + 1)
            elif a.attribute in catalog.PRESENCE_FLAGS:
                rec.update(k=5, key=FLAG_KEYS.index(catalog.PRESENCE_FLAGS[a.attribute]))
            elif a.attribute in catalog.FLAG_ATTRS:
                if raw is not None and raw.get("flag"):
                    rec.update(k=5, key=FLAG_KEYS.index(catalog.short(a.attribute)))
            elif a.attribute == "the_vault:unique_effect":
                name = catalog.EFFECT_FLAGS.get(raw.get("effectKey")) if raw is not None else None
                if name:
                    rec.update(k=5, key=FLAG_KEYS.index(name))
            elif a.attribute == "the_vault:ability_special_modification":
                name = catalog.SPECIAL_MODS.get(raw.get("specialModificationKey")) if raw is not None else None
                if name and a.value is not None:
                    rec.update(k=7, key=SPECIAL_KEYS.index(name), v=float(a.value))
            elif a.attribute in catalog.ABILITY_MODS and raw is not None and "abilityKey" in raw:
                rec.update(k=6, key=amod_keys(raw["abilityKey"]), amp=AMOD_KINDS[catalog.ABILITY_MODS[a.attribute]],
                           v=float(a.value))
            elif a.value is not None:
                rec.update(k=0, a=attr(a.attribute), v=float(a.value))
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
            else:
                num = None
            raw = e.get("raw")
            tl = None
            if isinstance(raw, dict) and "talentKey" in raw:
                tl = [tal_keys(raw["talentKey"]), float(raw.get("levelChange", 0))]
            q = [i for i, name in enumerate(ETCH_QUERIES) if e["attribute"] in ("the_vault:" + name, name)]
            self.etch_idx[e["id"]] = len(etchings)
            self.etch_objs.append(e)
            etchings.append({"id": e["id"], "num": num, "truthy": bool(val), "q": q, "tl": tl,
                             "stacking": e["attribute"].split(":")[-1] in _stacking()})
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
                                "affixes": [affix(a) for a in u["affixes"]], "explicit": list(u["explicit"])})
        self.unique_idx = {u: i for i, u in enumerate(self.unique_ids)}
        unique_seals = [affix(a) for a in ctx.unique_seals]

        lvl_hits = []
        for k in lvl_keys.names:
            hits = []
            for s in spec_ids:
                if families.ability_level_bonus([(k, 1.0)], ctx.abilities[s]["skill"], ctx.ability_groups, False) > 0:
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
            trinkets.append({"id": t["id"], "color": colors.index(t["slot"]), "effects": effs})
        # trinket ability-level keys may have been added after lvl_hits was built
        for k in lvl_keys.names[len(lvl_hits):]:
            hits = [self.spec_idx[s] for s in spec_ids
                    if families.ability_level_bonus([(k, 1.0)], ctx.abilities[s]["skill"], ctx.ability_groups, False) > 0]
            lvl_hits.append({"all": k == "all_abilities", "hits": hits, "name": k, "ok_any": k == "all_abilities" or bool(hits)})

        amod_hits = [{"name": k, "hits": [self.spec_idx[s] for s in spec_ids if k == s or k == ctx.abilities[s]["skill"]]}
                     for k in amod_keys.names]

        self.god_names = list(ctx.charms)
        gods = [{"name": g, "mods": [{"a": attr(m["attribute"]), "v": float(m["value"]), "g": group("charm:" + str(m["group"]))}
                                     for m in ctx.charms[g]]} for g in self.god_names]

        self.card_ids = _Interner()
        deck = [[{"card": self.card_ids(cid), "a": attr(o[0]), "c": float(o[1])} for cid, o in opts.items()]
                for opts in ctx.deck_options]

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
                             "mmp": float(x["maxManaPercentage"]) if _num(x.get("maxManaPercentage")) else None})
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
                          "baseline_skill": a["skill"] in stages.BASELINE_ABILITIES})

        self.greed_ids = [n["id"] for n in ctx.greed_nodes]
        self.greed_idx = {n: i for i, n in enumerate(self.greed_ids)}
        greed = [{"id": n["id"], "parents": [self.greed_idx[p] for p in ctx.greed_parents(n["id"]) if p in self.greed_idx],
                  "free": ctx.greed_free(n["id"]), "sp": float(n.get("skillPoints", 0)),
                  "entries": [[attr(e["attribute"]), float(e["value"])] for e in n.get("entries", [])]}
                 for n in ctx.greed_nodes]

        p_adds, masterful, berserk = [], False, False
        for p in ctx.prestige:
            if p.get("type") == "gear_attribute_power" and isinstance(p.get("value"), (int, float)):
                p_adds.append([attr(p["attribute"]), float(p["value"])])
            if p.get("type") == "masterful_power":
                masterful = True
            if p.get("type") == "berserk_power":
                berserk = True

        for name in ("the_vault:attack_damage", "minecraft:generic.attack_damage", "minecraft:generic.max_health",
                     "the_vault:generic.mana_max", "the_vault:generic.mana_regen"):
            attr(name)
        caps = catalog.load()["caps"]
        st = ctx.stage
        self.doc = {
            "attrs": attr.names, "n_groups": len(group.names), "affixes": affixes, "etchings": etchings,
            "etch_queries": ETCH_QUERIES, "cfg_keys": CFG_KEYS, "bug_ids": BUG_IDS,
            "lvl_keys": lvl_hits, "tal_keys": tal_key_rec, "types": types,
            "offhand_types": [self.type_idx[t] for t in ctx.offhand_types],
            "necklace_type": self.type_idx["vault_necklace"],
            "armor_types": [self.type_idx[t] for t in catalog.ARMOR],
            "trinkets": trinkets, "trinket_slots": [st["trinket_slots"][c] for c in colors],
            "trinket_fusion": bool(st.get("trinket_fusion")),
            "gods": gods, "charm_prefixes": st["charm_prefixes"], "deck": deck,
            "talents": talents,
            "gates": [{"t": self.talent_idx[t], "spent": float(g["spent"]),
                       "deps": [self.talent_idx.get(x, -1) for x in g["deps"]],
                       "either": [[self.talent_idx[x] for x in grp if x in self.talent_idx] for grp in g["either"]],
                       "locked": [self.talent_idx[x] for x in g["locked"] if x in self.talent_idx]}
                      for t, g in ctx.gates.items() if t in self.talent_idx],
            "amod_keys": amod_hits, "uniques": uniques, "unique_seals": unique_seals,
            "unique_max": st["unique_max"], "unique_seals_max": st["unique_seals"],
            "heal_specs": [self.spec_idx[s] for s in sorted(spec_ids) if ctx.abilities[s]["skill"] == "Heal"],
            "leech_spec": [s in ab.LEECH_SPECS for s in spec_ids], "lucky_spec": [s in ab.LUCKY_SPECS for s in spec_ids],
            "specs": specs, "buff_specs": [self.spec_idx[s] for s in families.BUFF_SPECS if s in self.spec_idx],
            "greed": greed, "greed_budget": ctx.greed_budget,
            "prestige": {"adds": p_adds, "masterful": masterful, "berserk": berserk},
            "knobs": {k: float(v) for k, v in ctx.knobs.items() if _num(v) or isinstance(v, bool)},
            "caps": {"lucky": float(caps.get("luckyHitCap", 0.562)), "aoe": float(caps.get("aoeCap", 0.8))},
            "hyper": {k: float(v) for k, v in stages.HYPER.items()},
            "boss_grid": _grid_doc(ctx),
            "seals": st["seals"], "unusual": st["unusual"], "mode_bugged": ctx.mode == "bugged",
            "talent_names": self.talent_ids, "spec_names": spec_ids,
        }
        self._bytes = json.dumps(self.doc, separators=(",", ":")).encode()
        self.digest = hashlib.sha1(self._bytes).hexdigest()

    def family_doc(self, fam):
        ctx = self.ctx
        doc = {"id": fam.id, "mainhands": [self.type_idx[t] for t in ctx.mainhands(fam)]}
        if fam.kind == "melee":
            doc.update(kind="melee", weapon=fam.weapon, main=-1)
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
                "greed": sorted(self.greed_idx[g] for g in b.greed), "bugs": mask, "weave": bool(getattr(b, "weave", False))}

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
        b.talents = {t: n for t, n in zip(self.talent_ids, doc["talents"]) if n > 0}
        b.abilities = {s: n for s, n in zip(self.spec_ids, doc["abilities"]) if n > 0}
        b.greed = {self.greed_ids[i] for i in doc["greed"]}
        b.prestige = ctx.prestige
        b.weave = bool(doc.get("weave", False))
        return b

    def _etching(self, gear_type, idx):
        eid = self.etch_objs[idx]["id"]
        return next(e for e in self.ctx.etchings[gear_type] if e["id"] == eid)

    def call(self, fam, payload):
        """Run the kernel with this context, a family and a command payload; returns the parsed JSON reply."""
        if not os.path.exists(EXE):
            raise FileNotFoundError(f"Rust kernel not built: {EXE} (cargo build --release in libs/buildkernel/)")
        head = b'{"ctx":' + self._bytes + b',"family":' + json.dumps(self.family_doc(fam)).encode() + b',"cmd":'
        data = head + json.dumps(payload).encode() + b"}"
        proc = subprocess.run([EXE], input=data, capture_output=True)
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
            else:
                fallback_n(key, f"(kernel) {rec['msg']}", rec["n"])


def _grid_doc(ctx):
    from .evaluate import boss_grid
    g = boss_grid(ctx)
    return {"c0": g["c0"], "step": g["step"], "H": g["H"], "D": g["D"], "M": g["M"]}


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


def batch_anneal(jobs, threads=0):
    """Run many anneals in one kernel process on a thread pool over all cores.

    jobs: [(ctx, fam, iters, seed, schedule, legacy)]. Returns [(score, build_doc, info)] in job order; build docs are
    mapped back with Export.to_build (in this process or any process that rebuilt the same Context).
    """
    exports, ctx_idx, fam_docs, fam_idx, job_docs = [], {}, [], {}, []
    for ctx, fam, iters, seed, schedule, legacy in jobs:
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
    if not os.path.exists(EXE):
        raise FileNotFoundError(f"Rust kernel not built: {EXE} (cargo build --release in libs/buildkernel/)")
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


def evaluate_many(ctx, fam, builds):
    """Kernel scores for Python builds (parity checks)."""
    ex = export_for(ctx)
    out = ex.call(fam, {"op": "eval", "builds": [ex.build_doc(b) for b in builds]})
    return out["results"]
