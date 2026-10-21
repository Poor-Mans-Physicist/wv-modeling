"""Render out/results.json into the single-file build ledger HTML (in-game styled build viewer)."""
import json
import os
import re
import struct
import sys
from decimal import ROUND_HALF_UP, Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import catalog, stages  # noqa: E402

REWORK = stages.LEDGER == "rework"
SRC = os.path.join(ROOT, "out", "results_rework.json" if REWORK else "results.json")
GOD_ASSETS = os.path.join(ROOT, "data", "god_assets.json")
ASSETS = os.path.join(ROOT, "data", "assets.json")
TEMPLATE = os.path.join(ROOT, "report", "template.html")
MODEL3D = os.path.join(ROOT, "report", "js", "model3d.js")
NODE_NOTES = os.path.join(ROOT, "report", "node_notes.json")
UNIQUE_BENCH = os.path.join(ROOT, "out", "unique_bench_rework.json")
DEFAULT_OUT = (os.path.join(ROOT, "out", "build_explorer_rework.html") if REWORK
               else os.path.join(ROOT, "out", "build_explorer.html"))

DROP_SOURCES = {"the_vault:item_quantity", "the_vault:item_rarity", "the_vault:soul_chance", "the_vault:mining_speed_percent",
                "the_vault:fruit_effectiveness", "the_vault:movement_speed", "the_vault:knockback_resistance",
                "the_vault:attack_range", "the_vault:hit_hearts"}
if REWORK:
    # Unstoppable Greed and Kinetic Impact read these in the rework, so keep their source lists.
    DROP_SOURCES -= {"the_vault:item_quantity", "the_vault:item_rarity", "the_vault:movement_speed"}


def rnd(x, sig=5):
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return None
        if x == 0:
            return 0
        return float(f"{x:.{sig}g}")
    if isinstance(x, list):
        return [rnd(v, sig) for v in x]
    if isinstance(x, dict):
        return {k: rnd(v, sig) for k, v in x.items()}
    return x


AD_ONLY_FLAGS = ("Concentrate", "Berserking", "Depleted")


def relevant_flags(b):
    ap = str(b.get("scaling", "")).startswith(("AP", "Mana", "HP"))
    out = []
    for f in b.get("flags", []):
        if ap and any(x in f for x in AD_ONLY_FLAGS):
            continue
        if not ap and "Blood_Chakra" in f:
            continue
        out.append(f)
    return out


def slim(b):
    """One build for the page: relevant flags, combat sources, rounded numbers. A rotation row's unlimited reach is
    kept as "inf", since rnd() maps infinities to None."""
    b = dict(b)
    b["flags"] = relevant_flags(b)
    b["sources"] = {k: v for k, v in b["sources"].items() if k not in DROP_SOURCES}
    if isinstance(b.get("rotation"), list):
        b["rotation"] = [dict(r, reach="inf") if isinstance(r, dict) and r.get("reach") == float("inf") else r for r in b["rotation"]]
    if isinstance(b.get("deck"), dict) and b["deck"].get("menu"):
        b["deck"] = slim_menu_deck(b["deck"])
    return rnd(b)


def slim_menu_deck(d):
    """A menu deck without what the Decks view already carries per design (DeckFAST's seed cards, group scores, the
    other core sets), and without empty fields on greed and empty cells."""
    d = dict(d)
    d["n_core_sets"] = len(d.pop("core_sets", []) or [1])
    for k in ("score_by_group", "implicits"):
        d.pop(k, None)
    cells = []
    for c in d["cells"]:
        c = {k: v for k, v in c.items() if k not in ("seed", "seed_kind") and v not in (None, [], "")}
        if c.get("kind") in ("dead", "greed"):
            c = {k: c[k] for k in ("r", "c", "t", "color", "kind") if k in c}
        if c.get("parts"):
            c["parts"] = rnd(c["parts"], 4)
        cells.append(c)
    d["cells"] = cells
    return d


