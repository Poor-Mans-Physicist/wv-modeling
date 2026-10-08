# Routerunner room-running model

Predicts how many chests per minute a player clears in vault rooms with Chain Miner or Vein Miner, by
planning the route the [Routerunner](https://github.com/Poor-Mans-Physicist/Routerunner) mod would
show and pricing it with a time model fitted on real runs. Use it to compare crystals (Bonus × Cascade),
miners, movement speed, or router settings without playing the vaults.

## Pieces

| Piece | Where | Role |
|---|---|---|
| Planner | `libs/lane` (`lane_cli`) | plans lanes and triggers through one room, prices every move |
| Time model | `weights/timemodel_shape.json` | per-miner move/click/reach costs, room entry/exit, coverage, room switch time |
| Older model | `weights/legmodel_ridge.json` | 12-feature ridge leg model (replaced by the shape model; still accepted by `lane_cli`) |
| Room picker gain | `weights/picker_factor.json` | the adaptive room picker's simulated gain over a straight run (chain ×1.053, vein ×1.070) |
| Picker params | `weights/roompicker.json` | the in-game picker's tuning; the picker itself is Java-only and not in this repo |
| Rooms | `libs/vaultsim` (`wv_vault_grid cells`) | simulated rooms per crystal |
| Runner | `sim/run_cells.py` | rooms → planner → fixed-point chest rate per cell and miner |
| Results | `benchmarks/` | panel v2.2 (every reachable crystal) and validation against real laps |

## The shape time model

A route is a sequence of moves (walk, turn, mine through, click a chest group). Each move costs seconds
from a small set of fitted coefficients per miner: `click`, `side`, `wide`, `behind`, `above`, `below`,
`reach`, `burst`, `size`, scaled by `moveScale`, plus fixed `roomEntryS` / `roomExitS`. Movement time
scales with `(vRef / speed)^speedElasticity`, elasticity 0.6, so doubling movement speed does not halve
route time.

The cell rate is `coverage × Σ planned chests / Σ (planned seconds + switchS)`: coverage (0.900 chain,
0.924 vein) is the share of planned chests a player really collects, `switchS` the mean time between
rooms (1.03 s chain, 1.18 s vein). The router's bail floor (stop a lane when it yields less than
0.6 × the current rate) and pruning of small chest groups depend on the rate itself, so `run_cells.py`
iterates to a fixed point, as the game converges.

## Accuracy, and what it is accurate for

- Fit: room-level R² 0.60 (chain) and 0.71 (vein).
- Validation against the author's real laps on Routerunner 1.2.0: real ÷ expected median 1.01, 8 of 8
  within ±10 %, five of the crystals outside the fit data (`benchmarks/validation/`).
- A tile's 95 % margin on the expected rate is about ±11 % (chain) and ±15 % (vein); sampling adds
  ±5–6 % of that at 64 rooms per cell.
- **It is one player's benchmark.** The model was fitted on the author's play. Other players differ by a
  roughly constant factor (the mod calibrates it per player; one tester ran 0.53–0.71 of the benchmark).
  Compare crystals and settings against each other; quote absolute chests/min as "benchmark player".
- Tiles with Bonus 0 are extrapolation: no fit data had them.
- Freehand play (no router) measured 0.83 of the router's expected rate on the same rooms.
- `run_cells.py` reproduces panel v2.2 within 0.3 % on the checked cells. In-game 1.2.0 also prunes
  sparse vein taps (a minimum seconds-per-hit floor), which `run_cells.py` does not; it moved vein rates
  by at most 2 % and did not change which miner wins anywhere.

## Knobs worth exploring

- `--speed`: movement speed attribute (0.400 is the panel setting).
- `--bail`: `laneBailRateFrac`; 0.8 instead of 0.6 measured +2.7 % chain and +4.9 % vein offline (not yet
  confirmed in game).
- `--per`: rooms per cell; 64 gives the margins above, fewer is faster and noisier.
- The miner: `MINER` in `run_cells.py` is (chain range, chain limit): Chain Miner 6/32, Vein Miner 1/896.
