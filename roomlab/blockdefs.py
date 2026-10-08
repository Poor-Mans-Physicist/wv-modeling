"""Block id -> per-face textures, transparency and luminance.

Indexes every jar in the live instance's mods folder plus the vanilla client jar, then walks
blockstates -> model parent chain -> texture resource locations the same way the game does.
Luminance is NOT in the assets (it lives in each mod's Java), so it comes from LUMINANCE below,
which was read out of decompiled sources -- see the note on each entry.
"""
import zipfile, json, os, io, glob, functools
from PIL import Image

INSTANCE = os.environ.get("WV_INSTANCE", "")
VANILLA = os.environ.get("MC_CLIENT_JAR", "")
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "roomlab", "asset_index.json")

FACES = ("up", "down", "north", "south", "east", "west")

# Blocks that exist but render as nothing.
INVISIBLE = {
    "minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:light",
    "minecraft:barrier", "minecraft:structure_void", "minecraft:jigsaw",
}

# Light emission, 0-15. Vanilla values are the well-known ones; auxiliaryblocks values were read
# from the decompiled `iskallia.auxiliaryblocks.init.ModBlocks` (gelatin = lightLevel 8; the
# crystal blocks call no lightLevel at all, so they are genuinely 0).
LUMINANCE = {
    "minecraft:glowstone": 15, "minecraft:sea_lantern": 15, "minecraft:shroomlight": 15,
    "minecraft:ochre_froglight": 15, "minecraft:verdant_froglight": 15,
    "minecraft:pearlescent_froglight": 15, "minecraft:lantern": 15, "minecraft:soul_lantern": 10,
    "minecraft:torch": 14, "minecraft:wall_torch": 14, "minecraft:soul_torch": 10,
    "minecraft:end_rod": 14, "minecraft:campfire": 15, "minecraft:soul_campfire": 10,
    "minecraft:jack_o_lantern": 15, "minecraft:beacon": 15, "minecraft:conduit": 15,
    "minecraft:crying_obsidian": 10, "minecraft:magma_block": 3, "minecraft:glow_lichen": 7,
    "minecraft:amethyst_cluster": 5, "minecraft:large_amethyst_bud": 4,
    "minecraft:medium_amethyst_bud": 2, "minecraft:small_amethyst_bud": 1,
    "minecraft:lava": 15, "minecraft:fire": 15, "minecraft:soul_fire": 10,
    "minecraft:redstone_lamp": 0, "minecraft:sculk_catalyst": 6, "minecraft:shroomlight ": 15,
    "auxiliaryblocks:blue_gelatin": 8, "auxiliaryblocks:cyan_gelatin": 8,
    "auxiliaryblocks:lime_gelatin": 8, "auxiliaryblocks:magenta_gelatin": 8,
    "auxiliaryblocks:orange_gelatin": 8, "auxiliaryblocks:purple_gelatin": 8,
    "auxiliaryblocks:red_gelatin": 8, "auxiliaryblocks:yellow_gelatin": 8,
    "the_vault:vault_glass": 0,
}


def build_index():
    if not INSTANCE or not os.path.isdir(os.path.join(INSTANCE, "mods")):
        raise SystemExit("[blockdefs][ERROR] set WV_INSTANCE to your Wold's Vaults instance folder (the one holding mods/); "
                         "python setup/doctor.py finds it for you")
    if not VANILLA or not os.path.isfile(VANILLA):
        print("[blockdefs][FALLBACK] MC_CLIENT_JAR not set or missing; vanilla blocks will have no textures")
    idx = {}
    for jp in ([VANILLA] if VANILLA and os.path.isfile(VANILLA) else []) + sorted(glob.glob(INSTANCE + "/mods/*.jar")):
        try:
            z = zipfile.ZipFile(jp)
        except Exception as e:
            print(f"  [warn] unreadable jar, skipped: {os.path.basename(jp)} ({e})")
            continue
        for n in z.namelist():
            if n.startswith("assets/") and (n.endswith(".json") or n.endswith(".png")):
                idx[n] = jp
        z.close()
    return idx


def load_index():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    print("building asset index (one-off, ~10s) ...")
    idx = build_index()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(idx, open(CACHE, "w"))
    return idx


IDX = None
_zips = {}