def spec_facts():
    """Per-spec facts for the Damage tab's flowchart (data/spec_table.json): label, reach rule, i-frame class, buff
    kind and the boss debuffs a cast can apply. The flowchart falls back to these when a result predates the per-row
    reach and debuff fields."""
    from model.abilities import SPEC_TABLE
    from model.abilities import free_iframes
    return {sid: {"label": r["label"], "reach": r.get("reach"), "iframe": "free" if free_iframes(sid) else (r.get("hit") or {}).get("iframe"),
                  "buff": r.get("buff"), "debuffs": [[k, label] for k, _, label in r.get("cast_debuffs") or []]}
            for sid, r in SPEC_TABLE.items()}


def ui_data():
    with open(ASSETS, encoding="utf-8") as f:
        assets = json.load(f)
    cat = catalog.load()
    gui = assets["gui"]
    abil = catalog.abilities()
    skills = []
    for sid, st in gui["abilities"]["styles"].items():
        specs = []
        for spec, ss in st.get("specializationStyles", {}).items():
            a = abil.get(spec)
            specs.append({"id": spec, "max": (a or {}).get("maxLearnableTier", len((a or {}).get("tiers", [])))})
        skills.append({"id": sid, "x": st.get("x", 0), "y": st.get("y", 0), "specs": specs})
    talents_cat = {t["id"]: t for t in cat["talents"]}
    talents = []
    for tid, st in gui["talents"]["styles"].items():
        t = talents_cat.get(tid, {})
        talents.append({"id": tid, "x": st.get("x", 0), "y": st.get("y", 0), "frame": st.get("frameType", "RECTANGULAR"),
                        "max": t.get("maxLearnableTier", len(t.get("tiers", [t])) if t else 0)})
    gstyles = gui["greed"]["styles"]
    nodes = []
    for n in cat["greed_nodes"]:
        s = gstyles.get(n["id"])
        if not s:
            continue
        parents = n.get("parents") or ([n["parent"]] if n.get("parent") else [])
        nodes.append({"id": n["id"], "name": n.get("name", n["id"]), "x": s["x"], "y": s["y"], "frame": s.get("frameType", "CIRCLE"),
                      "parents": parents, "tier": n.get("tier", 0), "type": n.get("type"), "free": bool(n.get("present")),
                      "entries": n.get("entries", []), "skillPoints": n.get("skillPoints", 0)})
    return {
        "icons": assets["icons"], "attrs": assets["attributes"], "models": assets.get("models", {}),
        "abilityTree": {"skills": skills, "labels": gui["abilities"].get("labels", []), "lines": gui["abilities"].get("lines", [])},
        "talentTree": talents, "greedTree": nodes,
        "abilityInfo": skill_info(abil, ABILITY_DESC_FILES, "data"),
        "talentInfo": skill_info(talents_cat, TALENT_DESC_FILES, "descriptions"),
        "prestigeInfo": skill_info({p["id"]: p for p in cat["prestige"]}, PRESTIGE_DESC_FILES, "descriptions"),
        "godTrees": god_trees(cat) if REWORK else None,
        "nodeNotes": node_notes() if REWORK else None,
        "uniqueBench": unique_bench() if REWORK else None,
        "uniqueCards": unique_cards() if REWORK else None,
        "etchings": etching_catalog() if REWORK else None,
        "specs": spec_facts(),
    }


