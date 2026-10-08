"""Extraction room generator (the worked example for roomlab).

    python examples/extraction1/generate.py [out.nbt] [--seed N]

Every structural parameter is a named constant below. The seed only changes rock mottle.
"""
import os, sys, math, random

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import nbtio
from room import Room, N, CX, CZ, GATES, CARDINALS, AIR, NEIGHBOURS


STONE = "the_vault:vault_stone"
ROCK = ["the_vault:vault_stone", "the_vault:vault_stone", "the_vault:vault_cobblestone",
        "the_vault:vault_bedrock"]
WORKED = ["the_vault:vault_stone_bricks", "the_vault:vault_stone_bricks_cracked",
          "the_vault:chiseled_vault_stone", "the_vault:polished_vault_stone"]
FLOOR_MAIN = "the_vault:vault_stone_bricks"
FLOOR_WORN = "the_vault:vault_stone_bricks_cracked"
FLOOR_RING = "the_vault:chiseled_vault_stone"
PILLAR = "the_vault:vault_stone_pillar"

# One stand-in only: every crystal in the room is the same colour, and which colour is decided
# by the palette the pool entry pins (extraction_{idona,tenos,velara,wendarr}.json).
CRYSTAL = "minecraft:white_wool"
GLOW_CRYSTAL = "minecraft:light[level=13,waterlogged=false]"
GLOW_VEIN = "minecraft:light[level=14,waterlogged=false]"   # the veins ARE the floor lighting
GLOW_FILL = "minecraft:light[level=11,waterlogged=false]"
SPAWNER = "ispawner:spawner[facing=north,mirror=none,powered=false]"
# Placed directly, exactly as the shipped crystal_caves decor pieces do. The
# deepslate_coal_ore -> ispawner:spawner conversion is an aquarium-only palette rule,
# NOT a general convention -- relying on it would have produced ore, not spawners.
# Palettes then configure the placed spawner (the_vault:generic/spawner_base sets the
# timer, the_vault:generic/challenge_elite_spawners picks the mobs).

FLOOR_Y = 19          # air starts here -> top solid block is y=18. The basin IS the floor.
DOME_R = 18           # hemisphere springs straight off the basin floor: no ledge, no ring
GATE_Y = 24

CAVE_ANGLES = (45, 135, 225, 315)   # corners, where the wall is thickest
MOUTH_H = 6                         # cave passage height; guarantees a clear 4-tall door
MOUTH_HALF_W = 3.0                  # -> ~6 blocks across, so a clean 3-wide door always fits
VEIN_COUNT = 8                      # crystal lines: pedestal -> out -> up the dome -> apex
VEIN_START = 1.0                    # start at the pedestal, no gap in the middle
CROWN_COUNT = 8                     # crystals hanging from the dome edge, evenly spaced
CROWN_Y = 32                        # higher up the dome, so the angle down is real
CROWN_LEN = 10.0   # long and lean: with taper 0.10 / sharpness 2.0 almost all of this
                   # is the 1-cell needle, so length reads as elegance, not bulk
CROWN_R = 2.2      # diamond radii quantise to 13 / 5 / 1 cells, so a wider base plus a
                   # steep exponent gives three real width tiers instead of just two
CROWN_AIM = 2                       # aim low: with the y32 anchor this is ~42 deg down
CROWN_TAPER = 0.10                  # barely any full-width run at all
CROWN_SHARP = 2.0                   # squared falloff: wide shoulder, then a 3-block needle


def corridor_floor(d):
    """Descending entry passage: y22 at the gate, stepping down to the basin over three blocks."""
    return 22 if d <= 1 else 21 if d == 2 else 20 if d == 3 else FLOOR_Y


def glow_in_air(r, cells, rng, level, n=1):
    """Drop light blocks into air cells touching a cluster. The crystals emit nothing on their
    own -- auxiliaryblocks CrystalBlock never calls lightLevel -- so every glow is a colocated
    minecraft:light, exactly as the shipped crystal_caves rooms do."""
    cand = []
    for (x, y, z) in cells:
        for dx, dy, dz in NEIGHBOURS:
            p = (x + dx, y + dy, z + dz)
            if r.get(*p) == AIR:
                cand.append(p)
    rng.shuffle(cand)
    for p in cand[:n]:
        r.set(*p, level)
    return len(cand[:n])


def dome_radius_at(y):
    dy = y - FLOOR_Y
    return math.sqrt(max(0.0, DOME_R * DOME_R - dy * dy))


