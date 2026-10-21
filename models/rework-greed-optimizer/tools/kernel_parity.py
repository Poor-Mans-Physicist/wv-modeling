"""Parity check: random legal builds and the directed cases of tools/parity_cases.py, scored by model/evaluate.py and
by the Rust kernel, must agree.

python tools/kernel_parity.py [--ledger both|rework|0.34.1] [--per-family 25] [--steps 120] [--no-cases] [--exe PATH]
--ledger both (default) runs each ledger in its own process with WV_LEDGER set (the model reads it at import).
Random builds come from search.initial_build plus a random chain of search.mutate moves; a quarter of them get random
per-bug overrides so every bug switch is exercised in both directions. Each directed case runs in every mode it lists.
The older case lists run too: tools/parity_cases_talents.py (make(ctx, fam, rng) -> (build, live), for their stage in
every mode; a case whose feature is not live is a mismatch) and tools/parity_cases_etchings.py (Case objects with knob
overrides, cases_for(stage, mode)).
"""
import argparse
import math
import multiprocessing as mp
import os
import random
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

FIELDS = ("score", "cycle", "cycle_damage", "cycle_survival", "dps", "pack_dps", "ehp", "requirements.met",
          "derived.move_speed", "healing.per_s", "execute_threshold")
LEDGERS = ("rework", "0.34.1")


def py_fields(r):
    return {"score": r["score"], "cycle": r["cycle"], "cycle_damage": r["cycle_damage"], "cycle_survival": r["cycle_survival"],
            "dps": r["dps_no_hp_scaling"], "pack_dps": r["pack_dps"], "ehp": r["ehp"],
            "requirements.met": float(r["requirements"]["met"]), "derived.move_speed": r["derived"]["move_speed"],
            "healing.per_s": r["healing"]["per_s"], "execute_threshold": r["family_result"].get("execute_threshold", 0.0)}


def kernel_fields(kr):
    out = {f: kr[f] for f in ("score", "cycle", "cycle_damage", "cycle_survival", "dps", "pack_dps", "ehp")}
    out.update({"requirements.met": float(kr["met"]), "derived.move_speed": kr["move_speed"], "healing.per_s": kr["heal"],
                "execute_threshold": kr["theta"]})
    return out


def close(a, b):
    if a == b:
        return True
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)) or abs(a - b) <= 1e-7


def compare(builds, ctx, fam, exe, label):
    """Score `builds` in both engines; returns (n, mismatches, errors in both, worst[], Python results)."""
    from model import kernel
    from model.evaluate import evaluate
    ks = kernel.evaluate_many(ctx, fam, builds, exe=exe)
    n = bad = errs_both = 0
    worst, results = [], []
    for b, kr in zip(builds, ks):
        n += 1
        try:
            res = evaluate(b, ctx)
            pr, perr = py_fields(res), None
        except Exception as e:
            res, pr, perr = None, None, f"{type(e).__name__}: {e}"
        results.append(res)
        if perr or not kr["ok"]:
            if perr and not kr["ok"]:
                errs_both += 1
                continue
            bad += 1
            worst.append((label, "error mismatch", perr, kr.get("error")))
            continue
        kf = kernel_fields(kr)
        diffs = [(f, pr[f], kf[f]) for f in FIELDS if not close(pr[f], kf[f])]
        if diffs:
            bad += 1
            worst.append((label, diffs))
    return n, bad, errs_both, worst, results


def run(args):
    stage, mode, per_family, steps, seed, exe, cases_on = args
    from model.context import Context
    from model import families, search
    from model.kernel import BUG_IDS
    ctx = Context(stage, mode)
    rng = random.Random(seed)
    n = bad = eb = 0
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
        dn, dbad, deb, dw, _ = compare(builds, ctx, fam, exe, fam.id)
        n, bad, eb = n + dn, bad + dbad, eb + deb
        worst += dw
    named, nd, skipped = [], 0, []
    if cases_on:
        from parity_cases_talents import CASES as TCASES, seed as tseed
        import parity_cases_etchings as pce
        from model import stages
        for name, cstage, fid, ledgers, make in TCASES:
            if cstage != stage or stages.LEDGER not in ledgers:
                continue
            fam = families.get(fid)
            b, live = make(ctx, fam, random.Random(tseed(name)))
            dn, dbad, deb, dw, results = compare([b], ctx, fam, exe, name)
            n, bad, eb = n + dn, bad + dbad, eb + deb
            worst += dw
            ok = results[0] is not None and bool(live(results[0]))
            if not ok and not dbad and not deb:
                bad += 1
                worst.append((name, "feature not live"))
            named.append((name, results[0]["score"] if results[0] is not None else float("nan"), ok, not dbad and not deb))
        for c in pce.cases_for(stage, mode):
            cctx = pce.context(c, ctx)
            out = c.make(cctx)
            if out is None:
                skipped.append(c.name)
                continue
            cb = list(out) if isinstance(out, tuple) else [out]
            fam = families.get(c.family)
            dn, dbad, deb, dw, _ = compare(cb, cctx, fam, exe, c.name)
            n, bad, eb, nd = n + dn, bad + dbad, eb + deb, nd + dn
            worst += dw
    return stage, mode, n, bad, eb, worst[:8], named, nd, skipped


