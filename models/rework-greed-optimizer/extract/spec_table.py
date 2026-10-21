"""Write data/spec_table.json: model/spec_overlay.py merged with each catalog's ability config keys.

python extract/spec_table.py

Per spec: the overlay record plus, for every catalog present (data/catalog_0.34.1.json, data/catalog_rework.json),
the spec's skill, tier count, the config keys its tiers carry (union over tiers), its cooldown key and its mana keys.
Run it after every overlay edit: model/abilities.py refuses to load a table whose overlay part differs from the
overlay. A spec a catalog lacks gets null for that catalog and a warning.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model.spec_overlay import SPEC_OVERLAY, WEAPON_COMBOS  # noqa: E402

OUT = os.path.join(ROOT, "data", "spec_table.json")
CATALOGS = {"0.34.1": os.path.join(ROOT, "data", "catalog_0.34.1.json"),
            "rework": os.path.join(ROOT, "data", "catalog_rework.json")}
NON_CONFIG = {"type", "id", "learnPointCost", "regretPointCost", "unlockLevel"}
MANA_KEYS = ("manaCost", "manaCostPerSecond", "additionalManaPerBolt", "manaRampPerSecond")


def warn(msg):
    print("[spec_table][WARN] " + msg, file=sys.stderr)


def catalog_specs(path):
    """{spec id: spec config with its skill id} as catalog.abilities() builds it."""
    with open(path, encoding="utf-8") as fh:
        cat = json.load(fh)
    out = {}
    for s in cat["abilities"]:
        for spec in s.get("specializations", [s]):
            out[spec["id"]] = dict(spec, skill=s["id"])
    return out


def config_facts(spec):
    tiers = spec.get("tiers", [spec])
    keys = sorted(set().union(*[set(t) for t in tiers]) - NON_CONFIG)
    return {"skill": spec["skill"], "tiers": len(tiers), "config_keys": keys,
            "cooldown_key": "cooldownTicks" if "cooldownTicks" in keys else None,
            "mana_keys": [k for k in MANA_KEYS if k in keys]}


def build():
    cats = {}
    for name, path in CATALOGS.items():
        if os.path.exists(path):
            cats[name] = catalog_specs(path)
        else:
            warn(f"{path} not found; the table has no {name} config keys")
    specs = {}
    for sid, rec in SPEC_OVERLAY.items():
        cat = {}
        for name, abil in cats.items():
            if sid in abil:
                cat[name] = config_facts(abil[sid])
            else:
                warn(f"{sid} is not in the {name} catalog")
                cat[name] = None
        specs[sid] = dict(rec, catalog=cat)
    return {"specs": specs, "weapon_combos": WEAPON_COMBOS}


if __name__ == "__main__":
    table = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(table, fh, indent=1)
        fh.write("\n")
    print(f"spec_table: {len(table['specs'])} specs -> {OUT}")
