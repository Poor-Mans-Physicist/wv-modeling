"""Expected chests per minute for Chain Miner and Vein Miner in simulated vault rooms.

Runs the Routerunner room planner (libs/lane, `lane_cli`) with the shape time model on rooms that the
vault simulator (libs/vaultsim, `wv_vault_grid cells`) generates for each (Bonus, Cascade) cell, and
iterates the chest rate to the fixed point the in-game router converges to. This is the method behind
the public panel v2.2 (benchmarks/panel-v2.2), reduced to a single command.

    uv run python models/routerunner/sim/run_cells.py --cells 51,74 30,180 0,0
    uv run python models/routerunner/sim/run_cells.py --cells 45,45 --miners vein --per 32 --picker

Per (cell, miner) the chest rate lambda is iterated until it changes by under 0.5 % (at most 5 rounds):
  * prune: chest groups (Chebyshev range 1 for vein, 6 for chain) smaller than lambda x tHit are left
    solid, tHit = the shape model's cost of one click on an 8-chest group; nothing is pruned below 2;
  * bail floor = laneBailRateFrac x lambda (0.6, the 1.2.0 default);
  * shape model at movement speed 0.400 (vanilla walking), planning reach 5.0;
  * rate = coverage x sum(planned chests) / sum(planned seconds + mean room switch).
`--picker` multiplies by the adaptive room picker's simulated gain (weights/picker_factor.json).

Rooms: living chests, level 475, each room a random map theme (beach / cave / desert / nether / void),
a straight run along region x = 0 with the chunk alignment cycling through all eight z offsets; room k is
the same base room in every cell (common random numbers), so cells compare cleanly.

Output: one JSON line per (cell, miner) in out/routerunner/, plus a summary table on stdout.
"""
import argparse
import base64
import collections
import gzip
import json
import math
import os
import random
import subprocess
import sys
import time
from multiprocessing import Pool

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
EXE = ".exe" if os.name == "nt" else ""
GEN = os.path.join(REPO, "libs", "vaultsim", "target", "release", "wv_vault_grid" + EXE)
LANE = os.path.join(REPO, "libs", "lane", "target", "release", "lane_cli" + EXE)
WEIGHTS = os.path.join(HERE, "..", "weights")
SHAPE = os.path.join(WEIGHTS, "timemodel_shape.json")
PICKER = os.path.join(WEIGHTS, "picker_factor.json")
OUT = os.path.join(REPO, "out", "routerunner")

REACH = 5.0
SWITCH = {"chain": 1.03, "vein": 1.18}
MINER = {"chain": (6, 32), "vein": (1, 896)}
AX = "1,0,0,0,0,0,0,0"
AZ = ",".join(["0.125"] * 8)
MAX_ROUNDS = 5
TOL = 0.005
START_CPM = 30.0

S = json.load(open(SHAPE))
COV = {m: S["miners"][m]["coverage"] for m in MINER}


def log1p_round(n):
    return round(math.log1p(n) * 1e9) / 1e9


THIT = {m: max(0.0, S["miners"][m]["moveScale"] * (S["miners"][m]["click"] + S["miners"][m]["size"] * log1p_round(8))) for m in MINER}


def need(path, how):
    if not os.path.exists(path):
        sys.exit(f"[run_cells][ERROR] missing {os.path.relpath(path, REPO)}: {how}")


