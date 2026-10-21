"""God-tree assets for the greed-rework ledger -> data/god_assets.json: the star-frame sheets, node icons and god icons
(base64 PNG, as the in-game screen draws them) and the node descriptions.

Sources: addon feature/extraction-vaults (star sheets, descriptions), pack-merge god gui styles (icon ids), and the
the_vault jar (node icons, god icons).
"""
import base64
import glob
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "god_assets.json")
ADDON = os.environ["WV_ADDON_ROOT"]
PACK = os.environ["WV_PACK"]
JAR = os.environ["WV_JAR"]
GODS = ["idona", "velara", "wendarr", "tenos"]


def b64(data):
    return "data:image/png;base64," + base64.b64encode(data).decode()


def main():
    jar = zipfile.ZipFile(JAR)
    names = set(jar.namelist())
    out = {"sheets": {}, "icons": {}, "god_icons": {}, "descriptions": {}}
    missing = []
    for g in GODS + ["neutral"]:
        p = f"{ADDON}/src/main/resources/assets/woldsvaults/textures/gui/gods/stars_{g}.png"
        if os.path.exists(p):
            out["sheets"][g] = b64(open(p, "rb").read())
        else:
            missing.append(p)
    for g in GODS:
        key = f"assets/the_vault/textures/gui/screen/icon_{g}.png"
        if key in names:
            out["god_icons"][g] = b64(jar.read(key))
        else:
            missing.append(key)
        styles = json.load(open(f"{PACK}/gods/god_tree_{g}_gui_styles.json", encoding="utf-8"))["styles"]
        for s in styles.values():
            ic = s.get("icon")
            if not ic or ic in out["icons"]:
                continue
            ns, path = ic.split(":", 1)
            key = f"assets/{ns}/{path}"
            if key in names:
                out["icons"][ic] = b64(jar.read(key))
            else:
                missing.append(key)
        dfile = f"{ADDON}/src/generated/resources/data/woldsvaults/vault_configs/skill/descriptions/god_nodes_{g}.json"
        if os.path.exists(dfile):
            out["descriptions"].update(json.load(open(dfile, encoding="utf-8"))["descriptions"])
        else:
            missing.append(dfile)
    for m in missing:
        print(f"[god-assets][WARN] missing {m}; the report falls back to a plain marker", file=sys.stderr)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"wrote {OUT}: {len(out['sheets'])} star sheets, {len(out['icons'])} node icons, {len(out['god_icons'])} god icons, "
          f"{len(out['descriptions'])} descriptions")


if __name__ == "__main__":
    main()
