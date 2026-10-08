# Mechanics library

Reference docs for game mechanics the models depend on, verified against configs and game code at the
time of writing (each doc says which version). They describe the game, not the models; for a model's own
assumptions see its spec.

| Doc | What it covers |
|---|---|
| `CHEST_LOOT_GENERATION_SPEC.md` | how vault chest loot is generated end to end: loot tables, tiers, loot-info groups, which layer to edit |
| `CRAFTING_MATERIALS_REFERENCE.md` | where crafting materials come from (drop rates, ore weights), what consumes them, research costs, fluid machines |
| `ATTRIBUTE_CAPS.md` | the lucky-hit and AoE attribute cap overrides: who reads them and what the live caps are |

Code citations (`Class.method`, line numbers) refer to a CFR decompile of the_vault or to the open-source
addon (github.com/iwolfking/Wolds-Vaults-Official-Mod).
