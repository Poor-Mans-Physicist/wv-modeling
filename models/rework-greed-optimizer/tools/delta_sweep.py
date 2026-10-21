"""Delta sweep (test only): one short kernel anneal per family x stage x mode with verify_delta, which re-scores every
delta-scored candidate from scratch; any disagreement above 1e-9 relative is a mismatch (a rounding edge or a stale
cache in delta scoring).

python tools/delta_sweep.py [--ledger both|rework|0.34.1] [--iters 150000] [--seeds 2] [--threads 0]
--ledger both (default) runs each ledger in its own process. Modes are the ledger's search modes (stages.MODES).
Runtime on the 24-thread dev machine: about 20 s for the rework and 40 s for 0.34.1 at the defaults (~1 minute in
all, about 150M candidates checked); it scales linearly with --iters x --seeds.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LEDGERS = ("rework", "0.34.1")
SCHEDULE = {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05}


def one_ledger(a):
    os.environ["WV_LEDGER"] = a.ledger
    from model import stages, families, kernel
    from model.context import Context
    t0 = time.time()
    jobs, labels = [], []
    for stage in stages.STAGES:
        for mode in stages.MODES:
            ctx = Context(stage, mode)
            for fam in families.all_families():
                if not ctx.mainhands(fam):
                    continue
                for seed in range(1, a.seeds + 1):
                    jobs.append((ctx, fam, a.iters, seed, SCHEDULE, False))
                    labels.append((stage, mode, fam.id, seed))
    t1 = time.time()
    res = kernel.batch_anneal(jobs, threads=a.threads, verify_delta=True)
    checked = bad = 0
    worst = 0.0
    for (stage, mode, fid, seed), (_, _, info) in zip(labels, res):
        d = info["delta"]
        checked += d["checked"]
        bad += d["mismatch"]
        worst = max(worst, d["max_err"])
        if d["mismatch"]:
            print(f"[{a.ledger}] {stage:5s} {mode:8s} {fid:34s} seed {seed}: {d['mismatch']} of {d['checked']} mismatched, "
                  f"max error {d['max_err']:.2e}")
    print(f"[{a.ledger}] {len(jobs)} anneals, {checked} candidates checked, {bad} mismatches, max error {worst:.1e} "
          f"(contexts {t1 - t0:.0f}s, kernel {time.time() - t1:.0f}s)")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", choices=("both",) + LEDGERS, default="both")
    ap.add_argument("--iters", type=int, default=150000)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--threads", type=int, default=0)
    a = ap.parse_args()
    if a.ledger != "both":
        sys.exit(1 if one_ledger(a) else 0)
    rc = 0
    for led in LEDGERS:
        argv = [sys.executable, os.path.abspath(__file__), "--ledger", led, "--iters", str(a.iters), "--seeds", str(a.seeds),
                "--threads", str(a.threads)]
        rc |= subprocess.run(argv, env=dict(os.environ, WV_LEDGER=led)).returncode
    print("DELTA SWEEP " + ("FAILED" if rc else "OK (both ledgers, 0 mismatches)"))
    sys.exit(1 if rc else 0)