def generate(cells, per, seed, work):
    tsv = os.path.join(work, "cells.tsv")
    out = os.path.join(work, "rooms.jsonl")
    with open(tsv, "w") as f:
        for b, c in cells:
            f.write(f"{b} {c}\n")
    r = subprocess.run([GEN, "cells", "--cells", tsv, "--per", str(per), "--seed", str(seed), "--align-x", AX, "--align-z", AZ,
                        "--out", out, "--crn"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[run_cells][ERROR] generator failed:\n{r.stderr[-2000:]}")
    for line in r.stderr.splitlines():
        if "fallback" in line.lower() or "error" in line.lower() or "warn" in line.lower():
            print("  [vaultsim] " + line)
    by = collections.defaultdict(list)
    for line in open(out):
        rec = json.loads(line)
        by[(rec["real"]["b"], rec["real"]["c"])].append(rec)
    for k in cells:
        if len(by.get(k, [])) < per * 0.98:
            print(f"  [run_cells][WARN] generator wrote {len(by.get(k, []))} of {per} rooms for cell {k}")
    return by


def wall_of(g, sx, sz):
    x, _, z = g
    d = {"W": x, "E": sx - 1 - x, "N": z, "S": sz - 1 - z}
    return min(d, key=d.get)


OPP = {"W": "E", "E": "W", "N": "S", "S": "N"}


def doors(r):
    """A random entry wall, then straight through when the opposite wall has a gate, else a turn. Seeded by
    the room index, theme and template (not the cell), so room k keeps its doors in every cell."""
    rng = random.Random("crn|" + "|".join(r["key"].split("|")[3:]))
    sx, sz = r["grid"]["sx"], r["grid"]["sz"]
    by = collections.defaultdict(list)
    for g in (tuple(g) for g in r.get("gates") or []):
        by[wall_of(g, sx, sz)].append(g)
    walls = sorted(by)
    if len(walls) < 2:
        return r["entrance"], r["exit"]
    ew = rng.choice(walls)
    others = [w for w in walls if w != ew]
    straight = [w for w in others if w == OPP[ew]]
    turns = [w for w in others if w != OPP[ew]]
    xw = straight[0] if straight else rng.choice(turns)
    return list(rng.choice(by[ew])), list(rng.choice(by[xw]))


def group_sizes(chests, reach):
    if len(chests) == 0:
        return np.zeros(0, int)
    P = np.array(chests)
    pairs = cKDTree(P).query_pairs(r=reach + 1e-9, p=np.inf, output_type="ndarray")
    n = len(P)
    m = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n)) if len(pairs) else coo_matrix((n, n))
    _, lab = connected_components(m, directed=False)
    return np.bincount(lab)[lab]


def prep(args):
    """One lane_cli input line for (room, miner, lambda): pruned chests (kept solid) and the bail floor."""
    r, miner, lam, speed, bail_frac = args
    reach, limit = MINER[miner]
    chests = r["chests"]
    thr = lam * THIT[miner]
    grid = r["grid"]
    kept = chests
    if thr >= 2.0 and chests:
        keep = group_sizes(chests, max(1, reach)) >= thr
        if (~keep).any():
            kept = [c for c, k in zip(chests, keep) if k]
            raw = bytearray(gzip.decompress(base64.b64decode(grid["solidZ"])))
            sy, sz = grid["sy"], grid["sz"]
            for c, k in zip(chests, keep):
                if not k:
                    i = (c[0] * sy + c[1]) * sz + c[2]
                    if (i >> 3) < len(raw):
                        raw[i >> 3] |= 1 << (i & 7)
            grid = dict(grid, solidZ=base64.b64encode(gzip.compress(bytes(raw), 1)).decode())
    ent, ex = doors(r)
    rec = dict(key=f"{r['key']}|{miner}", grid=grid, chests=kept, entrance=ent, exit=ex, origin=[0, 0, 0], chainRange=reach,
               chainLimit=limit, modes=["point"], params=dict(breakReach=REACH, speedAttr=speed, bailFloor=bail_frac * lam, summary=1))
    return json.dumps(rec, separators=(",", ":"))


def plan_round(jobs, lam, pool, work, threads, speed, bail_frac):
    inp = os.path.join(work, "lane_in.jsonl")
    outp = os.path.join(work, "lane_out.jsonl")
    args = [(r, k[2], lam[k], speed, bail_frac) for k, rs in jobs.items() for r in rs]
    with open(inp, "w") as f:
        for line in pool.imap(prep, args, chunksize=16):
            f.write(line + "\n")
    p = subprocess.run([LANE, inp, outp, SHAPE, str(threads)], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"[run_cells][ERROR] lane_cli failed:\n{p.stderr[-2000:]}")
    out = collections.defaultdict(list)
    bad = 0
    for line in open(outp):
        d = json.loads(line)
        if "point" not in d:
            bad += 1
            continue
        parts = d["key"].split("|")
        out[(int(parts[1]), int(parts[2]), parts[-1])].append((d["point"]["yieldTotal"], d["point"]["tTotal"]))
    if bad:
        print(f"  [run_cells][WARN] {bad} rooms failed to plan this round")
    return out


