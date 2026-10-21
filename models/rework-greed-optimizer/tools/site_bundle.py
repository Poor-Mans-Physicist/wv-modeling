"""Data bundle for the deck optimizer rework site (WoldsVaultsDeckOptimizerRework): per stage, the value curves of
every stored rework build, its level-grant curves, the stage card catalog and the playstyle groups.

Stat curves: each build (out/found_rework.json, variant "all", the deck-menu run) keeps its own deck; a probe slot adds
j x step to one attribute's deck total, and the kernel "eval" op reads the damage and survival cycles (as
tools/deck_weights.py does, but around the build's menu deck). The grid runs from 0 to the attribute's largest
reachable deck total (every stat slot of the best design holding the attribute's best card).

Level curves: the build's Knack / Arcane grant for one talent or ability is replaced by L levels (L = 0 ..
--levels), everything else fixed, and the Python model scores it (deck grants add to the same level lists as gear,
build.assemble). Keys whose curve is flat are dropped.

Run:  WV_LEDGER=rework uv run python tools/site_bundle.py [--stages early,mid,end,max] [--out out/site_bundle]
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

os.environ.setdefault("WV_LEDGER", "rework")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import catalog, deck, deck_rework, families, kernel, log as mlog, stages  # noqa: E402
from model.context import COMBAT_TALENTS, Context  # noqa: E402
from model.evaluate import evaluate  # noqa: E402

FOUND = os.path.join(ROOT, "out", "found_rework.json")
ASSETS = os.path.join(ROOT, "data", "assets.json")
RESULTS = os.path.join(ROOT, "out", "results_rework.json")

# Playstyle groups by the ledger's scaling classification (results "scaling"); every family lands in exactly one.
PLAYSTYLES = {
    "Melee": {"AD (melee)"},
    "Caster": {"AP", "Mana"},
    "Physical abilities": {"AD"},
    "Thorns & Block": {"Thorns", "AD+Thorns", "AD+Block", "HP"},
}

_PROBE = {"grant": None}
_orig_grants = deck_rework.level_grants


def _grants(cards):
    ab, tal = _orig_grants(cards)
    g = _PROBE["grant"]
    if g:
        kind, key, lv = g
        lst = ab if kind == "ability" else tal
        lst[:] = [(k, v) for (k, v) in lst if k != key]
        if lv > 0:
            lst.append((key, float(lv)))
    return ab, tal


deck_rework.level_grants = _grants


def menu_ctx(stage):
    stages.DECK_MENUS = "1"
    try:
        return Context(stage, "intended", "all")
    finally:
        stages.DECK_MENUS = ""


def stat_catalog(ctx):
    """Stat cards per slot family at the stage tier, banned stats out (as deck_rework.build_designs offers them)."""
    tier = ctx.stage["card_tier"]
    out = {}
    for fam in ("evo", "typeless", "deluxe"):
        rows = []
        for cid, card in sorted(ctx.card_registry[fam].items()):
            a = card["attribute"]
            if a not in catalog.COMBAT_ATTRS or a in stages.REWORK_BANNED_CARDS:
                continue
            rows.append({"id": cid, "attr": a, "value": deck.card_value(card, tier), "groups": list(card["groups"])})
        out[fam] = rows
    return out


def xmax_by_attr(ctx, cards, attrs):
    """Largest deck total per attribute: over every menu design and core set, each stat slot holds the attribute's
    best card for its slot family."""
    best = {a: 0.0 for a in attrs}
    for d in ctx.designs:
        for a in attrs:
            choice = []
            for i, opts in enumerate(d["opts"]):
                k = max(range(len(opts)), key=lambda j: opts[j]["value"] if opts[j].get("attr") == a else -1.0)
                choice.append(k if opts[k].get("attr") == a else d["seed"][i])
            if not deck_rework.limits_ok(d, choice):
                choice = list(d["seed"])
            for ci in range(len(d["csets"])):
                tot = deck_rework.totals(deck_rework.design_terms(d, ci, choice), deck_rework.design_cards(d, choice))
                best[a] = max(best[a], tot.get(a, 0.0))
    return best


def own_totals(ctx, b):
    d = ctx.designs[b.design]
    cards = deck_rework.design_cards(d, b.dk)
    return deck_rework.totals(deck_rework.design_terms(d, b.cset, b.dk), cards), deck_rework.level_grants(cards)


_W = {}


def _level_worker_init(stage):
    _W["ctx"] = menu_ctx(stage)
    _W["ex"] = kernel.export_for(_W["ctx"])


def _level_probe(task):
    """Level curves of one build: {key: {"kind", "l0", "d": [...], "s": [...]}} over L = 0..levels."""
    doc, fid, keys, levels = task
    ctx, ex = _W["ctx"], _W["ex"]
    fam = families.get(fid)
    b = ex.to_build(doc, fam)
    _PROBE["grant"] = None
    _, (ab0, tal0) = own_totals(ctx, b)
    r0 = evaluate(b, ctx)
    d0, s0 = r0["cycle_damage"], r0["cycle_survival"]
    out = {"_base": {"d": d0, "s": s0}}
    for kind, key in keys:
        own = sum(v for k, v in (ab0 if kind == "ability" else tal0) if k == key)
        _PROBE["grant"] = (kind, key, own + 1)
        r1 = evaluate(b, ctx)
        if abs(r1["cycle_damage"] - d0) < 1e-9 and abs(r1["cycle_survival"] - s0) < 1e-9:
            continue
        dd, ss = [], []
        for lv in range(levels + 1):
            _PROBE["grant"] = (kind, key, lv)
            r = evaluate(b, ctx)
            dd.append(r["cycle_damage"] - d0)
            ss.append(r["cycle_survival"] - s0)
        out[key] = {"kind": kind, "l0": own, "d": dd, "s": ss}
    _PROBE["grant"] = None
    return fid, out


def cores_page(stage, res, groups):
    """The Cores page's numbers for one stage: how often the stored builds' decks use each core (all builds and per
    playstyle), the deck-menu designs with the builds that chose them, and DeckFAST's deck ranking for all builds."""
    menus = deck_rework.load_menus(None).get(stage) or {}
    builds = [b for b in res["builds"] if b["stage"] == stage and b.get("variant") != "eot" and (b.get("deck") or {}).get("menu")]
    fam_group = {f: g for g, fs in groups.items() if g != "Balanced" for f in fs}
    use, per_group, totals = {}, {}, {"Balanced": len(builds)}
    for b in builds:
        g = fam_group.get(b["family"], "Balanced")
        totals[g] = totals.get(g, 0) + 1
        for c in b["deck"].get("cores", []):
            use[c["core"]] = use.get(c["core"], 0) + 1
            per_group.setdefault(g, {})[c["core"]] = per_group.setdefault(g, {}).get(c["core"], 0) + 1
    designs = []
    for i, m in enumerate(menus.get("menu", [])):
        users = [b for b in builds if b["deck"].get("index") == i]
        designs.append({"label": m["label"], "deck": m["deck"], "mystery": m["mystery"], "implicit_keys": m["implicit_keys"],
                        "found_for": m["found_for"], "cores": m["core_sets"][0] if m["core_sets"] else [],
                        "slots": m["slots"], "arcane_slots": m["arcane_slots"],
                        "cells": [[c["t"], c["color"]] for c in m["cells"]], "score": m["score"],
                        "builds": len(users), "mean_cycle": (sum(b["cycle"] for b in users) / len(users)) if users else None,
                        "users": [b.get("family_label", b["family"]) for b in sorted(users, key=lambda b: -b["cycle"])[:8]]})
    return {"builds": len(builds), "core_use": use, "core_use_by_group": per_group, "group_builds": totals,
            "designs": designs, "ranking": (menus.get("ranking") or {}).get("all", [])[:40]}


