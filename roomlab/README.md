# roomlab

Author, validate and preview 47×47×47 Vault Hunters rooms without opening the game. Built for hand-made
special rooms; the extraction room in `examples/extraction1/` was made with it from scratch.

```bash
python examples/extraction1/generate.py out.nbt --seed 7         # generate (writes a .nbt)
python lint.py out.nbt                                           # validate against the room contract
python build.py out.nbt --name myroom \
       --palette examples/extraction1/palettes/extraction_tenos.json   # compile for the viewer
python serve.py                                                  # then open http://localhost:8430
```

Viewer: click to capture the mouse, **WASD** move, **space/shift** up and down, **scroll** to change
speed, **esc** to release. The room picker lists every room `build.py` has compiled into `web/`.

`build.py` needs textures from your own game install: set `WV_INSTANCE` to your Wold's Vaults instance
folder (the one holding `mods/`) and `MC_CLIENT_JAR` to the Minecraft 1.18.2 client jar.
`python setup/doctor.py` finds both. Generation and lint need neither.

- `ROOM_CONTRACT.md`: the format every vault room must satisfy (what `lint.py` checks, and why).
- `AUTHORING.md`: the workflow for building a new room with an agent.
- `examples/extraction1/`: a finished room, its generator, palettes, and how to wire it into the pack.

**The HUD shows a `built` timestamp.** If it does not match the build you just ran, you are looking
at a stale page. Serve with `serve.py`, not `python -m http.server`: the stdlib server sends no
`Cache-Control`, so browsers reuse the old room JSON and atlas across a normal reload. `serve.py` sends
`no-store`, and the viewer also cache-busts every fetch.

## Pieces

| File | What it does |
|---|---|
| `nbtio.py` | Type-preserving NBT read/write. Round-trips 13/13 real vault rooms byte-for-byte. |
| `room.py` | The `Room` DSL: `box`, `disc`, `cylinder`, `dome`, `blob`, `spire`, `prism`, `tube`, `wall_cell`, plus the gate constants. |
| `lint.py` | Room contract checks: gate positions and inward facings, doorway carve, 47³ density, placeholder-type validity, four-gate connectivity. |
| `blockdefs.py` | Block id → per-face textures, transparency, luminance, indexed from your instance's mod jars plus the vanilla client jar (cached in `cache/roomlab/`). |
| `palette.py` | Minimal interpreter for the_vault's `weighted_target` tile processors, so the preview is skinned the way the game will skin it. |
| `build.py` | Room `.nbt` → texture atlas + room JSON with baked 15-level block light, into `web/`. |
| `serve.py`, `web/` | three.js freecam viewer. |

## Notes worth knowing

- **Lighting is real.** `build.py` runs a 15-level flood fill from every emissive block, decaying 1 per
  block and stopped by opaque blocks, so a dark cave previews as a dark cave. The `ambient` slider is the
  whole-vault ambient light the theme contributes on top (cave themes ship `0.2`).
- **Crystals do not glow on their own.** `auxiliaryblocks:CrystalBlock` extends `StainedGlassBlock` and
  never sets a light level. The `*_gelatin` blocks are light level 8. Anything brighter has to come from
  colocated `minecraft:light` blocks, which is what the shipped `crystal_caves` rooms do (279 of them in
  `crystal_caves1`).
- **Luminance is not in the assets.** It lives in each mod's Java, so `blockdefs.LUMINANCE` is a
  hand-maintained table. Add to it when a room uses an emissive block that renders flat here.
- **Texture coverage is ~94 %** of the 7,542 distinct block states across the pack's 1,143 room and decor
  files. The misses are dynamic-model blocks (Create copycats, Supplementaries wall lanterns); they render
  as gaps and `build.py` warns about them by name.
- Blocks are drawn as **full cubes**. Slabs, stairs, panes and plants have the right texture but the wrong
  shape. Fine for layout and mood, not for fine detail.
- `spire` sweeps a sphere (organic, lumpy); `prism` sweeps a diamond cross-section (flat faces, sharp
  tip). Use `prism` for anything meant to read as a crystal.
- Each room gets its **own atlas** (`atlas_<label>.png`); never mix a room JSON with another room's atlas.
