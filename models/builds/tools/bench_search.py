"""Search-quality benchmark for the Rust kernel: independent replicates of each search config on a fixed problem set.

python tools/bench_search.py [--replicates 12] [--configs a,b] [--ref-iters 1000000] [--out out/bench.json]

Each replicate runs one config (iterations x restarts, schedule, legacy or legal-only proposals) with fresh seeds and
keeps the best restart, as run.py does. The gap of a replicate is best-known minus its score, where best-known is the
highest score any run (including the long reference runs) found for that problem. Every replicate's best build is
re-scored in Python; a mismatch is counted and reported.
"""
import argparse
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAMILIES = ["melee:axe", "ability:Ice_Bolt_Base", "ability:Fangs_Maw", "ability:Smite_Archon",
            "ability:Fireball_Fireshot", "ability:Javelin_Base"]
STAGES = ["early", "mid", "end", "max"]
MODE = "intended"

GEOM = {"kind": "geometric", "t0": 0.6, "t1": 0.004}
TARGET = {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05}
CONFIGS = {
    "old: 20k x4, Python rules": dict(iters=20000, restarts=4, schedule=GEOM, legacy=True),
    "20k x4, legal-only": dict(iters=20000, restarts=4, schedule=GEOM, legacy=False),
    "80k x2, legal-only": dict(iters=80000, restarts=2, schedule=GEOM, legacy=False),
    "new: 80k x2, legal, target": dict(iters=80000, restarts=2, schedule=TARGET, legacy=False),
}

_ctx = {}


def ctx_for(stage):
    from model.context import Context
    if stage not in _ctx:
        _ctx[stage] = Context(stage, MODE)
    return _ctx[stage]


def run(task):
    stage, fid, cname, cfg, rep = task
    from model import families, kernel
    from model.evaluate import evaluate
    ctx = ctx_for(stage)
    fam = families.get(fid)
    base = zlib.crc32(f"{stage}|{fid}|{cname}|{rep}".encode())
    seeds = [base * 16 + k for k in range(cfg["restarts"])]
    t = time.time()
    res = kernel.anneal(ctx, fam, cfg["iters"], seeds, schedule=cfg["schedule"], legacy=cfg["legacy"])
    wall = time.time() - t
    scores = [r[0] for r in res]
    best = max(res, key=lambda r: r[0])
    py = evaluate(best[1], ctx)["score"]
    return {"stage": stage, "family": fid, "config": cname, "rep": rep, "best": best[0], "scores": scores,
            "evals": sum(r[2]["evals"] for r in res), "secs": sum(r[2]["secs"] for r in res), "wall": wall,
            "parity_ok": abs(py - best[0]) <= 1e-7 * max(1.0, abs(py))}


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=12)
    ap.add_argument("--configs", default="")
    ap.add_argument("--config-file", default="", help="JSON {name: {iters, restarts, schedule, legacy}} replacing CONFIGS")
    ap.add_argument("--ref-iters", type=int, default=1000000)
    ap.add_argument("--ref-replicates", type=int, default=3)
    ap.add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "bench_search.json"))
    a = ap.parse_args()
    if a.config_file:
        CONFIGS = json.load(open(a.config_file))
    configs = {k: v for k, v in CONFIGS.items() if not a.configs or any(x in k for x in a.configs.split(","))}
    if a.ref_iters > 0:
        configs["reference"] = dict(iters=a.ref_iters, restarts=1, schedule=GEOM, legacy=False)
    tasks = []
    for cname, cfg in configs.items():
        reps = a.ref_replicates if cname == "reference" else a.replicates
        for s in STAGES:
            for f in FAMILIES:
                for r in range(reps):
                    tasks.append((s, f, cname, cfg, r))
    tasks.sort(key=lambda t: -t[3]["iters"] * t[3]["restarts"])
    t0 = time.time()
    with mp.Pool(a.procs) as pool:
        rows = list(pool.imap_unordered(run, tasks, chunksize=1))
    print(f"{len(rows)} replicates in {time.time() - t0:.0f}s")
    best_known = {}
    for r in rows:
        k = (r["stage"], r["family"])
        best_known[k] = max(best_known.get(k, -1e18), max(r["scores"]))
    summary = {}
    for cname in configs:
        R = [r for r in rows if r["config"] == cname]
        gaps = [best_known[(r["stage"], r["family"])] - r["best"] for r in R]
        summary[cname] = {
            "replicates": len(R), "gap_median": statistics.median(gaps), "gap_mean": statistics.mean(gaps),
            "gap_p90": pct(gaps, 0.9), "gap_max": max(gaps), "within_0.05": sum(g <= 0.05 for g in gaps) / len(gaps),
            "within_0.2": sum(g <= 0.2 for g in gaps) / len(gaps), "evals_mean": statistics.mean(r["evals"] for r in R),
            "secs_mean": statistics.mean(r["secs"] for r in R), "parity_fail": sum(not r["parity_ok"] for r in R),
            "by_problem": {f"{s}|{f}": {"gap_median": statistics.median(
                [best_known[(s, f)] - r["best"] for r in R if r["stage"] == s and r["family"] == f]),
                "gap_max": max(best_known[(s, f)] - r["best"] for r in R if r["stage"] == s and r["family"] == f)}
                for s in STAGES for f in FAMILIES},
        }
    print(f"\n{'config':28s} {'gap med':>8s} {'mean':>7s} {'p90':>7s} {'max':>7s} {'<=0.05':>7s} {'<=0.2':>7s} {'evals':>9s} {'cpu s':>7s} parity")
    for cname, s in summary.items():
        print(f"{cname:28s} {s['gap_median']:8.3f} {s['gap_mean']:7.3f} {s['gap_p90']:7.3f} {s['gap_max']:7.3f} "
              f"{s['within_0.05']:7.0%} {s['within_0.2']:7.0%} {s['evals_mean']:9.0f} {s['secs_mean']:7.2f} {s['parity_fail']}")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"rows": rows, "best_known": {f"{k[0]}|{k[1]}": v for k, v in best_known.items()}, "summary": summary},
              open(a.out, "w"), indent=1)
    print("wrote", a.out)