# Order of a pair/throuple etching's rolls as its generator declares them (the_vault EtchingGearAttributes): %valueA,
# %valueB and %valueC read them in this order, which is not always the order of the config's keys.
ETCH_VALUE_KEYS = {
    "the_vault:etching_glacial_shatter_explosion": ("percentAD", "radius"),
    "the_vault:etching_ice_bolt_effect": ("effect", "chance", "duration"),
    "the_vault:etching_lucky_low_health": ("chance", "healthThreshold"),
    "the_vault:etching_life_tap_lucky_hit": ("chance", "duration"),
    "the_vault:etching_nova_low_mana_damage": ("damageMultiplier", "manaThreshold"),
    "the_vault:etching_stone_fall_stun": ("chance", "duration", "effect"),
    "the_vault:etching_mana_shield_nova": ("ability", "level"),
    "the_vault:etching_dash_warp_nova": ("ability", "level"),
    "the_vault:etching_spirit_walk_ability": ("ability", "level"),
    "the_vault:etching_storm_arrow_blizzard_effect": ("effect", "duration", "level"),
    "the_vault:etching_totem_player_damage_effect": ("effect", "duration", "level"),
    "the_vault:etching_lucky_vulnerable": ("effect", "duration", "level"),
    "the_vault:etching_lucky_vortex": ("radius", "duration"),
}
# Mob effect names as the game's lang file prints them (effect.the_vault.*).
EFFECT_NAMES = {"the_vault:noai": "Mob NoAi", "the_vault:vulnerable": "Vulnerable", "the_vault:glacial_shatter": "Glacial Shatter"}
GEAR_ORDER = ["HELMET", "CHESTPLATE", "LEGGINGS", "BOOTS", "SWORD", "AXE", "WAND", "FOCUS", "SHIELD", "MAGNET", "VOID_STONE", "CHARM"]
_COND = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|!=|>|<|=)\s*(-?\d+(?:\.\d+)?)=(.*)")
_warned = set()


def _warn(key, msg):
    if key not in _warned:
        _warned.add(key)
        print(f"[report][FALLBACK] {msg}")


def _ident(s, i, slash=False):
    j = i
    while j < len(s) and (s[j].isalnum() or s[j] in "_$" or (slash and s[j] == "/")):
        j += 1
    return s[i:j]


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _f32(x):
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def _choice_text(v, case):
    opts = v if isinstance(v, list) else [v]
    if len(opts) > 4:
        return f"one of {len(opts)} " + {"ability": "abilities", "effect": "effects"}.get(case, "options")
    names = []
    for o in opts:
        x = o.get("value") if isinstance(o, dict) else o
        if case == "ability":
            a = catalog.abilities().get(x)
            if not a or not a.get("name"):
                _warn("ability-" + str(x), f"etching text: no ability name for {x}; printed as the id (the game does the same)")
            names.append(a["name"] if a and a.get("name") else str(x))
        elif case == "effect":
            if x not in EFFECT_NAMES:
                _warn("effect-" + str(x), f"etching text: no effect name for {x}; printed as the id")
            names.append(EFFECT_NAMES.get(x, str(x)))
        else:
            names.append(EFFECT_NAMES.get(x, str(x)))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def fill_etching(desc, values):
    """the_vault EtchingHelper.formatDescription: fill %d / %s / %t / %.Nf / %.Nfp placeholders from `values`
    (keys value, valueA, valueB, valueC), apply the {key>n=text} conditionals, then strip colour tags."""
    out, i, n, shown = [], 0, len(desc), {}
    while i < n:
        j = desc.find("%", i)
        if j == -1 or j + 1 >= n:
            out.append(desc[i:])
            break
        out.append(desc[i:j])
        c = desc[j + 1]
        if c == "d":
            key = _ident(desc, j + 2)
            v = values.get(key)
            if _isnum(v):
                out.append(str(int(v)) if float(v).is_integer() else f"{v:g}")
                shown[key] = float(v)
                i = j + 2 + len(key)
                continue
        elif c == "s":
            full = _ident(desc, j + 2, slash=True)
            key, _, case = full.partition("/")
            v = values.get(key)
            if v is not None:
                out.append(_choice_text(v, case))
                i = j + 2 + len(full)
                continue
        elif c == ".":
            k = j + 2
            while k < n and desc[k].isdigit():
                k += 1
            pctg = desc.startswith("fp", k)
            if k > j + 2 and k < n and desc[k] == "f":
                ks = k + (2 if pctg else 1)
                key = _ident(desc, ks)
                v = values.get(key)
                if _isnum(v):
                    x = _f32(v) * (100.0 if pctg else 1.0)
                    out.append(str(Decimal(repr(x)).quantize(Decimal(1).scaleb(-int(desc[j + 2:k])), rounding=ROUND_HALF_UP)))
                    shown[key] = x
                    i = ks + len(key)
                    continue
        elif c == "t":
            key = _ident(desc, j + 2)
            v = values.get(key)
            if _isnum(v):
                s = int(v) // 20
                out.append(str(s))
                shown[key] = float(s)
                i = j + 2 + len(key)
                continue
        out.append("%")
        i = j + 1
    text = "".join(out)

    def cond(m):
        for part in m.group(1).split("|"):
            mm = _COND.fullmatch(part.strip())
            if not mm or mm.group(1) not in shown:
                continue
            a, op, b = shown[mm.group(1)], mm.group(2), float(mm.group(3))
            if {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b, "=": a == b, "!=": a != b}[op]:
                return mm.group(4)
        return ""
    text = re.sub(r"\{([^}]*)\}", cond, text)
    return re.sub(r"<[^>]*>", "", text).strip()


