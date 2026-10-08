# Crafting Materials Reference — vault drops, tech intermediates, fluids

Where every bulk crafting material in the pack comes from, how hard it is to farm, and what already
consumes it. The reference covers:
- **Vault-native materials:** the_vault plus the Wold's Vaults addon.
- **Tech intermediates:** pack-modified recipes, with the research that gates each mod.
- **Bulk fluid machines.**

It was compiled 2026-09-30 for costing new recipes and is written to be reusable for any recipe design.

**Confidence:** facts are **VERIFIED** from files unless marked **(L)** = likely, from mod knowledge,
not checked. Every material stacks to 64 (no non-64 `stacksTo` on any the_vault material; fluid
buckets stack to 1).

## 0. How to read the drop numbers

- **E/c** = expected items per chest at base Item Quantity / Item Rarity. It is computed as
  roll count × sub-pool share × item share × mean count. It does **not** include IIQ/IIR or the WV
  log-quantity mixin.
- **Chest brackets** come from the `generic/*_placeholder` palettes:
  - `wooden_chest_30`
  - `gilded_chest_50` (strongbox 1 in 20)
  - `living_chest_70`
  - `ornate_chest_65`
  - `_100_map` variants on maps
- **Completion crates** (`loot_table.json`): boss = `base_crate_100`, cursed objectives =
  `base_crate_cursed_100`.
- **Ore rates** come from `generic/ore_placeholder` (level 40+).
  - 12 % of ORE markers become ore.
  - Weights out of 706:

    | gem | weight | share |
    |---|---|---|
    | larimar | 300 | 42 % |
    | benitoite | 120 | 17 % |
    | painite | 100 | 14 % |
    | wutodie | 80 | 11 % |
    | alexandrite | 40 | 5.7 % |
    | black opal | 20 | 2.8 % |
    | each of the 9 POG gems | 5 | 0.7 % |
    | echo | 1 | 0.14 % |

  - Theme overrides:
    - nether: each POG gem 24/1012
    - void: echo 20/747
    - blood moon: painite 300/916
    - ice: larimar 500/936
- **Ore drop overrides** (`kubejs/data/the_vault/loot_tables/blocks/ore_*.json`): each ore drops its
  gem 82–95 % of the time (fortune applies). Otherwise it drops 1 `woldsvaults:smashed_vault_gem`.
- **Not measured anywhere:** chest-type counts per vault and ore blocks per vault.
  - Cross-type comparisons (a gilded drop vs a wooden drop, or an ore gem vs a chest drop) therefore
    need an assumption about relative frequency.
  - Within one chest table or within the ore table, ratios are exact.
- **Branch note:** the `greed_crate_loot_*` tables exist only on `greed-test-dist`. They are not
  counted here.

## 1. Series and structural metals

