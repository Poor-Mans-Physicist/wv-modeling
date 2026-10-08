# Playbook: which crystal modifiers give the most chests?

Questions like "how many living chests does 45 Bonus / 45 Cascade give?", "is 30/60 better than 45/45?",
"Chain Miner or Vein Miner for this crystal?", "what is the best crystal for my budget?".

## Ask first (if the user didn't say)

- Chest type (living, gilded, ornate) and vault level (strongbox rolls depend on it; panels use 475).
- Do they care about chests per **room** (what the crystal makes) or per **minute** (what they can clear)?
  Dense crystals make more chests than a player can clear, so the two rankings differ.
- For per-minute: which miner, and their movement speed if they know it.

## Chests per room

```
python libs/vaultsim/scripts/chest_counts.py --cells 30,30 45,45 30,60 --chest living --per 64
```

Report the mean with its 95 % interval. Two crystals whose intervals overlap are not distinguishable at
this sample size; raise `--per` before calling a winner. `clump` is how connected the chests are
(higher favours Vein Miner).

## Chests per minute

1. Check the published panel first: `models/routerunner/benchmarks/panel-v2.2/cells_v22.json` covers
   Bonus 0–90 × Cascade 0–90 in steps of 3, plus Bonus ≤ 60 × Cascade 93–240, for both miners, at speed 0.400.
   If the crystal is a panel cell, quote it with the tile margin from `tile_margins.json`.
2. Otherwise run the model:
   ```
   python models/routerunner/sim/run_cells.py --cells 51,74 --picker
   ```
   `--picker` includes the in-game room picker's gain; leave it on to match what a Routerunner user gets.

## How to report

- Per-minute numbers are the **benchmark player's** rates (the time model was fitted on the author's
  play). Present them as comparisons ("vein clears about 1.7× chain here"), or say a player's own rate is
  roughly a constant fraction of these.
- Expected-rate margins are about ±11 % (chain) and ±15 % (vein). Don't rank two crystals that are
  closer than that without saying it's within noise.
- Bonus-0 cells are extrapolation.
- Data pin: release 0.34.1. Say so if the user is on a different version.

## Pitfalls

- Don't use `wv-modifier-panel` for absolute counts: it predates the 3.21.6 event order and under-counts
  stacked crystals by 18–35 %.
- The sim picks themes uniformly from beach / cave / desert / nether / void. Real theme weights are not
  modeled; pass `--themes` to restrict.
- Enigma chest replacement is not modeled.
