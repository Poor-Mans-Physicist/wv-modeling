"""Compile a vault room .nbt into the viewer's payload: a texture atlas + a room JSON with
baked block-light.

    python build.py <room.nbt> [--out web] [--name label]

Emits <out>/atlas.png and <out>/room_<label>.json, plus refreshes <out>/rooms.json (the picker
list). Lighting is a real 15-level flood fill from every emissive block, matching how the game
propagates block light, so a dark cave with glowing crystals previews as a dark cave with
glowing crystals.
"""
import sys, os, json, base64, collections, time
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nbtio, blockdefs as bd

FACE_ORDER = ("up", "down", "north", "south", "east", "west")
# offsets matching FACE_ORDER, in (x, y, z)
FACE_OFF = ((0, 1, 0), (0, -1, 0), (0, 0, -1), (0, 0, 1), (1, 0, 0), (-1, 0, 0))


def build_atlas(texture_names):
    """[resloc] -> (PIL atlas image, {resloc: tile index}, cols, rows)"""
    names = [t for t in texture_names if t]
    imgs, index = {}, {}
    for t in names:
        im = bd.texture_image(t)
        if im is not None:
            imgs[t] = im
    ordered = sorted(imgs)
    n = len(ordered)
    cols = max(1, int(np.ceil(np.sqrt(n))))
    rows = max(1, int(np.ceil(n / cols)))
    atlas = Image.new("RGBA", (cols * 16, rows * 16), (0, 0, 0, 0))
    for i, t in enumerate(ordered):
        atlas.paste(imgs[t], ((i % cols) * 16, (i // cols) * 16))
        index[t] = i
    return atlas, index, cols, rows


def compute_light(sx, sy, sz, lum, opaque):
    """15-level block-light flood fill. lum/opaque are flat arrays indexed by idx()."""
    light = np.zeros(sx * sy * sz, dtype=np.uint8)
    idx = lambda x, y, z: (y * sz + z) * sx + x
    buckets = [collections.deque() for _ in range(16)]
    seeded = np.nonzero(lum)[0]
    for i in seeded:
        light[i] = lum[i]
        buckets[lum[i]].append(int(i))
    for level in range(15, 0, -1):
        q = buckets[level]
        while q:
            i = q.popleft()
            if light[i] != level:
                continue
            x = i % sx
            z = (i // sx) % sz
            y = i // (sx * sz)
            nl = level - 1
            if nl == 0:
                continue
            for dx, dy, dz in FACE_OFF:
                nx, ny, nz = x + dx, y + dy, z + dz
                if not (0 <= nx < sx and 0 <= ny < sy and 0 <= nz < sz):
                    continue
                j = idx(nx, ny, nz)
                if opaque[j]:
                    continue
                if light[j] < nl:
                    light[j] = nl
                    buckets[nl].append(j)
    return light


def main():
    src = sys.argv[1]
    out = "web"
    label = os.path.splitext(os.path.basename(src))[0]
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    if "--name" in sys.argv:
        label = sys.argv[sys.argv.index("--name") + 1]
    os.makedirs(out, exist_ok=True)

    (sx, sy, sz), ids, grid = nbtio.load_room(src)
    print(f"{label}: size {sx}x{sy}x{sz}, {len(ids)} palette entries, {len(grid)} cells")

    pal_files = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--palette"]
    if pal_files:
        import palette
        ids, grid, rep = palette.apply(ids, grid, palette.load(pal_files), seed=1)
        print(f"  palette: {len(pal_files)} file(s), {rep['rules']} rules -> "
              + ", ".join(f"{k} x{v}" for k, v in sorted(rep["substituted"].items())))
        if rep["unmatched_rules"]:
            print(f"  [warn] palette rules that matched nothing in this room: {rep['unmatched_rules']}")
        if rep["skipped_processor_types"]:
            print(f"  [warn] unsupported processor types skipped: {rep['skipped_processor_types']}")

    # ---- palette metadata -------------------------------------------------
    meta, wanted = [], set()
    unresolved = []
    for bid in ids:
        inv = bd.is_invisible(bid)
        ft = None if inv else bd.face_textures(bid)
        if not inv and ft is None:
            unresolved.append(bid)
        if ft:
            wanted.update(ft.values())
        meta.append({"id": bid, "ft": ft, "invisible": inv,
                     "transparent": (not inv) and bd.is_transparent(bid),
                     "lum": bd.luminance(bid)})
    if unresolved:
        print(f"  [warn] {len(unresolved)} palette entries have no resolvable texture; "
              f"they will render as magenta placeholders: {unresolved[:6]}"
              f"{' ...' if len(unresolved) > 6 else ''}")

    atlas, tindex, cols, rows = build_atlas(wanted)
    atlas_file = f"atlas_{label}.png"
    atlas.save(os.path.join(out, atlas_file))
    print(f"  atlas: {len(tindex)} tiles, {cols}x{rows} ({atlas.width}x{atlas.height}px) -> {atlas_file}")

    palette = []
    for m in meta:
        if m["ft"] is None:
            faces = [-1] * 6
        else:
            faces = [tindex.get(m["ft"][f], -1) for f in FACE_ORDER]
        palette.append({"id": m["id"], "faces": faces, "invisible": m["invisible"],
                        "transparent": m["transparent"], "lum": m["lum"]})

    # ---- voxel arrays -----------------------------------------------------
    idx = lambda x, y, z: (y * sz + z) * sx + x
    n = sx * sy * sz
    states = np.zeros(n, dtype=np.uint16)
    air_state = next((i for i, p in enumerate(palette) if p["id"] == "minecraft:air"), None)
    if air_state is None:
        palette.append({"id": "minecraft:air", "faces": [-1] * 6, "invisible": True,
                        "transparent": True, "lum": 0})
        air_state = len(palette) - 1
    states[:] = air_state
    for (x, y, z), s in grid.items():
        if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
            states[idx(x, y, z)] = s

    inv_arr = np.array([p["invisible"] for p in palette], dtype=bool)
    tr_arr = np.array([p["transparent"] for p in palette], dtype=bool)
    lum_arr = np.array([p["lum"] for p in palette], dtype=np.uint8)

    solid = ~inv_arr[states]                       # renders something
    opaque = solid & ~tr_arr[states]               # blocks light
    lum = lum_arr[states]
    light = compute_light(sx, sy, sz, lum, opaque)
    lit_cells = int((light > 0).sum())
    print(f"  lighting: {int((lum > 0).sum())} emitters -> {lit_cells} lit cells "
          f"({100 * lit_cells / n:.1f}% of volume), max {int(light.max())}")

    payload = {
        "name": label, "size": [sx, sy, sz], "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        "atlas": {"cols": cols, "rows": rows, "tile": 16, "tiles": len(tindex), "file": atlas_file},
        "order": "idx = (y*sz + z)*sx + x",
        "palette": palette,
        "states": base64.b64encode(states.astype("<u2").tobytes()).decode(),
        "light": base64.b64encode(light.tobytes()).decode(),
    }
    dst = os.path.join(out, f"room_{label}.json")
    json.dump(payload, open(dst, "w"))
    print(f"  wrote {dst} ({os.path.getsize(dst)/1024:.0f} KB)")

    listing = sorted(f[5:-5] for f in os.listdir(out)
                     if f.startswith("room_") and f.endswith(".json"))
    json.dump(listing, open(os.path.join(out, "rooms.json"), "w"))
    print(f"  rooms available: {listing}")


if __name__ == "__main__":
    main()