| id | name | sources | bulk | competing sinks |
|---|---|---|---|---|
| `the_vault:driftwood` | Driftwood | wooden_chest_30 sub0 w2/40 ×1 and sub1 w9/15 ×1–2, **E/c 2.9** (map 4.2). Gladiator crate (completion_crate_20) ~24. supply_box w64/1338 ×8–32 | easy — every wooden chest | 30 tool recipes (2–64), mod box 16–32, greed cauldron 750–1,250, 30 kubejs refs |
| `woldsvaults:infused_driftwood` | Infused Driftwood | Vault Infuser: 4 Vault Essence + 1 Driftwood | easy | **none found** |
| `the_vault:vault_plating` | Vault Plating | wooden_chest_30 sub0 w2/40 and sub1 w6/15 ×1, **E/c 1.55** (map 1.2). Gladiator crate ~20. supply box ×4–24 | easy | **almost none**: magnet/paxel repair, greed cauldron 600–1,000, 9:1 WV block, 0 kubejs refs. The least-contested series material |
| `the_vault:chromatic_iron_ingot` | Chromatic Iron Ingot | Overworld deepslate ore (y < −32, 1 in 8 chunks, veins 12/36). **Infinite Create Ore Excavation vein** (`kubejs/.../create_ore_excavation/veins.js`). supply box ×8–64 | trivial, automatable | steel, 9 per tool, 99 kubejs refs |
| `the_vault:carbon_nugget` / `the_vault:carbon` | Raw Carbon / Refined Carbon | ornate_chest_65 sub0 w6/54 ×3–5 and sub1 w8/22 ×3–6, **E/c 8.4 nuggets**. Ornate strongbox 38. 9 nuggets = 1 carbon | easy | steel, gem box, greed cauldron 375–625 |
| `the_vault:chromatic_steel_ingot` | Chromatic Steel Ingot | Crafted from 4 Chromatic Iron + 2 Carbon → 1 (shaped `iii/icc`). Alternatives: IE arc furnace, PNC compressed-iron route. Loot negligible | easy (crafted; carbon-limited) | **230 kubejs refs**, the main modded-tech gate. Every AE2 processor uses 1. Also alloy, BCS, mod box 4–8 |
| `the_vault:black_chromatic_steel_ingot` | Black Chromatic Steel | 8 Chromatic Steel + 1 Perfect Black Opal → 1. omega_box w128/2056 ×8–16. braziers ≤ 0.12 | moderate | 26 recipes, 88 kubejs refs, 12 per tool, pogominium, AE2 singularity (4) |
| `the_vault:vaulterite_ingot` | Vaulterite Ingot | 2 Painite + 4 Vault Scrap → 1 (shapeless). No loot source | easy–moderate | alloy, repair core, unique codex |
| `the_vault:vault_alloy` | Vault Alloy | 2 Vaulterite + 2 Chromatic Steel → 1. Loot only ornate_100_map 0.17, hellish sand | moderate | 3–16 per gear piece (16 forge recipes), void stone |
| `the_vault:vault_scrap` | Vault Scrap | Recycler: 4–8 per gear piece, magnet, void stone, god charm. ornate_65 sub1 w2/22, E/c 0.45 | trivial (recycle gear) | vaulterite, greed cauldron 300–500 |
| `woldsvaults:chromatic_gold_ingot` | Chromatic Gold Ingot | 2 gold + Vault Essence block + Magic Silk | easy | vault ingot, 89 kubejs refs |
| `the_vault:vault_ingot` | Vault Ingot | chromatic iron + chromatic steel + chromatic gold + smashed gem cluster. Vault nugget ×7 from gladiator crate | moderate | few |

## 2. Gems

