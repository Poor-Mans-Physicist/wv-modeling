"""Deck scoring: per-slot multipliers for a fixed precomputed layout, and the stat-card assignment on top.

Port of the DeckFAST 2.0 tagged kernel (ndm_core/src/tagsim.rs: slot_scan + finish_term), Max-mode
semantics (mono colour, colour core applies to every scorable card), Wold's config.yaml values.
A slot's NDM term is `base x core_mult x greed_boost x mirror`, where `core_mult` is additive
(1 + sum(core - 1) + implicit addends). Only the implicit addend depends on the card placed, through
its category groups, so each slot is stored as (base x boost x mirror, common core sum, implicit rules).
"""
import json
import os

from . import log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUTS = os.path.join(ROOT, "data", "deck_layouts.json")
MODIFIERS = os.path.join(os.environ.get("WV_SNAPSHOT") or os.path.join(ROOT, "..", "..", "cache"), "pack", "config", "the_vault", "card", "modifiers.json")

CFG = {
    "greed": 4.0, "pure_base": 1.0, "pure_scale": 0.07, "foil": 2.5, "color": 1.75,
    "void_base": 1.0, "void_scale": 0.3, "deluxe_flat": 2.0, "deluxe_core_base": 1.0, "deluxe_core_scale": 0.2,
    "archive": 1.2,
}

POSITIONAL = {"R": "row", "C": "col", "S": "surr", "X": "diag"}
GREED = {"^": (-1, 0), "v": (1, 0), "<": (0, -1), ">": (0, 1)}

IMPLICITS = {
    "anvil": {"kind": "global", "value": 1.0, "groups": ["Defensive"]},
    "wall": {"kind": "freq", "value": 2.0, "ptype": "col"},
    "bishop": {"kind": "freq", "value": 2.0, "ptype": "diag"},
    "pillager": {"kind": "freq", "value": 2.0, "ptype": "surr"},
    "cake": {"kind": "row_pos", "value": 1.0},
    "runic": {"kind": "mirror", "value": 2.0},
    "champion": {"kind": "global", "value": 1.5, "groups": ["Physical"]},
    "fairy": {"kind": "global", "value": 0.5, "groups": ["Magical"]},
    "belt": {"kind": "global", "value": 2.0, "groups": ["Utility"]},
    "cactus": {"kind": "global", "value": 2.5, "groups": ["Offensive", "Defensive"]},
    "snake": {"kind": "chain", "value": 0.05},
}


def parse_grid(grid):
    """Grid chars -> {(r, c): char}. Space = not a slot; '_' = an empty (dead) slot."""
    cells = {}
    for r, line in enumerate(grid):
        for c, ch in enumerate(line):
            if ch != " ":
                cells[(r, c)] = ch
    return cells


