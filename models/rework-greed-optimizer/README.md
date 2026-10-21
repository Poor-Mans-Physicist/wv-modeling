# wv-combat-model

Build optimizer for Wold's Vaults combat balance (level 100, hyperboss benchmark). Two ledgers: release 0.34.1
(`WV_LEDGER=0.34.1`, the default) and the greed rework (`WV_LEDGER=rework`).
Design notes, the bug registry and the engineering rules (both engines, gates, provenance, checklists) are kept outside
this repository; `MECHANICS_0.34.1.md` in `models/builds` documents the 0.34.1 mechanics the two builds tools share.

## Pipeline

1. Catalogs (only when a pack or the addon changes):
   - `python extract/extract.py` reads the 0.34.1 snapshots (`cache/{pack,addon}` from `python setup/fetch_sources.py`, or `WV_SNAPSHOT`)
     and writes `data/catalog_0.34.1.json`. It applies vhapi's merge rules (gear groups appended, etchings,
     trinkets and decks put by key), keeps only cards that appear in an obtainable booster pool, and writes the card
     registry (`catalog["card_registry"]`) that `model/deck.py` reads.
   - `python extract/extract_rework.py` writes `data/catalog_rework.json` the same way from the pack
     `WV_PACK` and the addon `WV_ADDON_ROOT` (required: the greed-rework sources are not published; the 0.34.1 talent table, user
     ruling), plus the god trees, god levels and greed ranks from greed-test-dist. `catalog["meta"]` records the
     sources. greed-test-dist differs from the ledger's pack in six talents and has no Ultimate Shield
     (the ledger change notes, kept outside this repository).
   - `python extract/extract_hyper.py` writes `data/hyper_pools_0.34.1.json`: the three hyper chaos pools with grouped
     modifiers flattened, reduced to boss health/damage modifiers and Frenzy stacks.
   - Report assets (only for `report/extract_assets.py` and `report/extract_god_assets.py`): `WV_JAR` (the_vault jar),
     `WV_THE_VAULT_SRC` (decompiled ModGearAttributes.java), `WV_PACK` and `WV_ADDON_ROOT` (rework sources).
   - `python extract/spec_table.py` writes `data/spec_table.json` (Layout, spec table). Rerun it after every catalog
     or overlay change; the model refuses a stale table.
2. Simulated tables (only when their rules change; each has `--check`, which recomputes and compares with the file):
   - `python tools/echo_table.py` (pulse table `v`) and `python tools/echo_table.py --reverb` (Reverberating Echo
     table `v_reverb`) write `data/echo_table.json`.
   - `python tools/fang_geometry.py` writes `data/fang_hits.json` (fang hits per cast by effective radius over the
     boss roster, at the knob `fang_boss_distance`; rerun it when that knob changes).