def _value_keys(e):
    """The etching's roll keys in %valueA/B/C order, or None for a single-value or flag etching."""
    attr = e["attributes"][0]
    raw = attr["tiers"][0]["value"] if attr.get("tiers") else None
    if not isinstance(raw, dict) or "min" in raw or raw.get("flag") is True or "talentKey" in raw or list(raw) == ["strings"]:
        return None
    if attr["attribute"] in ETCH_VALUE_KEYS:
        return ETCH_VALUE_KEYS[attr["attribute"]]
    _warn("keys-" + attr["attribute"], f"etching text: {attr['attribute']} has no ETCH_VALUE_KEYS entry; its rolls are read in config key order")
    return tuple(raw)


def _placeholders(e, rolled):
    keys = _value_keys(e)
    if keys is None:
        if rolled is True or rolled is None or (isinstance(rolled, dict) and ("flag" in rolled or "talentKey" in rolled)):
            return {}
        if isinstance(rolled, dict) and "strings" in rolled:
            return {"value": rolled["strings"]}
        return {"value": rolled}
    return {"value" + "ABC"[i]: rolled.get(k) for i, k in enumerate(keys)}


def _rolled(raw, q):
    """catalog.etching_options' roll of one bracket value at quality q."""
    val = catalog.roll(raw, q)
    if val is None and isinstance(raw, dict):
        val = {k: (catalog.roll(v, q) if catalog.roll(v, q) is not None else v) for k, v in raw.items()}
        if raw.get("flag") is True:
            val = True
    return val


def _bracket(tiers, greed_tier):
    """Index of the bracket catalog.etching_options picks at this greed tier (the last that covers it), or None."""
    idx = None
    for i, t in enumerate(tiers):
        lo, hi = t.get("minGreedTier", 0), t.get("maxGreedTier", -1)
        if lo <= greed_tier and (hi == -1 or hi >= greed_tier):
            idx = i
    return idx


def _kinds(desc):
    """Placeholder key -> format letter (p percent, t ticks, d, s, f) as the description prints it."""
    out = {}
    for m in re.finditer(r"%(?:(d|t)|s|\.\d+(fp|f))([A-Za-z0-9_$]+)", desc):
        out[m.group(3)] = "p" if m.group(2) == "fp" else (m.group(1) or ("f" if m.group(2) else "s"))
    return out


def _range_part(v, kind):
    def one(x):
        x = float(x)
        if kind == "p":
            return f"{x * 100:.4g}"
        if kind == "t":
            return f"{x / 20:g}"
        return f"{x:g}"
    unit = "%" if kind == "p" else " s" if kind == "t" else ""
    if _isnum(v):
        return one(v) + unit
    if isinstance(v, dict) and "min" in v and "max" in v:
        lo, hi = one(v["min"]), one(v["max"])
        return (lo if lo == hi else f"{lo}-{hi}") + unit
    return None


def _range_text(e, raw):
    kinds = _kinds(e.get("description") or "")
    if raw is True or (isinstance(raw, dict) and raw.get("flag") is True):
        return "on"
    if isinstance(raw, dict) and "talentKey" in raw:
        return f"+{float(raw.get('levelChange', 0)):g} {str(raw['talentKey']).split(':')[-1].replace('_', ' ')}"
    keys = _value_keys(e)
    if keys is None:
        if isinstance(raw, dict) and "strings" in raw:
            return f"one of {len(raw['strings'])}"
        return _range_part(raw, kinds.get("value")) or "-"
    parts = []
    for i, k in enumerate(keys):
        p = _range_part(raw.get(k), kinds.get("value" + "ABC"[i]))
        if p is not None:
            parts.append(f"{re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', k).lower()} {p}")
    return ", ".join(parts) or "-"


