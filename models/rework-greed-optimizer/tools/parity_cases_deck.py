"""Deck-model parity fixtures: random rework decks scored by the Python reference (model/deck_rework.py).

Writes out/deck_fixture.json: per deck the slots, slot types, colours, cards (kind, groups mask, value), core set,
modifiers, greed and the reference terms. Two checks read it:
  * the kernel:  WV_DECK_FIXTURE=out/deck_fixture.json cargo test --release deck_fixture   (kernel/src/deck.rs)
  * DeckFAST:    uv run --no-sync python scripts/deck_fixture_check.py <fixture>   (tagsim.rs simulate_rework)
All three must agree to 1e-9 per slot (DeckFAST is checked on the deck total and a value-weighted total).

Coverage: every core, every modifier kind (Mystery-scaled values included), Shiny, Nitwit slots, greed stacking,
battery cards, empty and Arcane slots, multi-colour decks. Cards are drawn so DeckFAST can represent them: stat cards
carry their type's groups (Evolution / Stat / Deluxe), battery cards one non-stat group, Arcane cards only Arcane.

Run:  uv run --no-sync python tools/parity_cases_deck.py [--n 2000] [--seed 7]
"""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from model import deck_rework as D  # noqa: E402

CATS = ["Offensive", "Defensive", "Physical", "Magical", "Utility", "Essence"]
CORES = list(D.CORE_CODE)


def rand_deck(rng):
    w, h = rng.randint(3, 9), rng.randint(2, 6)
    cells = [(r, c) for r in range(h) for c in range(w) if rng.random() < 0.85]
    if len(cells) < 4:
        cells = [(0, 0), (0, 1), (1, 0), (1, 1)]
    multi = rng.random() < 0.4
    base_col = rng.randrange(4)
    shiny = rng.random() < 0.2
    types, colors, cards = [], [], []
    for _ in cells:
        x = rng.random()
        if x < 0.40:
            t = rng.choice([D.T_ROW, D.T_COL, D.T_SURR, D.T_DIAG])
            kind = "evo"
        elif x < 0.52:
            t, kind = D.T_DELUXE, "deluxe"
        elif x < 0.64:
            t, kind = D.T_TYPELESS, "typeless"
        elif x < 0.72:
            t, kind = D.T_TYPELESS, "battery"
        elif x < 0.86:
            t, kind = rng.choice([D.T_G_UP, D.T_G_DOWN, D.T_G_LEFT, D.T_G_RIGHT]), "greed"
        elif x < 0.93:
            t, kind = D.T_DEAD, "dead"
        else:
            t, kind = D.T_ARCANE, "arcane"
        cats = rng.sample(CATS, rng.randint(1, 3))
        if kind == "evo":
            groups = cats + ["Evolution"]
        elif kind == "deluxe":
            groups = cats + ["Stat", "Deluxe"]
        elif kind == "typeless":
            groups = cats + ["Stat"]
        elif kind == "battery":
            groups = [rng.choice(["Resource", "Knack", "Temporal"])]
        elif kind == "greed":
            groups = ["Greed"]
        elif kind == "arcane":
            groups = ["Arcane"]
        else:
            groups = []
        types.append(t)
        colors.append(rng.randrange(4) if multi else base_col)
        cards.append({"kind": {"battery": "knack"}.get(kind, kind), "groups": groups, "g": D.group_mask(groups),
                      "value": round(rng.uniform(0.005, 3.0), 4), "fixture_kind": kind})
    n_cores = rng.randint(0, 4)
    cores = []
    for name in rng.sample(CORES, n_cores):
        k = rng.randint(1, 3) if name == "empyreal" else 0
        col = rng.randrange(4) if name == "color" and rng.random() < 0.7 else D.COLOR_NONE
        cores.append((name, round(rng.uniform(0.01, 2.0), 4), k, col))
    nitwit = round(rng.uniform(0.5, 3.0), 3) if rng.random() < 0.3 else 0.0
    nslots = [nitwit > 0 and rng.random() < 0.15 for _ in cells]
    imps = []
    for kind in rng.sample(list(D.IMP_CODE), rng.randint(0, 3)):
        code = D.IMP_CODE[kind]
        scale = 0.75 if rng.random() < 0.3 else 1.0
        if kind == "freq":
            imps.append((code, 1.0 + scale, 0, 0, rng.choice([D.T_COL, D.T_SURR, D.T_DIAG])))
        elif kind == "global":
            gm = D.group_mask(rng.sample(CATS + ["Stat", "Foil"], rng.randint(1, 2)))
            cm = (1 << rng.randrange(4)) if rng.random() < 0.3 else 0
            imps.append((code, round(rng.uniform(0.3, 2.5), 3) * scale, gm, cm, 0))
        elif kind == "adjacency":
            imps.append((code, round(rng.uniform(0.3, 1.0), 3) * scale, D.group_mask([rng.choice(["Resource", "Knack"])]),
                         0, rng.randint(0, 1)))
        else:
            imps.append((code, round(rng.uniform(0.04, 1.0), 3) * scale, 0, 0, 0))
    return {"slots": [list(p) for p in cells], "types": types, "colors": colors, "cards": cards,
            "cset": {"cores": cores, "nitwit": nitwit, "nitwit_slots": nslots, "shiny": shiny},
            "imps": imps, "greed": rng.choice([0.75, 1.0, 1.25, 1.5]), "deluxe_base": 1.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "deck_fixture.json"))
    args = ap.parse_args()
    rng = random.Random(args.seed)
    decks = []
    for _ in range(args.n):
        d = rand_deck(rng)
        geo = D.Geometry(d["slots"])
        d["terms"] = D.evaluate(geo, d["types"], d["colors"], d["cards"], d["cset"], d["imps"], d["greed"],
                                d["deluxe_base"])
        decks.append(d)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"decks": decks}, f)
    nz = sum(1 for d in decks for t in d["terms"] if t)
    print(f"wrote {args.out}: {len(decks)} decks, {nz} scoring cards")


if __name__ == "__main__":
    main()
