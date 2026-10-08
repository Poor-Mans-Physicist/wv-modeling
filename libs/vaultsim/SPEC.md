# Vault simulator (vaultsim) mechanic specification

Purpose: an exhaustive, sourced list of every game mechanic `wv-chest-sim` (and its `wv-web`/`wv-modifier-panel` consumers) is supposed to be modeling, with the exact source (decompiled Java file:line, or config JSON path) each claim is based on. Written after finding and fixing one confirmed bug (see §6) in the same investigation that produced this document — treat every claim below as "should be re-verified," not "is definitely correct." Status tags used throughout:

- **CONFIRMED** — read directly from decompiled bytecode/source or from a config file, or verified against an in-game test the user performed.
- **INFERRED** — derived from confirmed facts via reasoning (math, structural analogy to a *different* confirmed mechanic), but the specific code path was not directly read.
- **UNCONFIRMED / OPEN** — a real gap. Flagged explicitly so it isn't mistaken for settled fact.

This pack targets **Wold's Vaults**, a modpack built on **Vault Hunters** (`the_vault`, the base mod, by Iskall85/the Vault Hunters team) plus the **WoldsVaults addon** (`xyz.iwolfking.woldsvaults`, GitHub source). Two separate codebases, two separate decompile trees — see §1.

---

## 1. Source-of-truth map