def etching_catalog():
    """Rework: every etching for the Etchings view, with its in-game text rolled at each stage (that stage's greed-tier
    bracket at its gear roll quality, as builds get it), the roll range per bracket and its audit tag."""
    cat = catalog.load()["etchings"]
    groups = cat["groups"]
    sts = [(s, stages.STAGES[s]["greed_tier"], stages.STAGES[s]["roll_quality"]) for s in ("early", "mid", "end", "max")]
    for eid in sorted(set(catalog.ETCHING_NOTES) | catalog.ETCHING_NO_BOSS | catalog.ETCHING_PLANNED):
        if eid not in cat["etchings"]:
            print(f"[report][FALLBACK] catalog.ETCHING_NOTES names {eid}, which is not in the etching catalog; ignored")
    both = catalog.ETCHING_NO_BOSS & catalog.ETCHING_PLANNED
    if both:
        print(f"[report][FALLBACK] {sorted(both)} are in both ETCHING_NO_BOSS and ETCHING_PLANNED; tagged no-boss")
    out = []
    for eid, e in cat["etchings"].items():
        tiers = e["attributes"][0].get("tiers", [])
        if not tiers:
            print(f"[report][FALLBACK] etching {eid} has no tiers; left out of the Etchings view")
            continue
        desc = e.get("description") or ""
        texts, cur = [], []
        for _, gt, q in sts:
            b = _bracket(tiers, gt) if e.get("minGreedTier", 0) <= gt else None
            cur.append(b)
            texts.append(fill_etching(desc, _placeholders(e, _rolled(tiers[0 if b is None else b]["value"], q))))
        gear = set()
        for tg in e.get("typeGroups", []):
            gear.update(groups.get(tg, [tg]))
        tag, note = catalog.etching_status(eid)
        out.append({"id": eid, "name": e.get("name") or eid, "mgt": e.get("minGreedTier", 0), "groups": e.get("typeGroups", []),
                    "gear": sorted(gear, key=lambda g: GEAR_ORDER.index(g) if g in GEAR_ORDER else 99),
                    "tag": tag, "note": note, "text": texts[0] if len(set(texts)) == 1 else texts, "cur": cur,
                    "ranges": [[t.get("minGreedTier", 0), t.get("maxGreedTier", -1), _range_text(e, t["value"])] for t in tiers]})
    out.sort(key=lambda x: x["name"].lower())
    return {"stages": {s: [gt, q] for s, gt, q in sts}, "list": out}


def affix_doc(a):
    """One rolled affix for the report (run.affix_json without the slot)."""
    out = {"attribute": a.attribute, "value": a.value, "kind": a.kind, "label": a.label}
    if isinstance(a.raw, dict):
        keep = {k: v for k, v in a.raw.items() if k in ("abilityKey", "talentKey", "levelChange", "effectKey", "amplifier",
                                                        "specialModificationKey", "abilityId", "hitsRolled", "flag")}
        if keep:
            out["raw"] = keep
        if "min" in a.raw and "max" in a.raw:
            out["range"] = [a.raw["min"], a.raw["max"]]
    return out


