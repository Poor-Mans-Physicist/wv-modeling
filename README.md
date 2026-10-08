# wv-modeling

Simulators, optimizers and room-building tools for modeling [Wold's Vaults](https://github.com/iwolfking/Wolds-Vaults),
a Vault Hunters modpack. They answer design and play questions with numbers instead of playtests: how
many chests a crystal makes, how fast a player clears them, which deck layout or combat build is
strongest, and what a balance change would do. The tools were built to develop the pack, and this repo
is set up so you can hand it to a coding agent (Claude Code, Codex, Cursor, ...) and ask it questions.

Not affiliated with the Vault Hunters team. Game data is fetched from the public Wold's Vaults repos at
setup; see [Data and licensing](#data-and-licensing).

## What it can answer

| Question | Tool | How good is it | Start here |
|---|---|---|---|
| How many chests does this crystal (Bonus × Cascade) make per room? | vault simulator | real ÷ simulated 1.03–1.15 on living vaults | [playbook](playbooks/pick-crystal-modifiers.md) |
| How many chests per minute will a player clear, Chain vs Vein Miner? | room-running model | real ÷ predicted median 1.01 on validation laps; ±11 % (chain) / ±15 % (vein) per crystal; benchmark player | [playbook](playbooks/pick-crystal-modifiers.md) |
| What is +X % movement speed, a router setting or a different miner worth? | room-running model | ratios between runs are tighter than absolute rates | [playbook](playbooks/price-a-route-change.md) |
| Best card layout for a deck, and what a core is worth | deck optimizer | exact scoring (parity-checked); annealing search | [playbook](playbooks/optimize-a-deck.md) |
| Strongest combat build per skill and progression stage; effect of a balance change | build optimizer | exact Python/Rust parity; rankings rest on stated assumptions | [playbook](playbooks/rank-builds.md) |
| Design and preview a new vault room | roomlab | rooms pass the same contract as every shipped room | [playbook](playbooks/build-a-room.md) |

Every model documents what it was validated against and where it stops being reliable: read the
model's own doc before trusting a number.

## Quick start

You need git, Python 3.9+, a Rust toolchain (https://rustup.rs) and, for the deck optimizer,
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Poor-Mans-Physicist/wv-modeling.git
cd wv-modeling
pip install numpy scipy pillow          # or: uv sync, then prefix python commands with `uv run`
python setup/fetch_sources.py           # game data from the public pack + addon repos -> cache/
python setup/doctor.py --build          # checks tools and builds the Rust crates
python setup/check.py                   # verifies every model against its reference numbers
```

Then, for example:

```bash
python libs/vaultsim/scripts/chest_counts.py --cells 30,30 45,45 --chest living
python models/routerunner/sim/run_cells.py --cells 45,45 --picker
cd models/builds && python extract/extract.py && python extract/extract_hyper.py && python run.py && python tools/summarize.py --stage max
```

## Using it with an agent

Open the repo in your agent and ask your question in plain words. `AGENTS.md` tells the agent how to
set up, which playbook fits the question, which checks must pass after a change, and how to report
uncertainty. (`CLAUDE.md` points Claude Code at the same file.) Some prompts that work well:

- "Set this repo up and run the checks."
- "For living chests, compare 45/45 and 30/60 for Vein Miner. Which is better per minute, and is the
  difference outside the model's error?"
- "What does +25 % movement speed do to chests per minute at 51 Bonus / 75 Cascade, for both miners?"
- "Build me a circular boss arena room with four entrances and a raised centre, and let me preview it."
- "Rank the endgame ability builds and tell me which assumptions the top three depend on."

## Layout

```
setup/          fetch_sources.py, doctor.py, check.py
libs/           Rust kernels
  vaultsim/       vault room assembly + chest placement simulator (SPEC.md is its source of truth)
  lane/           Routerunner room planner + time-model pricing (lane_cli)
  buildkernel/    combat build evaluator + annealing search
  ndm/            deck NDM scoring + annealing search (Python extension)
models/         the model built on each kernel, with its data, docs and validation
  routerunner/    MODEL.md, weights/, sim/run_cells.py, benchmarks/ (panel v2.2, validation)
  builds/         combat model (Python reference), MECHANICS_0.34.1.md, extractors, run.py
  decks/          deck optimizer CLI (snapshot of WoldsVaultsDeckOptimizer), MODELING_CHOICES.md
roomlab/        room DSL, contract linter, palette interpreter, 3D preview; examples/extraction1
playbooks/      task recipes, one per kind of question
library/        mechanics references (chest loot generation, crafting materials, attribute caps)
cache/, out/    fetched game data and run outputs (gitignored)
```

## Versions and updates

All models are pinned to Wold's Vaults **release 0.34.1** (pack `c5963442`, addon `0f0a9254`, the_vault
3.21.6). `python setup/fetch_sources.py --pack-ref <ref> --addon-ref <ref>` points them at another version;
the accuracy figures above were measured at the pin.

This repo is a periodic snapshot of the tools used to develop the pack, refreshed after major fixes rather
than on every change. The Routerunner mod (https://github.com/Poor-Mans-Physicist/Routerunner) and the deck
optimizer (https://github.com/Poor-Mans-Physicist/WoldsVaultsDeckOptimizer, with a hosted web app) have
their own repos.

## Data and licensing

- Code and docs: GPL-3.0 (see `LICENSE`).
- Vault Hunters (`the_vault`) is closed source and all rights reserved. The simulators reimplement its
  behaviour from observation and decompilation for interoperability; no game code, jars, textures or room
  files are included. Pack and addon data are fetched at setup from the public Wold's Vaults repositories.
  Mechanics docs cite game classes and line numbers so you can check a claim against your own decompile.
- Exceptions shipped in the repo: the deck optimizer's card and deck tables (`models/decks/*.json`,
  `models/decks/decks/*.json`, as published in its own repo) and the extraction room and palettes, which
  were made with roomlab.
- `roomlab/build.py` reads block textures from your own game install at run time and never writes them
  into the repo.
