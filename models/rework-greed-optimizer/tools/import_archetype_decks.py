"""Import archetype decks from DeckFAST (scripts/archetype_decks.py output) into data/deck_layouts_archetypes.json.

Each stage's archetype layouts become ledger layout entries (grid, cores incl. tag cores, implicits). The ledger only
offers them to builds when WV_DECK_CHOICES=archetypes (model/stages.py), or through tools/deck_study.py --layouts.

Run:  uv run python tools/import_archetype_decks.py <arch_end.json> [<arch_max.json> ...] [--best 1]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import deck  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--best", type=int, default=1, help="layouts per archetype to import")
    args = ap.parse_args()
    out = {"_source": "DeckFAST scripts/archetype_decks.py via tools/import_archetype_decks.py", "decks": {},
           "stages": {}}
    if os.path.exists(deck.ARCHETYPES):
        with open(deck.ARCHETYPES, encoding="utf-8") as f:
            out = json.load(f)
    for path in args.files:
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
        stage = res["stage"]
        old = out["stages"].get(stage, [])
        for k in old:
            out["decks"].pop(k, None)
        keys = []
        for a, info in sorted(res["archetypes"].items()):
            for key in info["best"][:args.best]:
                entry = dict(res["layouts"][key], archetype=a, members=info["members"])
                for name, rule in (entry.get("implicit_rules") or {}).items():
                    deck.IMPLICITS.setdefault(name, rule)
                slots, _ = deck.score_layout(entry["grid"], entry["cores"], entry["implicits"])
                print(f"{stage} archetype {a}: {key}  ({len(slots)} stat slots, blanket NDM "
                      f"{deck.blanket_ndm(slots):.0f} at default greed)")
                out["decks"][key] = entry
                keys.append(key)
        out["stages"][stage] = keys
    with open(deck.ARCHETYPES, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {deck.ARCHETYPES}")


if __name__ == "__main__":
    main()
