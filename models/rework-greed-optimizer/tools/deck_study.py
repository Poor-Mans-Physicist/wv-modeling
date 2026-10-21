"""Deck study: how much each found build's score depends on its deck, and which deck variant suits which build.

For every stored rework build (out/found_rework.json, variant "all") at the chosen stages, each deck variant gets its own
Context. The stored build is re-annealed from where it ended on the phase-2 cool schedule, with the same seeds for every
variant. That includes re-picking its deck cards, so gear may follow the new deck. The control variant "base" re-anneals
under the unchanged stage deck. Differences are reported against that control, never against the stored scores (code
changed after that run).

Variants:
  base               the stage deck as is
  nodeck             every deck contribution zeroed (what the deck is worth)
  ndm125             every deck contribution x1.25 (what more NDM is worth)
  +<tag>             the stage deck plus one tag core at greater max (arsenal/aegis/tool/natural/fae)
  pure-><tag>        the stage deck with its Pure core replaced by that tag core

Run:  WV_LEDGER=rework uv run python tools/deck_study.py [--stages end,max] [--seeds 3] [--iters 300000]
"""
import argparse
import copy
import json
import os
import statistics
import sys
import time

os.environ.setdefault("WV_LEDGER", "rework")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import catalog, deck, families, kernel, stages  # noqa: E402
from extract.convert_deck_layouts import CHAR  # noqa: E402
import run  # noqa: E402

TAGS = list(deck.TAG_CORES)
REWORK_OPTS = {"weights": True, "repair": 4}


def variants_for(layout):
    """{variant name: (layout entry or None for the stage layout, contribution factor)}."""
    out = {"base": (None, 1.0), "nodeck": (None, 0.0), "ndm125": (None, 1.25)}
    for t in TAGS:
        lay = copy.deepcopy(layout)
        lay["cores"] = list(lay["cores"]) + [t]
        out[f"+{t}"] = (lay, 1.0)
        if "pure" in layout["cores"]:
            lay = copy.deepcopy(layout)
            lay["cores"] = [t if c == "pure" else c for c in lay["cores"]]
            out[f"pure->{t}"] = (lay, 1.0)
    return out


def deck_greedy(ex, fam, doc, options, max_steps=40):
    """Deck-only steepest ascent through the kernel eval op: apply the best single-slot card change until none
    improves the score. Gives every layout a comparably optimized starting deck before the re-anneal."""
    ids = [[ex.card_ids.ids[c] for c in opts] for opts in options]
    cur = list(doc["deck"])
    best = ex.call(fam, {"op": "eval", "builds": [doc]})["results"][0]["score"]
    for _ in range(max_steps):
        cands, moves = [], []
        for i, opts in enumerate(ids):
            for o in opts:
                if o != cur[i]:
                    d = list(cur)
                    d[i] = o
                    cands.append(dict(doc, deck=d))
                    moves.append((i, o))
        if not cands:
            break
        res = ex.call(fam, {"op": "eval", "builds": cands})["results"]
        k = max(range(len(res)), key=lambda j: res[j]["score"] if res[j]["ok"] else float("-inf"))
        if not res[k]["ok"] or res[k]["score"] <= best + 1e-12:
            break
        best = res[k]["score"]
        cur[moves[k][0]] = moves[k][1]
    return dict(doc, deck=cur)


