"""Kernel throughput and delta-scoring check: one 80k-step anneal per sample family and stage.

python tools/kernel_speed.py [--verify] [--exe PATH ...] [--repeat N] [--iters 80000]
--verify re-scores every delta-scored candidate from scratch and reports disagreements (expect 0 and max error
around 1e-12 cycles); throughput roughly halves while it runs.
--exe (repeatable) times each executable on the same anneals, interleaved, and prints the median evals/s of
--repeat runs per executable and the ratio to the first one (A/B: `--exe kernel/target/release/wvk_base.exe --exe
kernel/target/release/wvk.exe --repeat 5`). Same seeds, so every executable walks the same build sequence as long as
its scores agree.
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.context import Context
from model import families, kernel

SCHEDULE = {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05}
FAMILIES = ("melee:axe", "ability:Ice_Bolt_Base", "ability:Smite_Archon", "ability:Fangs_Maw")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--exe", action="append", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--iters", type=int, default=80000)
    a = ap.parse_args()
    exes = [os.path.abspath(e) for e in a.exe] if a.exe else [None]
    bad = 0
    for stage in ("early", "max"):
        ex = kernel.export_for(Context(stage, "bugged"))
        for fid in FAMILIES:
            rates = [[] for _ in exes]
            extra = ""
            for _ in range(a.repeat):
                for k, exe in enumerate(exes):
                    out = ex.call(families.get(fid), {"op": "anneal", "iters": a.iters, "seeds": [3], "schedule": SCHEDULE,
                                                      "trace": False, "verify_delta": a.verify}, exe=exe)
                    r, d = out["results"][0], out["delta"]
                    bad += d["mismatch"]
                    rates[k].append(r["evals"] / r["secs"] / 1000)
                    if a.verify:
                        extra = f"  delta checked {d['checked']}, mismatches {d['mismatch']}, max error {d['max_err']:.1e}"
            med = [statistics.median(x) for x in rates]
            cols = "  ".join(f"{m:5.0f}k" for m in med)
            ratio = "  " + "  ".join(f"x{m / med[0]:.3f}" for m in med[1:]) if len(med) > 1 else ""
            print(f"{stage:5s} {fid:26s} {cols} evals/s{ratio}{extra}")
    sys.exit(1 if bad else 0)
