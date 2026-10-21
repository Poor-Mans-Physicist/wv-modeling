"""Re-score the saved search winners (out/found_rework.json) with one kernel build or the Python model, for
bit-identical A/B checks.

WV_LEDGER=rework python tools/rescore_found.py [--exe PATH | --python] [--out FILE]

Each row's Context is rebuilt with run.ctx_for, its build doc mapped back with Export.to_build and scored by
kernel.evaluate_many with the given executable (default: the normal kernel build), or by model.evaluate with
--python. Prints (or writes) a JSON list with one record per row in file order; floats are written with repr, so two
runs agree only if every score is bit-identical.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if os.environ.setdefault("WV_LEDGER", "rework") != "rework":
    sys.exit(f"rescore_found: out/found_rework.json holds rework builds; WV_LEDGER is {os.environ['WV_LEDGER']!r}")

import run  # noqa: E402
from model import families, kernel  # noqa: E402
from model.evaluate import evaluate  # noqa: E402

FOUND = os.path.join(ROOT, "out", "found_rework.json")
KEYS = ("stage", "mode", "family", "variant", "deck")
FIELDS = ("ok", "error", "score", "cycle", "cycle_damage", "cycle_survival", "dps", "pack_dps", "ehp")
PY_FIELDS = {"score": "score", "cycle": "cycle", "cycle_damage": "cycle_damage", "cycle_survival": "cycle_survival",
             "dps": "dps_no_hp_scaling", "pack_dps": "pack_dps", "ehp": "ehp"}


def py_scores(ctx, builds):
    out = []
    for b in builds:
        try:
            r = evaluate(b, ctx)
        except Exception as e:
            out.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        out.append(dict({"ok": True}, **{k: r[v] for k, v in PY_FIELDS.items()}))
    return out


def rescore(rows, exe=None, python=False):
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault((r["stage"], r["mode"], r["variant"], r["deck"], r["family"]), []).append(i)
    out = [None] * len(rows)
    for (stage, mode, variant, deck, fid), idx in groups.items():
        ctx = run.ctx_for(stage, mode, variant, deck)
        fam = families.get(fid)
        ex = kernel.export_for(ctx)
        builds = [ex.to_build(rows[i]["doc"], fam) for i in idx]
        scores = py_scores(ctx, builds) if python else kernel.evaluate_many(ctx, fam, builds, exe=exe)
        for i, kr in zip(idx, scores):
            rec = {k: rows[i][k] for k in KEYS}
            rec.update({f: kr[f] for f in FIELDS if f in kr})
            out[i] = rec
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=None, help="kernel executable (default: kernel/target/release/wvk)")
    ap.add_argument("--python", action="store_true", help="score with the Python model instead of the kernel")
    ap.add_argument("--found", default=FOUND)
    ap.add_argument("--out", default=None, help="write the JSON here instead of stdout")
    a = ap.parse_args()
    with open(a.found, encoding="utf-8") as fh:
        rows = json.load(fh)
    if a.python and a.exe:
        sys.exit("rescore_found: --python and --exe are exclusive")
    res = rescore(rows, os.path.abspath(a.exe) if a.exe else None, a.python)
    text = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        bad = sum(1 for r in res if not r.get("ok"))
        print(f"rescore_found: {len(res)} rows, {bad} {'Python' if a.python else 'kernel'} errors -> {a.out}", file=sys.stderr)
    else:
        print(text)