def layout_variants(path, stage):
    """Candidate layouts (extract/deckfast_study_layouts.py output) as {name: (layout entry, 1.0)}. Each candidate's
    global implicit rule is registered, and candidates whose ledger NDM disagrees with DeckFAST's are dropped."""
    addend = catalog.load()["greed_cards"][stages.STAGES[stage]["greed_card"]] - 1.0
    out = {}
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict):
        keys = data.get("stages", {}).get(stage)
        entries = [dict(data["decks"][k], key=k) for k in (keys if keys is not None else data["decks"])]
        for e in entries:
            for name, rule in (e.get("implicit_rules") or {}).items():
                deck.IMPLICITS.setdefault(name, rule)
            out[e["key"]] = (e, 1.0)
        return out
    for o in data:
        for name, rule in o.get("implicit_rules", {}).items():
            deck.IMPLICITS[name] = rule
        cells = {tuple(p): "_" for p in o["slots"]}
        for r, c, t in o["assignment"]:
            if t != "dead":
                cells[(r, c)] = CHAR[t]
        rmin, rmax = min(r for r, _ in cells), max(r for r, _ in cells)
        cmin, cmax = min(c for _, c in cells), max(c for _, c in cells)
        grid = ["".join(cells.get((r, c), " ") for c in range(cmin, cmax + 1)) for r in range(rmin, rmax + 1)]
        lay = {"deck": o["deck"], "key": o["key"], "config": o["config"], "ndm_deckfast": o["ndm"],
               "cores": o["cores"], "implicits": o["implicits"], "core_slots": o["core_slots"], "grid": grid}
        slots, _ = deck.score_layout(grid, lay["cores"], lay["implicits"], core_values={"greed": addend})
        ndm = deck.blanket_ndm(slots)
        if abs(ndm - o["ndm"]) > 1e-6 * max(1.0, o["ndm"]):
            print(f"deck_study: dropped {o['deck']} {'+'.join(o['cores'])}: ledger NDM {ndm:.2f} vs DeckFAST {o['ndm']:.2f}")
            continue
        out[f"{o['deck']}|{'+'.join(o['cores'])}"] = (lay, 1.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="end,max")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--iters", type=int, default=300000)
    ap.add_argument("--families", default="")
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "deck_study.json"))
    ap.add_argument("--layouts", default="", help="candidate layout JSON: compare whole layouts (one stage only)")
    ap.add_argument("--greedy-init", action="store_true", help="deck-only greedy ascent before each re-anneal "
                    "(fair starts for layouts the stored build was not optimized on)")
    ap.add_argument("--mult-tags", default="", help="comma factors: variants x<k><tag> multiply the stage deck's "
                    "contribution of cards carrying that tag group (a hypothetical multiplicative tag core)")
    args = ap.parse_args()
    if stages.LEDGER != "rework":
        raise SystemExit("deck_study: run with WV_LEDGER=rework")

    found = json.load(open(os.path.join(ROOT, "out", "found_rework.json"), encoding="utf-8"))
    want = set(args.stages.split(","))
    fams = set(args.families.split(",")) if args.families else None
    rows = [r for r in found if r["variant"] == "all" and r["mode"] == "intended" and r["stage"] in want
            and (fams is None or r["family"] in fams)]

    base_layouts = deck.load_layouts()
    extra = {}
    real_load = deck.load_layouts
    deck.load_layouts = lambda: dict(real_load(), **extra)

    jobs, keys, ctxs = [], [], {}
    for stage in sorted(want):
        key0 = stages.STAGES[stage]["deck"]["layout"]
        if args.mult_tags:
            vars_ = {"base": (None, 1.0)}
            for k in args.mult_tags.split(","):
                for t, groups in deck.TAG_CORES.items():
                    vars_[f"x{k}{t}"] = (None, (float(k), groups[0]))
        elif args.layouts:
            vars_ = dict({"base": (None, 1.0), "base*": (None, 1.0)}, **layout_variants(args.layouts, stage))
        else:
            vars_ = variants_for(base_layouts[key0])
        for vname, (lay, factor) in vars_.items():
            vkey = f"{key0}|study:{vname}"
            extra[vkey] = lay if lay is not None else dict(base_layouts[key0], key=vkey)
            ctx = run.Context(stage, "intended", "all", vkey)
            if isinstance(factor, tuple):
                k, group = factor
                fam_of = [deck.SLOT_FAMILY[sl["kind"]] for sl in ctx.deck_slots]
                ctx.deck_options = [
                    {cid: ((a, c * k, v, m * k) if group in ctx.card_registry[fam_of[i]][cid]["groups"]
                           else (a, c, v, m)) for cid, (a, c, v, m) in opts.items()}
                    for i, opts in enumerate(ctx.deck_options)]
            elif factor != 1.0:
                ctx.deck_options = [{cid: (a, c * factor, v, m * factor) for cid, (a, c, v, m) in opts.items()}
                                    for opts in ctx.deck_options]
            ctxs[(stage, vname)] = ctx

    checked = 0
    greedy_jobs = []
    for r in rows:
        fam = families.get(r["family"])
        ctx0 = run.ctx_for(r["stage"], "intended", "all", r["deck"])
        ex0 = kernel.export_for(ctx0)
        b = ex0.to_build(r["doc"], fam)
        for (stage, vname), ctx in ctxs.items():
            if stage != r["stage"]:
                continue
            ex = kernel.export_for(ctx)
            if vname != "base*" and len(ctx.deck_options) == len(b.deck) and all(
                    c in o for c, o in zip(b.deck, ctx.deck_options)):
                init = dict(r["doc"], deck=[ex.card_ids.ids[c] for c in b.deck])
            else:
                init = dict(r["doc"], deck=[ex.card_ids.ids[next(iter(o))] for o in ctx.deck_options])
            if vname == "base" and checked < 3:
                s0 = ex0.call(fam, {"op": "eval", "builds": [r["doc"]]})["results"][0]["score"]
                s1 = ex.call(fam, {"op": "eval", "builds": [init]})["results"][0]["score"]
                if abs(s0 - s1) > 1e-9:
                    raise SystemExit(f"deck_study: remapped doc scores {s1} vs {s0} for {r['family']}; index tables differ")
                checked += 1
            if args.greedy_init:
                greedy_jobs.append((len(jobs), ex, fam, init, list(ctx.deck_options)))
            for k in range(args.seeds):
                jobs.append((ctx, fam, args.iters, 7919 * k + 17, run.P2_SCHEDULE, False, init, False, -1, REWORK_OPTS))
                keys.append((r["stage"], r["family"], vname))

    if greedy_jobs:
        from concurrent.futures import ThreadPoolExecutor
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            inits = list(pool.map(lambda g: deck_greedy(g[1], g[2], g[3], g[4]), greedy_jobs))
        for (j0, *_rest), init in zip(greedy_jobs, inits):
            for k in range(args.seeds):
                jobs[j0 + k] = jobs[j0 + k][:6] + (init,) + jobs[j0 + k][7:]
        print(f"  greedy deck init for {len(greedy_jobs)} build x variant starts in {time.time() - t0:.0f}s", flush=True)

    print(f"deck_study: {len(rows)} builds, {len(ctxs)} stage variants, {len(jobs)} kernel jobs", flush=True)
    t0 = time.time()
    res, secs = {}, []
    for key, (score, _doc, info) in zip(keys, kernel.batch_anneal(jobs)):
        res.setdefault(key, []).append(score)
        secs.append((info.get("secs", 0.0), info.get("evals", 0), key))
    print(f"  done in {time.time() - t0:.0f}s", flush=True)
    secs.sort(key=lambda x: -x[0])
    tot = sum(x[0] for x in secs)
    print(f"  job time: total {tot:.0f} s, median {secs[len(secs) // 2][0]:.2f} s; slowest:")
    for sc, ev, key in secs[:12]:
        print(f"    {sc:8.1f} s  {ev:>9} evals  {key}")

    out = [{"stage": s, "family": f, "variant": v, "scores": sc} for (s, f, v), sc in sorted(res.items())]
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    if args.layouts or args.mult_tags:
        layout_summary(res, ctxs, args.seeds)
        return
    m = {k: statistics.mean(v) for k, v in res.items()}
    for stage in sorted(want):
        names = [v for (s, v) in ctxs if s == stage]
        fams_s = sorted({f for (s, f, _v) in res if s == stage})
        base = {f: m[(stage, f, "base")] for f in fams_s}
        sd = [statistics.stdev(res[(stage, f, "base")]) for f in fams_s]
        se = statistics.mean(sd) / len(res[(stage, fams_s[0], "base")]) ** 0.5
        print(f"\n=== {stage}: mean-over-seeds score minus base (cycles), {len(fams_s)} builds; "
              f"base seed sd {statistics.mean(sd):.2f}, so one variant-vs-base difference has se ~{se * 2 ** 0.5:.2f}")
        print(f"{'variant':16s} {'mean':>7s} {'min':>7s} {'max':>7s}  best-for-n-builds")
        wins = {}
        for f in fams_s:
            v = max((v for v in names if v.startswith("+")), key=lambda v: m[(stage, f, v)])
            wins[v] = wins.get(v, 0) + 1
        for v in names:
            d = [m[(stage, f, v)] - base[f] for f in fams_s]
            print(f"{v:16s} {statistics.mean(d):7.3f} {min(d):7.3f} {max(d):7.3f}  {wins.get(v, '')}")
        print(f"\n  per build (mean over seeds): deck worth, +25% NDM, and each added tag core vs base")
        print(f"    {'build':40s} {'base':>6s} {'nodeck':>7s} {'ndm125':>7s} " + " ".join(f"{'+' + t:>9s}" for t in TAGS))
        for f in fams_s:
            print(f"    {f:40s} {base[f]:6.2f} {m[(stage, f, 'nodeck')] - base[f]:+7.2f} {m[(stage, f, 'ndm125')] - base[f]:+7.2f} "
                  + " ".join(f"{m[(stage, f, '+' + t)] - base[f]:+9.2f}" for t in TAGS))


