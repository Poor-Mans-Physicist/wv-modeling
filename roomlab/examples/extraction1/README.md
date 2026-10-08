# Example: the extraction room

The arena for Wold's Vaults' extraction vaults (a wave-defence objective in development, not yet
released). Built entirely with roomlab: `generate.py` regenerates `extraction1.nbt` exactly
(`python setup/check.py roomlab` verifies this).

| File | What it is |
|---|---|
| `generate.py` | the generator; every tunable is a named constant at the top |
| `extraction1.nbt` | the generated room, 47³, contract-valid |
| `palettes/extraction_{idona,tenos,velara,wendarr}.json` | one colour palette per vault god |
| `plan_*.png`, `lightmap_y20.png` | top-down plans and the floor light map from the design passes |
| `WIRING.md` | how to get a room like this into the pack so vaults generate it |

## Design brief (as built)

- Cavelike, black and grey vault stone; a fixed look, like an omega room, classified `/special`.
- A flat circular arena (air from y19, radius 18), reached from each of the four gates by a passage that
  steps down y22 → y19 inside the wall thickness. Nothing stands on the arena floor but the pedestal, so
  combat reads cleanly.
- A domed ceiling springing straight off the floor, apex y37.
- A 3×3, two-block-tall crystal pedestal at the centre. There is **no objective placeholder**: the
  extraction objective spawns its extractor entity on the pedestal top (y21), and an unclaimed
  placeholder would linger in a forced room.
- Four corner spawn chambers with 12 `ispawner:spawner` blocks (3 each), joined to the arena by passages
  at least 8 wide × 5 tall.
- Eight crystal veins, flush in the floor from the pedestal outward, then inset into the dome wall and
  converging at the apex. They are the arena's main light source. Eight crown crystals angle 41° down
  and inward, one over each entrance.
- Arena block light: min 5, mean ~10.4, max 13 of 15.

## Colour variants

One `.nbt`, four palettes. Every crystal is authored as `minecraft:white_wool`; the palette the pool
entry pins decides the colour.

| Palette | God | Colour |
|---|---|---|
| `extraction/idona` | Idona, The Malevolent | red |
| `extraction/tenos` | Tenos, The Omniscient | aqua |
| `extraction/velara` | Velara, The Benevolent | green |
| `extraction/wendarr` | Wendarr, The Timekeeper | gold |

Gelatin is weighted into every family on purpose: `auxiliaryblocks:*_gelatin` is light level 8 and is
the only member of each crystal family that emits light. All other illumination comes from
`minecraft:light` blocks, which are invisible in game.

## Changing it

Edit the constants at the top of `generate.py` (`DOME_R`, `FLOOR_Y`, `VEIN_COUNT`, `VEIN_START`,
`CROWN_COUNT`, `CROWN_Y`, `CROWN_LEN`, `CROWN_R`, `CROWN_TAPER`, `CROWN_SHARP`, `MOUTH_H`,
`MOUTH_HALF_W`, `CAVE_ANGLES`), regenerate, lint, preview. Entry passages and cave mouths are carved
after the crystals so nothing blocks a doorway; spawners are placed after that, or the carve deletes
them. If you change the room on purpose, regenerate `extraction1.nbt` too, or the roomlab gate in
`setup/check.py` fails.
