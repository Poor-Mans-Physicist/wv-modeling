"""Candidate deck layouts for tools/deck_study.py, optimized by the DeckFAST tagged kernel under rework greed values.

Run inside the DeckFAST repo:  uv run --no-sync python <this file> <out.json> <greed addend> <label>
For every EVO deck whose implicit the ledger can model, every candidate core set is annealed (Rust jobs on all cores)
and the top TOP_PER_DECK core sets by NDM are kept. Global-tag implicits are exported with their rule so the ledger
can register them.
"""
import json
import sys
import zlib

sys.path.insert(0, ".")

from src import simulate as S  # noqa: E402
from src.config import DECKS  # noqa: E402
from src.implicits import implicits_for_deck  # noqa: E402
from src.simulate import candidate_cores, run_sa_jobs, sa_optimize_tagged  # noqa: E402
from src.types import CardClass  # noqa: E402

TOP_PER_DECK = 3
LEDGER_IMPLICIT = {"freq": None, "row_pos": "cake", "mirror": "runic", "global": None}
SKIP_KINDS = {"adjacency", "chain", "empty_slots", "color_mismatch", "unique_groups"}
PHASE2 = (4, 180000, "target", [0.1, 0.001, 0.005, 0.01])


def main():
    out_path, addend, label = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    S.MULT_DIR_GREED_VERT = addend
    S.MULT_DIR_GREED_HORIZ = addend
    jobs, keys = [], []
    for d in DECKS:
        imps = implicits_for_deck(d.key)
        if any(t[0] in SKIP_KINDS for t in imps):
            continue
        for cores in candidate_cores(CardClass.EVO, d):
            cs = sorted(c.value for c in cores)
            jobs.append(sa_optimize_tagged(d, CardClass.EVO, cores, n_iter=60000, implicits=imps, restarts=12,
                                           phase2=PHASE2, seed=zlib.crc32(f"{d.key}|{cs}".encode()), defer=True))
            keys.append((d, cs, imps))
    print(f"{len(jobs)} deck x core-set jobs", flush=True)
    outs = run_sa_jobs(jobs)
    per_deck = {}
    for (d, cs, imps), (asgn, score) in zip(keys, outs):
        per_deck.setdefault(d.key, []).append((score, cs, asgn, d, imps))
    out = []
    for key, lst in sorted(per_deck.items()):
        lst.sort(key=lambda x: -x[0])
        for score, cs, asgn, d, imps in lst[:TOP_PER_DECK]:
            names, rules = [], {}
            for (kind, value, groups, colors, extra) in imps:
                if kind == "global":
                    names.append(key)
                    rules[key] = {"kind": "global", "value": value, "groups": list(groups)}
                elif kind == "freq":
                    names.append(key)
                elif kind in LEDGER_IMPLICIT:
                    names.append(LEDGER_IMPLICIT[kind])
            slots = list(d.slots)
            out.append({"deck": d.name, "key": key, "config": label, "ndm": score, "cores": cs, "implicits": names,
                        "implicit_rules": rules, "core_slots": d.core_slots,
                        "assignment": [[slots[i][0], slots[i][1], c[0]] for i, c in enumerate(asgn)],
                        "slots": [list(p) for p in slots]})
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for o in out:
        print(f"{o['deck']:24s} {o['ndm']:9.1f} {'+'.join(o['cores']):40s} {o['implicits']}")


if __name__ == "__main__":
    main()
