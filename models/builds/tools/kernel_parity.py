"""Parity check: random legal builds scored by model/evaluate.py and by the Rust kernel must agree.

python tools/kernel_parity.py [--per-family 25] [--steps 120]
Builds come from search.initial_build plus a random chain of search.mutate moves; a quarter of them get random
per-bug overrides so every bug switch is exercised in both directions.
"""
import argparse
import math
import multiprocessing as mp
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIELDS = ("score", "cycle", "cycle_damage", "cycle_survival", "dps", "pack_dps", "ehp")


def py_fields(r):
    return {"score": r["score"], "cycle": r["cycle"], "cycle_damage": r["cycle_damage"], "cycle_survival": r["cycle_survival"],
            "dps": r["dps_no_hp_scaling"], "pack_dps": r["pack_dps"], "ehp": r["ehp"]}


def close(a, b):
    if a == b:
        return True
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)) or abs(a - b) <= 1e-7


def run(args):
    stage, mode, per_family, steps, seed = args
    from model.context import Context
    from model import families, search, kernel
    from model.evaluate import evaluate
    from model.kernel import BUG_IDS
    ctx = Context(stage, mode)
    rng = random.Random(seed)
    n = bad = errs_both = 0
    worst = []
    for fam in families.all_families():
        if not ctx.mainhands(fam):
            continue
        builds = []
        for k in range(per_family):
            b = search.initial_build(ctx, fam, rng)
            for _ in range(rng.randrange(steps)):
                c = search.mutate(ctx, b, fam, rng)
                if c is not None:
                    b = c
            if rng.random() < 0.25:
                b.bug_overrides = {bid: rng.choice(["bugged", "intended"]) for bid in BUG_IDS if rng.random() < 0.5}
            builds.append(b)
        ks = kernel.evaluate_many(ctx, fam, builds)
        for b, kr in zip(builds, ks):
            n += 1
            try:
                pr = py_fields(evaluate(b, ctx))
                perr = None
            except Exception as e:
                pr, perr = None, f"{type(e).__name__}: {e}"
            if perr or not kr["ok"]:
                if perr and not kr["ok"]:
                    errs_both += 1
                    continue
                bad += 1
                worst.append((fam.id, "error mismatch", perr, kr.get("error")))
                continue
            diffs = [(f, pr[f], kr[f]) for f in FIELDS if not close(pr[f], kr[f])]
            if diffs:
                bad += 1
                worst.append((fam.id, diffs))
    return stage, mode, n, bad, errs_both, worst[:8]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-family", type=int, default=25)
    ap.add_argument("--steps", type=int, default=120)
    a = ap.parse_args()
    tasks = [(s, m, a.per_family, a.steps, 17 + i) for i, (s, m) in enumerate(
        [(s, m) for s in ("early", "mid", "end", "max") for m in ("bugged", "intended")])]
    total = total_bad = 0
    with mp.Pool(min(8, os.cpu_count())) as pool:
        for stage, mode, n, bad, eb, worst in pool.imap_unordered(run, tasks):
            total += n
            total_bad += bad
            print(f"{stage:5s} {mode:8s}: {n} builds, {bad} mismatches, {eb} errors in both")
            for w in worst:
                print("   ", w)
    print(f"TOTAL {total} builds, {total_bad} mismatches")
    sys.exit(1 if total_bad else 0)
