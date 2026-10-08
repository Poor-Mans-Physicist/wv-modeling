# lane: the Routerunner room planner

The room-routing planner and time model from the [Routerunner](https://github.com/Poor-Mans-Physicist/Routerunner)
mod, as a standalone Rust crate and CLI. Given one vault room (a solid/air voxel grid, chest positions, an
entrance and an exit) it plans the mining route a player would follow and prices it in seconds with a
fitted time model. This is the same code the mod runs in game through JNI (Routerunner 1.2.0); the JNI
glue is left out here.

It is a 1:1 port of the mod's Java planner (`com.routerunner.lane`): on the 13-room reference set the
plan JSON was byte-for-byte identical to the Java output. Floating-point results can differ by one ULP
on other platforms (different libm); the planner rounds angles and logarithms before comparing them
for this reason.

## Build

```
cargo build --release
```

gives `target/release/lane_cli` (`lane_cli.exe` on Windows). Stable Rust, no feature flags.

## CLI

```
lane_cli rooms.jsonl plans.jsonl <model.json> [threads]
```

- `model.json` is `../../models/routerunner/weights/timemodel_shape.json` (the current default) or
  `legmodel_ridge.json` (the older 12-feature ridge model). The CLI tells them apart by the `miners` block.
- `threads` defaults to `available_parallelism() - 1`.
- Per-room progress goes to stderr; a room that fails writes `{key, error}` instead of a plan.

You normally don't call it by hand: `models/routerunner/sim/run_cells.py` generates rooms with the vault
simulator, prepares the records and iterates the chest rate.

### Room record (one JSON object per line)

| Field | Meaning |
|---|---|
| `key` | free-form id, echoed back |
| `grid` | `{sx, sy, sz, solidZ}`; `solidZ` is a gzip + base64 bitset, cell index `(x*sy + y)*sz + z`, LSB first, set = solid |
| `chests` | `[[x, y, z], ...]`, room-local |
| `entrance`, `exit`, `origin` | `[x, y, z]` |
| `chainRange`, `chainLimit` | miner: 6 / 32 = Chain Miner (default), 1 / 896 = Vein Miner |
| `modes` | `["point"]` (what the mod uses) and/or `"corridor"` |
| `params` | optional overrides: `speedAttr` (movement speed attribute), `breakReach`, `bailFloor` (chests/s below which a lane is abandoned), `summary` (1 = only totals), `timeScale`, `bailAggression`, `sweepGain`, `proxyTopK`, `beamWidth`, `minLaneLen`, `minLaneClr`, `turnCap`, `triggerS` |
| `tEntry`, `solo` | optional; fixed entry time and solo-chest handling |

Other fields (the simulator writes `gates` and `real{...}`) are ignored.

### Plan output

One line per room: `{key, <mode>: {...}, log: [...]}`. With `summary: 1` each mode holds
`{tTotal, yieldTotal, cover, nLanes, nTrig}`: planned seconds, planned chests, the share of chests
covered, lanes and trigger count. Without it, the full plan (lanes, runs, transitions, triggers, ghost
path) from `export.rs`. `log` holds wall-clock timings and is not deterministic.

## Results on `rooms_v2.3c.jsonl` (13 rooms, point mode, 1068–1316 chests each)

| | Java (mod) | Rust |
|---|---|---|
| sum of per-room times | 13.12 s | 2.13 s |
| speedup | | **6.16x** (per room 4.8x – 8.2x) |
| process wall time | 13.4 s | 2.2 s |

All 13 rooms agree on `nLanes`, `nRuns`, `cover`, `tTotal`, `yieldTotal`, every run polyline,
`exitPath`, `exitStraight`, `ghost`, `clears` and `heat` — and the serialized mode object is
byte-identical to Gson's output.

Corridor mode (3 rooms, both modes): 6.6 s Java vs 1.0 s here, and 5 of the 6 mode objects are
byte-identical. The sixth differs only in `tTotal`, by **one ULP** (18.105703037159795 vs
18.1057030371598); every lane, run, trigger and ghost sample in that plan is identical. The cause
is `Math.exp` / `Math.log`, which HotSpot replaces with its own x86 stub while Rust calls the UCRT;
both are sub-ULP accurate but they are not the same implementation, so a leg time can land one ULP
apart. No decision the planner makes was affected.

## Java behaviours this port reproduces on purpose

The plan depends on several things the Java source never states, so `jcompat.rs` reproduces them:

- **`java.util.PriorityQueue`'s heap.** The A* comparator orders on `f` alone, so equal-`f` entries
  pop in whatever order the binary heap happens to hold them. `JPq` is Java's exact
  `siftUp`/`siftDown`, which is what makes A* return the same path among equal-cost ones.
- **`java.util.HashMap` iteration order.** In point mode the candidate list is built by iterating a
  `HashMap<Long, P>`, and equal-scoring candidates keep that order through the stable sorts, so it
  decides which candidates get evaluated. `java_hashmap_order` reproduces it: table bucket first,
  insertion order within a bucket, with Java's capacity growth and `Long.hashCode` spreading.
- **`Math.round`.** The exact JDK bit algorithm, which is not `floor(x + 0.5)`.
- **`Double.toString` and `String.format("%.Nf")`**, so the JSON bytes and the log lines match.
- **`Math.hypot`** over integer coordinate differences: verified against the JDK to be exactly
  `sqrt(dx*dx + dz*dz)` for every integer pair in range, so plain `sqrt` is used.
- **Integer negation before widening** in the ghost yaw: Java's `-(b.x - a.x)` is an int, so a zero
  delta gives `+0.0` and `atan2` returns `+180`, not `-180`.

## Where the speed comes from

Every per-cell map Java keys by a packed cell key is a flat array indexed by `(x*sy+y)*sz+z`,
stamped with a generation counter so a search neither allocates nor clears: `gcost`/`parent` and
the sweep-discount memo share one 24-byte struct per cell, the closed set is a bitset, `standable`
and `columnFree` are baked bitsets, and the reach lists live in one arena.

The largest single win is `SuccTable`: which walk steps a cell has, where they land and what they
cost is a pure function of the walls (the head-room test, the diagonal corner test and the drop
scan all read only the grid), so the whole walk graph is baked once per room and the A*, the
reachability BFS and the exit field walk a table instead of re-deriving it tens of millions of
times. Entries keep the `dx`/`dz` order Java emits them in, which is what the A* ties break on.

Two smaller ones that are worth knowing about because they *look* like semantic changes and are
not: the sweep-discount memo is kept for a whole `evalTop` instead of being rebuilt per candidate
(the discount is a pure function of cell and `remaining`, which do not change across one `evalTop`),
and a relax is skipped without consulting the discount when the cell is already cheaper than the
floor-discounted step could ever make it (the discount is bounded below by `DISCOUNT_FLOOR`). The
discount scales walk steps only, never a DROP edge — that asymmetry is Java's and getting it wrong
changes plans.

## Known limitation

`java_hashmap_order` models Java's `HashMap` as (bucket, insertion order). That is exact while
every bin stays a linked list, but when a bin reaches 9 entries in a table of 64 or more Java
treeifies it and `moveRootToFront` moves the red-black root to the head of that bin, which the
model does not reproduce. The port detects this, warns once, and reports the count at the end of a
CLI run (`1895` bins over the 13 reference rooms). On this data it changed nothing — all 13 rooms
are byte-identical — but it is a real divergence risk on other rooms. Closing it properly means
porting `HashMap.TreeNode` (`treeify`, `balanceInsertion`, `moveRootToFront`, `putTreeVal` and
`split`); nothing short of that is exact.

Two deliberate deviations from `LaneCli`, neither visible to `tools/lane_bundle.py`, which keys
records by `key`:

- output lines are written in input order rather than completion order;
- the per-room `log` line carries this port's own timing, so it will never match Java's.
