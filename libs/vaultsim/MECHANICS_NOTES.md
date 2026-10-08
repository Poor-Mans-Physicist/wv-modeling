# Vault simulator: mechanics research notes

Findings collected while building the simulator, cited from `SPEC.md`. Class and line citations refer to
a CFR decompile of the_vault 3.21.x; decompile your own jar to check one.

## Confirmed game-mechanics findings

### decorator_add / decorator_cascade (already implemented in the Rust core)

Ported and validated in `decorator.rs`; see `SPEC.md` §3–§6 for the full mechanic writeup.

### Strongbox (base `the_vault` mod) — fully researched

- Strongbox upgrade is **a single weighted roll at the exact same placeholder slot as a normal chest** — mutually exclusive, never an independent extra spawn, never both. Mechanism: `BernoulliWeightedTileProcessor` → `WeightedList.getRandom` picks exactly one outcome (plain chest, strongbox, or — structurally possible but never triggered in practice since every chest config uses `probability: 1.0` — a failure/air result) and overwrites that one position.
- Keyed by **vault level** (not reputation). Wooden chests have **no strongbox upgrade at any level** — no `wooden_strongbox` block even exists. Gilded/Living/Ornate do:
  - Gilded: ~5% at level 50, ~8.3% at 80, ~12.5% at 100.
  - Living: flat 5% from level 50 onward.
  - Ornate: ~5% at 50/65, ~8.3% at 80, ~12.5% at 100.
- **decorator_add ("Bonus X") chests of matching type CAN roll into strongboxes** — they route through the identical placeholder/tile-processor pipeline as static room chests (confirmed: the `decorator_add` modifier configs for gilded/living/ornate use `output: "the_vault:placeholder[type=...]"`, a real placeholder block). Bonus Wooden cannot, consistent with the no-upgrade rule above.
- **decorator_cascade chests can never become strongboxes, and strongboxes can never be cascaded** — not because of ordering/priority, but because cascade copies an existing block-entity's exact state directly (never touches the roll logic at all), and a strongbox is a genuinely distinct block id (`the_vault:gilded_strongbox` etc.) that no cascade modifier's `filter` (an exact block-id match) ever names. This is a correctness-by-construction result, not a special-cased exclusion.
- **No separate strongbox counter exists.** Confirmed no distinct `VaultChestType` enum value, no separate stat key, no distinct icon. A strongbox of a given type increments the exact same counter as a plain chest of that type and rarity.

**Modeling implication for the simulator**: strongboxes don't need their own UI category or separate counter — they're already counted as their base chest type. What needs modeling is just the upgrade *roll itself*: after resolving a baseline POI chest or a decorator_add chest of an eligible type (gilded/living/ornate, not wooden), roll the level-bracketed strongbox probability and flag that one slot as a strongbox for loot-table purposes — this doesn't change the "total target chests" count at all, it only matters if the app ever wants to separately report "of these, N are strongboxes" as a bonus detail (not currently in the spec, but cheap to add given the mechanic is now fully understood).

### Room entrance/exit positions — CONFIRMED

Doorways are marked by `the_vault:placeholder` blocks with blockstate `type=gate` (a `PlaceholderBlock$Type` enum value, confirmed in the decompiled class). Tunnels are **not jigsaw-assembled** — there are only 6 fixed tunnel templates (`gen/1.0/structures/vault/tunnels/tunnel{1-6}.nbt`, each exactly 11×11×47, zero jigsaw blocks, reused verbatim across every biome theme), each with exactly 2 gate placeholders at its own two ends (local `(5,6,0)` facing north, `(5,6,46)` facing south).

Scanned all 118 room `.nbt` files across every category. **95 (80%) have exactly 4 gate placeholders** (one per cardinal wall); the other 23 are standalone/dead-end rooms (vendors, graveyards, treasure vaults, "raw" cave rooms, `lost_void`) with zero gates — these don't connect to a tunnel on any side and should be treated as having no entrance/exit stat at all, not an error.

For any room that does have gates, the rule is exact and nearly invariant across all 95:

| Wall | Local position | Facing into room |
|---|---|---|
| West (X=0) | (0, 24, 23) | east |
| East (X=46) | (46, 24, 23) | west |
| North (Z=0) | (23, 24, 0) | south |
| South (Z=46) | (23, 24, 46) | north |