3. Deck layouts (only when layouts or deck rules change):
   - copy the DeckFAST repo to a scratch folder;
   - put `extract/deckfast_structural_layouts.json` there as `decks/structural_layouts.json` and
     `extract/deckfast_run_layouts.py` as `run_layouts.py`;
   - run `uv run python run_layouts.py` inside the copy;
   - copy its `stage_layouts.json` to `data/stage_layouts_raw.json`, then run `python extract/convert_deck_layouts.py`.
   - This gives fixed EVO layouts per stage. `model/deck.py` re-scores them per slot (it matches DeckFAST's NDM exactly),
     and the optimizer only chooses the stat card in each slot.
   - Rework deck menus (replace the fixed layouts when `run.py --menus` is passed): after a ledger run,
     `WV_LEDGER=rework python tools/deck_weights.py` probes every stored build at all four stages; then in DeckFAST
     (branch `test/core-rework`) `uv run --no-sync python scripts/deck_menus.py <out/deck_weights.json> <stage>
     <menu_stage.json> --k 5 --regret` per stage (about 4 to 6 minutes each), and the four results go into
     `data/deck_menus_rework.json` as `{stage: result}`. The rules live in DeckFAST's `config.yaml` `rework:` section and
     are scored identically here by `model/deck_rework.py` and `kernel/src/deck.rs`: `python tools/parity_cases_deck.py`
     writes fixtures that `cargo test --release deck_fixture` (with `WV_DECK_FIXTURE`) and DeckFAST's
     `scripts/deck_fixture_check.py` must match to 1e-9.
4. `python report/extract_assets.py` pulls icons (the_vault 3.21.6 jar + addon resources), GUI tree layouts and the
   game's attribute names and colors into `data/assets.json`.
5. `cargo build --release` in `kernel/` (once, and after kernel changes), then
   `python run.py [--iters N --restarts N --p2-top N --p2-iters N --schedule target|geometric --engine rust|python --threads T --procs P --families a,b --stages ... --unique-pass --merge]`.
   All anneals (every family x stage x mode x restart) go to one kernel process that spreads them over all logical
   cores. Each build is polished (coordinate descent over affixes, implicits, etchings, deck cards, trinkets, charm),
   the secondaries are pruned until the main-share rule holds, and a process pool re-scores every final build in
   Python (`kernel-parity` fallback on a mismatch) and writes `out/results.json` (rework: `out/results_rework.json`)
   with per-build bug and etching attributions. The rework default is two-phase (16 x 90k restarts with locked
   strata, then the best 4 re-annealed for 1.1M steps) plus an Edge of Time pass. Run time: 0.34.1 about 2 min on 24
   threads; rework 91 min on 2026-10-19 (45 min before this pass; the kernel costs 1.3-1.7x more per step after it and a
   performance agent was compiling alongside; 300 builds, 0 kernel-parity fallbacks). `run.py` prints a per-stage table of the
   secondaries that would take the most damage uncapped (the `secondary-hungry` flag).
   `out/found_rework.json` stores every winner as a kernel build doc (export indices), so after any change to the
   export order (new talents, trinkets, specs, affix kinds) translate it by name before a rescore or warm start.
6. `python report/build_report.py [out.html]` renders the build ledger (default:
   `out/build_explorer.html`; with `WV_LEDGER=rework`, `out/build_explorer_rework.html`; `WV_PACK_CFG` and `WV_ADDON_CFG` are required for the rework).
   Rework views: Builds, Progression, Nodes, Gear, Etchings and Method. The Etchings view lists usage per stage and
   the whole etching catalogue with each description filled at the stage's roll (`build_report.fill_etching`, which
   `run.etching_text` also uses, so a build's etching text and the catalogue agree) and the audit tag from
   `catalog.ETCHING_NOTES` (modeled / not modeled), `ETCHING_NO_BOSS` (nothing against the boss) and
   `ETCHING_PLANNED`. The Damage tab draws the rotation flowchart from the rotation rows (sources, mana, debuffs,
   positioning). Every missing field logs one `[ledger][FALLBACK]` console warning and shows "-" or a footnote.
7. Rework only: `WV_LEDGER=rework python tools/unique_bench.py` re-anneals every standard winner (from
   `out/found_rework.json`, written by run.py) with each benched unique locked into its slot, plus unlocked controls
   at the same budget, and writes `out/unique_bench_rework.json` for the Gear view. Ability-specific uniques are
   benched at every stage (ancient at end and max), every unique at max (ancient). Rerun it after every optimizer
   run; the Gear view flags a bench that is older than the results.

### Watching a run

`tools/progress_server.py` serves a live progress page from the run log; it only reads (the log, the kernel's CPU
time, the Windows sleep log), so the run is unaffected. Run the pipeline unbuffered into one log:

```
WV_LEDGER=rework python -u run.py --iters 360000 --p2-iters 4400000 > out/run.log 2>&1
WV_LEDGER=rework python -u tools/unique_bench.py >> out/run.log 2>&1
WV_LEDGER=rework python -u report/build_report.py >> out/run.log 2>&1
python tools/progress_server.py out/run.log
```

and open http://127.0.0.1:8765 (in the Claude desktop app: the `progress` entry of `.claude/launch.json`, which
watches `out/run.log`). `--then ""` when only run.py writes the log; `--port` for a second watcher; `--repo` when the
run uses another checkout's kernel. The page shows a bar per phase (phase 1, phase 2, Edge of Time pass, Python
rescore, unique bench, build report), an overall bar and an ETA, and a banner for time lost to sleep (the Windows
wake events and, live, the unbiased interrupt clock) or to a kernel stall (CPU time against wall time). Phases are
weighted by the cost per step of past runs (`out/phase_costs.json`, seeded from `data/phase_costs.json`; the watcher
appends each run it saw finish), rescaled by the measured speed of the phases already done. run.py and
unique_bench.py print a `[progress] plan` line with each batch's job count and steps per job, and the kernel prints
`[wvk] n/m jobs` every 1% of a batch or 5 s. Keep the machine awake during long runs: the kernel clock and the run's
timings count time asleep.

