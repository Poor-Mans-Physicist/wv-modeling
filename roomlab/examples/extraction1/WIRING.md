# Wiring a custom room into the pack

How to get a finished `.nbt` and its palettes into a Wold's Vaults pack checkout so vaults generate it.
Paths are relative to the pack repo root (github.com/iwolfking/Wolds-Vaults). The example is the
extraction room; substitute your own names.

## 1. Files to add

**a. The structure**

```
config/the_vault/gen/1.0/structures/vault/rooms/special/extraction1.nbt
```

The `/special` path segment matters: the game classifies rooms by path substring, and this makes it a
`SPECIAL` room (visible to the compass, the vault map and discovery events). Other choices are `common`,
`challenge`, `omega`, `raw`.

**b. Register the template** in `config/the_vault/gen/templates.json`:

```json
{
  "id": "the_vault:vault/rooms/special/extraction1",
  "name": "Extraction Site",
  "1.0": {
    "type": "structure",
    "path": "config/the_vault/gen/1.0/structures/vault/rooms/special/extraction1.nbt"
  }
}
```

**c. The palettes**: copy them to `config/the_vault/gen/1.0/palettes/extraction/{idona,tenos,velara,wendarr}.json`,
**then register each one** in `config/the_vault/gen/palettes.json`. An unregistered palette silently does
nothing.

```json
{
  "id": "the_vault:extraction/tenos",
  "name": "Extraction Tenos",
  "1.0": "config/the_vault/gen/1.0/palettes/extraction/tenos.json"
}
```

**d. The room pool**: `config/the_vault/gen/1.0/template_pools/vault/rooms/special/extraction.json`,
modelled on `challenge/crystal_caves.json`:

```json
[
  { "weight": 1, "value": { "template": "the_vault:vault/rooms/special/extraction1",
                            "palettes": ["the_vault:extraction/tenos"] } }
]
```

Add one entry per colour, or pin one colour per vault; that is a design choice.

**e. Packwiz**: add every new file to `index.toml` with its hash and bump `pack.toml`. On Windows with
`core.autocrlf=true`, a bare `packwiz refresh` hashes the CRLF working copies and writes hashes that do
not match what GitHub serves; hash the LF blobs instead.

## 2. Making it generate

**Quick look**: add a weighted reference to an existing room pool and play a few vaults:

```json
{ "weight": 1, "reference": "the_vault:vault/rooms/special/extraction" }
```

Probabilistic and disposable; revert it before shipping.

**Placed by an objective**: register on `CommonEvents.LAYOUT_TEMPLATE_GENERATION` and call
`data.setTemplate(...)` for the regions you choose. Precedents in the addon
(github.com/iwolfking/Wolds-Vaults-Official-Mod): `HyperVaultObjective` forces the boss arena at a
start-relative region; `CorruptedVaultHelper.generateMonolithRoom` does the same with a palette override.
Both force exactly one room at a fixed region; choosing several regions deterministically from the vault
seed is new code.

## 3. Other things a special room usually needs

| Need | How the game does it elsewhere |
|---|---|
| No block breaking in the room | `WorldZonesData.get(server).getOrCreate(dim).add(new WorldZone().add(cuboid).setModify(false))`, as the Wild West, Raid, Temple and Elite challenge managers do. `IAllowZone` exempts specific blocks. |
| Spawner mobs | a palette entry configuring the placed spawners: `the_vault:generic/spawner_base` (timer only) or `the_vault:generic/challenge_elite_spawners` |
| A map icon | a room-icon entry in the addon's vault-map icon provider plus a PNG |
| An objective block | an objective placeholder in the room, or an objective that spawns its own block or entity (the extraction room does the latter) |

## 4. Verify in this order

1. `python lint.py extraction1.nbt`.
2. Load it with a structure block in a creative world (copy to `generated/<namespace>/structures/`).
3. Generate it in a vault through the temporary pool entry and walk every entrance and passage.
4. Only then build objective logic on top.