def unique_cards():
    """Rework: every unique and ancient with all its modifiers (combat or not) rolled at each stage's unique roll
    quality, for the Gear view's item cards."""
    cat = catalog.load()
    allu = dict(cat["uniques"]["uniques"])
    allu.update(cat.get("ancients", {}))
    out = {}
    for s, st in stages.STAGES.items():
        q = st["unique_roll_quality"]
        opts, _ = catalog.unique_options(q, st["seal_roll_quality"])
        cards = {}
        for uid, u in sorted(allu.items()):
            aff = [a for a in (catalog.affix_from(m, m["kind"], q) for m in u["modifiers"]) if a is not None]
            missing = len(u["modifiers"]) - len(aff)
            if missing:
                print(f"[report][FALLBACK] {uid}: {missing} modifier(s) have no open tier; left off its card")
            cards[uid] = {"name": u["name"], "type": u["type"], "ancient": bool(u.get("ancient")),
                          "powers": opts[uid]["powers"] if uid in opts else [],
                          "affixes": [affix_doc(a) for a in aff]}
        out[s] = {"q": q, "cards": rnd(cards)}
    out["proposed"] = {uid: PROPOSED_UNIQUE_TEXT.get(uid.replace("ancient:", ""), "proposed change in this ledger")
                       for uid in stages.REWORK_UNIQUE_OVERRIDES}
    if stages.KNOBS.get("frozen_orb_additive"):
        for uid in ("the_vault:frozen_orb", "ancient:the_vault:frozen_orb"):
            out["proposed"][uid] = PROPOSED_UNIQUE_TEXT["the_vault:frozen_orb"]
    return out


# Plain-language summary of each proposed unique change, shown on its card (keyed by the regular id).
PROPOSED_UNIQUE_TEXT = {
    "the_vault:warbound_helmet": "Proposed: after a Scatter javelin splits on a block hit, its javelins home in on the nearest enemy, so "
                                 "every split javelin can hit a lone boss.",
    "the_vault:inferno_reach": "Proposed: renamed Steel Inferno. Its Fireball AoE roll becomes a tighter explosion: Fireball, Volley and "
                               "Mitosis explosions have 50-60% of their radius (ancient 55-65%) and deal up to +150-200% damage at the "
                               "centre (ancient +175-225%), falling off towards the edge.",
    "the_vault:stormcrown": "Proposed: your Storm Arrow's storm locks onto the mob closest to where the arrow hits and follows it at up "
                            "to 10 blocks per second. Its two storm rolls no longer appear as seals.",
    "woldsvaults:trirang": "Proposed (ancient): named Ancient Quad-Rang (it chains and pierces up to 4).",
    "the_vault:frozen_orb": "Proposed: its Frost Nova adds the rolled value once to the target's existing Vulnerable, instead of "
                            "applying (value - 1) only when that is stronger.",
    "the_vault:frostguards": "Proposed (ancient): +5 Frost Stonefall levels (was +2, below the regular +4) and 12-16 armor (was 10-13).",
}


def unique_bench():
    """Rework: tools/unique_bench.py output, or None (the Gear view then shows only the worn uniques)."""
    if not os.path.exists(UNIQUE_BENCH):
        print(f"[report][FALLBACK] no unique bench at {UNIQUE_BENCH}; the Gear view shows worn uniques only")
        return None
    with open(UNIQUE_BENCH, encoding="utf-8") as f:
        return rnd(json.load(f))


def node_notes():
    """Rework: hand-written commentary for the Nodes view (report/node_notes.json)."""
    with open(NODE_NOTES, encoding="utf-8") as f:
        return json.load(f)


def god_trees(cat):
    """Rework: the four god trees (node layout, edges, labels) plus star sheets, icons and descriptions."""
    with open(GOD_ASSETS, encoding="utf-8") as f:
        ga = json.load(f)
    from model.godfx import NOT_COUNTED
    desc = dict(ga["descriptions"])
    for eff, text in stages.REWORK_NODE_TEXT.items():
        if eff not in desc:
            print(f"[report][FALLBACK] REWORK_NODE_TEXT names {eff}, which has no pack description; added anyway")
        desc[eff] = text
    trees = {}
    for g, t in cat["gods"].items():
        trees[g] = {"nodes": [{"id": n["id"], "name": n["name"], "type": n["type"], "effect": n["effect"], "x": n["x"], "y": n["y"],
                               "icon": n.get("icon"), "enabled": n["enabled"]} for n in t["nodes"]],
                    "edges": t["edges"], "labels": t["labels"]}
    return {"trees": trees, "sheets": ga["sheets"], "icons": ga["icons"], "godIcons": ga["god_icons"],
            "desc": desc, "proposed": sorted(stages.REWORK_NODE_TEXT), "eotOnly": sorted(stages.REWORK_EOT_ONLY_EFFECTS), "archmageAP": cat["gods"]["idona"]["effects"]["idona_grand_archmage"]["ability_damage"], "notCounted": NOT_COUNTED, "levels": cat["god_levels"],
            "ranks": cat["greed_ranks"]}


