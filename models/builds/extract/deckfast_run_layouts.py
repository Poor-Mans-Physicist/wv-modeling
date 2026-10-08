"""Optimize EVO layouts for the combat model's stage decks (0.34.1 rules) and dump them as JSON."""
import json
import multiprocessing
import random
import sys

sys.argv += ["--structural-cores"]

JOBS = [("anvil", "no greed", -1, 0), ("anvil", "unconstrained", -1, -1),
        ("wall", "unconstrained", -1, -1), ("wall", "min 8 reg", 8, -1),
        ("mystery", "unconstrained", -1, -1), ("mystery", "min 8 reg", 8, -1)]


def run(job):
    from src.config import DECKS, STRUCTURAL_IMPLICITS
    from src.types import CardClass
    from src.simulate import candidate_cores, sa_optimize_tagged
    from src.implicits import implicits_for_deck
    key, label, min_reg, max_greed = job
    random.seed()
    deck = next(d for d in DECKS if d.key == key).with_constraints(min_reg, max_greed)
    imps = implicits_for_deck(deck.key)
    forced = STRUCTURAL_IMPLICITS.get(deck.key)
    if forced:
        imps = [t for k in forced for t in implicits_for_deck(k)]
    best = {"score": -1.0}
    for cores in candidate_cores(CardClass.EVO, deck):
        for _ in range(12):
            asgn, score = sa_optimize_tagged(deck, CardClass.EVO, cores, n_iter=60000, implicits=imps)
            if score > best["score"]:
                best = {"score": score, "cores": sorted(c.value for c in cores), "assignment": asgn}
    slots = list(deck.slots)
    rows = [r for r, _ in slots]; cols = [c for _, c in slots]
    return {"deck": deck.name, "key": key, "config": label, "ndm": best["score"], "cores": best["cores"],
            "implicits": forced or [key], "core_slots": deck.core_slots,
            "assignment": [[p[0], p[1], t.value] for p, t in best["assignment"].items()],
            "arcane_slots": [list(p) for p in deck.arcane_slots], "slots": [list(p) for p in slots]}


if __name__ == "__main__":
    with multiprocessing.Pool(len(JOBS)) as pool:
        out = pool.map(run, JOBS)
    with open("stage_layouts.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for o in out:
        print(o["deck"], o["config"], round(o["ndm"], 1), o["cores"], o["implicits"], "core slots", o["core_slots"])
