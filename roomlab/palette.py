"""Minimal interpreter for the_vault's palette `tile_processors`, enough to preview a room the
way the game will actually skin it.

Supports `weighted_target` (the substitution type every crystal_caves colour palette is built
from). Anything else is reported and skipped, so the preview never silently pretends to have
applied a rule it does not understand.
"""
import json, random


def load(paths):
    procs = []
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        procs.extend(d.get("tile_processors", []))
    return procs


def _norm(target):
    """Palette files quote property values inconsistently: type="gilded_chest" vs type=gilded_chest."""
    return target.replace('"', "").replace(" ", "")


def apply(ids, grid, procs, seed=0):
    """ids: palette list. grid: {(x,y,z): palette index}. Returns (new ids, new grid, report)."""
    rng = random.Random(seed)
    rules, skipped = {}, []
    for p in procs:
        t = p.get("type")
        if t == "weighted_target":
            tgt = _norm(str(p.get("target", "")))
            out = p.get("output") or {}
            if tgt and out:
                names = [_norm(k) for k in out]
                weights = [float(v) for v in out.values()]
                rules[tgt] = (names, weights)
        else:
            skipped.append(t)

    ids = list(ids)
    index = {b: i for i, b in enumerate(ids)}

    def idx_of(b):
        if b not in index:
            index[b] = len(ids)
            ids.append(b)
        return index[b]

    hits = {}
    new_grid = {}
    for pos, s in grid.items():
        bid = ids[s]
        rule = rules.get(_norm(bid))
        if rule is None:
            new_grid[pos] = s
            continue
        names, weights = rule
        pick = rng.choices(names, weights=weights, k=1)[0]
        new_grid[pos] = idx_of(pick)
        hits[_norm(bid)] = hits.get(_norm(bid), 0) + 1

    report = {
        "rules": len(rules),
        "substituted": hits,
        "unmatched_rules": sorted(set(rules) - set(hits)),
        "skipped_processor_types": sorted(set(t for t in skipped if t)),
    }
    return ids, new_grid, report
