# Vault room contract

Every 47³ vault room the pack ships agrees on the facts below (measured across 36 common, 30 omega,
37 challenge and 3 special rooms). A room that breaks one of them will either fail to load, fail to
connect to its tunnels, or behave differently from every other room. `lint.py` checks the first five
rows; the rest are conventions you have to follow by hand.

| Constraint | Value | Checked by lint |
|---|---|---|
| File | vanilla structure NBT, gzip, `DataVersion 2975` (Minecraft 1.18.2) | yes |
| Size | exactly `[47,47,47]`, **dense**: all 103,823 cells listed, no `structure_void` | yes |
| Gates | 4 × `the_vault:placeholder[type=gate]` at `(0,24,23)`, `(23,24,0)`, `(23,24,46)`, `(46,24,23)` | yes |
| Gate `facing` | **inward**: west wall → `east`, north wall → `south`, south wall → `north`, east wall → `west` | yes |
| Doorway | a 3 wide × 5 tall air opening at each gate, `y` 22–26, ±1 along the wall | yes |
| Connectivity | every gate reaches every other gate through air | yes |
| Placeholder types | only types `PlaceholderBlock.Type` defines | yes |
| Tunnels | 11 × 11 × 47 with their own gate at `(5,6,0)`; this is what fixes the doorway height | no |
| Walkable floor at the gates | `y = 22` (the top solid block is y 21) | no |
| Objective marker | `the_vault:placeholder[facing=up,type=objective]` where a room hosts an objective block; `boss1` puts its one at `(23,20,23)`. An unclaimed placeholder becomes air (`global/remove_placeholders.json`). | partly (notes its absence) |
| Spawners | place `ispawner:spawner` directly in the room; palettes configure it (`the_vault:generic/spawner_base` sets the timer, `generic/challenge_elite_spawners` the mobs) | no |
| Room type | decided by the path: `.../rooms/special/...` makes a `SPECIAL` room (compass, map, discovery events). The `RoomType` enum is base-mod and cannot be extended. | no |

## Gotchas

- **Spawner marker blocks are not a convention.** The `minecraft:deepslate_coal_ore` → `ispawner:spawner`
  conversion exists in exactly one palette (`aquarium/spawner_settings.json`). Rooms place spawners
  directly, as the `crystal_caves` decor pieces do. Relying on the ore marker gives you ore.
- **Palettes are registered twice.** A palette file under `gen/1.0/palettes/` does nothing until it is
  also listed in `config/the_vault/gen/palettes.json`. Nothing warns you.
- **Colour comes from the palette, not the room.** Author recolourable parts in a stand-in block
  (the extraction room uses `minecraft:white_wool` for every crystal) and let the palette pinned by the
  pool entry swap it, so one `.nbt` gives several colour variants.
- **Light comes from `minecraft:light` blocks.** Crystal blocks emit nothing; place light blocks in the
  air next to them (see `glow_in_air` in the extraction generator).
- **Order of carving matters.** Carve entry passages and openings last, after decoration, so nothing can
  block a doorway; place spawners and gates after the carve, or it deletes them.
- **Diagonal passages:** never sweep a perpendicular offset in integer steps. At 45° the offsets round
  onto the same cells and the passage comes out as a narrow staircase. Use `Room.tube()`, which tests
  distance to the axis.
- **Diamond cross-sections quantise hard** (radius < 1 → 1 cell, 1–1.9 → 5, 2–2.9 → 13). A wide base with
  a steep `sharpness` gives three width tiers and reads as a needle; a small base reads blunt.

## Proving a room loads

The linter shows a room matches the shipped rooms' conventions. It does not prove Minecraft accepts the
file. Before building anything on a new room:

1. Copy the `.nbt` into a creative world's `generated/<namespace>/structures/` and load it with a
   structure block (47 is under the 48-block limit).
2. Generate it in a vault through a temporary weighted pool entry (see `examples/extraction1/WIRING.md`)
   and walk every entrance.
