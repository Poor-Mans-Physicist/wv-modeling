"""Per-build deck value curves and deck archetypes, for DeckFAST's build-weighted objective.

For every stored rework build (out/found_rework.json, variant "all") at the chosen stages, a probe slot is added to
the stage deck. Its options shift one deck attribute's total by j x step, and the kernel "eval" op reads the build's
damage and survival cycles as that attribute's deck total sweeps from 0 to its maximum (whole deck in that stat), with
everything else fixed. That gives, per build and attribute, a damage curve and a survival curve; DeckFAST scores a
deck for a build as min(damage, survival) with every attribute's curve applied to that deck's totals.

Builds are then clustered (k-means on normalized marginal-gain profiles) into archetypes per stage. The output JSON
also carries the stage's card catalog (rework bans, tier) so DeckFAST builds honest stat stacks from it.

Run:  WV_LEDGER=rework uv run python tools/deck_weights.py [--stages end,max] [--k 2,3] [--points 24]
"""
import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("WV_LEDGER", "rework")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import catalog, deck, families, kernel, log as mlog, stages  # noqa: E402
import run  # noqa: E402


def card_catalog(ctx):
    """The stage's obtainable stat cards per slot family, exactly as Context.init_deck filters them."""
    tier = ctx.stage["card_tier"]
    out = {}
    for fam, cards in ctx.card_registry.items():
        rows = []
        for cid, card in sorted(cards.items()):
            if card["attribute"] not in catalog.COMBAT_ATTRS:
                continue
            if ctx.rework and card["attribute"] in stages.REWORK_BANNED_CARDS:
                continue
            rows.append({"id": cid, "attr": card["attribute"], "groups": list(card["groups"]),
                         "value": deck.card_value(card, tier)})
        out[fam] = rows
    return out


def probe_context(stage, key0, attrs, xmax, points):
    """A context for the stage deck plus one probe slot: option (a, j) adds j * xmax[a] / points to attribute a."""
    key = f"{key0}|probe"
    real_load = deck.load_layouts
    lay = real_load()[key0]
    deck.load_layouts = lambda: dict(real_load(), **{key: dict(lay, key=key)})
    try:
        ctx = run.Context(stage, "intended", "all", key)
    finally:
        deck.load_layouts = real_load
    opts = {}
    for a in attrs:
        step = xmax[a] / points
        for j in range(-points, points + 1):
            opts[f"probe|{a}|{j}"] = (a, j * step, 0.0, 0.0)
    ctx.deck_options = list(ctx.deck_options) + [opts]
    return ctx


def kmeans(vectors, k, seeds=32):
    """Plain k-means with random restarts; returns (labels, inertia)."""
    best = None
    rng = random.Random(1)
    n, d = len(vectors), len(vectors[0])
    for _ in range(seeds):
        cent = [list(v) for v in rng.sample(vectors, k)]
        for _ in range(100):
            lab = [min(range(k), key=lambda c: sum((v[i] - cent[c][i]) ** 2 for i in range(d))) for v in vectors]
            new = []
            for c in range(k):
                mem = [vectors[i] for i in range(n) if lab[i] == c]
                new.append([statistics.mean(m[i] for m in mem) for i in range(d)] if mem else cent[c])
            if new == cent:
                break
            cent = new
        inertia = sum(sum((vectors[i][j] - cent[lab[i]][j]) ** 2 for j in range(d)) for i in range(n))
        if best is None or inertia < best[1]:
            best = (lab, inertia)
    return best


