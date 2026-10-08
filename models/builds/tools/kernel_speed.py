"""Kernel throughput and delta-scoring check: one 80k-step anneal per sample family and stage.

python tools/kernel_speed.py [--verify]
--verify re-scores every delta-scored candidate from scratch and reports disagreements (expect 0 and max error
around 1e-12 cycles); throughput roughly halves while it runs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.context import Context
from model import families, kernel

SCHEDULE = {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05}

if __name__ == "__main__":
    verify = "--verify" in sys.argv
    bad = 0
    for stage in ("early", "max"):
        ex = kernel.export_for(Context(stage, "bugged"))
        for fid in ("melee:axe", "ability:Ice_Bolt_Base", "ability:Smite_Archon", "ability:Fangs_Maw"):
            out = ex.call(families.get(fid), {"op": "anneal", "iters": 80000, "seeds": [3], "schedule": SCHEDULE,
                                              "trace": False, "verify_delta": verify})
            r, d = out["results"][0], out["delta"]
            bad += d["mismatch"]
            extra = f"  delta checked {d['checked']}, mismatches {d['mismatch']}, max error {d['max_err']:.1e}" if verify else ""
            print(f"{stage:5s} {fid:26s} {r['evals'] / r['secs'] / 1000:5.0f}k evals/s  {r['secs']:.2f}s{extra}")
    sys.exit(1 if bad else 0)
