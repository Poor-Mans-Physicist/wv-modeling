"""Chests per room for a list of (Bonus, Cascade) crystals, with the 3.21.6 event schedule.

Uses `wv_vault_grid cells` (themed common rooms, chunk alignment, strongbox rolls), which is the
validated path (real ÷ simulated 1.03–1.15 on living vaults). Prefer it over `wv-modifier-panel`,
which predates the 3.21.6 schedule and under-counts stacked crystals.

    python libs/vaultsim/scripts/chest_counts.py --cells 0,0 30,30 51,74 --chest living --per 64
    python libs/vaultsim/scripts/chest_counts.py --cells 45,45 --themes desert --level 100

Prints mean chests per room with a 95 % interval on the mean, mean clumpiness (sum of squared chain
component sizes over N; higher favours Vein Miner) and the strongbox share. Raw records go to
out/vaultsim/.
"""
import argparse
import collections
import json
import math
import os
import subprocess
import sys
import time

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
GEN = os.path.join(REPO, "libs", "vaultsim", "target", "release", "wv_vault_grid" + (".exe" if os.name == "nt" else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", nargs="+", required=True, help="Bonus,Cascade pairs")
    ap.add_argument("--chest", default="living", choices=["living", "gilded", "ornate"])
    ap.add_argument("--per", type=int, default=64, help="rooms per cell")
    ap.add_argument("--level", type=int, default=475)
    ap.add_argument("--themes", default="beach,cave,desert,nether,void")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    if not os.path.exists(GEN):
        sys.exit(f"[chest_counts][ERROR] {GEN} not built: cargo build --release in libs/vaultsim")
    out_dir = os.path.join(REPO, "out", "vaultsim")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    tsv = os.path.join(out_dir, f"cells_{stamp}.tsv")
    rooms = os.path.join(out_dir, f"rooms_{stamp}.jsonl")
    with open(tsv, "w") as f:
        for s in a.cells:
            b, c = s.split(",")
            f.write(f"{int(b)} {int(c)}\n")
    cmd = [GEN, "cells", "--cells", tsv, "--per", str(a.per), "--seed", str(a.seed), "--level", str(a.level),
           "--themes", a.themes, "--chest", a.chest, "--out", rooms, "--crn"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[chest_counts][ERROR] generator failed:\n{r.stderr[-2000:]}")
    for line in r.stderr.splitlines():
        if any(w in line.lower() for w in ("fallback", "warn", "error")):
            print("  [vaultsim] " + line)
    by = collections.defaultdict(list)
    for line in open(rooms):
        real = json.loads(line)["real"]
        by[(real["b"], real["c"])].append(real)
    print(f"{a.chest} chests, level {a.level}, themes {a.themes}, {a.per} rooms per cell")
    print(f"{'bonus':>5} {'cascade':>7} {'chests/room':>11} {'95% CI':>15} {'clump':>7} {'strongbox':>9}")
    for (b, c), rs in sorted(by.items()):
        n = [x["n"] for x in rs]
        m = sum(n) / len(n)
        sd = math.sqrt(sum((v - m) ** 2 for v in n) / max(1, len(n) - 1))
        h = 1.96 * sd / math.sqrt(len(n))
        clump = sum(x.get("clump", 0) for x in rs) / len(rs)
        sbox = sum(x.get("strongbox", 0) for x in rs) / max(1, sum(n))
        print(f"{b:>5} {c:>7} {m:>11.1f} {m - h:>7.1f}-{m + h:<7.1f} {clump:>7.1f} {100 * sbox:>8.1f}%")
    print(f"\nraw rooms: {os.path.relpath(rooms, REPO)}")


if __name__ == "__main__":
    main()
