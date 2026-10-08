"""Verify every model against its reference numbers. Run after setup and after any model change.

    python setup/check.py                 # all gates
    python setup/check.py roomlab builds  # selected gates

Gates:
  roomlab     regenerating examples/extraction1 reproduces the shipped .nbt exactly, and lint passes
  routerunner run_cells on two panel cells lands within 1 % of the published panel v2.2 numbers
  builds      the Rust build kernel scores 8000+ random legal builds exactly like the Python model
  decks       the Rust deck kernel scores random assignments exactly like the Python reference
"""
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(cmd, cwd):
    print(f"  $ {' '.join(os.path.basename(c) if c == PY else c for c in cmd)}   (in {os.path.relpath(cwd, ROOT) or '.'})", flush=True)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def payload_md5(path):
    with gzip.open(path) as f:
        return hashlib.md5(f.read()).hexdigest()


def gate_roomlab():
    lab = os.path.join(ROOT, "roomlab")
    ref = os.path.join(lab, "examples", "extraction1", "extraction1.nbt")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "regen.nbt")
        r = run([PY, os.path.join("examples", "extraction1", "generate.py"), out, "--seed", "7"], lab)
        if r.returncode != 0:
            return False, r.stderr[-1500:]
        same = payload_md5(out) == payload_md5(ref)
    r = run([PY, "lint.py", ref], lab)
    lint_ok = r.returncode == 0 and "contract satisfied" in r.stdout
    return same and lint_ok, f"regenerated payload identical: {same}; lint: {'pass' if lint_ok else r.stdout[-800:]}"


def gate_routerunner():
    panel = os.path.join(ROOT, "models", "routerunner", "benchmarks", "panel-v2.2", "cells_v22.json")
    with open(panel, encoding="utf-8") as f:
        cells = json.load(f)
    cells = cells if isinstance(cells, list) else cells.get("cells", cells)
    ref = {(c["b"], c["c"]): c for c in cells}
    want = [(30, 30), (45, 45)]
    r = run([PY, os.path.join("models", "routerunner", "sim", "run_cells.py"), "--cells", *[f"{b},{c}" for b, c in want],
             "--picker"], ROOT)
    if r.returncode != 0:
        return False, (r.stdout + r.stderr)[-1500:]
    path = next((l.split("wrote ", 1)[1].split(" (")[0] for l in r.stdout.splitlines() if l.startswith("wrote ")), None)
    if not path:
        return False, "run_cells printed no output path"
    worst, lines = 0.0, []
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            exp = ref[(d["b"], d["c"])][d["miner"]]
            dev = d["cpm"] / exp - 1
            worst = max(worst, abs(dev))
            lines.append(f"{d['b']},{d['c']} {d['miner']}: {d['cpm']:.0f} vs panel {exp} ({100 * dev:+.2f} %)")
    return worst <= 0.01, "; ".join(lines)


def gate_builds():
    b = os.path.join(ROOT, "models", "builds")
    for script in ("extract/extract.py", "extract/extract_hyper.py"):
        if not os.path.exists(os.path.join(b, "data", "catalog_0.34.1.json" if "hyper" not in script else "hyper_pools_0.34.1.json")):
            r = run([PY, script], b)
            if r.returncode != 0:
                return False, r.stderr[-1500:]
    r = run([PY, os.path.join("tools", "kernel_parity.py")], b)
    tail = [l for l in r.stdout.splitlines() if l.startswith("TOTAL")]
    passed = r.returncode == 0 and bool(tail) and " 0 mismatches" in tail[-1]
    return passed, tail[-1] if tail else (r.stdout + r.stderr)[-1500:]


def gate_decks():
    d = os.path.join(ROOT, "models", "decks")
    env = dict(os.environ, UV_LINK_MODE="copy")
    print("  $ uv run python scripts/parity_2_0.py --fast   (in models/decks)", flush=True)
    r = subprocess.run(["uv", "run", "python", "scripts/parity_2_0.py", "--fast"], cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    part_a = [l.strip() for l in r.stdout.splitlines() if "Part A:" in l]
    if not part_a:
        return False, (r.stdout + r.stderr)[-1500:]
    return "PASS" in part_a[-1], part_a[-1] + "  (Part B is a stochastic search check and is not gated here)"


GATES = {"roomlab": gate_roomlab, "routerunner": gate_routerunner, "builds": gate_builds, "decks": gate_decks}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    names = sys.argv[1:] or list(GATES)
    unknown = [n for n in names if n not in GATES]
    if unknown:
        sys.exit(f"[check][ERROR] unknown gate(s) {unknown}; choose from {list(GATES)}")
    failed = []
    for n in names:
        print(f"\n[{n}]")
        try:
            passed, detail = GATES[n]()
        except FileNotFoundError as e:
            passed, detail = False, f"{e} (run python setup/doctor.py)"
        print(f"  {'PASS' if passed else 'FAIL'}  {detail}")
        if not passed:
            failed.append(n)
    print(f"\n{len(names) - len(failed)}/{len(names)} gates passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