def merge_small(feats, lab, k, min_size):
    """Merge every cluster smaller than min_size into the cluster with the nearest centroid; relabel 0..n-1."""
    lab = list(lab)
    d = len(feats[0])

    def cents():
        out = {}
        for c in set(lab):
            mem = [feats[i] for i in range(len(feats)) if lab[i] == c]
            out[c] = [statistics.mean(m[j] for m in mem) for j in range(d)]
        return out

    while True:
        sizes = {c: lab.count(c) for c in set(lab)}
        small = [c for c in sizes if sizes[c] < min_size]
        if not small or len(sizes) == 1:
            break
        c0 = min(small, key=lambda c: sizes[c])
        cen = cents()
        tgt = min((c for c in cen if c != c0), key=lambda c: sum((cen[c][j] - cen[c0][j]) ** 2 for j in range(d)))
        lab = [tgt if x == c0 else x for x in lab]
    order = {c: i for i, c in enumerate(sorted(set(lab), key=lambda c: -lab.count(c)))}
    return [order[x] for x in lab]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="early,mid,end,max")
    ap.add_argument("--k", default="2,3,4,5,6")
    ap.add_argument("--min-group", type=int, default=4, help="clusters smaller than this merge into the nearest one")
    ap.add_argument("--points", type=int, default=24)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "deck_weights.json"))
    args = ap.parse_args()
    if stages.LEDGER != "rework":
        raise SystemExit("deck_weights: run with WV_LEDGER=rework")

    found = json.load(open(os.path.join(ROOT, "out", "found_rework.json"), encoding="utf-8"))
    result = {"generated": time.strftime("%Y-%m-%d %H:%M"), "stages": {}}
    for stage in args.stages.split(","):
        key0 = stages.STAGES[stage]["deck"]["layout"]
        ctx0 = run.ctx_for(stage, "intended", "all", key0)
        ex0 = kernel.export_for(ctx0)
        attrs = sorted({o[0] for opts in ctx0.deck_options for o in opts.values()})
        xmax = {a: sum(max((o[1] for o in opts.values() if o[0] == a), default=0.0) for opts in ctx0.deck_options)
                for a in attrs}
        attrs = [a for a in attrs if xmax[a] > 0]
        pctx = probe_context(stage, key0, attrs, xmax, args.points)
        pex = kernel.export_for(pctx)
        rows = [r for r in found if r["stage"] == stage and r["variant"] == "all" and r["mode"] == "intended"]

        def probe(r):
            fam = families.get(r["family"])
            b = ex0.to_build(r["doc"], fam)
            x0 = {a: 0.0 for a in attrs}
            for cid, opts in zip(b.deck, ctx0.deck_options):
                a, c = opts[cid][0], opts[cid][1]
                if a in x0:
                    x0[a] += c
            base = [pex.card_ids.ids[c] for c in b.deck]
            plan = [(None, 0)]
            for a in attrs:
                step = xmax[a] / args.points
                lo = -min(args.points, int(math.floor(x0[a] / step + 1e-9)))
                hi = min(args.points, int(math.floor((xmax[a] - x0[a]) / step + 1e-9)))
                plan += [(a, j) for j in range(lo, hi + 1)]
            docs = [dict(r["doc"], deck=base + [pex.card_ids.ids[f"probe|{a or attrs[0]}|{j}"]]) for a, j in plan]
            out = pex.call(fam, {"op": "eval", "builds": docs})["results"]
            if not all(o["ok"] for o in out):
                bad = next(o for o in out if not o["ok"])
                raise SystemExit(f"deck_weights: eval failed for {r['family']}: {bad.get('error')}")
            d0, s0, sc0 = out[0]["cycle_damage"], out[0]["cycle_survival"], out[0]["score"]
            curves = {}
            for (a, j), o in zip(plan[1:], out[1:]):
                curves.setdefault(a, []).append((x0[a] + j * xmax[a] / args.points,
                                                 o["cycle_damage"] - d0, o["cycle_survival"] - s0, o["score"] - sc0))
            cards = [[sl["pos"][0], sl["pos"][1], cid] for sl, cid in zip(ctx0.deck_slots, b.deck)]
            return {"family": r["family"], "score": sc0, "damage": d0, "survival": s0, "x0": x0, "curves": curves,
                    "deck_cards": cards}

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            builds = list(pool.map(probe, rows))
        # Fidelity check data: every build scored with every other build's deck (same layout, same slots).
        decks_by = {r["family"]: ex0.to_build(r["doc"], families.get(r["family"])).deck for r in rows}

        def cross(r):
            fam = families.get(r["family"])
            fams_c = sorted(decks_by)
            docs = [dict(r["doc"], deck=[ex0.card_ids.ids[c] for c in decks_by[fc]]) for fc in fams_c]
            out = ex0.call(fam, {"op": "eval", "builds": docs})["results"]
            return {fc: (o["score"] if o["ok"] else None) for fc, o in zip(fams_c, out)}

        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            crosses = list(pool.map(cross, rows))
        for b, cx in zip(builds, crosses):
            b["cross"] = cx
        n_min = sum(1 for b in builds if abs(b["score"] - min(b["damage"], b["survival"])) < 1e-9)
        print(f"{stage}: probed {len(builds)} builds x {len(attrs)} attributes in {time.time() - t0:.0f}s; "
              f"score == min(damage, survival) for {n_min}/{len(builds)}", flush=True)
        if n_min < len(builds):
            mlog.fallback("deck-weights-score-not-min", f"{stage}: {len(builds) - n_min} builds score != min(damage, "
                          "survival); their model channels use damage/survival anyway")

        def gain(b, a):
            """Score gain from one probe step up in attribute a (0 when already at the top)."""
            pts = sorted(b["curves"][a])
            up = [p for p in pts if p[0] > b["x0"][a] + 1e-9]
            return up[0][3] if up else 0.0

        feats = []
        for b in builds:
            g = [max(0.0, gain(b, a)) for a in attrs]
            tot = sum(g) or 1.0
            feats.append([v / tot for v in g])
        clusters = {}
        for k in [int(x) for x in args.k.split(",")]:
            lab, inertia = kmeans(feats, k)
            lab = merge_small(feats, lab, k, args.min_group)
            kk = len(set(lab))
            clusters[str(k)] = {"labels": {b["family"]: lab[i] for i, b in enumerate(builds)}, "inertia": inertia,
                                "groups": kk}
            print(f"  k={k}: inertia {inertia:.3f}; sizes {[lab.count(c) for c in range(kk)]}"
                  + (f" (merged groups under {args.min_group} builds)" if kk < k else ""))
            for c in range(kk):
                mem = [i for i in range(len(builds)) if lab[i] == c]
                prof = {attrs[j].split(':')[1]: statistics.mean(feats[i][j] for i in mem) for j in range(len(attrs))}
                top = ", ".join(f"{a} {v:.2f}" for a, v in sorted(prof.items(), key=lambda kv: -kv[1])[:5])
                names = ", ".join(sorted(builds[i]["family"].split(":")[1] for i in mem))
                print(f"    archetype {c}: {top}\n      {names}")

        result["stages"][stage] = {
            "layout": key0, "layout_entry": deck.load_layouts()[key0], "card_tier": ctx0.stage["card_tier"],
            "implicit_rules": {n: deck.IMPLICITS[n] for n in deck.load_layouts()[key0]["implicits"]
                               if n in deck.IMPLICITS},
            "greed_addend": catalog.load()["greed_cards"][stages.STAGES[stage]["greed_card"]] - 1.0
            if stages.STAGES[stage].get("greed_card") else None,
            "attrs": attrs, "xmax": xmax, "points": args.points, "cards": card_catalog(ctx0),
            "builds": builds, "clusters": clusters,
        }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