| Tree | Where | What lives here |
|---|---|---|
| Base mod (`the_vault`) | closed source; decompile the jar yourself to check a citation | core vault generation (`core.world.*`), base (non-settable) modifier classes (`core.vault.modifier.modifier.*`), `Modifiers`/`VaultModifierStack` container classes (`core.vault.*`) |
| Addon (`woldsvaults`) | github.com/iwolfking/Wolds-Vaults-Official-Mod | settable-modifier subclasses (`modifiers.vault.map.modifiers.*`), the settable-value base class (`modifiers.vault.lib.*`), modifier-type registry mixin, `VaultModifierUtils` |
| Pack config | `cache/pack/config/the_vault/` (`python setup/fetch_sources.py`) | `vault_modifiers.json` (every modifier's registered type + properties + display text), `gen/1.0/` (room/tunnel NBT structures, template pool JSON), `gen/room_level_lock.json` |
| Rust implementation | `libs/vaultsim/src/` | the simulator: `assemble.rs`, `decorator.rs`, `schedule.rs`, `strongbox.rs`, `data.rs`, `pools.rs`, `transform.rs`, `voxel.rs`, `structure.rs`, `main.rs`, `src/bin/*` |
| Web port | not in this repo | a wasm-bindgen wrapper that re-applies the same modifier logic per call |
| Research notes | `libs/vaultsim/MECHANICS_NOTES.md` | strongbox, gate/entrance, room-theme weighting and Enigma-chest findings, cited below rather than duplicated |

**Why two decompile trees**: the addon defines its own settable subclasses (`DecoratorAddModifierSettable`, `CascadeDecoratorModifierSettable`) for a *different* mechanic (Mapped Vault crystals specifically) that happens to share display names with the base mod's plain, non-settable modifiers the player-stackable "Bonus Gilded"/"Gilded Cascade" catalysts actually use (§6 — confirmed via a controlled in-game test, after an earlier false start the original investigation that assumed the addon classes were the ones in play). Both trees matter: the base-mod tree for what actually executes here, the addon tree for ruling out the alternative and for whoever picks up the separate, real Mapped Vault thread later.

---

## 2. Room assembly (jigsaw structure generation)

**What it does**: recursively assembles a room from its root `.nbt` structure by following jigsaw connector blocks, each one sampling a weighted pool for a child template, finding a compatible connector on the child, computing a rigid transform (rotation + translation) to attach it, and recursing.

- Room footprint is a **fixed 47×47 box** (`CELL_SIZE = 47`), anchored at `(region.0 * 47, 9, region.1 * 47)` in world space — `region` is the room's grid-cell index, not a chunk coordinate. **CONFIRMED**, source: `VaultGridLayout.java`/`RegionPos.java` (base mod decompile), cross-referenced in `decorator.rs:26-40` doc comment.
- Rotation happens around the room's own center `(23.5, _, 23.5)` (`cell_size/2`), one of 4 fixed 90°-step rotations, chosen randomly per room instance. Mirroring (`NONE`/`FRONT_BACK`) is real in-game but deliberately not modeled — doesn't change footprint/interior volume or per-room chest counts, only which exact mirrored position one specific chest lands at. **CONFIRMED** for rotation; mirroring omission is a **deliberate, documented scope decision**, not an oversight — `decorator.rs:31-35`.
- Jigsaw attachment: child's matching connector found by `target` name + facing-direction compatibility (vertical connectors need matching `side` too, unless `rollable`); rotation chosen via `JigsawTemplate.getRotation` semantics (vertical+rollable → fully random rotation; vertical+non-rollable → side-direction lookup table; horizontal → facing-direction lookup table). **CONFIRMED**, ported in `transform.rs:91-134`, cross-referenced against `JigsawTemplate.getRotation(Direction,Direction)` and `JigsawTemplate.getRotation(JigsawData,JigsawData,RandomSource)` (base mod decompile — exact file path not re-verified the original investigation, was read in an earlier segment; **re-verify file path if auditing this**).
- Connector pool resolution: `TemplatePool.selectEntry` semantics — flat weighted pick across a pool's entries, each entry being either a direct `value` (template leaf), a `reference` (cross-file pool indirection), or a `pool` (inline nested weighted sub-group). **CONFIRMED no anti-repeat/dedup memory** — a fresh weighted list is built on every single call, so repeated draws from the same pool are i.i.d., not "without replacement." **CONFIRMED**, source: `TemplatePool.java` (base mod decompile, `core.world.template.data`), ported in `pools.rs` (schema) + `data.rs:75-104` (`sample_entries`/`resolve_entry`).
  - **CONFIRMED separately**: `selectEntry` supports per-entry weight `multipliers`/`conditions` (context-dependent weight overrides) that this simulator does NOT model. Checked via grep that none of the 69 common-room theme pools (`*_common_rooms.json`) use these fields — only `omega_rooms.json` does — so the omission is currently safe, but **if this spec is ever extended to challenge/omega/raw tier rooms, this needs to be modeled.**
  - A pool entry resolving to `the_vault:empty` means "no template" (a legitimate dead-end, not an error). **CONFIRMED**, `data.rs:91-94`.
  - `ROOM_LEVEL_LOCK` retry mechanism (`VaultGridLayout.getRoom()`, up to 10 re-roll attempts if the picked room is blacklisted at the vault's current level) — **CONFIRMED to only ever affect `challenge/`/`omega/` tier rooms**, never any `common/` tier room, via direct read of `config/the_vault/gen/room_level_lock.json`. Not modeled, correctly irrelevant to this simulator's current common-room-only scope.
- Pieces-placed / "dud" tracking (a jigsaw point where the pool rolled a real template but no compatible connector existed on the child) is tracked for diagnostic reporting only, not part of any chest-count logic. `assemble.rs:175` / `124-148`.

---

## 3. Baseline POI chest population

**What it does**: while pasting a structure's blocks into the world grid, any block using the `the_vault:placeholder` block-state with a `type=wooden_chest|gilded_chest|living_chest|ornate_chest` property becomes a baseline chest at that position; `type=gate` becomes a tracked doorway marker.

- **CONFIRMED**: chest type classification is a prefix match (`classify_chest`, `assemble.rs:12-14`) against exactly 4 known type strings — `CHEST_TYPES` constant, `assemble.rs:10`.
- **CONFIRMED**: this pack never uses `minecraft:structure_void` in any room/decor structure (checked empirically in an earlier session) — every placed piece's blocks unconditionally overwrite whatever was there before, no transparency handling needed. `assemble.rs:79-82`.
- Liquid tracking (water/lava) is separate from the solid/air grid — needed because `decorator_cascade`'s target-cell check is air-**or**-liquid, while `decorator_add`'s is strict air-only (§5/§6). **CONFIRMED**, `assemble.rs:32-36`.
- Gate detection (`the_vault:placeholder[type=gate]`) is per-template, not hardcoded — most common rooms have 4 (one per cardinal wall), 23 of 118 scanned room files (standalone/dead-end rooms) have 0. Full position table, Y-offset exceptions, and floor-reference notes already documented in `MECHANICS_NOTES.md`— not repeated here. **CONFIRMED** (direct scan of all 118 room files in an earlier session).

---

## 4. Strongbox upgrade roll

Fully researched and documented in `MECHANICS_NOTES.md`— summary, not re-derivation:

- A strongbox is **a single weighted roll at the exact same placeholder slot** as a normal chest (`BernoulliWeightedTileProcessor` → `WeightedList.getRandom`), never an independent extra spawn. **CONFIRMED**.
- Keyed by **vault level**, not reputation. Wooden chests have **no strongbox upgrade at any level** (no `wooden_strongbox` block exists). Gilded/Ornate: ~5% at level 50, ~8.3% at 80, ~12.5% at 100. Living: flat 5% from level 50 onward. **CONFIRMED**, implemented verbatim in `strongbox.rs:8-30`.
- `decorator_add` chests of an eligible type CAN roll into strongboxes (same placeholder pipeline as baseline chests). `decorator_cascade` chests **can never** become strongboxes, and strongboxes can never be a cascade source — not a special case, a structural consequence: cascade copies an existing block-entity's exact state directly, never touching the roll pipeline, and a strongbox is a genuinely distinct block id that no cascade modifier's `filter` (an exact block-id match) ever names. **CONFIRMED**, `strongbox.rs:32-37`, `decorator.rs:150-153`.
- **Implementation gap, flagged for audit**: `main.rs`'s `run_export` function (the JSON-export path used by the 3D voxel visualizer) calls `decorator_add_pass` in a loop (lines 345-358) but never calls `apply_strongbox_rolls` on the result — unlike `wv_modifier_panel.rs:166` and `wv-web/src/lib.rs:128`, which both do. This doesn't affect any chest **count**, only whether an exported add-pass chest is ever visually flagged as a strongbox. Worth fixing for consistency, low priority.

---

## 5. `decorator_cascade` ("X Cascade" / "More X")

Real mechanic, cross-checked against both `DecoratorCascadeModifier.java` (base mod, non-settable — **the class that actually executes for an ordinary, non-Mapped crystal's "Gilded Cascade" catalyst**, per §6) and `CascadeDecoratorModifierSettable.java` (addon, settable — used only for Mapped-Vault-bundled modifiers, see §6). Both share the same per-attempt spatial algorithm; only the *source* of the `chance` parameter differs (§6):

- **CONFIRMED**: registers exactly **two** listeners per modifier instance regardless of stack count — one on `SURFACE_GENERATION` ("generation" phase), one on `TEMPLATE_GENERATION.POST` ("population" phase), both at priority `-100`. Source: `CascadeDecoratorModifierSettable.java:40-53`.
- **CONFIRMED**: each invocation (`onGenerate`) builds its candidate-source list **once**, by scanning every block entity in the chunk (`access.getBlockEntitiesPos()`), keeping only ones matching the modifier's `filter` (an exact block-id predicate, e.g. `the_vault:gilded_chest`) AND not already flagged `cascade_duped` AND either untagged or tagged with the *same* phase string as this call. Source: lines 55-80.
- **CONFIRMED — a cascaded chest can never itself become a future cascade source.** The moment a chest gets placed via cascade, its NBT gets `cascade_duped=true` (line 112), which permanently excludes it from ever being scanned as a source again (the filter at line 70 checks this flag). The simulator's design (passing a fixed `sources: &[ChestSpot]` list into `decorator_cascade_pass`, never growing it with the pass's own output) matches this correctly. Source: `decorator.rs:118-177` doc comment + implementation.
- **CONFIRMED — per-source attempt count is a stochastic-rounding loop, not a guaranteed count**: starting from the modifier value p, keep making attempts while a uniform draw is below p, subtracting 1 from p after each attempt (around line 92). Each iteration that passes the check gets one placement attempt (which can still independently fail to find a valid spot — no retry, silently wasted). Implemented verbatim in `decorator.rs:160-173`.
  - Mathematical property (**INFERRED**, straightforward expectation calculation, not separately verified empirically): this loop's expected number of "true" iterations always equals its starting `p` exactly, for any non-negative `p`. Consequence: splitting one accumulated `p` into `k` separate calls of `p/k` each gives the *same expected total* as one call with `p` — relevant to §6's stacking-equivalence argument.
- **CONFIRMED — candidate search is a 7×7×7 cube (origin ±3 in all three axes) clipped to the source's own chunk**: every cell within ±3 of the source on each axis is considered, but only if it lies in the same 16×16 chunk column as the source (lines 122-141). This is the **chunk-border-blocking mechanic** — a source near a chunk edge has a correspondingly *smaller* real search volume than the nominal 7×7×7 cube suggests, since candidates that fall in a neighboring chunk are skipped even though they're geometrically inside the cube. **Already correctly implemented** in `decorator.rs:194-227` (`find_cascade_spot`, the `x.div_euclid(16) != chunk.0` check at line 208) — verified against this exact source during the original investigation's investigation; this was NOT the explanation for the open numeric discrepancy in §7.
- **CONFIRMED — candidate validity**: target cell must be air-or-liquid (`state.isAir() || LiquidBlock`), AND the cell directly below must pass `isFaceSturdy(..., UP)` (lines 132, decompiled bytecode for the equivalent base-mod check) — not a blanket solidity test. **CONFIRMED** reservoir-sampling: every valid candidate in the clipped cube is found (search never stops at first hit), each equally likely via `random.nextInt(++index) == 0`. Implemented in `decorator.rs:213-222`.
- **CONFIRMED — `decorator_add` always resolves before `decorator_cascade` for the same chunk**: event listener priority 0 (add) beats -100 (cascade); `Event.java`'s `getListeners()` sorts descending. Consequence: an add-pass's own newly-placed chests are valid cascade sources within the same trial. `decorator.rs:118-126` doc comment; modeled correctly in every call site (`main.rs` phase 3b, `wv_modifier_panel.rs`, `wv-web/lib.rs`) by always running add before constructing cascade's `sources` list.

---

## 6. `decorator_add`/`decorator_cascade` ("Bonus X"/"X Cascade") and the settable-modifier dead end

### 6a. Two genuinely distinct, identically-named "Bonus Gilded" modifiers exist

`vault_modifiers.json` contains **two separate modifier IDs that both display as "Bonus Gilded"** to the player, registered under different types:

| | `the_vault:gilded` | `the_vault:map_gilded` |
|---|---|---|
| Registered type | `the_vault:modifier_type/decorator_add` (base mod, non-settable) | `the_vault:modifier_type/decorator_add_settable` (addon, settable) |
| Java class | `DecoratorAddModifier` | `DecoratorAddModifierSettable` |
| `attemptsPerChunk` source | **literal `8` in its own JSON properties** (plus `"roomTypeWhitelist": ["COMMON","ORE"]`, confirmed present in this entry specifically) | none in JSON — computed as `(int)value` at runtime |
| `display.description` | static: `"+1 Set of Gilded Chests"` | static: `"Adds chances for Gilded chests"` |
| `display.descriptionFormatted` | **absent** | present: `"+%d chances for Gilded Chests"` |

**CONFIRMED, read directly from the JSON.** The same split exists for cascade: `the_vault:gilded_cascade` (`decorator_cascade`, non-settable, `chance: 0.25` literal, descriptionFormatted `"+%d%% Gilded Chests"`) vs `the_vault:map_gilded_cascade` (`decorator_cascade_settable`).

### 6b. RESOLVED — it's `the_vault:gilded`/`the_vault:gilded_cascade` (non-settable), confirmed by a controlled in-game test

This was investigated back and forth the original investigation; here is the final, settled conclusion plus the evidence trail, because the reasoning matters for trusting the conclusion.

**Settled by a direct, controlled test the user performed**: applying a *second* Bonus Gilded catalyst to a crystal left the tooltip reading exactly **"+1 Set(s) of Bonus Gilded Chests"** — unchanged from one catalyst. This is decisive:

- `the_vault:map_gilded` has a real `%d`-scaling `descriptionFormatted` template (`"+%d chances for Gilded Chests"`). If this were the modifier in play and stacking accumulated a `value`, the tooltip would have read "+2" after the second catalyst. It didn't.
- `the_vault:gilded` has **no** `descriptionFormatted` at all — `VaultModifier.getDisplayNameFormatted()` falls through to the static literal `"+1 Set of Gilded Chests"` **unconditionally**, regardless of how many separate entries of this modifier exist. A frozen "+1" after stacking is exactly this signature.
- **Independently corroborated by a structural argument, not just the tooltip**: `the_vault:map_gilded`/`map_gilded_cascade` are only ever attached as part of the addon's "Mapped Vault" bundle, imbued via `VaultMapItem`/`applyCrystalRecipe()` (`MECHANICS_NOTES.md`— confirmed in an earlier session). The user confirmed the crystals in question don't require Mapped Vault status at all — so `map_gilded` isn't even a reachable modifier for them, independent of any tooltip-formatting argument.

**Earlier in the original investigation, before this test existed, the opposite conclusion was reached** (§7 records the full reasoning that produced it, kept for the audit trail) — built on the *original* "+66 bonus" report alone, which is ambiguous (consistent with either "a single tooltip number that says 66" or "66 catalysts stacked, colloquially called +66" — and the controlled 2-catalyst test is what actually distinguishes those readings, not the original report). **That earlier conclusion is wrong and has been reverted** — see §6e.

### 6c. The real mechanic: `the_vault:gilded`/`the_vault:gilded_cascade`, non-settable, N independent stack entries

- **CONFIRMED, `DecoratorAddModifier.java`** (base mod): `attemptsPerChunk` is a literal config constant (`8` for `the_vault:gilded`), read directly from `Properties`, no dynamic `value` involved at all.
- Stacking N catalysts of a non-settable modifier goes through the generic `Modifiers.addModifier(modifier, amount, ...)` pathway (`core/vault/Modifiers.java`, base mod) — confirmed this creates `amount` **separate** `Entry` objects, each independently calling `initServer()` later. N catalysts ⇒ N independent registered listeners, each running its own fixed `attemptsPerChunk=8` loop (add) or `chance=0.25` stochastic loop (cascade) — **not** one accumulated total.
- This is exactly what `decorator.rs`'s spatial algorithm (per-attempt sampling, chunk-clipped cascade search, validity rules — §5/this section) already implements when called once per stacked catalyst, each with the fixed config constant. **No change needed to the spatial algorithm at all** — only the call-site numeric constant (§6e) was ever in question.
- `requireConditions=true`, `roomTypeWhitelist: ["COMMON","ORE"]` both confirmed present on `the_vault:gilded` directly in `vault_modifiers.json`. The whitelist is confirmed (in an earlier session, not re-verified the original investigation) force-bypassed by a WoldsVaults mixin (`isRoomTypeWhitelisted()` forced to always return `true`), so every room type is eligible in this pack regardless.

### 6d. `the_vault:map_gilded`/`map_gilded_cascade` (settable) — kept for reference, not believed to be in play for ordinary crystals

Documented in case a future audit needs it for *Mapped Vault* crystals specifically (a real, separate, confirmed-modelable mechanic — §10), but **not** used for the plain Bonus Gilded/Gilded Cascade catalyst stacking this whole project is about (§6b):

- `DecoratorAddModifierSettable.getAttemptsPerChunk(context)` = `(int) this.getValue()` exactly — no multiplier, no vault-level/difficulty scaling, confirmed by direct read.
- `CascadeDecoratorModifierSettable`'s chance loop reads `this.properties.getValue()` directly as `p`, same stochastic-rounding structure as the non-settable version.
- `VaultModifierUtils.incrementModifierValueOfType` (`api/util/VaultModifierUtils.java:116-124`) is the addon's accumulate-into-one-entry pathway for settable modifiers — confirmed used for `CrateItemQuantityModifierSettable` (an unrelated brewing/alchemy-objective system, `AlchemyObjective.java:453`), never confirmed used for `map_gilded` specifically. Moot now that §6b has settled which modifier is actually in play, but the call site for *if* `map_gilded` ever gets applied via Mapped Vault remains unfound, for whoever picks up the Mapped Vault thread later.

### 6e. The fix that was applied, then reverted, the original investigation — full history for the audit trail

1. **Original code** (present for most of this project's history): `decorator_add_pass`/`decorator_cascade_pass` called once per stacked catalyst, with `attempts_per_chunk=8`/`chance=0.25` fixed per call — i.e., exactly §6c's real mechanic. At the time this was written, doc comments (`main.rs:120`, `decorator.rs:42`) correctly named `the_vault:gilded`/`DecoratorAddModifier` as the source.
2. **Mid-session (incorrect) fix**: prompted by a reported ~2x gap between this model's prediction at (+66 bonus, +66 cascade) (1591 chests/room) and a second-hand report of ~830. Investigation found the settable `map_gilded`/`map_gilded_cascade` mechanic (§6d) and, reasoning from the *original* ambiguous "+66 bonus" report plus tooltip-formatter code alone (without a controlled test), concluded `map_gilded` was the real target and that stacking accumulates one `value` rather than creating N entries. Changed every `decorator_add_pass` call site's `attempts_per_chunk` from `8` to `1` accordingly. This dropped the (+66,+66) prediction to ~245 — *further* from the reported 830, in the opposite direction.
3. **Reverted, the original investigation, after the controlled 2-catalyst test (§6b)**: changed `8` back at every call site (`main.rs` phases 2/3b and `run_export`; `wv-web/src/lib.rs`; `wv_modifier_panel.rs`'s bonus-stepping loop). Rebuilt and re-verified: (+66,+66) now reproduces **~1599**, matching the original pre-investigation figure within Monte Carlo noise. Current code state matches step 1 — the settable-architecture detour is a dead end, not a fix.
- **Why cascade was never touched, in either direction**: the cascade stochastic-rounding loop's expected attempt count equals its starting parameter exactly (linearity of expectation), so whether 66 catalysts are modeled as 66 independent `chance=0.25` calls (§6c, confirmed real) or one call with an accumulated `p=16.5` (§6d, confirmed not real) gives the *same expected total* either way. The existing per-catalyst-call cascade code was never wrong under either hypothesis — this is a fortunate mathematical coincidence specific to cascade's stochastic structure, not something to assume holds for any other modifier.

---

## 7. RESOLVED — the "~2x" was a bad data point plus one real ~10% floor overcount (now fixed)

**Update (Opus deep-audit session).** The 830 figure was wrong; the user's source corrected it to **~1270/room averaged over mixed room types** (challenge/omega included — and in *base* Vault Hunters those get ~0 Bonus Gilded, since the `COMMON/ORE` whitelist is only bypassed by woldsvaults' `MixinDecoratorAddModifier`), plus an imperfect hand count. Re-auditing every mechanic against the decompiled code found the ports faithful and the 66→66-listener stacking correct (`Modifiers.addModifier`→`flatten()`→unique-UUID entries), and empirically ruled out hidden/buried chests (95% have air above, 99.6% reachable from the gate cavity). The **one genuine sim overcount was the `isFaceSturdy` floor approximation** (the last bullet of the old §7 below): the sim accepted any non-air block as a floor, but the game requires a sturdy top face. Measured ~9-10% of floor-spots sit on non-sturdy blocks (bottom slabs/grass/fern/fences; glass IS sturdy). Now fixed via `src/sturdy.rs` + a `non_sturdy` set threaded into `decorator_add_pass`/`decorator_cascade_pass`/`count_chest_slots` (and `decorator_add`'s liquid-floor hole closed). The ~10% raw cut compounds to **~17% at (66,66): ~1592 → ~1328 gilded/common room**, which reconciles with ~1270 once common-only + perfect-count are accounted for. Original §7 text kept below for the audit trail.

## 7-historical. Open / unresolved — the original ~2x discrepancy, still unexplained

With the §6e revert in place, the simulator is back to predicting **~1599 average gilded chests/room** at (+66 bonus, +66 cascade) (36-room average) — matching its pre-investigation behavior. The discrepancy that started this whole investigation is **back to being the open question**, now with the settable-modifier-architecture hypothesis explicitly ruled out as a dead end rather than a live candidate:

- A user's friend reportedly measured **~830** gilded chests in one such crystal's vault, "clearing nearly the entire room" — still a **second-hand, unverified claim**, not a first-hand measurement. ~1599 / ~830 ≈ 1.9×.
- **Ruled out the original investigation** (checked directly against source or empirically, not assumed): the settable-value/accumulated-`value` architecture (§6b-§6e, now confirmed not applicable to ordinary crystals); chunk-border-clipping on cascade (§5, confirmed already correctly modeled in `decorator.rs:194-227`, matches both the settable and non-settable Java versions); out-of-bounds grid handling (defaults to solid/bedrock, the conservative-correct direction — `voxel.rs:63-71`); the `attemptsPerChunk=8`/`chance=0.25` constants themselves (re-read directly from `vault_modifiers.json`, exact match for `the_vault:gilded`/`the_vault:gilded_cascade` specifically, not a sibling or guess).
- **Not yet checked, candidates for further audit, roughly in priority order**:
  - The user's **original hypothesis, never directly tested**: is `roomTypeWhitelist: ["COMMON","ORE"]` really force-bypassed in this exact pinned version of the WoldsVaults mixin? This was confirmed in an *earlier* session (per `project_wv_chest_sim_rust.md`'s Phase 2 findings) but not re-verified the original investigation — worth a direct re-read of that specific mixin given how much else turned out to need re-checking.
  - Whether `Modifiers.addModifier`'s `amount` really does create `amount` fully independent entries with no cap, dedup, or diminishing-returns logic at high stack counts (checked the method's existence and basic shape, not stress-tested against a large `amount` specifically).
  - Whether there's a vault-level or difficulty-based scaling factor on `attemptsPerChunk`/`chance` that this simulator doesn't model at all (no `ScalarReputationProperty` is configured for either `the_vault:gilded` or `the_vault:gilded_cascade` — checked directly — but that's one specific scaling mechanism, not an exhaustive search for all possible ones).
  - Whether "830" and the simulator's "~1599" are measuring the same thing: same room (this simulator averages across all 36 `common/` rooms; one specific room could plausibly land far from the mean — though §7's predecessor investigation checked per-room variance at a different, since-reverted parameter setting and found only a ~1.8× spread, not enough alone to explain 1.9× from the mean, but worth re-checking at the *current*, reverted model specifically), same actual catalyst count (confirmed via tooltip rather than recalled), all chest types vs. gilded specifically, and what "clearing nearly the entire room" actually corresponds to numerically.
  - Whether the per-attempt validity rule (`state.getBlock() == Blocks.AIR` for add; air-or-liquid + `isFaceSturdy` below for cascade) has any subtlety this simulator's "any solid block counts as a valid floor" approximation misses — flagged as an explicit, unproven simplification as far back as this project's Phase 2 notes, never tightened since.

---

## 8. Saturation / chest-slot capacity

- `count_chest_slots` (`decorator.rs:238-264`): scans every position in a room's voxel grid; counts a position as an available chest slot if it's already an existing chest position, OR if it's air-or-liquid with a sturdy non-chest, non-liquid floor below — the exact same validity rule `find_cascade_spot` uses for live candidates, applied room-wide instead of within one source's search cube. **Computed once on the room's pre-modifier baseline state** — doesn't change as bonus/cascade counts increase, by design (it's the room's fixed total capacity, used as a denominator for a saturation-percent stat, not a hard cap enforced during generation itself). No corresponding real-game mechanic to verify against — this is the simulator's own derived statistic, not a port of any specific game code path.

---

## 9. Confirmed validation checkpoints (already passed, useful regression anchors)

- Baseline (zero modifiers) Monte Carlo average: **~22.1 total chests/room, ~2.08 gilded/room** — original foundational validation against a known reference figure (task #6, completed before the original investigation; exact reference source not re-derived the original investigation).
- `wv-web`'s in-browser background Monte Carlo (zero modifiers) independently reproduced **~3.34 average gilded/room** matching the native CLI's own number — confirmed in a real browser per `MECHANICS_NOTES.md`. (Different figure from the 2.08 above because of a different/updated room corpus or trial count at the time each was taken — not reconciled, flagged here so an auditor doesn't assume a contradiction without checking.)

---

## 10. Explicitly out of scope / could not be confirmed (carried over from `MECHANICS_NOTES.md`, still true)

Not bugs — deliberate scope boundaries or genuine dead ends from exhaustive prior searches. Listed so an audit doesn't waste time re-discovering the same wall:

- **Enigma chest replacement mechanism**: real enum/blocks exist (`VaultChestType.ENIGMA`), but **no code path in either pinned jar places or rolls one into a generated structure** — exhaustive search (~6500 files, 136 tool calls) came up empty. Three honest possibilities (server-side datapack config not in the client jar; registered-but-unwired in this pinned version; player terminology mismatch), none confirmed. Treated as absent, not modeled. `MECHANICS_NOTES.md`.
- **Upstream theme/palette-pool selection** (which of the ~69 `*_common_rooms.json` pools becomes active for a given vault/floor): confirmed to be a vault/floor-wide choice via `VaultGridLayout`/`ClassicVaultLayout`, but the weighting mechanism itself was never traced. Irrelevant to chest *counts* (68 of 69 themes are weight-identical on room-shape selection; only `dark_cavern` differs, and only in shape-family weighting, not chest mechanics) — relevant only if cosmetic/palette accuracy is ever added. `MECHANICS_NOTES.md`.
- **Mirroring** (`NONE`/`FRONT_BACK`) during room assembly: real, not modeled, deliberately — provably doesn't affect footprint, interior volume, or chest counts (§2).
- **Mapped Vault's full 19-modifier-class bundle**: confirmed real and triggerable via `VaultMapItem`/`applyCrystalRecipe()` (`MECHANICS_NOTES.md`), but only the 2 classes this document covers (decorator add/cascade settable) are modeled — the other 17 (`VaultLootableWeightModifierSettable`, `TrapChanceModifierSettable`, `MobSpawnModifierSettable`, etc.) are out of scope for a chest-counting tool.
