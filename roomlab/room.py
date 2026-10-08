"""Room-authoring DSL for 47x47x47 vault rooms.

Import it from a generator script (see examples/extraction1/generate.py), build a Room, place the
four gates from GATES last, write it with nbtio.write(path, room.to_nbt()), then run lint.py on it.

Vertical convention, forced by the tunnel geometry: a tunnel is 11x11x47 with its own gate at
(5,6,0), and the room gates sit at y=24, so the doorway air column is y 22..26. "Floor level y=N"
below therefore means *air starts at y=N* and the top solid block is y=N-1.
"""
import sys, math, random
import nbtio
from nbtio import T, INT, COMPOUND, LIST

N = 47
CX = CZ = 23                       # centre column

# Contract: verified identical across all 36 common, 30 omega, 37 challenge, 3 special rooms.
# `facing` points INWARD.
GATES = [((0, 24, 23), "east"), ((23, 24, 0), "south"),
         ((23, 24, 46), "north"), ((46, 24, 23), "west")]
CARDINALS = {(0, 24, 23): (1, 0), (46, 24, 23): (-1, 0),
             (23, 24, 0): (0, 1), (23, 24, 46): (0, -1)}

AIR = "minecraft:air"
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


class Room:
    def __init__(self, fill=AIR):
        self.pal, self.pidx = [], {}
        f = self.id(fill)
        self.g = [[[f] * N for _ in range(N)] for _ in range(N)]     # [x][y][z]

    def id(self, state):
        if state not in self.pidx:
            self.pidx[state] = len(self.pal)
            self.pal.append(state)
        return self.pidx[state]

    # ---- primitives -------------------------------------------------------
    def set(self, x, y, z, state):
        x, y, z = int(x), int(y), int(z)
        if 0 <= x < N and 0 <= y < N and 0 <= z < N:
            self.g[x][y][z] = self.id(state)

    def get(self, x, y, z):
        x, y, z = int(x), int(y), int(z)
        if 0 <= x < N and 0 <= y < N and 0 <= z < N:
            return self.pal[self.g[x][y][z]]
        return None

    def box(self, p0, p1, state):
        (x0, y0, z0), (x1, y1, z1) = p0, p1
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                for z in range(min(z0, z1), max(z0, z1) + 1):
                    self.set(x, y, z, state)

    def disc(self, cx, cz, y, r, state, r_inner=0):
        ri = math.ceil(r)
        for dx in range(-ri, ri + 1):
            for dz in range(-ri, ri + 1):
                d = math.hypot(dx, dz)
                if r_inner <= d <= r:
                    self.set(cx + dx, y, cz + dz, state)

    def cylinder(self, cx, cz, y0, y1, r, state, r_inner=0):
        for y in range(int(y0), int(y1) + 1):
            self.disc(cx, cz, y, r, state, r_inner)

    def dome(self, cx, cy, cz, r, state):
        """Upper hemisphere of radius r springing from y=cy."""
        ri = math.ceil(r)
        for dx in range(-ri, ri + 1):
            for dy in range(0, ri + 1):
                for dz in range(-ri, ri + 1):
                    if dx * dx + dy * dy + dz * dz <= r * r:
                        self.set(cx + dx, cy + dy, cz + dz, state)

    def blob(self, cx, cy, cz, rx, ry, rz, state):
        for dx in range(-math.ceil(rx), math.ceil(rx) + 1):
            for dy in range(-math.ceil(ry), math.ceil(ry) + 1):
                for dz in range(-math.ceil(rz), math.ceil(rz) + 1):
                    if (dx / rx) ** 2 + (dy / ry) ** 2 + (dz / rz) ** 2 <= 1.0:
                        self.set(cx + dx, cy + dy, cz + dz, state)

    def spire(self, origin, direction, length, base_r, state, shoulder=0.62, allow=None):
        """A crystal: a shaft of roughly constant girth that tapers to a point.

        `shoulder` is the fraction of the length carried at full width before the taper starts --
        that shoulder is what makes it read as a crystal rather than as a cone or a blob.
        Returns the cells it touched.
        """
        ox, oy, oz = origin
        dx, dy, dz = direction
        m = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / m, dy / m, dz / m
        touched = []
        steps = max(2, int(length * 2))
        for i in range(steps + 1):
            t = i / steps
            r = base_r if t <= shoulder else base_r * (1 - (t - shoulder) / (1 - shoulder))
            if r < 0.35:
                continue
            px, py, pz = ox + dx * length * t, oy + dy * length * t, oz + dz * length * t
            ri = math.ceil(r)
            for ax in range(-ri, ri + 1):
                for ay in range(-ri, ri + 1):
                    for az in range(-ri, ri + 1):
                        if ax * ax + ay * ay + az * az <= r * r:
                            c = (int(round(px)) + ax, int(round(py)) + ay, int(round(pz)) + az)
                            if allow is not None and not allow(*c):
                                continue
                            if self.get(*c) is not None:
                                self.set(*c, state)
                                touched.append(c)
        return touched

    def prism(self, origin, direction, length, base_r, state, taper=0.55, sharpness=1.0,
              allow=None):
        """A faceted crystal: a diamond cross-section swept along an axis, tapering to a point.

        `spire` sweeps a sphere, which reads as a lumpy blob. Sweeping a diamond
        (|a| + |b| <= r) in the plane perpendicular to the axis gives flat faces and a sharp
        tip -- the difference between "clump of rock" and "crystal".

        `taper` is the fraction of the length held at full width; `sharpness` > 1 narrows
        faster after that, which is what turns a stubby cone into a lean needle. Diamond
        radii quantise hard (r<1 -> 1 cell, 1-1.9 -> 5, 2-2.9 -> 13), so a long tapering
        run matters more than a big base. Returns the cells touched.
        """
        ox, oy, oz = origin
        dx, dy, dz = direction
        m = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        dx, dy, dz = dx / m, dy / m, dz / m
        # orthonormal basis perpendicular to the axis
        ref = (0.0, 1.0, 0.0) if abs(dy) < 0.9 else (1.0, 0.0, 0.0)
        ux, uy, uz = (dy * ref[2] - dz * ref[1], dz * ref[0] - dx * ref[2], dx * ref[1] - dy * ref[0])
        um = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        ux, uy, uz = ux / um, uy / um, uz / um
        vx, vy, vz = (dy * uz - dz * uy, dz * ux - dx * uz, dx * uy - dy * ux)
        touched = []
        steps = max(2, int(length * 2))
        for i in range(steps + 1):
            t = i / steps
            rr = (base_r if t <= taper
                  else base_r * (1 - (t - taper) / (1 - taper)) ** sharpness)
            if rr < 0.4:
                continue
            px, py, pz = ox + dx * length * t, oy + dy * length * t, oz + dz * length * t
            k = math.ceil(rr)
            for a in range(-k, k + 1):
                for b in range(-k, k + 1):
                    if abs(a) + abs(b) > rr:
                        continue
                    c = (int(round(px + ux * a + vx * b)),
                         int(round(py + uy * a + vy * b)),
                         int(round(pz + uz * a + vz * b)))
                    if allow is not None and not allow(*c):
                        continue
                    if self.get(*c) is not None:
                        self.set(*c, state)
                        touched.append(c)
        return touched

    def tube(self, p0, p1, half_w, y0, y1, state):
        """Carve a straight horizontal passage of guaranteed width.

        Sweeping a perpendicular offset in integer steps under-samples on a diagonal -- at 45
        degrees the offsets land on 0.707 multiples and round onto the same cell, so the passage
        comes out as a jagged staircase with a much narrower true width than intended. Testing
        distance-to-the-axis over the whole bounding box instead gives a solid, gap-free tube.
        """
        (x0, z0), (x1, z1) = p0, p1
        ax, az = x1 - x0, z1 - z0
        seg2 = ax * ax + az * az or 1.0
        lo_x, hi_x = int(min(x0, x1) - half_w - 1), int(max(x0, x1) + half_w + 1)
        lo_z, hi_z = int(min(z0, z1) - half_w - 1), int(max(z0, z1) + half_w + 1)
        for x in range(lo_x, hi_x + 1):
            for z in range(lo_z, hi_z + 1):
                t = ((x - x0) * ax + (z - z0) * az) / seg2
                if not (0.0 <= t <= 1.0):
                    continue
                px, pz = x0 + ax * t, z0 + az * t
                if math.hypot(x - px, z - pz) > half_w:
                    continue
                for y in range(int(y0), int(y1) + 1):
                    self.set(x, y, z, state)

    def wall_cell(self, azimuth, phi, centre_y, radius):
        """First SOLID cell outward along a dome ray -- i.e. the wall face itself.

        Placing at radius-0.4 lands inside the carved air volume, so the crystal sits proud of
        the wall like a decal. Walking outward until the rock starts puts it *in* the wall,
        flush, which is what an inlaid vein should look like.
        """
        dx = math.cos(azimuth) * math.cos(phi)
        dy = math.sin(phi)
        dz = math.sin(azimuth) * math.cos(phi)
        for step in range(0, 9):
            rr = radius - 1.0 + step * 0.5
            c = (int(round(CX + dx * rr)), int(round(centre_y + dy * rr)), int(round(CZ + dz * rr)))
            here = self.get(*c)
            if here is not None and here != AIR:
                return c
        return None

    # ---- serialisation ----------------------------------------------------
    def to_nbt(self):
        blocks = [{"pos": T(LIST, (INT, [x, y, z])), "state": T(INT, self.g[x][y][z])}
                  for y in range(N) for z in range(N) for x in range(N)]
        return T(COMPOUND, {
            "size": T(LIST, (INT, [N, N, N])),
            "entities": T(LIST, (0, [])),
            "blocks": T(LIST, (COMPOUND, blocks)),
            "palette": T(LIST, (COMPOUND, [nbtio.parse_id(s) for s in self.pal])),
            "DataVersion": T(INT, 2975),
        })


# ---------------------------------------------------------------------------
# Extraction room template
# ---------------------------------------------------------------------------

# Vault-stone family only. Every one of these sampled between RGB 42 and 56, so the whole set
# reads as one near-black grey; nothing here fights the crystal colours.
