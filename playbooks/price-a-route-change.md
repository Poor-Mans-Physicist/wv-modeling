# Playbook: what is a routing or movement change worth?

Questions like "how much does +20 % movement speed help chest farming?", "what if the router bailed lanes
earlier?", "is Vein Miner better than Chain Miner at this density?", "what does a bigger break reach do?".

## Method

Run the same cells twice, changing one thing, with the same `--seed` and `--per`. The rooms are shared
across runs (common random numbers), so the ratio between runs is much tighter than either run's
absolute margin.

```
python models/routerunner/sim/run_cells.py --cells 30,30 45,45 51,75 60,60 --speed 0.400
python models/routerunner/sim/run_cells.py --cells 30,30 45,45 51,75 60,60 --speed 0.480
```

Each run writes `out/routerunner/cells_<time>.jsonl`. Compare `cpm` cell by cell and report the ratio
range across cells, not one cell.

| Change | Flag or place |
|---|---|
| movement speed | `--speed` (attribute value; 0.400 is the benchmark) |
| lane bail threshold | `--bail` (fraction of the current rate; 0.6 in game) |
| miner | `--miners chain` / `vein`; ranges in `MINER` in `run_cells.py` |
| break reach | `REACH` in `run_cells.py` |
| time model coefficients | a copy of `weights/timemodel_shape.json`, passed by editing `SHAPE` |

## How to report

- Ratios ("+12–15 % across the four cells"), with the cells listed.
- Movement speed scales route time by `(0.400/speed)^0.6`, not linearly; room entry/exit and clicks do
  not scale, so the gain is smaller than the speed increase.
- If the change is something the planner does not model (a new movement ability, hammer-size mining),
  say so; the time model only prices moves it was fitted on. Planning such a change needs new planner
  code and new fit data, not a flag.
- Room-picker effects (choosing which room to run next) are a flat factor here
  (`weights/picker_factor.json`), not simulated per run.
