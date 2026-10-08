"""Print the build optimizer's results (out/results.json) as plain text.

    python tools/summarize.py                         # best build per (stage, family), intended mode
    python tools/summarize.py --mode bugged --stage max
    python tools/summarize.py --show melee:sword max  # one build in full: items, deck, abilities, talents, notes
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGES = ["early", "mid", "end", "max"]


def load(path):
    if not os.path.exists(path):
        sys.exit(f"[summarize][ERROR] {path} not found; run `python run.py` first")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def table(builds, mode, stage):
    rows = [b for b in builds if b["mode"] == mode and (stage is None or b["stage"] == stage)]
    if not rows:
        print(f"[summarize][WARN] no builds for mode={mode} stage={stage}")
        return
    rows.sort(key=lambda b: (STAGES.index(b["stage"]) if b["stage"] in STAGES else 99, -b["score"]))
    print(f"{'stage':<6} {'family':<34} {'score':>7} {'dmg cyc':>8} {'surv cyc':>8}  scaling")
    for b in rows:
        print(f"{b['stage']:<6} {b['family_label'][:34]:<34} {b['score']:>7.2f} {b['cycle_damage']:>8.2f} "
              f"{b['cycle_survival']:>8.2f}  {b.get('scaling') or ''}")


def show(builds, family, stage, mode):
    hit = [b for b in builds if b["family"] == family and b["stage"] == stage and b["mode"] == mode]
    if not hit:
        sys.exit(f"[summarize][ERROR] no build {family} / {stage} / {mode}; families: "
                 f"{sorted({b['family'] for b in builds})}")
    b = hit[0]
    print(f"{b['family_label']} | {stage} | {mode} | score {b['score']:.2f} "
          f"(damage cycle {b['cycle_damage']:.2f}, survival cycle {b['cycle_survival']:.2f})")
    for k in ("items", "trinkets", "charm", "deck", "abilities", "talents", "greed", "prestige", "skill_points"):
        print(f"\n## {k}")
        print(json.dumps(b.get(k), indent=1, ensure_ascii=False)[:4000])
    for k in ("notes", "flags", "bug_effects", "etching_effects"):
        if b.get(k):
            print(f"\n## {k}")
            for line in b[k]:
                print(" -", line if isinstance(line, str) else json.dumps(line, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(ROOT, "out", "results.json"))
    ap.add_argument("--mode", default="intended", choices=["intended", "bugged"])
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--show", nargs=2, metavar=("FAMILY", "STAGE"))
    a = ap.parse_args()
    d = load(a.results)
    if a.show:
        show(d["builds"], a.show[0], a.show[1], a.mode)
    else:
        table(d["builds"], a.mode, a.stage)


if __name__ == "__main__":
    main()