def run_case(args):
    """One directed case in one mode: (name, mode, status, detail); status ok / mismatch / skip / unchecked."""
    name, mode, seed, exe = args
    import parity_cases
    from model.context import Context
    from model import families
    c = next(x for x in parity_cases.CASES if x["name"] == name)
    ctx = Context(c["stage"], mode)
    fam = families.get(c["family"])
    if not ctx.mainhands(fam):
        return name, mode, "skip", f"{c['family']} has no main hand at {c['stage']}"
    b = c["build"](ctx, fam, random.Random(seed))
    if b is None:
        return name, mode, "skip", "the constructor could not express the feature here"
    _, bad, eb, worst, results = compare([b], ctx, fam, exe, name)
    if bad:
        return name, mode, "mismatch", worst
    if eb:
        return name, mode, "mismatch", "both engines raised"
    if c["check"] is not None:
        msg = c["check"](results[0], b, ctx)
        if msg:
            return name, mode, "unchecked", msg
    return name, mode, "ok", ""


def one_ledger(a):
    import parity_cases
    os.environ["WV_LEDGER"] = a.ledger
    exe = os.path.abspath(a.exe) if a.exe else None
    tasks = [(s, m, a.per_family, a.steps, 17 + i, exe, not a.no_cases) for i, (s, m) in enumerate(
        [(s, m) for s in ("early", "mid", "end", "max") for m in ("bugged", "intended")])]
    cases = [] if a.no_cases else [(c["name"], m, zlib.crc32(c["name"].encode()), exe) for c in parity_cases.CASES
                                   if a.ledger in c["ledgers"] for m in c["modes"]]
    optional = {c["name"]: c["optional"] for c in parity_cases.CASES}
    total = total_bad = 0
    with mp.Pool(min(8, os.cpu_count())) as pool:
        for stage, mode, n, bad, eb, worst, named, nd, skipped in pool.imap_unordered(run, tasks):
            total += n
            total_bad += bad
            print(f"[{a.ledger}] {stage:5s} {mode:8s}: {n} builds ({len(named)} talent cases, {nd} etching cases), {bad} mismatches, "
                  f"{eb} errors in both" + (f"; skipped etching cases: {skipped}" if skipped else ""))
            for w in worst:
                print("   ", w)
            for name, score, ok, same in named:
                if not ok or not same:
                    print(f"    talent case {name}: score {score:.4f}, feature {'live' if ok else 'NOT LIVE'}, "
                          f"{'match' if same else 'MISMATCH'}")
        counts = {}
        for name, mode, status, detail in pool.imap(run_case, cases):
            counts[status] = counts.get(status, 0) + 1
            if status != "ok":
                print(f"[{a.ledger}] case {name} ({mode}): {status} {detail}")
                if status != "skip" or not optional[name]:
                    total_bad += 1
    if cases:
        print(f"[{a.ledger}] directed cases: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"[{a.ledger}] TOTAL {total} random builds + {len(cases)} directed, {total_bad} mismatches")
    return total_bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", choices=("both",) + LEDGERS, default="both")
    ap.add_argument("--per-family", type=int, default=25)
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--no-cases", action="store_true", help="random builds only")
    ap.add_argument("--exe", default=None, help="kernel executable (default: kernel/target/release/wvk)")
    a = ap.parse_args()
    if a.ledger != "both":
        sys.exit(1 if one_ledger(a) else 0)
    rc = 0
    for led in LEDGERS:
        argv = [sys.executable, os.path.abspath(__file__), "--ledger", led, "--per-family", str(a.per_family),
                "--steps", str(a.steps)] + (["--no-cases"] if a.no_cases else []) + (["--exe", a.exe] if a.exe else [])
        rc |= subprocess.run(argv, env=dict(os.environ, WV_LEDGER=led)).returncode
    print("PARITY " + ("FAILED" if rc else "OK (both ledgers, 0 mismatches)"))
    sys.exit(1 if rc else 0)
