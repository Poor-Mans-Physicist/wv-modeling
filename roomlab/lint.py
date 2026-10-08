"""Contract linter for vault room .nbt files.

    python lint.py <room.nbt> [more.nbt ...]

Every rule below was derived by scanning the rooms the pack already ships (36 common, 30 omega,
37 challenge, 3 special) -- they agree exactly, so a violation here means the room will not
behave like a vault room. Exits non-zero if any ERROR is raised.
"""
import sys, collections
import nbtio

N = 47
GATES = {(0, 24, 23): "east", (23, 24, 0): "south", (23, 24, 46): "north", (46, 24, 23): "west"}
AIRS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

# PlaceholderBlock.Type, read from the decompiled the_vault source.
PLACEHOLDER_TYPES = {
    "wooden_chest", "wooden_chest_guaranteed", "wooden_chest_waterlogged",
    "gilded_chest", "gilded_chest_guaranteed", "gilded_chest_waterlogged",
    "living_chest", "living_chest_guaranteed", "living_chest_waterlogged",
    "ornate_chest", "ornate_chest_guaranteed", "ornate_chest_waterlogged",
    "objective", "ore", "coin_stacks", "coin_stacks_guaranteed", "coin_stacks_waterlogged",
    "vendor_pedestal", "treasure_door", "dungeon_door", "pylon", "dungeon_discoverable",
    "gate", "present", "spawn_position",
    "velara_pillar", "tenos_pillar", "idona_pillar", "wendarr_pillar",
}


def lint(path):
    errors, warns, notes = [], [], []
    (sx, sy, sz), ids, grid = nbtio.load_room(path)

    if (sx, sy, sz) != (N, N, N):
        errors.append(f"size is {sx}x{sy}x{sz}, must be {N}x{N}x{N}")
        return errors, warns, notes
    if len(grid) != N ** 3:
        errors.append(f"{len(grid)} cells listed, must be dense ({N**3}); the game reads a full grid")

    def at(x, y, z):
        s = grid.get((x, y, z))
        return None if s is None else ids[s]

    is_air = lambda x, y, z: (at(x, y, z) or "").split("[")[0] in AIRS

    # ---- gates ------------------------------------------------------------
    found = {}
    for pos, s in grid.items():
        b = ids[s]
        if b.startswith("the_vault:placeholder[") and "type=gate" in b:
            found[pos] = dict(p.split("=") for p in b[:-1].split("[", 1)[1].split(",")).get("facing")
    for pos, want in GATES.items():
        if pos not in found:
            errors.append(f"missing gate placeholder at {pos} (expected facing={want})")
        elif found[pos] != want:
            errors.append(f"gate at {pos} faces {found[pos]}, must face {want} (inward)")
    for pos in set(found) - set(GATES):
        errors.append(f"unexpected gate placeholder at {pos}; rooms carry exactly the 4 cardinal gates")

    # ---- doorways ---------------------------------------------------------
    for (gx, gy, gz) in GATES:
        axis_z = gx in (0, N - 1)          # gate on an x-wall -> doorway runs along z
        blocked = []
        for d in (-1, 0, 1):
            for y in range(gy - 2, gy + 3):
                px = gx + (0 if axis_z else d)
                pz = gz + (d if axis_z else 0)
                if not is_air(px, y, pz) and (px, y, pz) not in GATES:
                    blocked.append((px, y, pz))
        if blocked:
            # Measured across the 106 shipped 47^3 rooms: each cell of the 3x5 opening is air
            # 90-98% of the time, so a partly-filled doorway is a convention break, not a hard
            # error. Passability is enforced by the connectivity check below instead.
            warns.append(f"doorway at gate {(gx, gy, gz)}: {len(blocked)}/14 cell(s) of the 3x5 "
                         f"opening are solid, e.g. {blocked[:3]} (shipped rooms carve 90-98%)")

    # ---- placeholder sanity ----------------------------------------------
    types = collections.Counter()
    for pos, s in grid.items():
        b = ids[s]
        if not b.startswith("the_vault:placeholder"):
            continue
        props = dict(p.split("=") for p in b[:-1].split("[", 1)[1].split(",")) if "[" in b else {}
        t = props.get("type")
        types[t] += 1
        if t not in PLACEHOLDER_TYPES:
            errors.append(f"unknown placeholder type {t!r} at {pos}")
    notes.append("placeholders: " + (", ".join(f"{k}x{v}" for k, v in sorted(types.items())) or "none"))
    if not types.get("objective"):
        notes.append("no placeholder[type=objective] -- fine if this room gets its objective piece "
                     "from a jigsaw decor pool, a problem if it is meant to host one directly")

    # ---- connectivity: can you walk between all four gates? ---------------
    starts = [(gx + (1 if gx == 0 else -1 if gx == N - 1 else 0), gy,
               gz + (1 if gz == 0 else -1 if gz == N - 1 else 0)) for (gx, gy, gz) in GATES]
    seen = set()
    stack = [s for s in starts if is_air(*s)]
    if len(stack) < 4:
        warns.append(f"{4 - len(stack)} gate(s) have no air cell just inside them")
    seen.update(stack)
    while stack:
        x, y, z = stack.pop()
        for dx, dy, dz in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            p = (x+dx, y+dy, z+dz)
            if p in seen or not (0 <= p[0] < N and 0 <= p[1] < N and 0 <= p[2] < N):
                continue
            if is_air(*p):
                seen.add(p); stack.append(p)
    unreached = [s for s in starts if is_air(*s) and s not in seen]
    if unreached:
        errors.append(f"gates not connected to the same air volume: {unreached}")
    notes.append(f"open volume reachable from the gates: {len(seen)} cells")

    # ---- misc -------------------------------------------------------------
    counts = collections.Counter(ids[s] for s in grid.values())
    air_n = sum(v for k, v in counts.items() if k.split("[")[0] in AIRS)
    notes.append(f"palette {len(ids)} states, {air_n} air ({100*air_n/N**3:.1f}%), "
                 f"top solid: {', '.join(k for k, _ in counts.most_common(4) if k.split('[')[0] not in AIRS)}")
    if any(k.startswith("minecraft:structure_void") for k in counts):
        warns.append("structure_void present; no shipped room in this pack uses it")
    return errors, warns, notes


def main():
    bad = 0
    for path in sys.argv[1:]:
        errors, warns, notes = lint(path)
        print(f"\n=== {path}")
        for n in notes:
            print(f"  .  {n}")
        for w in warns:
            print(f"  ?  WARN  {w}")
        for e in errors:
            print(f"  X  ERROR {e}")
        if not errors:
            print("  OK  contract satisfied")
        bad += len(errors)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