- X/Z is **100% invariant** across all 95 gated rooms — always the wall-plane coordinate (0 or 46) crossed with the room's exact midpoint (23) on the free axis. Zero exceptions.
- Y is 24 in 93 of 95 gated rooms. Two named outliers: `omega/hellish_digsite.nbt` (all 4 gates at Y=23) and `omega/wolds_dinner.nbt` (west gate at Y=25, other 3 at Y=24) — read each room's own gate placeholders directly rather than hardcoding Y=24 universally.
- Doorway opening extent: 3 blocks wide (gate ±1 along the wall) × 5 blocks tall (gate Y ±2), centered on the gate block.
- **Floor reference for the "within 6 blocks vertically" stat**: the walkable floor sits roughly 2-3 blocks below the gate's Y, and the exact offset varies by room theme (confirmed via direct inspection: `cliffs1` floor at gate_Y−1, `arcade` floor at gate_Y−3). Recommend detecting the room's own actual floor height at that wall position from the assembled voxel grid directly (already have the data) rather than assuming a fixed offset.

**Implementation note**: look up gate positions per-template (parse once at load time, same way chest placeholders are already parsed) rather than hardcoding — a handful of rooms deviate, and 23 have none at all.

### Common vs ore room weighting — CONFIRMED, and the original framing was wrong on both axes

There is no "common vs ore" split anywhere in the game's logic — that framing doesn't match how the game actually works, on either of the two axes it could refer to:

