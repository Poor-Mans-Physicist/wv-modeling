"""Greed-rework catalog: the 0.34.1 extractor pointed at the extraction branch (pack `feature/ledger-bugfixes`
worktree + addon `feature/ledger-bugfixes`, the extraction branches plus the ledger bug fixes and the execute rework), plus the god trees, god levels, greed ranks, ancient uniques and the
greed-card tiers.

Sources (required, no defaults):
  WV_PACK       the greed-rework pack's config/the_vault
  WV_ADDON_ROOT the greed-rework addon checkout
"""
import json
import os
import sys

for var in ("WV_PACK", "WV_ADDON_ROOT"):
    if var not in os.environ:
        sys.exit(f"[ERROR] {var} is not set; point it at the greed-rework sources (see the docstring)")
os.environ.setdefault("WV_CATALOG", "catalog_rework.json")
os.environ.setdefault("WV_RELEASE", "greed-rework (ledger-bugfixes e0d29bde, pack b8a13f22)")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract as base  # noqa: E402

GODS = ["idona", "velara", "wendarr", "tenos"]
ANCIENT_HELPER = "/src/main/java/xyz/iwolfking/woldsvaults/api/util/AncientUniqueHelper.java"


def extract_gods():
    gdir = base.PACK + "/gods"
    out = {}
    for god in GODS:
        tree = base.load(f"{gdir}/god_tree_{god}.json")
        styles = base.load(f"{gdir}/god_tree_{god}_gui_styles.json")["styles"]
        effects = base.load(f"{gdir}/god_node_effects_{god}.json")["effects"]
        nodes = []
        for n in tree["nodes"]:
            s = styles.get(n["id"])
            if s is None:
                base.warn(f"god {god}: node {n['id']} has no gui style; placed at 0,0")
                s = {}
            if n["effect"] not in effects:
                base.warn(f"god {god}: node {n['id']} effect {n['effect']} has no effect definition")
            nodes.append(dict(n, x=s.get("x", 0), y=s.get("y", 0), frame=s.get("frameType"), icon=s.get("icon")))
        out[god] = {"nodes": nodes, "edges": tree["edges"], "labels": tree.get("labels", []), "effects": effects}
    return out


def ancient_registry():
    """(registry key, display name) per ancient, from AncientUniqueHelper's vaultUnique/woldsUnique calls."""
    import re
    src = open(base.ADDON_ROOT + ANCIENT_HELPER, encoding="utf-8").read()
    out = []
    for ns, path, name in re.findall(r'(vaultUnique|woldsUnique)\("([a-z0-9_]+)", "([^"]+)"\)', src):
        out.append((("the_vault:" if ns == "vaultUnique" else "woldsvaults:") + path, path, name))
    return out


def extract_ancients():
    """Ancient uniques: the unique's registry modifier identifiers resolved against ONLY its ancient tier config
    (MixinVaultGearTierConfig replaces the whole unique config for an ANCIENT_UNIQUE item)."""
    reg = {}
    for f in (f"{base.PACK}/unique_gear.json", f"{base.ADDON}/gear/unique_gear/unique_gear.json"):
        reg.update(base.load(f)["registry"])
    gdir = f"{base.ADDON}/gear/gear_modifiers"
    out = {}
    for key, path, name in ancient_registry():
        u = reg.get(key)
        cfg = f"{gdir}/unique_ancient_{path}.json"
        if u is None or not os.path.exists(cfg):
            base.warn(f"ancient {key}: {'no registry entry' if u is None else 'no ancient config'}; skipped")
            continue
        gtype = u["item"].split(":")[-1]
        if gtype not in base.UNIQUE_TYPES:
            continue
        by_id = {}
        for g, lst in base.load(cfg)["modifierGroup"].items():
            for m in lst:
                by_id.setdefault(m["identifier"], m)
        mods = []
        for kind, ids in u.get("modifierIdentifiers", {}).items():
            if kind == "BASE_ATTRIBUTE":
                continue
            for i in ids:
                if i not in by_id:
                    base.warn(f"ancient {key}: modifier {i} resolves to nothing in its ancient config; skipped (as in game)")
                    continue
                mods.append(dict(base.summarize_modifier(by_id[i]), kind=kind))
        out["ancient:" + key] = {"name": name, "type": gtype, "modifiers": mods, "ancient": True, "base": key}
    return out


def extract_greed_cards():
    """Tier-3 roll per greed-card family; the deck adds roll - 1 per card (MixinCardDeck.getGreedMultiplier)."""
    vals = base.load(f"{base.PACK}/card/modifiers.json")["values"]
    out = {}
    for cid, e in vals.items():
        if e.get("type") != "greed":
            continue
        fam = cid.rsplit("_", 2)[0]
        top = max(e["multiplierPool"], key=lambda p: p["tier"])
        out.setdefault(fam, top["roll"])
    return out


def main():
    base.main()
    cat = base.load(base.OUT)
    cat["ledger"] = "rework"
    cat["gods"] = extract_gods()
    cat["god_levels"] = base.load(base.PACK + "/gods/god_levels.json")
    cat["greed_ranks"] = base.load(base.PACK + "/gods/greed_ranks.json")
    cat["ancients"] = extract_ancients()
    cat["greed_cards"] = extract_greed_cards()
    with open(base.OUT, "w", encoding="utf-8") as f:
        json.dump(cat, f, indent=1)
    print(f"rework extras: gods {[(g, len(cat['gods'][g]['nodes'])) for g in GODS]}, ancients {len(cat['ancients'])}, "
          f"greed cards {cat['greed_cards']}")


if __name__ == "__main__":
    main()