| id | name | source | bulk | competing sinks |
|---|---|---|---|---|
| `the_vault:gem_larimar` | Larimar | ore 42 % (ice theme 53 %) | trivial | catalyst infusion 16, inscriptions 128–256, greed 600–1,000. Perfect Larimar is in almost every pack-modified tech recipe (§5) |
| `the_vault:gem_benitoite` | Benitoite | ore 17 % | easy | overworld inscriptions 64 × 37, weaving up to 1,024 |
| `the_vault:gem_painite` | Painite | ore 14 % (blood moon 33 %), hellish sand | easy | vaulterite (2 each), inscriptions 32–64 |
| `the_vault:gem_wutodie` | Wutodie Gem — **lang says "(Legacy)"**, still placed by every ore palette | ore 11 % | easy | inscription 32, chroma core, pog prism, thermal_extra Shellite (extraordinary) |
| `the_vault:gem_alexandrite` | Alexandrite | ore 5.7 % | moderate | **192 per trinket** (37 recipes), thermal_extra Twinite (extraordinary) |
| `the_vault:gem_black_opal` | Black Opal | ore 2.8 %, haunted_brazier_lvl50 1.6, gate pearl recycle 10 % | moderate–hard | the Perfect form is needed for BCS and every gem cluster; decks 16 |
| `the_vault:gem_{iskallium,gorginite,sparkletine,ashium,bomignite,tubium,upaline,petzanite,xenium}` | the 9 POG gems (lang: Xeenium, Petezanite) | ore 0.7 % each (nether theme 2.4 %) | hard | POG, clusters |
| `the_vault:gem_puffium` | Puffium | **no ore palette places it; no loot source found** | none | — |
| `the_vault:gem_echo` | Echo Gem | ore 0.14 % (void theme 2.7 %), gem box 8/1,057, braziers 0.12 | very hard | echo POG, echoing ingot, inscriptions 2–48, thermal_extra Dragonsteel (perfect) |
| `the_vault:gem_pog` | POG | 1 of each of the 9 POG gems. crates ≤ 0.4, omega box w12 ×16–32 | hard | 30 recipes, 69 kubejs refs, augments 9–16, decks, infuser 9–16 |
| `the_vault:echo_pog` | Echo POG | 8 POG + 1 Perfect Echo. omega box w20/2056 | very hard | 76 kubejs refs, decks |
| `the_vault:omega_pog` | Omega POG | 1 chunk of each POG gem (chunk = 9 clusters; cluster = 8 gems + Perfect Black Opal) ≈ **648 POG gems + 324 Black Opal**. omega box w12/2056 | extreme | tools 9, jewels 9, decks, wold star |
| `the_vault:perfect_*` / `extraordinary_*` | Perfect / Extraordinary X | 4 gems → Perfect; 4 Perfect → Extraordinary. Perfect Black Opal also from dungeon pedestals 0.21 | ×4 / ×16 the gem | chroma core, pog prism, BCS, decks, tech recipes |
| `the_vault:vault_diamond` | Vault Diamond | gilded_chest_50 sub2 w14/23 and sub3 w450/1070, **E/c 0.46**. Gilded strongbox 1.7 + 1.9 nuggets. Gladiator crate 3.7. 9 WV nuggets | moderate | **heavily contested**: 64 per trinket (37 recipes), weaving 3–512, overworld inscription 256, mod box 12, alchemy table, 125 kubejs refs |
| `woldsvaults:vault_diamond_nugget` | Vault Diamond Nugget | wooden_30 sub3 w3/25 ×1–3 (E/c 0.07, map 0.49), gilded strongbox 1.9 | moderate | 9 → 1 Vault Diamond only |
| `woldsvaults:smashed_vault_gem(_cluster)` | Smashed Vault Gem (Cluster) | ore fallback 5–18 %; 4 → cluster | easy | gem box, vault ingot, greed 500–1,000 |
| Gem Box | (crafted: 4 smashed gems + 2 carbon nuggets + 2 vault diamonds + vault essence block) | weights out of 1,057: larimar 192, benitoite 128, wutodie/alexandrite 96, painite 72, POG gems 48 each, black opal 32, echo 8, POG 1. Rolls per box (L) | — | — |

## 3. Bulk drops and chest fodder