- **Not a theme choice.** The ~69 `*_common_rooms.json` theme-pool files (beach, dark_cavern, gingerbread, nether_crimson, etc.) aren't competing per-room — exactly one becomes the active `ROOM_POOL` once per vault/floor (`ClassicVaultLayout`/`VaultGridLayout`), via a separate, not-yet-traced theme-selection layer. There's no standalone "ore theme."
- **Not a separate RoomType-driven generation path either.** `RoomType.ORE` (the enum `DecoratorAddModifier`'s whitelist checks) is a **post-hoc classification, not a generation mechanism** — confirmed directly in `RoomCache.getMapFor()`, which buckets an *already-chosen* room template by checking whether its resource path string contains the substring `/ore`. A room drawn from `the_vault:vault/rooms/common/ore1` gets retroactively tagged `RoomType.ORE` purely because of its filename — it went through the exact same weighted pick, from the exact same pool, as every other common-room shape. (This is moot anyway for this pack specifically: the room-type whitelist that `RoomType` feeds is already force-disabled by woldsvaults' own Mixin per earlier research, so this classification doesn't gate decorator_add/cascade behavior here regardless.)
- **What "ore" actually is**: one of 9 room-shape families (bee, cliffs, glowstone_lakes, lakes, mushroom_forest, mustard, ore, pirate, rainbow_forest), each with 4 NBT variants — 36 physical structure files total, the same 36 in every theme. 68 of 69 theme pools reference these identical 36 leaf entries (just with theme-specific palette skinning, not different geometry), each shape-family at equal weight in 67/69 themes — making ore **4/36 ≈ 11.1%** of common-room draws in the overwhelming majority of cases. (Two named exceptions: `dark_cavern` doubles ore's weight to 8/40 = 20%; `chaos` has no ore variant at all, 0%.)
- **Practical conclusion for the simulator**: this needs no special "common vs ore" toggle or split at all. The existing simulator already enumerates these same 36 `rooms/common/*.nbt` files — sampling uniformly across all 36 for the 5x5 grid already reproduces the correct, real ~11% ore rate as a natural side effect, with zero extra logic. The ~11% figure is coincidentally close to the "~10%" the user remembered, just attached to a different decision point (a shape-family draw within a theme) than the "common vs ore room-type split" framing assumed.
- **Not chased further** (a separate, deeper question, explicitly not guessed at): the upstream weighting of which of the ~69 themes becomes active for a given floor in the first place. Not needed for the "25 independent rooms" feature as scoped (which doesn't model theme/palette skinning), but would matter if a future version wants theme-accurate cosmetics.

### Mapped Vault — CONFIRMED. Enigma chest replacement — genuinely could not be found, after an exhaustive search; treat as absent, don't model it

**Mapped Vault is confirmed**: triggered by `xyz.iwolfking.woldsvaults.items.gear.VaultMapItem` (an addon item, registered under the base mod's own `the_vault:map` id), a gear-slot item that rolls a tier, then gets consumed at a crystal-workbench-style anvil (`applyCrystalRecipe()`) to imbue a vault crystal with a `THEME`/`THEME_POOL`/`OBJECTIVE` plus a bundled set of the addon's own modifier classes (`xyz.iwolfking.woldsvaults.modifiers.vault.map.modifiers.*` — 19 classes, including `DecoratorAddModifierSettable`, `CascadeDecoratorModifierSettable`, `VaultLootableWeightModifierSettable`, `TrapChanceModifierSettable`, `MobSpawnModifierSettable`). There's no simple boolean flag — "Mapped Vault" *means* "entered with a crystal that has this map-modifier bundle attached," structurally the same way any other vault modifier works. This is real and modelable.

**Enigma chest replacement — could not be found in either pinned jar, despite an exhaustive search (full decompile of both mods at the exact pinned versions, ~6500 files grepped, GitHub source cross-checked, 136 tool calls).** Reporting this plainly rather than guessing at a mechanic, per this project's standing rule. What's actually confirmed:

- "Enigma chest" is a **base-mod** concept (`iskallia.vault`), not an addon-specific one — corrects an assumption baked into the original research brief. `VaultChestType.ENIGMA` is a real, distinct enum value; `ModBlocks.ENIGMA_CHEST`/`ENIGMA_CHEST_PLACEABLE` are real, registered blocks.
- **Critically, `ENIGMA_CHEST_PLACEABLE` is not a `PlaceholderBlock.Type` value** (unlike wooden/gilded/living/ornate chests) — so structurally, it *can't* go through the same `VaultLootTileProcessor`/`BernoulliWeightedTileProcessor` roll pipeline that strongbox uses. Whatever triggers an Enigma chest to actually appear in a generated vault, it isn't that mechanism.
- Every reference to Enigma found in either jar (12 hits) is inert plumbing — block/item registration, renderer textures, tooltip formatting, a JEI hide-list entry, tool-affinity matching, ability-targeting color config. **No code path anywhere places or rolls an Enigma chest into a generated structure.**
- Three honest possibilities, none confirmed: (a) the actual trigger lives in server-side datapack config that gets synced at runtime rather than shipped in the client jar (some configs in this codebase are explicitly `@Expose`-annotated for exactly this kind of override, which would explain why a client-jar-only search comes up empty); (b) the feature is registered but not currently wired up to spawn in this exact pinned version; (c) "Enigma chest" in player terminology maps to something not matched by that name in code. **Cannot confirm or deny the user's "enigma replaces more chest types than strongbox" belief at all — there's no replacement logic to check it against.**
- Side finding, not the mechanic in question: there's also an unrelated `ENIGMA_EGG` addon item (a lootbox item like `CATALYST_BOX`) — shares the name, nothing to do with chest-type replacement, don't confuse the two.
- Cascade interaction: unconfirmed for the same reason (the placement mechanism itself is missing), but Enigma does have its own distinct block id, so *if* cascade filtering ever applies to it, the same "distinct id ⇒ no existing filter matches it" logic that protects strongboxes would likely apply by structural analogy — not verified, just a plausible inference, flagged as such.
- Counter: Enigma chests *do* get their own genuine `VaultChestType.ENIGMA` bucket in the generic per-type stat system (unlike strongbox, which fully merges into its base type) — but the standard end-of-vault summary UI explicitly excludes displaying Enigma/Altar/Treasure/Hardened/Flesh stats, so a player would never see it even though the data exists.

**Recommendation**: model Mapped Vault as a real toggle (it's confirmed and modelable via the 19-modifier-class bundle, if/when that's worth the scope). For the Enigma-replaces-chests half of the spec specifically — ship v1 without it; there's nothing verified to model. If you have access to this pack's server-side config/datapack files (not just the client jars), that's the concrete next place to look, per the research agent's own suggestion — worth checking before spending more decompile effort on this specific question.


## Correction 2026-09-23: modifier event order (the_vault 3.21.6)

Bonus and cascade modifiers now run through `src/schedule.rs` (`apply_schedule`) instead of the per-room
`decorator_add_pass` / `decorator_cascade_pass` loops.

**What the game does:**
- Every region overlapping a chunk is placed in x-then-z order.
- Each placement's TEMPLATE_GENERATION POST event runs every bonus modifier over the whole chunk.
- The same event then re-cascades every non-duped chest in the chunk.

**Effect:** a room gets extra bonus attempts and extra cascade rounds in the chunks it shares with the tunnels
and empty cells placed after it.

**Evidence:** the old passes under-counted six recorded living vaults by 18-35 % chests and 30-58 % clumpiness
per template. The schedule brings them within the spread between vaults run with identical crystals.

**Consequences:**
- Rooms are placed on even/even regions (`random_room_region`).
- Bonus chests now need exactly air with air above, matching the game.
- The map's settable cascade is one more cascade modifier with a single stack.