def _open(jp):
    if jp not in _zips:
        _zips[jp] = zipfile.ZipFile(jp)
    return _zips[jp]


def read_asset(path):
    global IDX
    if IDX is None:
        IDX = load_index()
    jp = IDX.get(path)
    return None if jp is None else _open(jp).read(path)


def rl(s, kind, ext):
    ns, _, name = s.partition(":")
    if not name:
        ns, name = "minecraft", s
    return f"assets/{ns}/{kind}/{name}.{ext}"


def base_of(bid):
    return bid.split("[")[0]


def props_of(bid):
    if "[" not in bid:
        return {}
    return dict(p.split("=") for p in bid[:-1].split("[", 1)[1].split(","))


def _pick_model(bs, props):
    if "variants" in bs:
        v = bs["variants"]
        key = ",".join(f"{k}={val}" for k, val in sorted(props.items()))
        for cand in (key, ""):
            if cand in v:
                e = v[cand]
                return (e[0] if isinstance(e, list) else e).get("model")
        for vk, e in v.items():
            want = dict(p.split("=") for p in vk.split(",")) if vk else {}
            if all(props.get(k) == val for k, val in want.items()):
                return (e[0] if isinstance(e, list) else e).get("model")
        e = next(iter(v.values()))
        return (e[0] if isinstance(e, list) else e).get("model")
    if "multipart" in bs:
        for part in bs["multipart"]:
            a = part.get("apply")
            m = (a[0] if isinstance(a, list) else a).get("model")
            if m:
                return m
    return None


@functools.lru_cache(maxsize=None)
def _model_textures(bid):
    """Flattened `textures` map from the block's model parent chain, plus the parent chain itself."""
    raw = read_asset(rl(base_of(bid), "blockstates", "json"))
    if raw is None:
        return {}, []
    model = _pick_model(json.loads(raw), props_of(bid))
    tex, chain, guard = {}, [], 0
    while model and guard < 12:
        guard += 1
        chain.append(model)
        mraw = read_asset(rl(model, "models", "json"))
        if mraw is None:
            break
        m = json.loads(mraw)
        for k, v in (m.get("textures") or {}).items():
            tex.setdefault(k, v)
        model = m.get("parent")
    return tex, chain


def _deref(tex, key):
    v = tex.get(key)
    guard = 0
    while isinstance(v, str) and v.startswith("#") and guard < 8:
        guard += 1
        v = tex.get(v[1:])
    return v if isinstance(v, str) else None


@functools.lru_cache(maxsize=None)
def face_textures(bid):
    """-> {up,down,north,south,east,west: texture resloc} or None if unresolvable."""
    tex, _ = _model_textures(bid)
    if not tex:
        return None
    g = lambda *keys: next((t for t in (_deref(tex, k) for k in keys) if t), None)
    side = g("side", "all", "texture", "cross", "pane", "front", "wall", "bottom", "particle")
    up = g("top", "up", "end", "all") or side
    down = g("bottom", "down", "end", "all") or side
    if side is None and up is None:
        return None
    side = side or up
    return {"up": up, "down": down, "north": side, "south": side, "east": side, "west": side}


@functools.lru_cache(maxsize=None)
def texture_image(resloc):
    raw = read_asset(rl(resloc, "textures", "png"))
    if raw is None:
        return None
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    if im.height > im.width:          # animated strip -> first frame
        im = im.crop((0, 0, im.width, im.width))
    return im.resize((16, 16), Image.NEAREST)


@functools.lru_cache(maxsize=None)
def is_transparent(bid):
    """True if any face texture has non-opaque pixels (glass, panes, plants, ...)."""
    ft = face_textures(bid)
    if ft is None:
        return False
    for t in set(ft.values()):
        im = texture_image(t)
        if im is None:
            continue
        if any(p < 250 for p in im.getdata(3)):
            return True
    return False


def luminance(bid):
    b = base_of(bid)
    if b == "minecraft:light":
        return int(props_of(bid).get("level", 15))
    if b == "minecraft:redstone_lamp":
        return 15 if props_of(bid).get("lit") == "true" else 0
    if b in ("minecraft:cave_vines", "minecraft:cave_vines_plant"):
        return 14 if props_of(bid).get("berries") == "true" else 0
    return LUMINANCE.get(b, 0)


def is_invisible(bid):
    return base_of(bid) in INVISIBLE