8. Before publishing the greed-rework ledger, finish every item below. Do not publish until all of them are done,
   even if the pass only changed numbers.
   - **Gates are green** on the commit the results come from (Checks below): parity on both ledgers with the directed
     cases, `kernel_speed.py --verify`, the delta sweep and `tests/run_all.py`.
   - **Balance text in the UI matches the balance in the model.** For every node, card, skill or knob whose numbers
     the ledger changes (`REWORK_NODE_OVERRIDES`, `REWORK_BANNED_CARDS`, `REWORK_UNIQUE_OVERRIDES`, knobs such as
     `power_dump_sqrt_ref` or `ultimate_shield_scalar_mult`), update its in-game text in `REWORK_NODE_TEXT`
     (`model/stages.py`; the report shows it with a "proposed" tag) and the "Proposed balance" and "God node effects"
     paragraphs on the method page (`report/template.html`). Search the template for the old numbers; no stale value
     may remain anywhere in the UI. The knobs table reads `META.knobs` by itself, but sentences that name a knob do
     not: when a knob is added or removed, rewrite those sentences (the method page logs a `[ledger][FALLBACK]`
     warning for every knob it names that the results lack).
   - **Change notes are current.** Rewrite the ledger change notes (kept outside this repository) so they list
     only the difference between the ledger and instance (4) as it stands now. Overwrite it; never keep history.
     If a value was changed and then changed back, it leaves the list. Every new game change, bug modeled as fixed and
     ledger assumption of the pass gets a row (What / Where / Instance (4) / Ledger).
   - **Open questions are on the method page.** Model choices that stand in for an undecided ruling are listed in its
     "Open questions for you" paragraph; remove each one the user decides.
   - **Node commentary.** After an optimizer rerun, re-read `report/node_notes.json` against the Nodes view and update
     the text and `written_for` (the view warns while `written_for` differs from the results' timestamp).

## Checks

Run from the repository root; every model or kernel change must pass the first four before it is committed
(`ENGINEERING_PRACTICES.md` has the full gate list and the A/B procedure).

- `python tools/kernel_parity.py [--ledger both|rework|0.34.1] [--per-family 25] [--steps 120] [--no-cases] [--exe PATH]`:
  random legal builds (all families, stages, modes, random bug overrides) plus the directed cases, scored by both
  engines; must report 0 mismatches. `--ledger both` (default) runs each ledger in its own process with `WV_LEDGER`
  set. Directed cases (one build per feature, each checks that its feature is live so a case cannot pass idle):
  `tools/parity_cases.py` (the `@case` registry), `tools/parity_cases_talents.py` and
  `tools/parity_cases_etchings.py`, all run by `kernel_parity.py`; `tools/parity_cases_rotation.py` and
  `tools/parity_cases_survival.py [--ledger both] [--per 4]` run on their own.
- `python tools/kernel_speed.py --verify`: re-scores every delta-scored candidate from scratch (expect 0 mismatches,
  max error around 1e-12).
- `python tools/delta_sweep.py [--ledger both]`: one short anneal per family x stage x mode with `verify_delta`
  (about 1 minute, ~150M candidates); catches rounding edges that `kernel_speed.py --verify` misses.
- `python tests/run_all.py [-k substring]` (plain functions, so `python -m pytest tests` runs the same set where pytest
  is installed): the pinned worked example in both engines, the binomial quantile against enumeration, solver and
  echo-table monotonicity, the JSON contract and the rounding registry. `WV_LEDGER=rework python tests/worked_example.py
  --write` re-pins the worked example; only for an intended behaviour change, said in the commit.
- `python tools/kernel_speed.py --exe kernel/target/release/wvk_base.exe --exe kernel/target/release/wvk.exe --repeat 5`:
  speed A/B on the same seeds (median evals/s per executable and the ratio to the first).
- `WV_LEDGER=rework python tools/rescore_found.py [--exe PATH | --python] [--out FILE]`: re-scores the saved winners;
  two outputs must be byte-identical for a refactor, and for a feature change report how the scores move.
- `python tools/bench_search.py`: search-quality benchmark (gap to the best known build over independent replicates).

## Layout

- `model/stages.py`: stages, knobs, hyperboss formulas. Every assumption the report shows lives here; each knob has a
  one-line comment, and the method page names the ones its text depends on.
- `model/catalog.py`, `model/context.py`: legal options per stage (affix pools at L100 with stage roll quality,
  etchings by greed tier, trinkets, charms, deck slots and cards, talents, gates, greed graph, prestige). The
  etching audit (`ETCHING_NOTES`, `ETCHING_NO_BOSS`, `ETCHING_PLANNED`) and the trinket shapes the model keeps
  (`trinket_options`, every dropped shape logged) live in `catalog.py`.
- `model/deck.py`: DeckFAST tagged-kernel slot scoring over the catalog's card registry.
- `model/build.py`, `model/stats.py`: build representation, stat assembly (talents, stacking rule) and the derived-stat
  formulas (RECON §1).
- `model/damage.py`: the hurt chain (hit classes, the DoT class among them), with each 0.34.1 bug behind
  `bug(build, id)` (RECON §6.8); `model/bugs.py` holds the plain-language bug summaries shown on the report's stickers.
- `model/families.py`, `model/abilities.py`: melee (Better Combat cadence), the vaultarang family and the per-ability
  DPS models.
- `model/buffs.py` (kernel `buffs.rs`): buff abilities, one handler per buff spec in `BUFF_HANDLERS`, plus the mana,
  Rampage, Power Dump, Spectral Strike, mana-state and cloud helpers the families and the rotation share.
- Spec table: `model/spec_overlay.py` holds the per-spec facts the ability configs do not carry (label, scaling, tags,
  hit class, leech and lucky rules, Power Dump hold, Spectral Strike, cast debuffs, fang hits, reach, buff kind,
  secondary eligibility) and the melee combo tables. `python extract/spec_table.py` merges it with the catalogs'
  config keys into `data/spec_table.json`. `abilities.py` and `buffs.py` read their spec sets from the table and
  `kernel.py` exports the per-spec rules to the kernel. A new ability touches the overlay, its Python function
  (`abilities.MODEL_FNS` or `buffs.BUFF_HANDLERS`) and its kernel match arm.
- `model/hyper.py`: hyper vault environment by cycle. It runs a Monte Carlo over whole vaults with the opening and per-kill
  chaos dumps, the Brutal Pillar kills, ambient pulls, the 350 chaos budget and the stack caps. It gives the boss health factor,
  the damage modifiers, the Frenzy player-damage multiplier and the expected boss crit chance, cached under `out/`. It also
  builds the fine cycle grid that both the Python model and the kernel solve on. Knobs: `hyper_chaos_modifiers`,
  `hyper_frenzy`, `hyper_minutes_per_cycle`, `hyper_runners`.
- `model/evaluate.py` (kernel `survival.rs`): damage cycle (kill time, missing-HP dynamics, hyperboss shield downtime,
  +5-cycle execution cap), healing per second (leech, Life Steal, Heal on spare mana, Totem, x healing effectiveness,
  Bloodthirst), the attack stream (avoidance by stable speed, the ranged bonus, the Immortal Seal, block and dodge),
  the survival cycle (binomial window of `survive_hits` attempts at the `survival_risk` quantile with crit mitigation,
  Castle Bastion by speed, Mana Shield and Ultimate Shield absorption, Last Stand; never one-shot by an unguarded hit),
  the HP-state heuristic (a second pass at the scored cycle), The Dice at a percentile, and the score.
  `model/binomial.py` is the quantile both engines compute term by term.
- Uniques: `extract/extract.py` writes the merged unique registry and seal pool; `catalog.unique_options` rolls them per
  stage and notes which unique powers the model counts (`catalog.POWER_NOTES`). A worn unique keeps the normal piece's
  seal, etching and unusual parked on the item (`search.equip_unique`), so taking it off restores that piece.
- Thorns: `damage.thorns_reflect` (attack damage x 2 x thorns damage + flat thorns, Shell Porcupine multiplies flat
  thorns) and the `ability:Shell_Porcupine` family, which reflects once per boss attack that reaches the player.
- Rework movement minimum: the kernel anneal gives back the movement part of the requirement penalty at the start
  and ramps it to full by `MOVE_RELAX_END` (half) of the steps, so slow builds (The Stonewall) can be crossed early;
  the best build is only tracked once the rule is at full strength. The Python reference search does not relax it.
- Uniques: the unique model audit (kept outside this repository) lists every unique modifier and whether the model
  counts it (POWER_NOTES in `model/catalog.py` carry the reasons shown on the report's item cards). Proposed unique
  balance lives in `REWORK_UNIQUE_OVERRIDES` and the `frozen_orb_additive` knob. Multi-jump rule: normal boots with the
  Multi Jump implicit or the Prismatic Feather trinket (`search.multijump_ok` / kernel `mj_ok`).
- `model/procs.py` (kernel `procs.rs`): the proc layer. Event rates (normal hits, lucky hits, boss attacks landed or
  reaching the player) turn into boss debuff sources (kind, amplifier, uptime). Vulnerable keeps the strongest
  application (expected value over independent sources, no level cap); every other kind counts once for Sneaky
  Advantage, and Hexbreaker reads 1 - the product of (1 - uptime) over them. Cloud poison is a DoT stream here.
  Echo is a target state: one Echoing slot per target fed by every eligible hit stream, valued from
  `data/echo_table.json` (`tools/echo_table.py` simulates the addon's echo rules tick by tick; `--check` compares the
  table with fresh simulations; Reverberating Echo reads the `v_reverb` table). Streams of different strength mix by
  nested table lookups (strongest first).
- `model/rotation.py` (kernel `rotation.rs`): every ability family. Buffs are paid first; damage abilities (main plus
  learned secondaries) share the rest greedily by DPS per net mana (Mana Steal surplus joins the pool); Empower,
  Rampage and a non-main Implode take the leftover; then the mana-state uptimes, debuffs, i-frame clipping against the
  other sources, Power Dump, the single DoT slot and echo. Woven swings are scored (`score_swings`) as a secondary
  row. `main_damage_share_min` (stages.KNOBS, 0.7) keeps the main ability (with its echoes) at that share of the
  build's damage: 40 x gap during the search, enforced at the end of every search by `prune_secondaries`. The kernel
  skips the probe pass for a lone ability whose debuffs and Depleted uptime do not depend on its mana share.
- `model/godfx.py` (kernel `gods.rs`): god node effects, the Fury table and the Concentrate ladder.
- Arcane Cascade and Lucky Reset: one solver for "lucky hits shorten cooldowns" (`evaluate_lin`, both engines): secant
  then Illinois regula falsi on a 2^-20 grid of the lucky-hit rate, no warm start; a jump without a fixed point is
  flagged `cascade-unconverged` and keeps the last trial.
- Rounding rule: any floor, ceiling or ordering of a stat-derived value carries a 1e-9 tolerance (or `procs.tie_key`),
  because delta scoring drifts by rounding noise and a flip there is a score jump. Every rounding in `model/*.py` and
  `kernel/src/*.rs` is listed in `model/rounding.py` with its class; `tests/test_rounding.py` fails on an unlisted or
  stale entry. Check with the delta sweep, not only `kernel_speed.py --verify`.
- JSON contract: `kernel.contract_lists()` (affix kinds, flag and special keys, talent types, proc and cloud kinds,
  etching queries, config keys, bug ids, rule codes, cast-debuff classes, knob names) must equal the kernel's lists;
  `Problem::new` asserts them (the `contract` op prints the kernel's side), every context struct denies unknown fields,
  and a knob the kernel reads must be in `KNOB_NAMES`, a buff spec in `BUFF_SPS`: the kernel panics at load otherwise.
- `model/search.py`: the reference Python search (moves, polish, idle stripping, secondary prune); `--engine python`
  runs it.
- `model/kernel.py`: flattens a Context into index tables for the kernel and maps kernel builds back to Python objects.
- `kernel/`: Rust port of `evaluate()` and the search. `src/eval.rs` holds `Ev`, the tier/cooldown helpers and
  `evaluate_lin` (the cascade solver and the HP-state passes); the hurt chain is in `hit.rs`, buffs in `buffs.rs`,
  family dispatch, melee and the vaultarang in `families.rs`, the rotation in `rotation.rs`, per-ability formulas in
  `model.rs`, the attack stream, the survival window and healing in `survival.rs`, the proc layer in `procs.rs`, the
  god trees in `gods.rs`, the load and the contract in `problem.rs`. Builds are fixed-size `Copy` values (capacities
  in `kernel/src/build.rs`; loading fails with "raise MAX_..." when a table outgrows them). Candidates are scored by
  delta: additive stat totals are patched from the current build by diffing the two builds, and re-summed from
  scratch every 1000 accepted moves. One pass computes damage at target HP 0 and 1e9, with and without execution
  (one lane in the rework, which has no missing-HP execution); the cycles are solved exactly instead of by
  bisection. Proposals come only from legal moves that change the build; the `target` schedule steers temperature so
  the share of worse moves accepted falls from 25% to 2%. `legacy` mode reproduces the Python proposal rule for
  benchmarks. The `profile` op times the parts of a step.
- `tools/`: the checks above, `fang_geometry.py`, `echo_table.py`, `unique_bench.py`, `deck_study.py`,
  `deck_weights.py`, `import_archetype_decks.py`, `progress_server.py` (live run progress, above).
- `tests/`: the unit tests (`run_all.py`), the worked-example pinning script and its data.

Fallbacks print `[model][FALLBACK]` (or `[assets][FALLBACK]`, `[report][FALLBACK]`) once and are counted into the
report's method page; the page itself logs `[ledger][FALLBACK]` in the browser console.