def card_art(out_dir):
    """Card textures, stat icons and card art for the site's deck grid (keys as in the ledger report)."""
    if not os.path.exists(ASSETS):
        mlog.fallback("site-bundle-art", f"{ASSETS} missing (report/extract_assets.py); the site falls back to plain tiles")
        return
    icons = json.load(open(ASSETS, encoding="utf-8"))
    icons = icons.get("icons", icons)
    art = {k: v for k, v in icons.items() if k.startswith(("card:", "cardicon:", "cardart:"))}
    with open(os.path.join(out_dir, "card_art.json"), "w", encoding="utf-8") as f:
        json.dump(art, f, separators=(",", ":"))
    print(f"card art: {len(art)} images, {os.path.getsize(os.path.join(out_dir, 'card_art.json')) / 1e3:.0f} KB")


def git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        mlog.fallback("site-bundle-git", f"git HEAD unavailable ({e}); commit left blank")
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="early,mid,end,max")
    ap.add_argument("--points", type=int, default=24)
    ap.add_argument("--levels", type=int, default=6, help="granted levels probed per key (0..N)")
    ap.add_argument("--limit", type=int, default=0, help="builds per stage (testing)")
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "site_bundle"))
    args = ap.parse_args()
    if stages.LEDGER != "rework":
        raise SystemExit("site_bundle: run with WV_LEDGER=rework")
    os.makedirs(args.out, exist_ok=True)
    found = json.load(open(FOUND, encoding="utf-8"))
    res = json.load(open(RESULTS, encoding="utf-8"))
    meta = {"ledger_commit": git_head(), "results_generated": res["meta"]["generated"],
            "bundle_generated": time.strftime("%Y-%m-%d %H:%M")}
    scaling = {(b["stage"], b["family"]): b.get("scaling", "") for b in res["builds"] if b.get("variant") != "eot"}
    labels = {(b["stage"], b["family"]): b.get("family_label", b["family"]) for b in res["builds"]}
    special = deck_rework.special_cards(COMBAT_TALENTS)
    keys = sorted({(c["grant"][0], c["grant"][1]) for rows in special.values() for c in rows if c.get("grant")})
    manifest = {"meta": meta, "stages": {}}
    for stage in args.stages.split(","):
        t0 = time.time()
        ctx0 = menu_ctx(stage)
        if not ctx0.designs:
            raise SystemExit(f"site_bundle: no deck menu for {stage}")
        ex0 = kernel.export_for(ctx0)
        cards = stat_catalog(ctx0)
        attrs = sorted({r["attr"] for rows in cards.values() for r in rows})
        xmax = xmax_by_attr(ctx0, cards, attrs)
        attrs = [a for a in attrs if xmax[a] > 0]
        pctx = menu_ctx(stage)
        opts = {}
        for a in attrs:
            step = xmax[a] / args.points
            for j in range(-args.points, args.points + 1):
                opts[f"probe|{a}|{j}"] = (a, j * step, 0.0, 0.0)
        pctx.deck_options = [opts]
        pex = kernel.export_for(pctx)
        rows = [r for r in found if r["stage"] == stage and r["variant"] == "all" and r["mode"] == "intended"]
        if args.limit:
            rows = rows[:args.limit]

        def probe(r):
            fam = families.get(r["family"])
            b = ex0.to_build(r["doc"], fam)
            x0, _ = own_totals(ctx0, b)
            x0 = {a: x0.get(a, 0.0) for a in attrs}
            plan = [(attrs[0], 0)]
            for a in attrs:
                step = xmax[a] / args.points
                lo = -min(args.points + 1, int(math.floor(x0[a] / step + 1e-9)) + 1)
                hi = max(lo + 1, int(math.ceil((xmax[a] - x0[a]) / step - 1e-9)))
                hi = min(hi, args.points)
                plan += [(a, j) for j in range(lo, hi + 1)]
            docs = [dict(r["doc"], deck=[pex.card_ids.ids[f"probe|{a}|{j}"]]) for a, j in plan]
            out = pex.call(fam, {"op": "eval", "builds": docs})["results"]
            if not all(o["ok"] for o in out):
                bad = next(o for o in out if not o["ok"])
                raise SystemExit(f"site_bundle: eval failed for {r['family']}: {bad.get('error')}")
            d0, s0, sc0 = out[0]["cycle_damage"], out[0]["cycle_survival"], out[0]["score"]
            if abs(sc0 - r["score"]) > 1e-6 * max(1.0, abs(r["score"])):
                mlog.fallback("site-bundle-score", f"{stage} {r['family']}: probe base {sc0} != stored {r['score']}")
            curves = {}
            for (a, j), o in zip(plan[1:], out[1:]):
                curves.setdefault(a, []).append((x0[a] + j * xmax[a] / args.points,
                                                 o["cycle_damage"] - d0, o["cycle_survival"] - s0))
            packed = {}
            for a, pts in curves.items():
                pts.sort()
                if len(pts) < 2:
                    continue
                packed[a] = {"x0": pts[0][0], "dx": pts[1][0] - pts[0][0], "d": [p[1] for p in pts],
                             "s": [p[2] for p in pts]}
            return {"family": r["family"], "label": labels.get((stage, r["family"]), r["family"]),
                    "scaling": scaling.get((stage, r["family"]), ""), "score": sc0, "damage": d0, "survival": s0,
                    "x0": x0, "curves": packed}

        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            builds = list(pool.map(probe, rows))
        t1 = time.time()
        tasks = [(r["doc"], r["family"], keys, args.levels) for r in rows]
        with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 2), initializer=_level_worker_init,
                                 initargs=(stage,)) as pool:
            lev = dict(pool.map(_level_probe, tasks))
        for b in builds:
            lv = dict(lev.get(b["family"], {}))
            base = lv.pop("_base", None)
            if base and (abs(base["d"] - b["damage"]) > 1e-6 or abs(base["s"] - b["survival"]) > 1e-6):
                mlog.fallback("site-bundle-python-kernel", f"{stage} {b['family']}: Python cycles {base} != kernel "
                              f"({b['damage']}, {b['survival']}); level curves are relative to the Python base")
            b["levels"] = lv
        groups = {"Balanced": [b["family"] for b in builds]}
        for name, scs in PLAYSTYLES.items():
            groups[name] = [b["family"] for b in builds if b["scaling"] in scs]
        unplaced = [b["family"] for b in builds if not any(b["scaling"] in s for s in PLAYSTYLES.values())]
        if unplaced:
            mlog.fallback("site-bundle-playstyle", f"{stage}: no playstyle for {unplaced}; Balanced only")
        # Deck shapes the menus use that are not regular decks (the structural Construction v2 Mystery layout).
        extra_decks, seen = [], set()
        for d in ctx0.designs:
            m = d["menu"]
            if "(" not in m["deck"] or m["deck"] in seen:
                continue
            seen.add(m["deck"])
            extra_decks.append({"name": m["deck"].replace("(stage shape)", "(Construction v2)"), "ledger_deck": m["deck"],
                                "key": f"{m['key']}_structural", "slots": m["slots"],
                                "arcane_slots": m["arcane_slots"], "core_slots": m["core_slots"], "mystery": m["mystery"]})
        bundle = {"meta": meta, "stage": stage, "card_tier": ctx0.stage["card_tier"], "attrs": attrs, "xmax": xmax,
                  "extra_decks": extra_decks,
                  "cards": cards,
                  "special": {k: [{"id": c["id"], "groups": c["groups"], "grant": list(c["grant"]) if c["grant"] else None}
                                  for c in rows_] for k, rows_ in special.items()},
                  "level_keys": [{"kind": k, "key": key} for k, key in keys], "level_max": args.levels,
                  "playstyles": groups, "builds": builds, "cores_page": cores_page(stage, res, groups)}
        path = os.path.join(args.out, f"{stage}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bundle, f, separators=(",", ":"))
        n_lev = sum(len(b["levels"]) for b in builds)
        manifest["stages"][stage] = {"file": f"{stage}.json", "builds": len(builds), "bytes": os.path.getsize(path)}
        print(f"{stage}: {len(builds)} builds x {len(attrs)} attributes ({t1 - t0:.0f}s), {n_lev} level curves "
              f"({time.time() - t1:.0f}s); {os.path.getsize(path) / 1e6:.2f} MB", flush=True)
    card_art(args.out)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