| id | name | source | bulk | competing sinks |
|---|---|---|---|---|
| `the_vault:soul_shard` | Soul Shard | every vault mob (`soul_shard.json`): default 3, horde 3–6, assassin 6–12, tank/guardian 20–30, champion 60–140. Gate pearl recycle 16–64 | trivial | Black Market only (1,500 per trade) |
| `the_vault:vault_essence` | Vault Essence | gilded_50 sub0 w8/46 ×1–2 and sub1 w10/36 ×2–4, **E/c 4.9** (map 7.4). Gilded strongbox 7 | easy | 53 recipes, 137 kubejs refs, inscriptions 64, chromatic gold, greed 750–1,250 |
| `the_vault:magic_silk` | Magic Silk | gilded sub1 w16/36 ×1–2, **E/c 1.9**. Gilded strongbox 11.4. Blueprint recycle 0–4 | easy | 64 per deck (24 recipes), prismatic fiber |
| `the_vault:knowledge_star_essence` | Knowledge Essence | living_70 E/c 3.45, living strongbox 6.4 | easy | knowledge-star research progression, memory powder |
| `the_vault:vault_{bronze,silver,gold,platinum}` | coins (block items) | coin_pile_30 bronze 5.05/pile. base_crate_100 gold 8.5 (cursed 17). 9:1 tiers | trivial / easy | gold is the main currency: gear 1–10, jewels up to 1,000, trinkets 32, shops |
| `the_vault:inscription_piece` | Inscription Piece | recycle inscription 2–4 | easy | inscriptions 4–64 |
| `the_vault:trinket_scrap` | Trinket Scrap | recycle trinket 1:1 | moderate | trinkets, molten trinket |
| `the_vault:silver_scrap` / `gemstone` | Silver Scrap / Gemstone | recycle jewel 5–8 / 33 %. wooden chests 0.49 | easy / moderate | jewel crafting (up to 10,000 / 2,560) |
| `the_vault:vault_catalyst_fragment` | Catalyst Fragment | recycle infused catalyst 4–6 | moderate | 9 → catalyst |
| `woldsvaults:arcane_essence` | Arcane Essence | guaranteed in every base crate: 3.0 (cursed 4.5, cursed_2_100 9). Also the Standard extractor job | easy | 9 → shard; expertise orb takes 8 shards |
| `the_vault:eternal_soul` | Eternal Soul | crates 3.5–9 | easy | infused soul (16 POG) |
| chest junk | Wooden Chunk 6.6 · Sandy Rocks 4.3 · Gilded Ingot 3.9 · Topaz Shard 1.7 · Living Rock 6.4 · Overgrown Wooden Chunk 2.3 · Mossy Bone 1.2 · Vault Meat 2.4 · Ornate Ingot 3.3 · Ornate Chain 2.2 (block) · Soot 4.4 (block) · Velvet 1.5 | E/c in each item's own chest type | trivial | **effectively none**: 4:1 deco blocks, diffuser, Soot 64 per augment. Good sink candidates |
| `woldsvaults:augment_piece` | Augment Piece | recycle augment 0–4 | moderate | 54 augment recipes (4–32) |
| `woldsvaults:soul_ichor` | Soul Ichor | tombstone 0.25, enigma_map 0.38 | hard | Black Market slot |
| `woldsvaults:chunk_of_power` / `dust_of_power` | Chunk / Dust of Power | Hyper rewards only (epic 1/120, omega 40/611), enigma_map 0.018. Crushing: 3 dust per chunk (4 on Thermal pulverizer) | very hard | zephyr charm, weaving, glue |
| `woldsvaults:wold_star_chunk` | Wold Star Chunk | treasure chests 0.06–0.08, hyper omega 10/611 | very hard | Wold Star (8 + Omega POG) |
| `woldsvaults:pog_prism` / `chroma_core` | Pog Prism / Chroma Core | 6 perfect gems + 2 POG + echo / 5 perfect gems + 4 chromatic gold nuggets | very hard / hard | augments, weaving 128, 45 WV recipes |
| `woldsvaults:pogominium_ingot` / `prismatic_fiber` | POG-ominium / Prismatic Fiber | infuser: 16 POG + BCS / 9 POG + magic silk block | very hard / hard | echoing ingot (16 echo + pogominium; kubejs removed its craft recipe), weaving |
| `woldsvaults:{yellow,blue,green}_vault_essence` | (no lang names) | essence block + chromatic gold / memory powder / vault moss. Blue also tenos treasure 5.4 | moderate | god rituals |
| `woldsvaults:nullite_fragment` / `nullite_crystal` | Nullite Fragment / Crystal | corrupted crate 1–8 fragments; Lost Depths ore 0.5 %. Also the Containment extractor job (branch only) | hard | crystal: hyper reward only |
| `woldsvaults:spark_of_inspiration`, `pogging/echoing_seed_base` | — | **no source or sink found** | — | — |
| `the_vault:vault_rock` | Vault Rock | Vault Altar (not loot); Thermal pulverizer on vault stone 12 % | — | — |

**Rankings:**
- **Most bulk-farmable:**
  1. Soul Shard
  2. Vault Bronze
  3. Vault Scrap
  4. Chromatic Iron
  5. Chest junk
  6. Raw Carbon
  7. Larimar
  8. Vault Essence
  9. Knowledge Essence
  10. Driftwood (Vault Plating close behind, with fewer competing sinks)
