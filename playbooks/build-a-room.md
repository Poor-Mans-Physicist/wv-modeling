# Playbook: design and build a vault room

Requests like "make a boss arena room", "build a 3-variant puzzle room", "change the extraction room's dome".

1. Read `roomlab/ROOM_CONTRACT.md` and `roomlab/AUTHORING.md` in full before writing code.
2. Agree a brief with the human (`AUTHORING.md` §1). Ask about anything open: purpose, entrances,
   objective, spawners, colour variants, light level.
3. Start from `roomlab/examples/extraction1/generate.py`; put the new generator in its own folder. Named
   constants at the top, deterministic geometry, gates last.
4. After every generation run `python roomlab/lint.py <room.nbt>`. Never hand a room that fails lint to
   the human.
5. Preview: `python roomlab/build.py <room.nbt> --name <label> --palette <palette.json>` then
   `python roomlab/serve.py`. Give the human http://localhost:8430 and ask what to change. Do not
   iterate on looks by screenshot yourself; the human is the art director.
6. When the human approves, follow `roomlab/examples/extraction1/WIRING.md` to put it in a pack checkout,
   and tell them the room has not been loaded in game until they do the checks in
   `ROOM_CONTRACT.md` "Proving a room loads".

`build.py` needs `WV_INSTANCE` and `MC_CLIENT_JAR` (run `python setup/doctor.py`). If they're missing,
generation and lint still work; tell the human the preview is blocked and why.