def cell_rate(rows, miner):
    y = sum(r[0] for r in rows)
    t = sum(r[1] + SWITCH[miner] for r in rows)
    return COV[miner] * y / t if t > 0 else 0.0


def picker_gain(miner):
    f = json.load(open(PICKER))[miner]
    if f.get("b", 0.0) != 0.0:
        print(f"  [run_cells][WARN] picker factor for {miner} has a clumpiness slope; applying only its intercept")
    return math.exp(f["a"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cells", nargs="+", required=True, help="Bonus,Cascade pairs, e.g. 51,74 30,180")
    ap.add_argument("--miners", default="chain,vein")
    ap.add_argument("--per", type=int, default=64, help="rooms per cell (the panel used 64)")
    ap.add_argument("--seed", type=int, default=2611)
    ap.add_argument("--speed", type=float, default=0.400, help="movement speed attribute (0.400 = the panel's setting)")
    ap.add_argument("--bail", type=float, default=0.6, help="laneBailRateFrac")
    ap.add_argument("--picker", action="store_true", help="apply the adaptive room picker's simulated gain")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    need(GEN, "cargo build --release in libs/vaultsim")
    need(LANE, "cargo build --release in libs/lane")
    cells = [tuple(int(x) for x in s.split(",")) for s in a.cells]
    miners = a.miners.split(",")
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    work = os.path.join(OUT, f"work_{stamp}")
    os.makedirs(work)

    t0 = time.time()
    rooms = generate(cells, a.per, a.seed, work)
    print(f"generated {sum(len(v) for v in rooms.values())} rooms in {time.time() - t0:.0f} s")
    jobs = {(b, c, m): rooms.get((b, c), []) for (b, c) in cells for m in miners}
    lam = {k: START_CPM / 60 for k in jobs}
    done = {}
    active = {k: v for k, v in jobs.items() if v}
    with Pool(max(1, min(20, a.threads))) as pool:
        for rnd in range(1, MAX_ROUNDS + 1):
            if not active:
                break
            res = plan_round(active, lam, pool, work, a.threads, a.speed, a.bail)
            nxt = {}
            for k in active:
                rows = res.get(k, [])
                if not rows:
                    print(f"  [run_cells][WARN] no plans for {k}; dropped")
                    continue
                new = cell_rate(rows, k[2])
                conv = lam[k] > 0 and abs(new / lam[k] - 1) < TOL
                done[k] = dict(lam_in=lam[k], lam=new, rounds=rnd, rooms=len(rows), chests=sum(r[0] for r in rows),
                               seconds=sum(r[1] for r in rows))
                lam[k] = new
                if not conv and rnd < MAX_ROUNDS:
                    nxt[k] = active[k]
            print(f"  round {rnd}: {len(nxt)} of {len(active)} cell-miners still moving", flush=True)
            active = nxt

    res_path = os.path.join(OUT, f"cells_{stamp}.jsonl")
    with open(res_path, "w") as f:
        print(f"\n{'bonus':>5} {'cascade':>7} {'miner':>5} {'chests/min':>10} {'rounds':>6} {'converged':>9}")
        for (b, c, m), v in sorted(done.items()):
            gain = picker_gain(m) if a.picker else 1.0
            cpm = 60 * v["lam"] * gain
            conv = abs(v["lam"] / v["lam_in"] - 1) < TOL
            if not conv:
                print(f"  [run_cells][WARN] {b},{c} {m} stopped after {MAX_ROUNDS} rounds without converging")
            f.write(json.dumps(dict(b=b, c=c, miner=m, cpm=cpm, picker=a.picker, converged=conv, **v, speed=a.speed,
                                    bail=a.bail, per=a.per, seed=a.seed)) + "\n")
            print(f"{b:>5} {c:>7} {m:>5} {cpm:>10.1f} {v['rounds']:>6} {str(conv):>9}")
    print(f"\nwrote {os.path.relpath(res_path, REPO)} ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