def build(seed=7):
    rng = random.Random(seed)
    r = Room(STONE)

    # rock mottle, all inside the vault-stone family
    for x in range(N):
        for y in range(N):
            for z in range(N):
                if rng.random() < 0.16:
                    r.g[x][y][z] = r.id(ROCK[rng.randrange(len(ROCK))])

    # the chamber: one hemisphere sitting directly on the basin floor. No raised ring, no ledge --
    # the wall meets the floor all the way round except where an entry stair cuts in.
    r.dome(CX, FLOOR_Y, CZ, DOME_R, AIR)

    # entry passages, descending from the gate to the basin so the arena stays flat
    for gate, (ux, uz) in CARDINALS.items():
        gx, gy, gz = gate
        for d in range(0, 9):
            px, pz = gx + ux * d, gz + uz * d
            fy = corridor_floor(d)
            for w in (-1, 0, 1):
                cx, cz = px + (uz and 0 or 0) + (w if ux == 0 else 0), pz + (w if uz == 0 else 0)
                cx = px + (w if ux == 0 else 0)
                cz = pz + (w if uz == 0 else 0)
                for y in range(fy, 27):
                    r.set(cx, y, cz, AIR)
            # tread under each step, so the stair reads as built rather than hacked out
            if 2 <= d <= 4:
                for w in (-1, 0, 1):
                    cx = px + (w if ux == 0 else 0)
                    cz = pz + (w if uz == 0 else 0)
                    r.set(cx, fy - 1, cz, FLOOR_RING)

    # --- basin floor: dead flat, nothing on it but the pedestal ------------
    r.disc(CX, CZ, FLOOR_Y - 1, DOME_R + 1, FLOOR_MAIN)
    for _ in range(320):                                  # worn patches, still flush
        a, dd = rng.uniform(0, 2 * math.pi), rng.uniform(0, DOME_R)
        r.set(round(CX + math.cos(a) * dd), FLOOR_Y - 1, round(CZ + math.sin(a) * dd), FLOOR_WORN)
    for ring_r in (7, 12, 16):                            # ritual rings inlaid in the floor
        r.disc(CX, CZ, FLOOR_Y - 1, ring_r + 0.4, FLOOR_RING, r_inner=ring_r - 0.4)

    # --- corner spawn cave chambers (their mouths are cut last, see below) --
    for ang in CAVE_ANGLES:
        a = math.radians(ang)
        ux, uz = math.cos(a), math.sin(a)
        chx, chz = CX + ux * 25.5, CZ + uz * 25.5
        r.blob(round(chx), FLOOR_Y + 2, round(chz), 5.0, 3.4, 5.0, AIR)
        r.disc(round(chx), round(chz), FLOOR_Y - 1, 5.0, FLOOR_WORN)
        for ox, oz in ((0, 0), (round(ux * 3), round(uz * 3)), (round(-ux * 3), round(-uz * 3))):
            if r.get(round(chx) + ox, FLOOR_Y + 2, round(chz) + oz) == AIR:
                r.set(round(chx) + ox, FLOOR_Y + 2, round(chz) + oz, GLOW_FILL)

    # --- central pedestal: the only thing standing on the arena floor ------
    # Two blocks tall: the extractor model needs the height, and it lifts the objective clear
    # of the floor veins converging underneath it.
    r.box((CX - 1, FLOOR_Y, CZ - 1), (CX + 1, FLOOR_Y + 1, CZ + 1), CRYSTAL)
    # No objective placeholder: the extraction objective spawns its extractor ENTITY on the
    # pedestal top (y21); an unresolved placeholder block would linger in the forced room.
    for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):
        r.set(CX + dx, FLOOR_Y, CZ + dz, GLOW_CRYSTAL)

    # --- crystal veins: inlaid in the floor, climbing the dome to the apex --
    # These are the arena's main light source. Flush with the floor (they replace the top solid
    # block, they do not sit on it) so the battleground stays perfectly flat.
    for i in range(VEIN_COUNT):
        # offset so no vein runs down a cardinal (entry corridor) or a corner (cave mouth)
        a = 2 * math.pi * i / VEIN_COUNT + math.pi / VEIN_COUNT
        ca, sa = math.cos(a), math.sin(a)
        # Floor run: dead straight, out from the pedestal itself. These are meant to read as
        # channelled power, not as natural cracks, so there is no wobble and no gap at the centre.
        seen = set()
        for step in range(0, 400):
            rad = VEIN_START + step * 0.1
            if rad > DOME_R - 0.5:
                break
            cell = (int(round(CX + ca * rad)), FLOOR_Y - 1, int(round(CZ + sa * rad)))
            if cell in seen:
                continue
            seen.add(cell)
            r.set(*cell, CRYSTAL)
            if len(seen) % 3 == 0 and r.get(cell[0], FLOOR_Y, cell[2]) == AIR:
                r.set(cell[0], FLOOR_Y, cell[2], GLOW_VEIN)
        # Wall run: a clean meridian from the rim to the apex, inset into the rock.
        seen_w = set()
        for step in range(0, 400):
            ph = step * (math.pi / 2) / 399
            cell = r.wall_cell(a, ph, FLOOR_Y, DOME_R)
            if cell is None or cell in seen_w:
                continue
            seen_w.add(cell)
            r.set(*cell, CRYSTAL)
            if len(seen_w) % 4 == 0:
                glow_in_air(r, [cell], rng, GLOW_VEIN, n=1)

    # --- the crown: 8 uniform crystals angled down and in from the dome edge
    clusters = 0
    for i in range(CROWN_COUNT):
        a = 2 * math.pi * i / CROWN_COUNT   # one over each entry corridor and cave mouth
        ph = math.asin((CROWN_Y - FLOOR_Y) / DOME_R)
        rr = DOME_R - 0.6
        ax = CX + math.cos(a) * math.cos(ph) * rr
        az = CZ + math.sin(a) * math.cos(ph) * rr
        ay = FLOOR_Y + math.sin(ph) * rr
        # aim at a single point above the pedestal, so all eight converge identically
        tx, ty, tz = CX, FLOOR_Y + CROWN_AIM, CZ
        cells = r.prism((ax, ay, az), (tx - ax, ty - ay, tz - az),
                        CROWN_LEN, CROWN_R, CRYSTAL,
                        taper=CROWN_TAPER, sharpness=CROWN_SHARP)
        if cells:
            clusters += 1
            glow_in_air(r, cells, rng, GLOW_CRYSTAL, n=3)

    # --- general fill light ------------------------------------------------
    # Vault stone samples at RGB ~46, so an unlit room is genuinely black. This is the moody
    # baseline; the crystals sit brighter on top of it.
    for ring_y, count, inset in ((FLOOR_Y + 1, 30, 1.5), (FLOOR_Y + 3, 24, 2.5)):
        for i in range(count):
            a = 2 * math.pi * i / count + (0.13 if ring_y > FLOOR_Y + 1 else 0)
            rr = min(DOME_R - inset, dome_radius_at(ring_y) - inset)
            r.set(round(CX + math.cos(a) * rr), ring_y, round(CZ + math.sin(a) * rr), GLOW_FILL)
    for i in range(14):
        a = 2 * math.pi * i / 14 + 0.2
        rr = dome_radius_at(FLOOR_Y + 9) - 2
        r.set(round(CX + math.cos(a) * rr), FLOOR_Y + 9, round(CZ + math.sin(a) * rr), GLOW_FILL)
    # kept overhead rather than at foot level: light blocks are invisible and walk-through, but
    # a floor littered with them is confusing to edit later
    for rr, count, yy in ((6, 6, FLOOR_Y + 5), (12, 10, FLOOR_Y + 4)):
        for i in range(count):
            a = 2 * math.pi * i / count
            r.set(round(CX + math.cos(a) * rr), yy, round(CZ + math.sin(a) * rr), GLOW_FILL)

    # --- worked detail: pillars flanking each entry ------------------------
    for gate, (ux, uz) in CARDINALS.items():
        gx, gz = gate[0], gate[2]
        px, pz = gx + ux * 7, gz + uz * 7
        for w in (-2, 2):
            cx = px + (w if ux == 0 else 0)
            cz = pz + (w if uz == 0 else 0)
            for y in range(FLOOR_Y, FLOOR_Y + 6):
                if r.get(cx, y, cz) == AIR:
                    r.set(cx, y, cz, PILLAR)

    # --- entry passages re-cut LAST, so nothing stands in a doorway ---------
    # The stairs get walked every run; a crystal poking into one is the single most annoying
    # thing this room can do. Re-carving after the crystals removes anything that grew in,
    # and re-lays the treads underneath.
    for gate, (ux, uz) in CARDINALS.items():
        gx, gy, gz = gate
        for d in range(0, 13):
            px, pz = gx + ux * d, gz + uz * d
            fy = corridor_floor(d)
            for w in (-1, 0, 1):
                cx = px + (w if ux == 0 else 0)
                cz = pz + (w if uz == 0 else 0)
                for y in range(fy, 27):
                    r.set(cx, y, cz, AIR)
                if 2 <= d <= 4:
                    r.set(cx, fy - 1, cz, FLOOR_RING)

    # --- cave mouths, cut LAST so nothing can block them --------------------
    # Wide and tall enough that a wave walks straight through: 5 across, 5 high. Carving these
    # after the crystals and pillars is the whole point -- anything that grew into the opening
    # gets removed rather than left as a doorstop.
    for ang in CAVE_ANGLES:
        a = math.radians(ang)
        ux, uz = math.cos(a), math.sin(a)
        p0 = (CX + ux * 11.0, CZ + uz * 11.0)          # well inside the basin
        p1 = (CX + ux * 27.0, CZ + uz * 27.0)          # into the chamber
        r.tube(p0, p1, MOUTH_HALF_W, FLOOR_Y, FLOOR_Y + MOUTH_H - 1, AIR)
        r.tube(p0, p1, MOUTH_HALF_W, FLOOR_Y - 1, FLOOR_Y - 1, FLOOR_WORN)   # floor under it
        # No spawner blocks: the wave system spawns entities directly at the cave anchors
        # (ExtractionSpawnManager), so authored ispawner blocks would only add untuned
        # ambient pressure and visual clutter.

    # gates last, so nothing overwrites them
    for (gx, gy, gz), facing in GATES:
        r.set(gx, gy, gz, f"the_vault:placeholder[facing={facing},type=gate]")

    return r, clusters


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "extraction1.nbt")
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 7
    room, clusters = build(seed)
    nbtio.write(out, room.to_nbt())
    print(f"wrote {out}  seed={seed}  palette={len(room.pal)}  crystal clusters={clusters}")
