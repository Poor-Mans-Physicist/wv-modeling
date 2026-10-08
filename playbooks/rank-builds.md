# Playbook: rank combat builds, or test a balance change

Questions like "what's the best Smite build at endgame?", "how far behind melee are ability builds?",
"what happens to the rankings if Execution is capped?", "which bugs matter most?".

## Run

```
cd models/builds
python extract/extract.py && python extract/extract_hyper.py     # once per data version
python run.py                                                    # full search, ~2 min on 24 threads
python tools/summarize.py --stage end                            # ranking
python tools/summarize.py --show ability:Smite_Archon end              # one build in full
```

Use `--families`, `--stages`, `--iters`, `--restarts` to narrow a run while exploring; use the defaults
(320k × 8) for anything you report.

## Testing a balance change

1. Find where the number lives: config values come from the pack/addon (`cache/`), mechanics and
   assumptions from `model/` (`model/stages.py` holds every knob).
2. For a config change, edit a copy of the config and point `WV_SNAPSHOT` at it, or change the knob; for a
   mechanic, change the model (see `add-a-mechanic.md`).
3. Run before and after with the same settings and compare per family and stage.

## How to report

- Scores are hyper cycles: damage cycle = the last cycle the build kills the boss within 60 s;
  survival cycle = the last it survives 5 hits 1 s apart; score = the lower. Differences under ~0.1
  cycles are search noise.
- Say which mode: `bugged` (0.34.1 as it plays) or `intended` (bugs fixed). They can rank differently.
- Many inputs are author rulings (`MECHANICS_0.34.1.md` §8–§11): roll quality, deck layouts, uptime
  assumptions. Name the ones a conclusion depends on.
- The model is release 0.34.1, level 100, hyperboss only. It says nothing about clearing speed or
  non-boss content.