- **Late-game sprinkle candidates:** Omega POG, Echo POG, Echoing Ingot, Wold Star Chunk, Chunk of
  Power, Echo Gem / Perfect Echo, Pog Prism, Nullite Crystal.

## 4. Fluids that exist today

| fluid | made by | used by |
|---|---|---|
| `woldsvaults:molten_trinket` (`ForgeFlowingFluid`, tinted lava textures) | Trinket Scrap: Create superheated mixing 10 mB/scrap · Thermal Magma Crucible 20 mB/scrap (2,000,000 RF) · PNC Thermopneumatic Plant 30 mB (+100 mB plastic, ≥600 K, 2 bar) | Prismatic Glue |
| `woldsvaults:prismatic_glue` (custom `FlowingFluid`) | Create superheated mixing: 2 POG + 1 Dust of Power + 10 mB molten trinket → 10 mB. IF Dissolution: 4 POG + 2 dust + 10 mB → 30 mB. Glue Derrick extractor job (branch only) | Trinket Fusion Forge: 1,000 mB per fusion (tank 4,000). Via Create that is ≈200 POG + 100 dust per fusion |
| `the_vault:void_liquid` | Create Ore Excavation End fluid well (100 mB), Mekanism recipe | extruding vault stone |
| `the_vault:pyrite_flow` | **no source found** | — |
| `thermal:redstone` / `thermal:glowstone` / `thermal:ender` | Thermal Magma Crucible (Destabilized Redstone / Energized Glowstone / Resonant Ender) | Thermal recipes |

The pack has no KubeJS fluid registrations (`kubejs/startup_scripts`).

## 5. Tech intermediates (pack recipes)

**How to read this section:**
- **Vault content** = the vault materials the pack's recipe puts inside the item.
- **Ease:** T trivial, E easy, M moderate, H hard.
- **Research** gives the base KP (§6).

### 5.1 Recipes read directly

These were read in `scripts/*.zs` or `kubejs/server_scripts`:

| item | pack recipe | output | vault content |
|---|---|---|---|
| `ae2:logic_processor` / `calculation_processor` / `engineering_processor` | gold / redstone / diamond + silicon + 2 Larimar + **1 Chromatic Steel** (shaped; inscriber recipes removed) | 1 | 1 Chromatic Steel + 2 Larimar each |
| `ae2:formation_core` / `annihilation_core` | 2 Larimar + fluix block + logic processor + certus/quartz | 1 | +1 processor |
| `ae2:singularity` | 4 Extraordinary Larimar + 4 Black Chromatic Steel + 1 Vault Diamond Block | 1 | ≈32 Chromatic Steel + 4 Perfect Black Opal + 9 Vault Diamonds + 64 Larimar |
| `thermal:rf_coil` ("Redstone Flux Coil") | redstone block + 2 Perfect Larimar | 1 | 8 Larimar |
| `powah:crystal_niotic` | energizing: Vault Diamond + Carbon + Perfect Larimar (120k FE) | 3 | — |
| `powah:crystal_blazing` | energizing: 4 blaze powder + Perfect Larimar (27k FE) | 2 | — |
| `powah:capacitor_basic` | 4 dielectric paste + 2 Larimar + 2 tiny capacitor + Vault Essence | 1 | — |
| `powah:dry_ice` | energizing: 3 blue ice + Vault Essence + Perfect Larimar | 1 | — |
| `powah:dielectric_casing` | 4 Chromatic Iron + 4 dielectric rods + Chromatic Steel | 1 | — |
| `create:electron_tube` | polished rose quartz + redstone torch + Chromatic Iron nugget | 1 | — |
| `hostilenetworks:empty_prediction` | 4 glass panes + lapis + Chromatic Iron | 16 | — |
| `thermal_extra:shellite_dust` | 3 shulker shell + apatite dust + soul-infused dust + Extraordinary Wutodie | 4 | default smelter ingot recipe removed |
| `thermal_extra:twinite_dust` | 3 amethyst dust + apatite + 2 shellite dust + 2 Extraordinary Alexandrite | 4 | — |
| `thermal_extra:dragonsteel_dust` | 3 dragon breath + twinite dust + netherite dust + Perfect Echo + Perfect Black Opal | 4 | not mass-producible (dragon breath, netherite) |
| `thermal:{enderium,invar,signalum}_ingot` | Induction Smelter defaults, plus Mystical Agriculture essence recipes (2 / 4 / 4 per craft) | — | — |

