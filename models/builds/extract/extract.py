"""Flatten the 0.34.1 pack + addon configs into one catalog JSON for the combat model.

Merge rules follow vhapi: gear_modifiers groups are appended (paths containing overwrite/replace/remove
change that), etchings/trinkets/decks/cores are put-by-key with the addon winning, talent GUI styles are
replaced by the addon overwrite file.
"""
import glob
import json
import os
import sys

ROOT = (os.environ.get("WV_SNAPSHOT") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "cache")).replace(os.sep, "/")
PACK = ROOT + "/pack/config/the_vault"
ADDON = ROOT + "/addon/src/generated/resources/data/woldsvaults/vault_configs"
ADDON_MAIN = ROOT + "/addon/src/main/resources/data"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "catalog_0.34.1.json")

GEAR_TYPES = ["helmet", "chestplate", "leggings", "boots", "sword", "axe", "battlestaff", "trident", "rang",
              "shield", "focus", "wand", "plushie", "vault_necklace"]
LEVEL = 100


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def warn(msg):
    print("[extract][WARN] " + msg, file=sys.stderr)


def open_tiers(tiers, level=LEVEL):
    out = []
    for t in tiers:
        lo = t.get("minLevel", 0)
        hi = t.get("maxLevel", -1)
        if lo <= level and (hi == -1 or hi >= level):
            out.append(t)
    return out


def jump_tiers(tiers, level=LEVEL):
    return [t for t in tiers if t.get("minLevel", 0) > level]


def summarize_modifier(m):
    tiers = m.get("tiers", [])
    opened = open_tiers(tiers)
    entry = {
        "attribute": m["attribute"],
        "group": m.get("group"),
        "identifier": m.get("identifier"),
        "tags": m.get("tags", []),
        "open": [{"weight": t.get("weight", 0), "value": t.get("value")} for t in opened],
        "jump": [{"minLevel": t.get("minLevel"), "weight": t.get("weight", 0), "value": t.get("value")}
                 for t in jump_tiers(tiers)],
    }
    return entry


def merge_gear_file(pack_file, addon_files):
    groups = {}
    if os.path.exists(pack_file):
        d = load(pack_file)
        for g, lst in d.get("modifierGroup", {}).items():
            groups[g] = list(lst)
    else:
        warn("missing pack gear file " + pack_file)
    for af in addon_files:
        d = load(af)
        mode = "append"
        low = af.lower()
        if "overwrite" in low:
            mode = "overwrite"
        elif "replace" in low:
            mode = "replace"
        elif "remove" in low:
            mode = "remove"
        if mode == "overwrite":
            groups = {g: list(lst) for g, lst in d.get("modifierGroup", {}).items()}
            continue
        for g, lst in d.get("modifierGroup", {}).items():
            if mode == "replace":
                groups[g] = list(lst)
            elif mode == "remove":
                attrs = {m["attribute"] for m in lst}
                groups[g] = [m for m in groups.get(g, []) if m["attribute"] not in attrs]
            else:
                groups.setdefault(g, []).extend(lst)
    return groups


def extract_gear():
    gear = {}
    for t in GEAR_TYPES:
        for rarity, suffix in (("OMEGA", ""), ("MYTHIC", "_mythic")):
            name = t + suffix
            pack_file = f"{PACK}/gear_modifiers/{name}.json"
            addon_files = [p for p in glob.glob(f"{ADDON}/gear/gear_modifiers/**/{name}.json", recursive=True)]
            if not os.path.exists(pack_file) and not addon_files:
                if rarity == "MYTHIC":
                    warn(f"no mythic file for {t}; mythic {t} falls back to omega tables")
                continue
            groups = merge_gear_file(pack_file, addon_files)
            gear.setdefault(t, {})[rarity] = {g: [summarize_modifier(m) for m in lst] for g, lst in groups.items()}
    return gear


UNIQUE_TYPES = [t for t in GEAR_TYPES if t != "vault_necklace"]


def extract_uniques():
    """Equippable uniques (registry put-by-key, addon wins) with their fixed modifiers resolved against the merged UNIQUE
    tier config (pack groups first, addon appended, so the pack wins duplicate identifiers). Identifiers that resolve
    nowhere are skipped, like GearRollHelper.initializeUniqueGear does in game."""
    reg = {}
    for f in (f"{PACK}/unique_gear.json", f"{ADDON}/gear/unique_gear/unique_gear.json"):
        reg.update(load(f)["registry"])
    groups = merge_gear_file(f"{PACK}/gear_modifiers/unique.json", [f"{ADDON}/gear/gear_modifiers/unique.json"])
    by_id = {}
    for g, lst in groups.items():
        for m in lst:
            by_id.setdefault(m["identifier"], (g, m))
    out = {}
    for uid, u in reg.items():
        gtype = u["item"].split(":")[-1]
        if gtype not in UNIQUE_TYPES:
            continue
        mods = []
        for kind, ids in u.get("modifierIdentifiers", {}).items():
            if kind == "BASE_ATTRIBUTE":
                continue
            for i in ids:
                if i not in by_id:
                    warn(f"unique {uid}: modifier {i} resolves to no UNIQUE tier group; skipped (as in game)")
                    continue
                mods.append(dict(summarize_modifier(by_id[i][1]), kind=kind))
        if u.get("modifierTags"):
            warn(f"unique {uid}: modifierTags {u['modifierTags']} not extracted")
        out[uid] = {"name": u["name"], "type": gtype, "modifiers": mods}
    seals = [summarize_modifier(m) for m in groups.get("CORRUPTED_IMPLICIT", [])]
    return {"uniques": out, "seals": seals}


