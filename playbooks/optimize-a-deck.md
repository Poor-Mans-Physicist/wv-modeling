# Playbook: optimize a deck layout

Questions like "best layout for the Wold Deck with 8 regular cards", "how much does this core add?",
"shiny or evo for this deck?".

The deck optimizer maximizes NDM (net deck multiplier): the total multiplier one card type would receive
if every card-bearing slot held it. `models/decks/MODELING_CHOICES.md` is the scoring spec; read the
sections for the mechanic in question before answering.

## Quick answers

The hosted web app (https://poor-mans-physicist.github.io/WoldsVaultsDeckOptimizer/) runs the same kernel
in the browser and is the fastest way for a human to explore one deck. Point the user there for
interactive work.

## Batch runs (every deck, both classes, several greed constraints)

```
cd models/decks
uv sync
uv run optimize                # Wold's Vaults rules; writes Panel_*.xlsx
uv run optimize --mode vanilla # stock Vault Hunters rules
```

Settings (iterations, restarts, constraint configs) live in `config.yaml`, decks in `decks/`. On a synced
folder (OneDrive) set `UV_LINK_MODE=copy`.

## How to report

- Compare NDM within one card class (shiny vs evo differ in base stats, which NDM excludes).
- Simulated annealing is stochastic: re-run or raise restarts before calling a difference under ~1 %
  real. The parity script's search check (Part B) shows the two search kernels can disagree by a few
  percent on shiny layouts.
- Card and deck data are dumps of the game configs (`modifiers.json`, `vh_modifiers.json`,
  `decks/*.json`). If the user is on a newer pack, those may be stale; say so.

## Changing scoring

Update `MODELING_CHOICES.md` in the same change, then `uv run python scripts/parity_2_0.py` (Part A must
pass exactly).
