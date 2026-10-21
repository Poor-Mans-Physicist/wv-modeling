"""Collect in-game textures (first animation frame, base64 PNG) and GUI layouts into data/assets.json for the report.

Sources: the_vault 3.21.6 jar (the version 0.34.1 ships) and the 0.34.1 addon resources. Everything the report
draws that has an in-game sprite is taken from here; anything missing falls back to a CSS placeholder.
"""
import base64
import glob
import io
import json
import os
import zipfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "assets.json")
JAR = os.environ["WV_JAR"]
REL = (os.environ.get("WV_SNAPSHOT") or os.path.join(ROOT, "cache")).replace(os.sep, "/")
PACK = REL + "/pack/config/the_vault"
ADDON_CFG = REL + "/addon/src/generated/resources/data/woldsvaults/vault_configs"
ADDON_ASSETS = REL + "/addon/src/main/resources/assets"

_jar = zipfile.ZipFile(JAR)
_names = set(_jar.namelist())
missing = []


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def raw_png(ns_path):
    """'ns:path' (with or without textures/ prefix and .png) -> PNG bytes, or None."""
    ns, path = ns_path.split(":", 1) if ":" in ns_path else ("the_vault", ns_path)
    if not path.startswith("textures/"):
        path = "textures/" + path
    if not path.endswith(".png"):
        path += ".png"
    key = f"assets/{ns}/{path}"
    if key in _names:
        return _jar.read(key)
    fp = f"{ADDON_ASSETS}/{ns}/{path}"
    if os.path.exists(fp):
        with open(fp, "rb") as f:
            return f.read()
    return None


def encode(data):
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = img.size
    if h > w and h % w == 0:
        img = img.crop((0, 0, w, w))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def pick(key, candidates, out):
    for c in candidates:
        data = raw_png(c)
        if data:
            out[key] = encode(data)
            return True
    missing.append((key, candidates[:2]))
    return False


def merged_ability_gui():
    base = load_json(f"{PACK}/abilities_gui_styles.json")
    styles = dict(base["styles"])
    for f in glob.glob(f"{ADDON_CFG}/abilities/ability_gui/**/*.json", recursive=True):
        d = load_json(f)
        for k, v in d.get("styles", {}).items():
            if k in styles and "specializationStyles" in v:
                styles[k] = dict(styles[k])
                styles[k]["specializationStyles"] = dict(styles[k].get("specializationStyles", {}), **v["specializationStyles"])
            else:
                styles[k] = v
    return {"styles": styles, "labels": base.get("labels", []), "lines": base.get("lines", [])}


def model_layer0(model):
    """'ns:path#inventory' item model -> its layer0 texture id (None when the model is missing)."""
    ns, path = model.split("#")[0].split(":", 1) if ":" in model else ("the_vault", model.split("#")[0])
    key = f"assets/{ns}/models/item/{path}.json"
    data = None
    if key in _names:
        data = json.loads(_jar.read(key))
    elif os.path.exists(f"{ADDON_ASSETS}/{ns}/models/item/{path}.json"):
        data = load_json(f"{ADDON_ASSETS}/{ns}/models/item/{path}.json")
    if not data:
        return None
    return (data.get("textures") or {}).get("layer0")


def rework_art(icons):
    """Card art for the rework deck menus (Arcane, Knack, Temporal, Resource cards) and deck item icons, from the
    rework catalog's pack."""
    cat_path = os.path.join(ROOT, "data", "catalog_rework.json")
    if not os.path.exists(cat_path):
        print("[assets][FALLBACK] no data/catalog_rework.json; rework card art skipped")
        return
    pack = load_json(cat_path)["meta"]["pack"]
    full = load_json(f"{pack}/card/modifiers.json")
    vals = full["values"]
    for pool in ("arcane", "knack", "deluxe_knack", "temporal", "deluxe_temporal", "resource", "deluxe_resource"):
        for m in full.get("pools", {}).get(pool, []):
            cid = m["value"]
            e = vals.get(cid) or {}
            tex = model_layer0(e.get("model", "")) if e.get("model") else None
            if tex:
                pick("cardart:" + cid, [tex], icons)
            else:
                missing.append(("cardart:" + cid, [e.get("model")]))
    decks = load_json(f"{pack}/card/decks.json")
    for key in list((decks.get("values") or decks).keys()):
        if "deck:" + key in icons:
            continue
        pick("deck:" + key, [f"the_vault:item/deck/{key}_deck", f"woldsvaults:item/deck/{key}", f"the_vault:item/deck/{key}"],
             icons)


