"""Fang hits per cast on the hyperboss by effective radius: writes data/fang_hits.json.

python tools/fang_geometry.py [--check]

Geometry (addon EvokerFangsAbility:65-99, EvokerFangsMawAbility:36-58, CustomFangEntity:76, ModEntities:84):
- Wall of Fangs: rings at d = 1.0, 2.5, 4.0, ... while d <= R (R = radius x area of effect), int(2 pi d / 1.5) fangs
  per ring, evenly spaced, centred on the player.
- Hungry Maw: a line along the look direction from 1.5 to 1.5 x R in 1.0 steps, three fangs abreast at 0 and +-0.5
  blocks perpendicular to it, player pitch 0.
- A fang is a 1.0 x 1.0 box that damages every living entity inside its box inflated by 0.2 horizontally, once per
  entity; so a fang hits the boss when both horizontal centre offsets are below 0.7 + half the boss width.
- Boss roster (gen/1.0/palettes/special/boss/base.json, weight 1 each, rerolled every cycle): Golem 0.9, Boogieman 1.2,
  Black Widow 1.68 blocks wide, averaged uniformly.
- The boss stands fang_boss_distance (stages.KNOBS, 3.0 blocks: melee range) from the player: for the Wall in a
  uniformly random direction, for Maw straight along the look axis with the look direction uniform against the
  world axes (the boss box is axis-aligned). Both averages are midpoint quadratures over ANGLES directions, so the
  table is deterministic.

The table holds raw hits per cast on a uniform radius grid (GRID_STEP) from 0 to the largest AoE-scaled radius an
ability can reach (tier radius x the AoE cap); both engines interpolate it linearly and apply maw_pull_factor to Maw
at read time. A fang whose centre is more than (0.7 + half the widest boss) x sqrt(2) + 1.5 blocks farther out than
the boss can never touch it, so past SATURATION blocks of radius the count is constant and is copied instead of
recomputed. --check recomputes the table and compares it with the file.
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "data", "fang_hits.json")
FANG_HALF = 0.5 + 0.2
BOSS_WIDTHS = {"golem": 0.9, "boogieman": 1.2, "black_widow": 1.68}
ANGLES = 720
GRID_STEP = 0.25
SPECS = {"wall": "Fangs_Base", "maw": "Fangs_Maw"}
SATURATION = 10.0


def wall_positions(radius):
    pos = []
    d = 1.0
    while d <= radius:
        count = int(math.pi * 2 * d / 1.5)
        for i in range(count):
            a = i * (math.pi * 2 / count)
            pos.append((math.cos(a) * d, math.sin(a) * d))
        d += 1.5
    return pos


def maw_positions(radius, yaw):
    rng = radius * 1.5
    lx, lz = math.cos(yaw), math.sin(yaw)
    pos = []
    d = 1.5
    while d <= rng:
        for side in (-1, 0, 1):
            pos.append((lx * d - lz * side * 0.5, lz * d + lx * side * 0.5))
        d += 1.0
    return pos


def count_hits(positions, bx, bz, half):
    lim = FANG_HALF + half
    return sum(1 for x, z in positions if abs(x - bx) < lim and abs(z - bz) < lim)


def wall_hits(radius, dist):
    pos = wall_positions(radius)
    tot = 0.0
    for w in BOSS_WIDTHS.values():
        s = 0
        for i in range(ANGLES):
            phi = (i + 0.5) * 2.0 * math.pi / ANGLES
            s += count_hits(pos, dist * math.cos(phi), dist * math.sin(phi), w / 2.0)
        tot += s / ANGLES
    return tot / len(BOSS_WIDTHS)


def maw_hits(radius, dist):
    tot = 0.0
    for w in BOSS_WIDTHS.values():
        s = 0
        for i in range(ANGLES):
            yaw = (i + 0.5) * 2.0 * math.pi / ANGLES
            s += count_hits(maw_positions(radius, yaw), dist * math.cos(yaw), dist * math.sin(yaw), w / 2.0)
        tot += s / ANGLES
    return tot / len(BOSS_WIDTHS)


def max_radius():
    """Largest AoE-scaled radius either fang spec can reach in either catalog (tier radius x (1 + aoeCap))."""
    best = 0.0
    for name in ("0.34.1", "rework"):
        path = os.path.join(ROOT, "data", f"catalog_{name}.json")
        if not os.path.exists(path):
            print(f"[fang_geometry][FALLBACK] {path} missing; its radii are not covered", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)
        aoe = 1.0 + float(cat["caps"].get("aoeCap", 0.8))
        for s in cat["abilities"]:
            for sp in s.get("specializations", [s]):
                if sp["id"] in SPECS.values():
                    for t in sp.get("tiers", [sp]):
                        best = max(best, float(t["radius"]) * aoe)
    return best


def build():
    from model import stages
    dist = float(stages.KNOBS["fang_boss_distance"])
    top = max_radius()
    n = int(math.ceil(top / GRID_STEP - 1e-9)) + 1
    radii = [k * GRID_STEP for k in range(n)]
    sat = {}
    out = {"wall": [], "maw": []}
    for kind, fn in (("wall", wall_hits), ("maw", maw_hits)):
        last = None
        for r in radii:
            if last is not None and r > dist + SATURATION:
                out[kind].append([r, last])
                continue
            last = fn(r, dist)
            out[kind].append([r, last])
        sat[kind] = last
    out["meta"] = {"fang_boss_distance": dist, "grid_step": GRID_STEP, "max_radius": radii[-1], "angles": ANGLES,
                   "boss_widths": BOSS_WIDTHS, "fang_half_width": FANG_HALF, "saturated_hits": sat,
                   "source": "tools/fang_geometry.py (execution plan section 3)"}
    return out


if __name__ == "__main__":
    table = build()
    if "--check" in sys.argv:
        with open(OUT, encoding="utf-8") as fh:
            have = json.load(fh)
        bad = sum(1 for k in ("wall", "maw") for a, b in zip(have[k], table[k]) if abs(a[1] - b[1]) > 1e-12 or a[0] != b[0])
        bad += sum(1 for k in ("wall", "maw") if len(have[k]) != len(table[k]))
        print(f"fang_geometry --check: {bad} differing rows")
        sys.exit(1 if bad else 0)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(table, fh, indent=0)
        fh.write("\n")
    m = table["meta"]
    print(f"fang_geometry: {len(table['wall'])} radii up to {m['max_radius']:.2f}, boss at {m['fang_boss_distance']} blocks; "
          f"saturated hits wall {m['saturated_hits']['wall']:.3f}, maw {m['saturated_hits']['maw']:.3f} (raw) -> {OUT}")