def layout_summary(res, ctxs, n_seeds):
    """Specialization value with held-out seeds: each build picks its best layout on seeds A and is scored on seeds B;
    compared with the one layout whose mean over builds is best on seeds A (also scored on B)."""
    half = n_seeds // 2
    for stage in sorted({s for s, _ in ctxs}):
        names = [v for (s, v) in ctxs if s == stage]
        fams_s = sorted({f for (s, f, _v) in res if s == stage})
        A = {(f, v): statistics.mean(res[(stage, f, v)][:half]) for f in fams_s for v in names}
        B = {(f, v): statistics.mean(res[(stage, f, v)][half:]) for f in fams_s for v in names}
        common = max(names, key=lambda v: statistics.mean(A[(f, v)] for f in fams_s))
        own = {f: max(names, key=lambda v: A[(f, v)]) for f in fams_s}
        gain = [B[(f, own[f])] - B[(f, common)] for f in fams_s]
        sd = statistics.mean(statistics.stdev(res[(stage, f, "base")]) for f in fams_s)
        print(f"\n=== {stage}: {len(names)} layouts x {len(fams_s)} builds, seed sd {sd:.2f} cycles")
        print(f"  best common layout (seeds A): {common}")
        print(f"  specialization value on held-out seeds B: mean {statistics.mean(gain):+.3f}, "
              f"median {statistics.median(gain):+.3f}, min {min(gain):+.3f}, max {max(gain):+.3f} cycles")
        print(f"  base (current stage layout) vs common, seeds B: "
              f"{statistics.mean(B[(f, 'base')] - B[(f, common)] for f in fams_s):+.3f} cycles")
        ranked = sorted(names, key=lambda v: -statistics.mean(A[(f, v)] for f in fams_s))
        print("  top layouts by mean over builds (A | B):")
        for v in ranked[:12]:
            print(f"    {v:60s} {statistics.mean(A[(f, v)] for f in fams_s):7.2f} | "
                  f"{statistics.mean(B[(f, v)] for f in fams_s):7.2f}")
        counts = {}
        for f in fams_s:
            counts[own[f]] = counts.get(own[f], 0) + 1
        print("  each build's own best layout (seeds A) and its held-out gain over the common layout:")
        for f in fams_s:
            print(f"    {f:42s} {own[f]:60s} {B[(f, own[f])] - B[(f, common)]:+6.2f}")
        print("  distinct own-best layouts:", len(counts), sorted(counts.items(), key=lambda kv: -kv[1])[:8])


if __name__ == "__main__":
    main()