def score_layout(grid, cores, implicits, archive_mode="additive", nns_mode="current", core_values=None):
    """Per-slot scoring terms of a fixed layout. Returns (slots, meta).

    slots: list of dicts {pos, kind, base, boost, mirror, common, rules} for stat-giving slots, where the
    final multiplier for a card with groups G is base*boost*mirror*(common + sum(rule.value for rules matched by G)).
    archive_mode 'outside_sqrt' reproduces the pre-2026-07-21 rule (x base^(2.1 sqrt N) on the whole card), used
    only to validate against the July panel numbers.
    """
    cfg = dict(CFG, **(core_values or {}))
    cells = parse_grid(grid)
    placed = {p: ch for p, ch in cells.items() if ch != "_"}
    rows = {r for r, _ in cells}
    row_max = max(rows)
    cols = [c for _, c in cells]
    col_min, col_max = min(cols), max(cols)
    n_dead = sum(1 for ch in cells.values() if ch == "_")
    n_arcane = sum(1 for ch in placed.values() if ch == "a")
    n_greed = sum(1 for ch in placed.values() if ch in GREED)
    n_deluxe = sum(1 for ch in placed.values() if ch == "D")
    foil = "foil" in cores
    if foil:
        n_ns = n_greed + n_arcane
    elif nns_mode == "current":
        n_ns = len(placed)
    else:
        n_ns = n_greed + n_arcane + sum(1 for ch in placed.values() if ch in POSITIONAL)

    baseline = 0.0
    color_add = deluxe_add = void_add = archive_add = 0.0
    archive_outside = 1.0
    for core in cores:
        if core == "pure":
            baseline += cfg["pure_scale"] * n_ns
        elif core == "foil":
            baseline += cfg["foil"] - 1.0
        elif core == "color":
            color_add = cfg["color"] - 1.0
        elif core == "deluxe_core":
            deluxe_add = cfg["deluxe_core_scale"] * n_deluxe
        elif core == "void_core":
            void_add = cfg["void_scale"] * n_dead
        elif core == "archive_core":
            if archive_mode == "additive":
                archive_add = cfg["archive"] ** n_arcane - 1.0
            else:
                archive_outside = cfg["archive"] ** (2.1 * n_arcane ** 0.5) if n_arcane else 1.0
        else:
            log.fallback(f"deck-core-{core}", f"unknown deck core '{core}' ignored")

    freq = {"row": 1.0, "col": 1.0, "surr": 1.0, "diag": 1.0}
    mirror_value = None
    global_rules = []
    rowpos = None
    for name in implicits:
        imp = IMPLICITS.get(name)
        if imp is None:
            log.fallback(f"deck-implicit-{name}", f"implicit '{name}' not modeled; ignored")
            continue
        if imp["kind"] == "freq":
            freq[imp["ptype"]] *= round(imp["value"])
        elif imp["kind"] == "mirror":
            mirror_value = imp["value"]
        elif imp["kind"] == "global":
            global_rules.append({"implicit": name, "value": imp["value"], "groups": imp["groups"]})
        elif imp["kind"] == "row_pos":
            rowpos = imp["value"]
        else:
            log.fallback(f"deck-implicit-kind-{imp['kind']}", f"implicit kind '{imp['kind']}' not modeled")

    boost = {p: 1.0 for p in placed}
    for p, ch in sorted(placed.items()):
        if ch in GREED:
            dr, dc = GREED[ch]
            t = (p[0] + dr, p[1] + dc)
            if t in placed and placed[t] not in GREED and placed[t] != "a":
                boost[t] += cfg["greed"]

    slots = []
    for p, ch in sorted(placed.items()):
        if ch in GREED or ch in ("a", "."):
            continue
        r, c = p
        if ch in POSITIONAL:
            kind = POSITIONAL[ch]
            if kind == "row":
                raw = sum(1 for q in placed if q[0] == r)
            elif kind == "col":
                raw = sum(1 for q in placed if q[1] == c)
            elif kind == "surr":
                raw = sum(1 for q in placed if q != p and abs(q[0] - r) <= 1 and abs(q[1] - c) <= 1)
            else:
                raw = sum(1 for q in placed if q != p and (q[0] - q[1] == r - c or q[0] + q[1] == r + c))
            base = raw * freq[kind]
            if kind == "diag":
                base = max(base, 1.0)
        elif ch == "D":
            kind, base = "deluxe", cfg["deluxe_flat"]
        elif ch == "T":
            kind, base = "typeless", 1.0
        else:
            log.fallback(f"deck-char-{ch}", f"unknown layout char '{ch}' treated as empty")
            continue
        common = 1.0 + baseline + color_add + void_add + archive_add
        if kind != "deluxe":
            common += deluxe_add
        if rowpos is not None:
            common += rowpos * (row_max - r + 1)
        mirror = 1.0
        if mirror_value is not None:
            mc = col_max - (c - col_min)
            if mc == c or (r, mc) in placed:
                mirror = mirror_value
        slots.append({"pos": [r, c], "kind": kind, "base": base, "boost": boost[p],
                      "mirror": mirror * archive_outside, "common": common, "rules": global_rules})
    meta = {"n_ns": n_ns, "n_dead": n_dead, "n_arcane": n_arcane, "n_greed": n_greed, "n_deluxe": n_deluxe}
    return slots, meta


def slot_mult(slot, groups):
    add = sum(r["value"] for r in slot["rules"] if all(g in groups for g in r["groups"]))
    return slot["base"] * slot["boost"] * slot["mirror"] * (slot["common"] + add)


def blanket_ndm(slots):
    """NDM with every card carrying every implicit group (DeckFAST Max-mode blanket)."""
    return sum(slot["base"] * slot["boost"] * slot["mirror"] * (slot["common"] + sum(r["value"] for r in slot["rules"]))
               for slot in slots)


MANUAL = os.path.join(ROOT, "data", "deck_layouts_manual.json")


def load_layouts():
    with open(LAYOUTS, encoding="utf-8") as f:
        out = json.load(f)["decks"]
    if os.path.exists(MANUAL):
        with open(MANUAL, encoding="utf-8") as f:
            out.update(json.load(f)["decks"])
    return out


FAMILY_POOL = {"evo": "scaling", "typeless": "default", "deluxe": "deluxe_stat"}
SLOT_FAMILY = {"row": "evo", "col": "evo", "surr": "evo", "diag": "evo", "typeless": "typeless", "deluxe": "deluxe"}


def card_registry():
    """Obtainable stat cards per family: {family: {card_id: {attribute, groups, tiers{tier: value}}}}."""
    with open(MODIFIERS, encoding="utf-8") as f:
        full = json.load(f)
    vals = full["values"]
    reg = {}
    for fam, pool in FAMILY_POOL.items():
        reg[fam] = {}
        for m in full["pools"].get(pool, []):
            cid = m["value"]
            e = vals.get(cid)
            if not e or e.get("type") != "gear" or not e.get("attribute"):
                log.fallback(f"card-missing-{cid}", f"booster pool '{pool}' lists '{cid}' with no gear entry; skipped")
                continue
            tiers = {int(p["tier"]): float(p["min"]) for p in e.get("pool", [])}
            reg[fam][cid] = {"attribute": e["attribute"], "groups": [g for g in e.get("groups", [])], "tiers": tiers}
    return reg


def card_value(card, tier):
    t = card["tiers"]
    if tier in t:
        return t[tier]
    if len(t) == 1:
        return next(iter(t.values()))
    return t[max(k for k in t if k <= tier)]