def main():
    icons = {}
    gui = {"abilities": merged_ability_gui()}
    tal = {}
    for f in glob.glob(f"{ADDON_CFG}/talents/talent_gui/overwrite/*.json"):
        tal.update(load_json(f).get("styles", {}))
    gui["talents"] = {"styles": tal}
    gui["greed"] = load_json(f"{PACK}/greed/greed_gui_styles.json")
    gui["greed_tree"] = load_json(f"{PACK}/greed/greed_tree.json")

    for skill, st in gui["abilities"]["styles"].items():
        for spec, ss in st.get("specializationStyles", {}).items():
            pick("ability:" + spec, [ss.get("icon", "")], icons)
    for tid, st in tal.items():
        pick("talent:" + tid, [st.get("icon", "")], icons)
    for nid, st in gui["greed"]["styles"].items():
        pick("greed:" + nid, [st.get("icon", "")], icons)
    for tier, path in gui["greed_tree"].get("tierFrameTextures", {}).items():
        pick("greed_frame:" + tier, [path], icons)

    tv = "the_vault:item/gear/"
    gear = {
        "helmet": [tv + "armor/phoenix_goldflame/helmet", tv + "armor/shadow_king/helmet"],
        "chestplate": [tv + "armor/phoenix_goldflame/chestplate", tv + "armor/shadow_king/chestplate"],
        "leggings": [tv + "armor/phoenix_goldflame/leggings", tv + "armor/shadow_king/leggings"],
        "boots": [tv + "armor/phoenix_goldflame/boots", tv + "armor/shadow_king/boots"],
        "sword": [tv + "sword/sword_1", tv + "sword/moonshine"],
        "axe": [tv + "axe/axe_6", tv + "axe/axe_1"],
        "battlestaff": [tv + "axe/tenos_staff", tv + "wand/ninja_staff"],
        "trident": ["woldsvaults:item/gear/trident/fork", "woldsvaults:item/gear/trident/ocean_current"],
        "rang": [tv + "rang/trirang", tv + "rang/ourorangos"],
        "shield": [tv + "shield/crystal", tv + "shield/shield_1"],
        "focus": [tv + "focus/echoflare", tv + "focus/focus_1"],
        "wand": [tv + "wand/ender", tv + "wand/wand_1"],
        "plushie": ["woldsvaults:item/gear/plushie/pocket_penguin"],
        "vault_necklace": [tv + "pendants/pendant"],
    }
    names = sorted(n for n in _names if n.endswith(".png"))
    for gtype, cands in gear.items():
        extra = [n.replace("assets/", "").replace("/textures/", ":", 1)[:-4] for n in names
                 if f"/item/gear/{gtype}/" in n and "overlay" not in n][:3]
        pick("gear:" + gtype, cands + [e.replace("the_vault:", "the_vault:") for e in extra], icons)
    for god in ("idona", "tenos", "velara", "wendarr"):
        pick("charm:" + god.upper(), [f"{tv}charm/{god}/omega"], icons)
    pick("deck:anvil", ["the_vault:item/deck/anvil_deck"], icons)
    pick("deck:mystery", ["the_vault:item/deck/mystery_deck"], icons)
    pick("deck:wall", ["woldsvaults:item/deck/wall"], icons)
    for color in ("red", "green", "blue", "yellow"):
        for t in range(1, 6):
            pick(f"card:evo:{color}:{t}", [f"the_vault:item/card/{color}_evolution_{t}"], icons)
            pick(f"card:stat:{color}:{t}", [f"the_vault:item/card/{color}_{t}"], icons)
        pick(f"card:arcane:{color}", [f"the_vault:item/card/{color}_arcane"], icons)
    pick("card:greed", ["the_vault:item/card/greed"], icons)
    pick("card:back", ["the_vault:item/card/card_back"], icons)
    card_icon = {
        "attack_damage": "attack_damage", "ability_power": "ability_power", "ability_power_percent": "ability_power_percentile",
        "damage_increase": "attack_damage_percentile", "attack_speed_percent": "attack_speed", "armor_percentile": "armor",
        "health_percentile": "health", "resistance": "resistance", "cooldown_reduction": "cooldown_reduction",
        "lucky_hit_chance": "lucky_hit", "mana_additive": "mana", "mana_additive_percentile": "mana_percentile",
        "mana_regen": "mana_regeneration", "area_of_effect": "area_of_effect", "critical_hit_mitigation": "crit_hit_resistance",
        "thorns_damage_flat": "thorns", "knockback_resistance": "knockback_resistance", "healing_effectiveness": "healing_efficiency",
        # Rework: thorns % cards replace the flat thorns cards and keep their icon.
        "thorns_damage_flat_percentile": "thorns", "movement_speed": "movement_speed",
        "item_quantity": "item_quantity", "item_rarity": "item_rarity",
    }
    for attr, f in card_icon.items():
        pick("cardicon:" + attr, [f"the_vault:item/card/icon/{f}", f"the_vault:item/card/{f}"], icons)
    for color in ("red", "green", "blue", "yellow"):
        for t in range(1, 6):
            pick(f"card:temporal:{color}:{t}", [f"the_vault:item/card/{color}_temporal_{t}"], icons)
        pick(f"card:resource:{color}", [f"the_vault:item/card/{color}_resource"], icons)
    rework_art(icons)
    trinkets = load_json(f"{PACK}/trinket.json")["TRINKETS"]
    for af in glob.glob(f"{ADDON_CFG}/trinkets/*.json"):
        ad = load_json(af)
        trinkets.update(ad.get("TRINKETS", ad))
    for tid in trinkets:
        ns, name = tid.split(":", 1)
        pick("trinket:" + tid, [f"{ns}:item/trinket/{name}", f"the_vault:item/trinket/{name}", f"woldsvaults:item/{name}"], icons)
    pick("ui:slot", ["the_vault:gui/screen/inset_item_slot_background"], icons)
    pick("ui:window", ["the_vault:gui/screen/default_window_background.9"], icons)
    pick("ui:deck_slot", ["the_vault:gui/slot/curios/deck_slot"], icons)
    pick("ui:arcane_slot", ["the_vault:gui/screen/card_deck_arcane_slot"], icons)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"icons": icons, "gui": gui}, f)
    size = os.path.getsize(OUT)
    print(f"wrote {OUT}: {len(icons)} icons, {size / 1e3:.0f} kB")
    for key, cands in missing:
        print(f"[assets][FALLBACK] no texture for {key} (tried {cands})")