### 5.2 Wider candidates, by theme

From the 2026-09-30 survey; **V** unless marked:
- **Power:**
  - Powah energized steel (chromatic steel + larimar + essence → 2)
  - CC&A capacitor (zinc + copper + vault diamond)
  - IE capacitor_lv (treated wood, lead, chromatic iron, redstone acid)
  - IE coil_mv (electrum + chromatic gold block)
  - IE generator
  - Flux core (flux dust + larimar + vault diamond + obsidian → 2)
  - Draconium core (DE, H: draconium + BCS + pog prism)
- **Electronics:**
  - RS advanced processor (+2 larimar + chromatic steel)
  - PNC transistor / PCB (M, multi-stage, 2 BCS)
  - IE electronic_adv (vault diamond + chromatic gold)
  - IE circuit_board (magic silk block)
  - ID variable (menril + 4 perfect larimar → 24)
- **Structural:**
  - IE steel plate / sheetmetal / rod (no vault content)
  - Create brass sheet
  - Create sturdy sheet (obsidian dust sequenced; no vault content)
  - Create andesite alloy (andesite + chromatic iron, ×2 craft)
  - PNC compressed iron (chromatic iron, 2 bar)
  - Thermal alloys
- **Heat:**
  - IE radiator block (4 steel sheetmetal + 4 copper plate + water → 4)
  - PNC heat sink
  - PNC vortex tube
  - Powah thermoelectric plate
  - IE HOP graphite
  - Thermal cured rubber
  - Create blaze cake
- **Field:**
  - IE wirecoil_steel, coil_hv (BCS)
  - Powah ender core
  - CC&A spools
  - PNC upgrade matrix
- **Mechanics:**
  - Create precision mechanism (5-loop sequenced assembly, ≈2 larimar + 1 perfect larimar per loop)
  - Create cogwheels (larimar)
  - Thermal gears (4 ingots + perfect larimar)
  - PNC pneumatic cylinder
  - PNC turbine blade (chromatic steel)
  - IE component_steel (steel plates + chromatic steel + vault diamond)
- **Gating leak (L):** IE's Metal Press gear molds (`metalpress/gear_steel.json`, 4 ingots →
  `forge:gears/*`) are not removed. They may bypass the larimar on Thermal gears. Untested.

## 6. Research gates (`researches.json`, `researches_groups.json`, `skill_gates.json`)

| research | base KP | group (+KP per research already owned in the group) |
|---|---|---|
| Create | 6 | BigMods +10 |
| Thermal Expansion | 4 | BigMods +10 |
| Immersive Engineering (also covers Immersive Petroleum, more_immersive_wires, controlengineering) | 6 | BigMods +10 |
| PneumaticCraft | 2 | BigMods +10 |
| Industrial Foregoing (also locks block interaction) | 6 | BigMods +10 |
| Mekanism, Botania, Occultism, Ars | — | BigMods +10 |
| Powah | 5 | Power +6 |
| Flux Networks | 2 | Power +6 |
| Applied Energistics 2 / Refined Storage / Integrated Dynamics | 2 | Logistics +2 |
| Hostile Neural Networks | 3 | Production +12 |
| Create Crafts & Additions | 4 | Addons +1 |
| Draconic Evolution | 16 | Omega +10 |

Other rules:
- Each team share adds +50 % to the cost.
- None of these mods has a prerequisite research, except Thermal Dynamos, which needs Thermal
  Expansion.
- Ungated: `thermal_extra`, `moreburners`, `mifa` and every `kubejs*` addon (they still need gated
  parts to build).
- KubeJS addons installed: kubejs-thermal, -create, -mekanism, -botania, -additions,
  -computercraft.
