"""Convert the DeckFAST stage-layout dump (data/stage_layouts_raw.json) into grid layouts (data/deck_layouts.json).

The raw dump comes from extract/deckfast_run_layouts.py, run inside a copy of the DeckFAST repo
(`uv run python run_layouts.py`, with extract/deckfast_structural_layouts.json as its
decks/structural_layouts.json). That uses the current DeckFAST 2.0 tagged kernel, whose rules match
0.34.1 (additive Archive, wv aa5e7b39, is in the release), EVO class, Max mode.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "stage_layouts_raw.json")
OUT = os.path.join(ROOT, "data", "deck_layouts.json")

CHAR = {"row": "R", "col": "C", "surr": "S", "diag": "X", "deluxe": "D", "typeless": "T", "arcane": "a",
        "dir_greed_up": "^", "dir_greed_down": "v", "dir_greed_left": "<", "dir_greed_right": ">"}


def main():
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    out = {"_source": "DeckFAST tagged kernel via extract/deckfast_run_layouts.py", "decks": {}}
    for o in raw:
        cells = {tuple(p): "_" for p in o["slots"]}
        for r, c, t in o["assignment"]:
            if t not in CHAR:
                raise SystemExit(f"unknown card type {t}")
            cells[(r, c)] = CHAR[t]
        rmin = min(r for r, _ in cells)
        cmin = min(c for _, c in cells)
        rmax = max(r for r, _ in cells)
        cmax = max(c for _, c in cells)
        grid = ["".join(cells.get((r, c), " ") for c in range(cmin, cmax + 1)) for r in range(rmin, rmax + 1)]
        out["decks"][f"{o['deck']}|{o['config']}"] = {
            "deck": o["deck"], "key": o["key"], "config": o["config"], "ndm_deckfast": o["ndm"],
            "cores": o["cores"], "implicits": o["implicits"], "core_slots": o["core_slots"], "grid": grid,
        }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for k, v in out["decks"].items():
        print(k, round(v["ndm_deckfast"], 1), v["cores"], v["implicits"])
        for g in v["grid"]:
            print("   ", g.replace(" ", "."))


if __name__ == "__main__":
    main()
