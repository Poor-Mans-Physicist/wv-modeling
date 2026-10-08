# vaultsim: vault room and chest simulator

A Rust reimplementation of how Vault Hunters assembles vault rooms and places chests, used to answer
"how many chests of type X will this crystal give me, and where are they?". It reads the pack's real
room structures, template pools and palettes from `cache/pack/` (`python setup/fetch_sources.py`) and
reproduces:

- jigsaw room assembly from `.nbt` templates, with rotation, mirroring and palette processors;
- the base POI chests each room places, including strongbox and Enigma rolls by vault level;
- `decorator_add` ("Bonus X Chests") and `decorator_cascade` ("X Cascade") with 3.21.6's per-(region, chunk)
  event order (`src/schedule.rs`), chunk-border clipping and the sturdy-floor rule;
- themed vaults: rooms drawn from a theme's pool, chunk alignment along a run.

The crate is still named `wv_chest_sim` (the binaries import `wv_chest_sim::...`).

`SPEC.md` is the source of truth: every mechanic with its source and a CONFIRMED / INFERRED / OPEN tag.
`MECHANICS_NOTES.md` holds the supporting research. Read the relevant SPEC section before changing a
mechanic.

## Accuracy

- Living-chest vaults: real ÷ simulated chests 1.03–1.15 over 6 vaults, inside the spread between two
  vaults rolled from identical crystals.
- Baseline (no modifiers): ~22.1 chests per room, ~2.08 gilded per room.
- Known gaps (SPEC §10): Enigma replacement is not modeled (no code path places it in the pinned jars);
  upstream theme selection is not modeled, so you pick the theme.
- The data pin is the 0.34.1 pack. Room pools change between pack versions, so re-check after fetching a
  different ref.

## Build

```
cargo build --release
```

Set `WV_GEN_ROOT` to point at a different `config/the_vault/gen/1.0` folder (for example a pack checkout
with your own room edits). Output files go to `out/vaultsim/` at the repo root.

## Binaries

Core tools:

| Binary | What it answers |
|---|---|
| `wv_vault_grid cells` | rooms for a list of (Bonus, Cascade) cells, as lane-planner input records (used by `models/routerunner/sim/run_cells.py`) |
| `wv_vault_grid grid` | a whole themed vault (size × size rooms) at one crystal, as JSON |
| `scripts/chest_counts.py` | chests per room (mean, 95 % interval, clumpiness, strongbox share) for a list of crystals; wraps `wv_vault_grid cells`. **Start here for chest counts.** |
| `wv-modifier-panel` | a dense (Bonus, Cascade) surface up to 200 × 200 as CSV + HTML. **Pre-dates the 3.21.6 event schedule**: it runs per-room decorator passes, which under-counted real stacked vaults by 18–35 %. Use it for shape, not absolute counts. |
| `wv-chest-sim export <room.nbt> <out.json> ...` | one room, assembled and decorated, as voxel JSON |
| `wv_export_room [target] [add] [cascade] [seed] [out.json]` | one random common room with chests, for route previews |
| `wv_blobs` | chain-miner connected components (Chebyshev range, 32 cap) across a density sweep |

Investigation tools (each one was written to settle a specific question; the header comment says which):
`wv_blockcensus`, `wv_diag`, `wv_floattrace`, `wv_floorcap`, `wv_rainbow_float`, `wv_sandwich`
(floating-block and floor-rule audits), `wv_forensics_b` (cell-level trace for sim-vs-real comparisons),
and `wv_route`, `wv_line`, `wv_sweep` (prototypes of the retired waypoint router; kept for reference, the
current router is `libs/lane`). `wv_route` and `wv_line` write HTML that expects a `three.min.js` next
to it.

## Validating a change

1. Check the mechanic in SPEC.md and, if you change behaviour, update SPEC.md in the same change.
2. `python setup/check.py routerunner` must still reproduce the panel within 1 %.
3. For chest-count changes, compare against real vault data (chest counts from real runs at a known
   crystal), not against your expectation. Three earlier "simulator bugs" turned out to be bad
   assumptions or bad second-hand numbers.
