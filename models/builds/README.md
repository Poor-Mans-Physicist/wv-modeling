# Build optimizer (combat model)

Finds the strongest build per skill family and progression stage for Wold's Vaults release 0.34.1, at
level 100, measured against the hyperboss: how many hyper "cycles" a build can kill the boss within 60 s,
and how many it survives 5 boss hits 1 s apart. Builds cover gear (affixes, implicits, seals, etchings,
uniques), trinkets, charm, deck, abilities and talents on the shared skill-point budget, prestige and
the greed tree. Numbers are read from the pack and addon configs; mechanics are hand-coded from the game
code.

`MECHANICS_0.34.1.md` is the reference for every mechanic, formula, author ruling and known game bug
the model encodes (code comments cite it as `MECHANICS §n`). Read it before changing the model.

## Pipeline

1. **Catalog.** `python extract/extract.py` reads `cache/pack` and `cache/addon` (`python setup/fetch_sources.py`)
   and writes `data/catalog_0.34.1.json`. It applies vhapi's merge rules (gear groups appended, etchings,
   trinkets and decks put by key) and keeps only cards that appear in an obtainable booster pool.
   `python extract/extract_hyper.py` writes `data/hyper_pools_0.34.1.json`: the hyper chaos pools,
   reduced to boss health/damage modifiers and Frenzy stacks. Both files are generated, not committed.
2. **Deck layouts** (only when layouts or deck rules change). `data/deck_layouts.json` is committed. To
   regenerate it: copy `models/decks` to a scratch folder, put `extract/deckfast_structural_layouts.json`
   there as `decks/structural_layouts.json` and `extract/deckfast_run_layouts.py` as `run_layouts.py`,
   run `uv run python run_layouts.py` inside the copy, copy its `stage_layouts.json` to
   `data/stage_layouts_raw.json`, then run `python extract/convert_deck_layouts.py`. This gives fixed EVO
   layouts per stage. `model/deck.py` re-scores them per slot (it matches the deck optimizer's NDM
   exactly), and the optimizer only chooses the stat card in each slot.
3. **Kernel.** `cargo build --release` in `libs/buildkernel` (once, and after kernel changes).
4. **Search.** `python run.py [--iters 320000 --restarts 8 --schedule target|geometric --engine rust|python
   --threads T --procs P --families a,b --stages early,mid,end,max --no-unique-pass]`.
   All anneals (every family × stage × bug mode × restart, once with uniques allowed and once banned)
   go to one kernel process that spreads them over all cores. Each build is polished (coordinate descent
   over affixes, implicits, etchings, deck cards, trinkets, charm), re-scored in Python, and written to
   `out/results.json` with per-build bug and etching attributions. A full run (336 builds, 5376 anneals)
   takes about 2 minutes on 24 threads.
5. **Read the results.** `python tools/summarize.py [--mode intended|bugged] [--stage max]` prints the
   ranking; `python tools/summarize.py --show melee:sword max` prints one build in full.

## Bug modes

Every build is searched twice: `bugged` (0.34.1 as it plays, with known game bugs active) and `intended`
(bugs fixed). The bug registry is `MECHANICS §6.8`; `model/bugs.py` holds the plain-language summaries.

## Layout

- `model/stages.py`: stages, knobs, hyperboss formulas. Every assumption lives here.
- `model/catalog.py`, `model/context.py`: legal options per stage (affix pools at L100 with stage roll
  quality, etchings by greed tier, trinkets, charms, deck slots and cards, talents, gates, greed graph,
  prestige).
- `model/deck.py`: deck slot scoring and the obtainable card registry.
- `model/build.py`, `model/stats.py`: build representation, stat assembly, derived-stat formulas.
- `model/damage.py`: the hurt chain, with each 0.34.1 bug behind `bug(build, id)`.
- `model/families.py`, `model/abilities.py`: melee (Better Combat cadence) and per-ability DPS, buffs.
- `model/hyper.py`: hyper vault environment by cycle (Monte Carlo over chaos draws, the 350 chaos budget,
  stack caps), giving the boss health factor, damage modifiers and the Frenzy multiplier. Cached in `out/`.
- `model/evaluate.py`: damage cycle, healing per second, survival cycle, score.
- `model/search.py`: the reference Python search (`--engine python`).
- `model/kernel.py`: flattens a Context into index tables for the kernel and maps builds back.
- `libs/buildkernel`: Rust port of `evaluate()` and the search, ~500k evaluations/s per core. Capacities
  are in `src/build.rs`; loading fails with "raise MAX_..." when a table outgrows them.

## Changing the model

Python is the reference; the kernel must agree with it exactly.

1. Change the Python model (and `MECHANICS_0.34.1.md` if a mechanic or ruling changes).
2. Mirror the change in `libs/buildkernel`.
3. `python tools/kernel_parity.py` must report 0 mismatches; `python tools/kernel_speed.py --verify`
   re-checks delta scoring.
4. `python tools/bench_search.py` if you touched the search (median gap to the best known build was
   0.04 cycles at 320k × 8).

Fallbacks print `[model][FALLBACK]` once and are counted in `out/results.json`.

## What the score means, and what it does not

- It is a comparison tool for balance work: builds are ranked under one set of assumptions (roll
  quality, card tier, deck layout, uptime rulings in `model/stages.py`). Change an assumption and
  rankings can move.
- Several inputs are author rulings or in-game estimates, not code facts (for example Mitosis pack damage
  10–15× per cast, Rampage uptime). `MECHANICS §8–§11` lists them.
- Not modeled yet: hyper resistance shred before the cap, mob crit, Mana Leak, Inert's CDR loss.
