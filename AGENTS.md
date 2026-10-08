# AGENTS.md

You are working in **wv-modeling**: simulators and optimizers for the Wold's Vaults modpack (Minecraft
1.18.2, Vault Hunters). Users ask game-design and play questions; you answer them by running these
models and reporting numbers with their uncertainty. Read this file fully once per session.

## 1. First run

1. `python setup/doctor.py`. It reports missing tools, game data and builds, with the fix for each.
2. If `cache/` is empty: `python setup/fetch_sources.py`. This clones public repos (pack + addon) at the
   pinned release. Never download game jars or mod files from the web; if a task needs files from the
   user's game install (roomlab textures), ask the user for the path or use what `doctor.py` finds.
3. `python setup/doctor.py --build` builds the Rust crates (a few minutes the first time).
4. `python setup/check.py` must pass before you trust any model. If a gate fails, stop and report it.

Platform notes: commands are written for any OS. On Windows, Python multiprocessing uses spawn (keep
pool code under `if __name__ == "__main__":`), and `uv` inside OneDrive needs `UV_LINK_MODE=copy`.

## 2. Route the request

| The user wants to... | Read first |
|---|---|
| know chests per room or per minute for a crystal, or pick a crystal | `playbooks/pick-crystal-modifiers.md` |
| price movement speed, router settings, miner choice | `playbooks/price-a-route-change.md` |
| optimize a deck layout | `playbooks/optimize-a-deck.md` |
| rank combat builds or test a balance change | `playbooks/rank-builds.md` |
| design, edit or preview a vault room | `playbooks/build-a-room.md` |
| change a model, add a mechanic, or move to a new game version | `playbooks/add-a-mechanic.md` |
| understand a mechanic | the model's spec (below) or `library/` |

Specs (source of truth for each model): `libs/vaultsim/SPEC.md`, `models/routerunner/MODEL.md`,
`models/builds/MECHANICS_0.34.1.md`, `models/decks/MODELING_CHOICES.md`, `roomlab/ROOM_CONTRACT.md`.
`models/decks/CLAUDE.md` has extra rules for that folder (always `uv run` there).

If the question doesn't fit any model, say so. Don't stretch a model past what it represents.

## 3. Reporting results

- Quote the model's validated accuracy with every number (it's in the playbook and model doc). Call two
  options different only when the difference is outside that error or the sampling interval; otherwise
  say they're within noise.
- Separate three kinds of input in your answer: values read from game configs, mechanics confirmed from
  game code, and assumptions or author rulings (roll quality, uptime, the benchmark player). Name the
  assumptions a conclusion depends on.
- Per-minute rates are for the benchmark player (the time model was fitted on one player's runs).
  Present them as comparisons unless the user knows their own factor.
- Say when a query is extrapolation (Bonus-0 crystals, game versions other than 0.34.1, mechanics the
  model doesn't price).
- If a run printed `[FALLBACK]` or `[WARN]` lines, mention them and what they affected.
- If you can't establish something, say so plainly. A plausible-looking number without support is worse
  than no number.

## 4. Changing code

- Every model has a gate in `setup/check.py`. Run the relevant gate after a change; all of them before
  you finish. A gate whose reference numbers move needs an explanation, not a silent update.
- The builds model has two implementations that must agree exactly: change Python `models/builds/model/`
  first, mirror it in `libs/buildkernel/`, then `python models/builds/tools/kernel_parity.py` must report
  0 mismatches.
- Deck scoring changes must update `models/decks/MODELING_CHOICES.md` in the same change.
- Vault simulator behaviour changes must update `libs/vaultsim/SPEC.md` and be checked against real
  vault data, not intuition.
- New mechanics need a primary source: a config file in `cache/`, game code (the addon is open source;
  for the_vault decompile the user's own jar), or an in-game test. Mark anything else INFERRED.
- When code falls back to a default, print a `[component][FALLBACK]` line so the user can see it.
- Write outputs under `out/` (gitignored). Never write into `cache/`; it is a pinned copy of public data.

## 5. Working with the user on rooms

The human is the art director. Generate, lint, build the preview, give them http://localhost:8430 and ask
what to change. Don't judge looks by taking screenshots in a loop. Never hand over a room that fails
`roomlab/lint.py`. Say clearly that a room has not been loaded in game until the user has done it.

## 6. Where things are

```
setup/       fetch_sources.py (game data), doctor.py (environment), check.py (gates)
libs/        Rust: vaultsim (rooms + chests), lane (route planner), buildkernel (combat search), ndm (decks)
models/      routerunner/ (weights, sim, benchmarks), builds/ (combat model), decks/ (deck optimizer)
roomlab/     room DSL + linter + preview; examples/extraction1 is a finished room with its generator
playbooks/   task recipes
library/     mechanics references
cache/       fetched pack/addon data (gitignored)    out/  run outputs (gitignored)
```

Environment variables: `WV_GEN_ROOT` (vault simulator room data, default `cache/pack/config/the_vault/gen/1.0`),
`WV_SNAPSHOT` (builds model data root holding `pack/` and `addon/`, default `cache/`), `WV_INSTANCE` and
`MC_CLIENT_JAR` (roomlab textures).