def _gate(entry):
    spent, deps, either = 0, [], []
    for g in entry.get("dependsOn", []):
        if g["type"] == "talent_points_spent":
            spent = max(spent, int(g.get("amount", 0)))
        elif g["type"] == "constant":
            deps.append(g["id"])
        elif g["type"] == "either":
            either.append([x["id"] for x in g.get("gates", []) if x.get("type") == "constant"])
        else:
            warn(f"skill gate type {g['type']} not extracted")
    return {"spent": spent, "deps": deps, "either": either, "locked": [x["id"] for x in entry.get("lockedBy", []) if "id" in x]}


def extract_talent_gates():
    """Skill gates: pack entries, then the addon's skill/gates files put by key (vhapi SkillGatesConfigLoader)."""
    entries = dict(load(f"{PACK}/skill_gates.json")["SKILL_GATES"]["entries"])
    for f in sorted(glob.glob(f"{ADDON}/skill/gates/*.json")):
        if "overwrite" in f or "remove" in f:
            warn(f"skill gate file {f} uses overwrite/remove; not handled")
            continue
        entries.update(load(f)["SKILL_GATES"]["entries"])
    return {k: _gate(v) for k, v in entries.items()}


def extract_etchings():
    d = load(f"{PACK}/gear/etchings.json")
    etchings = dict(d["ETCHINGS"])
    groups = d["groups"]
    for af in glob.glob(f"{ADDON}/gear/etching/*.json"):
        ad = load(af)
        etchings.update(ad.get("ETCHINGS", ad.get("etchings", {})))
        groups.update(ad.get("groups", {}))
    return {"etchings": etchings, "groups": groups}


def extract_trinkets():
    t = dict(load(f"{PACK}/trinket.json")["TRINKETS"])
    for af in glob.glob(f"{ADDON}/trinkets/*.json"):
        ad = load(af)
        t.update(ad.get("TRINKETS", ad))
    return t


def extract_tree(path):
    return load(path)["tree"]["skills"]


def extract_talent_visibility():
    files = glob.glob(f"{ADDON}/talents/talent_gui/overwrite/*.json")
    if not files:
        warn("no addon talent GUI overwrite found; falling back to pack talents_gui_styles.json")
        files = [f"{PACK}/talents_gui_styles.json"]
    vis = set()
    for f in files:
        d = load(f)
        styles = d.get("styles", d)
        vis.update(styles.keys())
    return sorted(vis)


def extract_prestige_visibility():
    d = load(f"{PACK}/prestige_powers_gui_styles.json")
    return sorted(d.get("styles", d).keys())


def extract_ability_groups():
    d = load(f"{PACK}/abilities_group.json")["types"]
    groups = {k: list(v) for k, v in d.items()}
    for af in glob.glob(f"{ADDON}/abilities/group/*.json"):
        ad = load(af)
        for k, v in ad.get("types", ad).items():
            groups.setdefault(k, [])
            for x in v:
                if x not in groups[k]:
                    groups[k].append(x)
    return groups


def extract_cards():
    full = load(f"{PACK}/card/modifiers.json")
    d = full["values"]
    obtainable = set()
    for pname, members in full.get("pools", {}).items():
        if pname == "wild":
            continue
        for m in members:
            obtainable.add(m["value"])
    best = {}
    for cid, e in d.items():
        if cid not in obtainable:
            continue
        if e.get("type") != "gear":
            continue
        m = e.get("modifier", e)
        attr = m.get("attribute") or e.get("attribute")
        pool = m.get("pool") or e.get("pool") or []
        grp = e.get("groups", [])
        if not attr or not pool or "Shiny" in grp or "Deluxe" in grp:
            continue
        top = max(pool, key=lambda p: p.get("tier", 0))
        val = top.get("max", top.get("min"))
        if not isinstance(val, (int, float)):
            continue
        prev = best.get(attr)
        if prev is None or val > prev["value"]:
            best[attr] = {"card": cid, "tier": top.get("tier"), "value": val}
    return best


def main():
    cat = {
        "meta": {"release": "0.34.1", "level": LEVEL, "pack": os.path.relpath(PACK, ROOT).replace(os.sep, "/"), "addon": os.path.relpath(ADDON, ROOT).replace(os.sep, "/")},
        "gear": extract_gear(),
        "etchings": extract_etchings(),
        "trinkets": extract_trinkets(),
        "abilities": extract_tree(f"{PACK}/abilities.json"),
        "ability_groups": extract_ability_groups(),
        "talents": extract_tree(f"{PACK}/talents.json"),
        "talent_visible": extract_talent_visibility(),
        "greed_nodes": extract_tree(f"{PACK}/greed/greed_nodes.json"),
        "prestige": extract_tree(f"{PACK}/prestige_powers.json"),
        "prestige_visible": extract_prestige_visibility(),
        "cards": extract_cards(),
        "charms": load(f"{PACK}/gear_modifiers/vault_charm.json")["godModifiers"],
        "caps": load(f"{PACK}/attribute_cap_overrides.json"),
        "mana": load(f"{PACK}/mana.json"),
        "hyper": load(f"{PACK}/hyper_objective.json"),
        "uniques": extract_uniques(),
        "talent_gates": extract_talent_gates(),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=1)
    print(f"wrote {OUT}: gear types {len(cat['gear'])}, etchings {len(cat['etchings']['etchings'])}, "
          f"trinkets {len(cat['trinkets'])}, abilities {len(cat['abilities'])}, talents {len(cat['talents'])}, "
          f"visible talents {len(cat['talent_visible'])}, greed nodes {len(cat['greed_nodes'])}, "
          f"prestige {len(cat['prestige'])}, card stats {len(cat['cards'])}, uniques {len(cat['uniques']['uniques'])}")


if __name__ == "__main__":
    main()