if __name__ == "__main__":
    main()


ATTR_SRC = [
    ("the_vault", os.environ["WV_THE_VAULT_SRC"]),
    ("woldsvaults", REL + "/addon/src/main/java/xyz/iwolfking/woldsvaults/init/ModGearAttributes.java"),
]


def attribute_display():
    """attribute id -> {name, color, reader} from the ModGearAttributes reader declarations."""
    import re
    pat = re.compile(r'attr\("([a-z0-9_]+)".*?ModGearAttributeReaders\.(\w+)\("([^"]+)",\s*(-?\d+|0x[0-9A-Fa-f]+)', re.S)
    out = {}
    for ns, path in ATTR_SRC:
        if not os.path.exists(path):
            print(f"[assets][FALLBACK] attribute source missing: {path}")
            continue
        src = open(path, encoding="utf-8").read()
        for stmt in src.split(";"):
            m = pat.search(stmt)
            if not m:
                continue
            col = int(m.group(4), 0) & 0xFFFFFF
            out[f"{ns}:{m.group(1)}"] = {"name": m.group(3), "color": f"#{col:06X}", "reader": m.group(2)}
    return out


def add_attributes():
    with open(OUT, encoding="utf-8") as f:
        data = json.load(f)
    data["attributes"] = attribute_display()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"attribute display entries: {len(data['attributes'])}")


if __name__ == "__main__":
    add_attributes()


GEAR_MODELS = {
    "sword": ["crystalblade", "sword_7"], "axe": ["axe_7", "axe_0"], "battlestaff": ["battlestaff_purple", "battlestaff_0"],
    "trident": ["quindent", "marlin"], "rang": ["geta", "brick"], "shield": ["paladin", "grizzly"],
    "focus": ["frozenorb", "wizard_tome"], "wand": ["stormcallers_wand", "lightning"], "plushie": ["panda", "cow"],
}


def model_json(gtype, name):
    path = f"assets/the_vault/models/item/gear/{gtype}/{name}.json"
    if path in _names:
        return json.loads(_jar.read(path))
    fp = f"{ADDON_ASSETS}/the_vault/models/item/gear/{gtype}/{name}.json"
    if os.path.exists(fp):
        return load_json(fp)
    return None


def texture_entry(ref):
    data = raw_png(ref)
    if not data:
        return None
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = img.size
    if h > w and h % w == 0:
        h = w
    return {"src": encode(data), "w": w, "h": h}


def gear_models():
    """3D Blockbench gear models (cubes, face UVs, gui pose) for the report's CSS renderer."""
    out = {}
    for gtype, cands in GEAR_MODELS.items():
        for name in cands:
            j = model_json(gtype, name)
            if not j or "elements" not in j:
                continue
            tex = {}
            for key, ref in j.get("textures", {}).items():
                if key == "particle":
                    continue
                e = texture_entry(ref)
                if e:
                    tex[key] = e
            if not tex:
                continue
            els = []
            for el in j["elements"]:
                faces = {f: {"uv": v.get("uv"), "texture": str(v.get("texture", "")).lstrip("#"), "rotation": v.get("rotation", 0)}
                         for f, v in el.get("faces", {}).items()}
                els.append({"from": el["from"], "to": el["to"], "rotation": el.get("rotation"), "faces": faces})
            out[gtype] = {"name": name, "elements": els, "textures": tex,
                          "gui": (j.get("display") or {}).get("gui", {})}
            break
        else:
            print(f"[assets][FALLBACK] no 3D model for {gtype} (tried {cands})")
    return out


def add_models():
    with open(OUT, encoding="utf-8") as f:
        data = json.load(f)
    data["models"] = gear_models()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f)
    print(f"gear models: {sorted(data['models'])}, {os.path.getsize(OUT) / 1e3:.0f} kB")


if __name__ == "__main__":
    add_models()