REL = (os.environ.get("WV_SNAPSHOT") or os.path.join(ROOT, "cache")).replace(os.sep, "/")
PACKCFG = REL + "/pack/config/the_vault"
ADDCFG = REL + "/addon/src/generated/resources/data/woldsvaults/vault_configs"
if REWORK:
    # Greed-update text (e.g. the Executing Strike execute-threshold rework), same sources as extract_rework.py.
    PACKCFG = os.environ["WV_PACK_CFG"]
    ADDCFG = os.environ["WV_ADDON_CFG"]
ABILITY_DESC_FILES = [PACKCFG + "/abilities_descriptions.json", ADDCFG + "/abilities/descriptions/*.json"]
TALENT_DESC_FILES = [PACKCFG + "/skill_descriptions.json", ADDCFG + "/skill/descriptions/talent_overlevels.json",
                     ADDCFG + "/skill/descriptions/wolds_talents.json"]
PRESTIGE_DESC_FILES = [PACKCFG + "/skill_descriptions.json", ADDCFG + "/skill/descriptions/wolds_builtin_prestiges.json",
                       ADDCFG + "/skill/descriptions/prestige_overrides.json"]
TIER_SKIP = {"learnPointCost", "regretPointCost", "unlockLevel", "id", "type", "requiredGreedTier", "knowledgeCost"}


def color_tokens():
    import glob
    with open(PACKCFG + "/colors.json", encoding="utf-8") as f:
        groups = json.load(f).get("colors", {})
    out = {}
    for grp in groups.values():
        if isinstance(grp, dict):
            out.update(grp)
    return out


_COLORS = None


def rich(components):
    """Game text components -> [[text, color]] with $tokens resolved through colors.json."""
    global _COLORS
    if _COLORS is None:
        _COLORS = color_tokens()
    out = []
    for c in components if isinstance(components, list) else [components]:
        if isinstance(c, str):
            out.append([c, None])
            continue
        col = c.get("color")
        if isinstance(col, str) and col.startswith("$"):
            col = _COLORS.get(col[1:])
        out.append([c.get("text", ""), col])
    return out


def skill_info(registry, files, key):
    """Per skill id: description (game text) and numeric config per tier, for the report's hover cards."""
    import glob
    desc = {}
    for pattern in files:
        for f in sorted(glob.glob(pattern)):
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            for k, v in (d.get(key) or {}).items():
                desc[k] = v["description"] if isinstance(v, dict) and "description" in v else v
    out = {}
    for sid, s in registry.items():
        tiers = []
        for t in s.get("tiers", [s]):
            tiers.append({k: v for k, v in t.items() if k not in TIER_SKIP and isinstance(v, (int, float)) and not isinstance(v, bool)})
        entry = {"tiers": rnd(tiers)}
        if sid in desc:
            entry["desc"] = rich(desc[sid])
        if s.get("name"):
            entry["name"] = s["name"]
        out[sid] = entry
    return out


def main(out_path=DEFAULT_OUT):
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)
    data["builds"] = [slim(b) for b in data["builds"]]
    data["meta"] = rnd(data["meta"])
    data["ui"] = ui_data()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    with open(MODEL3D, encoding="utf-8") as f:
        html = html.replace("/*__MODEL3D__*/", f.read().replace("</", "<\/"))
    html = html.replace("/*__DATA__*/null", payload)
    if REWORK:
        html = html.replace("<title>Wold's Vaults Build Ledger</title>", "<title>Greed Rework Build Ledger</title>", 1)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out_path} ({len(html) / 1e6:.2f} MB, {len(data['builds'])} builds)")


if __name__ == "__main__":
    main(*sys.argv[1:])
