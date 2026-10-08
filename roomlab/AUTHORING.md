# Building a room with an agent

How the extraction room was made, as a repeatable loop. The agent writes a generator script; the human
art-directs from the 3D preview. Rooms are code, so every change is a reviewable diff and any version can
be regenerated exactly.

## 1. Brief

Write down, before any code:

- the room's job (combat arena, loot room, puzzle, transit) and what the player must be able to do;
- hard constraints from `ROOM_CONTRACT.md` (gates, doorway height, objective marker, spawners, type);
- the look in a few lines (materials, light level, one-colour or palette-driven);
- what must stay clear (the extraction arena floor is dead flat so combat reads cleanly).

The agent should ask the human about anything the brief leaves open rather than guess.

## 2. Generator script

Copy `examples/extraction1/generate.py` to `rooms/<name>/generate.py` (or anywhere) and replace its
`build()`. Rules that keep it maintainable:

- every structural parameter is a named constant at the top (radius, floor height, counts, angles);
- geometry is deterministic; a `--seed` only drives texture noise (rock mottle), so a seed change never
  moves a wall;
- build order: fill with stone → carve the main volume → shape (domes, pillars) → decorate → carve
  entrances and passages → place spawners and light → place the four gates last;
- recolourable parts use one stand-in block; the palette picks the colour.

The `Room` DSL (`room.py`) covers the shapes the shipped rooms need: `box`, `disc`, `cylinder`, `dome`,
`blob`, `spire` (organic), `prism` (crystal), `tube` (gap-free passages at any angle), `replace`.
`r.set(x, y, z, "block[state=...]")` handles anything else.

## 3. Lint after every generation

```
python generate.py out.nbt && python ../../lint.py out.nbt
```

A lint failure is a real break (it passes every room the pack ships). Fix it before previewing.

## 4. Preview, and let the human judge it

```
python build.py out.nbt --name <label> --palette <palette.json>
python serve.py
```

Then hand the human the URL (http://localhost:8430). **The agent should not judge looks from
screenshots in a loop**: build, confirm the build succeeded and the HUD timestamp is fresh, and ask the
human what to change. Useful things to report alongside: lint's open-volume count, the arena light levels
(`build.py` prints the emitter count and lit-cell count), and anything `build.py` warned about (missing textures,
unknown luminance).

Iterate by changing constants, not by hand-editing cells.

## 5. Palettes

A palette is a list of `tile_processors`; `weighted_target` swaps one block for a weighted set. Write one
palette per colour variant (see `examples/extraction1/palettes/`). Weight a light-emitting member into each
family if the room relies on it (the extraction palettes weight gelatin in, the only crystal-family block
that emits light). Preview each palette with `build.py --palette`.

## 6. Ship it

Follow `examples/extraction1/WIRING.md`: structure path, template registration, palette registration (two
places), room pool, then the in-game checks in `ROOM_CONTRACT.md` "Proving a room loads".