- CraftTweaker, CreateTweaker, Foregoing Tweaker and JEITweaker are installed.
- The pack already uses `event.custom` for IE, PNC, IF and Thermal recipe types.

## 7. Bulk fluid machines (Mekanism excluded)

| machine | mode | scaling | custom recipes | research | fit for "melt a gem" |
|---|---|---|---|---|---|
| **Thermal Magma Crucible** | item → fluid (`thermal:crucible`) | one op at a time, 8,000 mB output tank (MachineCrucibleTile); thermal_extra speed / tank augments (vault-gated; magnitudes L) | kubejs-thermal, JSON, CraftTweaker | Thermal Expansion 4 | **best** — purpose-built; WV already ships trinket → molten trinket on it |
| **IE Squeezer** (multiblock) | item → fluid (+item) | **8 concurrent** processes | `event.custom` JSON | IE 6 | very good for real bulk |
| IE Mixer / Refinery / Fermenter | fluid + items → fluid / 2 fluids + catalyst / item → fluid | 8 concurrent (mixer, fermenter) | JSON | IE 6 | mixer good for gem + carrier fluid |
| **Create Mixer + Basin** (heated / superheated) | items (+fluids) → fluid | 1 recipe per cycle (~56 t at 256 RPM); basin 2×1,000 mB in / out; scale by tiling | kubejs-create, CreateTweaker, JSON | Create 6 | good and cheap to tile; superheat needs Blaze Cake or a moreburners burner (heat level unchecked) |
| PNC Thermopneumatic Plant | item/fluid → fluid/item with temp / pressure gates | one op; speed upgrades (L) | JSON (pack uses it for larimar → plastic) | PNC 2 | good for a heat-gated tier |
| IF Dissolution Chamber | ≤ 8 items + fluid → item + fluid | MIFA tiers 2–4 | JSON (32 pack customs), Foregoing Tweaker | IF 6 | needs an input fluid |
| thermal_extra 2.0.3 | **no machines** (alloys, augments, tconstruct casting JSON only) | — | — | none | n/a |

**Crafters bigger than 3×3:** none is installed.
- Closest precedents:
  - Create Mechanical Crafter: arbitrary shaped grids, no pattern item.
  - IE Engineer's Workbench + inserted Blueprint.
  - PNC Assembly Controller + Assembly Program.
- AE2 / RS processing patterns can feed any block that exposes an item handler (L).

## 8. Files used

- **Pack:**
  - `config/the_vault/gen/1.0/loot_tables/*.json`
  - `config/the_vault/gen/1.0/palettes/generic/*_placeholder*.json`
  - `config/the_vault/{loot_table,vault_recycler,custom_vault_recycler,soul_shard,gem_box,supply_box,omega_box,hyper_objective,vault_diffuser,paxel,magnet_table}.json`
  - `config/the_vault/greed/greed_cauldron.json`
  - `config/the_vault/recipes/*.json`
  - `config/the_vault/{researches,researches_groups,skill_gates,research_exclusions}.json`
  - `kubejs/data/the_vault/loot_tables/blocks/ore_*.json`
  - `kubejs/server_scripts/vh_compat/**`
  - `scripts/*.zs`
- **Jars:**
  - the_vault: the the_vault 3.21.6 jar (recipes, block loot, lang)
  - the addon: the woldsvaults 0.31.6 jar
  - the instance `mods/` folder: Thermal Foundation / Expansion / Extra, IE, Create, PNC, IF, MIFA, CC&A, CDG, Powah, moreburners
- **Source:**
  - decompiled `iskallia/vault/{init/ModItems,ModFeatures,ModFluids,block/VaultOreBlock,research/ResearchTree}.java`
  - `Wolds-Vaults-Official-Mod/src/main/java/xyz/iwolfking/woldsvaults/{init/ModItems,ModFluids,ModBlocks,blocks/tiles/TrinketFusionForgeTileEntity}.java`
  - addon `data/woldsvaults/recipes/{thermal/crucible,create/mixing,pnc/thermo_plant,industrial_foregoing/dissolution}`
