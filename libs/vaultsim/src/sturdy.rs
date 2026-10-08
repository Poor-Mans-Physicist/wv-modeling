use std::collections::HashMap;

/// Approximates Minecraft 1.18 `BlockState.isFaceSturdy(level, pos, Direction.UP)` - i.e. "does
/// this block present a full, flat top face that another block (here, a vault chest) can be placed
/// on top of." The real test is whether the block's collision shape, projected onto its UP face,
/// covers the whole 1x1 square (`SupportType.FULL`). We don't replicate every collision shape, so
/// this is a name+blockstate heuristic tuned to the blocks that actually occur as floors in this
/// pack's room/decor structures (verified by a census of every `common/` room's below-air blocks).
///
/// Returns `true` for full-cube-topped blocks (stone, dirt, wool, glass, `*_block`, top/double
/// slabs, top-half stairs, ...) and `false` for blocks whose top face does NOT fully support a
/// placement (bottom slabs, fences, walls, panes, plants, carpets, torches, lanterns, ...). This
/// is what stops the simulator from "floating" a chest on a fence/slab/plant the way the buggy
/// any-solid-block-is-a-floor rule did. Only consumed by the decorator floor checks (`decorator.rs`)
/// and `count_chest_slots`; the air-vs-solid target check is unaffected (a non-sturdy solid block
/// is still solid, so it's still rejected as a placement *target*).
pub fn is_sturdy_top(name: &str, props: &HashMap<String, String>) -> bool {
    let n = name.strip_prefix("minecraft:").unwrap_or(name);

    // Air and liquids never support a chest. (Liquids are also tracked in their own set and
    // excluded from floors there, but classify them correctly here too for any direct callers.)
    if n == "air" || n == "cave_air" || n == "void_air" || n == "water" || n == "lava" {
        return false;
    }
    // `minecraft:light` is a no-collision light source marker (placed up in the air, never a floor);
    // exact-match it here because the colour words "light_blue"/"light_gray" prefix many real full
    // cubes (stained glass, wool, concrete) that a "light" substring would wrongly demote.
    if n == "light" {
        return false;
    }
    if let Some(verified) = verified_sturdy_top(name, props) {
        return verified;
    }
    // Exact-match a few non-full blocks whose names a substring rule would over-match: the azalea
    // *bushes* (azalea_planks / azalea_wood / azalea_bookshelf are real full cubes) and cocoa pods
    // (quark's cocoa_beans_sack is a full cube).
    if n == "azalea" || n == "flowering_azalea" || n == "cocoa" {
        return false;
    }
    // Beds are 9/16 high (not a sturdy top). Suffix-match, NOT substring - "_bed" appears inside
    // `the_vault:vault_bedrock` (the room's own floor/base block, ~100k/scan), and a substring rule
    // would catastrophically demote the entire vault floor and stop chests flooring anywhere.
    if n.ends_with("_bed") {
        return false;
    }

    // Slabs: a single slab only has a full top face when it's the upper slab; a double slab is a
    // full cube. A lower ("bottom") slab's top face sits at y=8, so it is NOT sturdy.
    if n.ends_with("_slab") || n == "slab" {
        return matches!(props.get("type").map(String::as_str), Some("top") | Some("double"));
    }
    // Stairs: the UP face is full only for the top half (the upper step is a full slab on top).
    if n.ends_with("_stairs") {
        return props.get("half").map(|h| h == "top").unwrap_or(false);
    }
    // Trapdoors: only a closed, top-half trapdoor presents a flat full top face.
    if n.ends_with("_trapdoor") {
        let closed = props.get("open").map(|o| o == "false").unwrap_or(true);
        let top = props.get("half").map(|h| h == "top").unwrap_or(false);
        return closed && top;
    }

    // Leaves: `LeavesBlock.getBlockSupportShape` returns `Shapes.empty()` (decompiled MC 1.18.2), so
    // isFaceSturdy(UP) is false and neither Bonus X nor a cascade can floor a chest on them, even though
    // they have a full collision box. Guarded before the `_block` rule below. Dirt path and farmland
    // (15/16 tall) and honey blocks (collision 1..15 x 0..15) have no full top face either; soul sand
    // overrides its support shape to a full block and stays sturdy.
    if n.ends_with("_leaves") || n == "dirt_path" || n == "grass_path" || n == "farmland" || n == "honey_block" {
        return false;
    }
    // Anything ending in `_block` in this pack's data is a real full cube (grass_block,
    // warped_wart_block, *_mushroom_block, diamond_block, ...). Guard this BEFORE the substring
    // list below so e.g. grass_block / nether_wart_block aren't caught by "grass"/"wart".
    if n.ends_with("_block") {
        return true;
    }

    // Known partial-collision blocks whose UP face cannot fully support a placement. Substring
    // match (covers wood-type prefixes like spruce_fence, warped_fence, dark_oak_pressure_plate).
    const NON_STURDY: &[&str] = &[
        "fence", "wall", "pane", "bars", "chain", "ladder", "scaffolding",
        "carpet", "snow", "torch", "lantern", "rail", "lever", "button", "tripwire",
        "pressure_plate", "sign", "banner", "candle", "cobweb", "pointed_dripstone",
        "amethyst_cluster", "_bud", "dripleaf", "lily_pad", "sea_pickle", "coral",
        "sapling", "sprouts", "roots", "fungus", "bush", "vine", "lichen", "blossom",
        "grass", "fern", "flower", "tulip", "orchid", "dandelion", "poppy", "allium",
        "azure_bluet", "oxeye_daisy", "cornflower", "lily_of_the_valley", "wither_rose",
        "lilac", "rose_bush", "peony", "sunflower", "petals", "sugar_cane", "bamboo",
        "kelp", "seagrass", "mushroom", "wart", "hanging_roots", "nether_sprouts",
        // 2-tall / thin / open-top decoration the real isFaceSturdy(UP) also rejects but which the
        // first cut of this list missed - these were letting the decorator floor a chest on top of
        // a cactus / campfire / skull / coin-pile etc., producing the user-reported "floating chest
        // on a single block, mid-room" artifact. (`*_block`/`*_leaves` full cubes already returned
        // sturdy above, so e.g. *_coral_block / hay_block / *_mushroom_block are unaffected.)
        "cactus", "campfire", "cauldron", "anvil", "brewing_stand", "composter",
        "enchanting_table", "_rod", "grindstone", "stonecutter", "lectern", "bell",
        "daylight_detector", "skull", "_head", "_egg", "potted_", "hopper",
        "potatoes", "carrots", "beetroots", "wheat",
        // doors (thin - trapdoors are handled above, before this list), real chests (own top isn't a
        // sturdy face), chorus stems, repeater/comparator (slab-height), and the end portal frame
        // (13/16 high) all fail isFaceSturdy(UP) too. (Beds, azalea bushes and cocoa are exact/suffix
        // matched up top so they can't over-match vault_bedrock / azalea_planks / cocoa_beans_sack.)
        "_door", "chest", "chorus", "repeater", "comparator", "end_portal_frame",
    ];
    for pat in NON_STURDY {
        if n.contains(pat) {
            return false;
        }
    }
    true
}

/// Blocks whose UP face is a full support face although a name rule in `is_sturdy_top` rejects them,
/// each checked against its decompiled class (no shape override, so collision = support = full cube):
/// - `tropicraft:bamboo_bundle`: `RotatedPillarBlock` (TropicraftBlocks.BAMBOO_BUNDLE).
/// - `architects_palette:{,lit_}{,withered_}osseous_skull`: plain `Block`, copy of BONE_BLOCK (APBlocks).
/// - `architects_palette:entrails`: `DrippyBlock extends Block`, Material.VEGETABLE (APBlocks.ENTRAILS).
/// - `minecraft:mushroom_stem`: `HugeMushroomBlock`; `minecraft:jack_o_lantern`: `CarvedPumpkinBlock`;
///   `minecraft:sea_lantern`: plain `Block` (Blocks.java).
/// - `twigs:stripped_bamboo_planks`, `twigs:bamboo_thatch`, `ecologics:snow_bricks`: plain `Block`.
/// - `*_bookshelf`: vanilla `Block`, Quark `VariantBookshelfBlock extends QuarkBlock` (also Every Compat's).
const STURDY_EXACT: &[&str] = &[
    "tropicraft:bamboo_bundle",
    "architects_palette:osseous_skull",
    "architects_palette:lit_osseous_skull",
    "architects_palette:withered_osseous_skull",
    "architects_palette:lit_withered_osseous_skull",
    "architects_palette:entrails",
    "minecraft:mushroom_stem",
    "minecraft:jack_o_lantern",
    "minecraft:sea_lantern",
    "twigs:stripped_bamboo_planks",
    "twigs:bamboo_thatch",
    "ecologics:snow_bricks",
];

/// Blocks without a full UP support face that the name rules in `is_sturdy_top` would accept, each
/// checked against its decompiled class:
/// - `minecraft:fire`, `soul_fire`, `redstone_wire`: `noCollission()` (Blocks.java), empty support shape.
/// - `ecologics:coconut_seedling` (`SaplingBlock`, noCollission), `seashell` (shape 3 high),
///   `pot` (2..14 wide), `coconut` (12 high), `hanging_coconut`, `surface_moss` (`MultifaceBlock`, noCollission).
/// - `quark:glow_shroom` (copy of RED_MUSHROOM, noCollission).
/// - `the_vault:pylon` (2..14 x 18), `the_vault:coin_pile` and the decor coin piles `vault_bronze` ..
///   `vault_platinum` (`CoinPileDecorBlock`), all at most 2..14 wide.
/// - `architects_palette:pipe` (`PipeBlock`, 2..14 cut-out), `*_nub` (`NubBlock`, 3..13).
/// - `decorative_blocks:brazier`/`soul_brazier` (2.5..13.5), `stone_pillar` (`PillarBlock`, 2..14),
///   `chandelier` (2..14 x 12).
/// - Supplementaries: `book_pile(_horizontal)`, `rope`, `rope_knot`, `goblet`, `stick`, `jar`, `sack`,
///   `statue`, `crank`, `doormat` (1 high), `pedestal`, `gunpowder` (copy of REDSTONE_WIRE).
/// - `cookingforblockheads:spice_rack` (2-px back plate).
const NOT_STURDY_EXACT: &[&str] = &[
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:redstone_wire",
    "ecologics:coconut_seedling",
    "ecologics:seashell",
    "ecologics:pot",
    "ecologics:coconut",
    "ecologics:hanging_coconut",
    "ecologics:surface_moss",
    "quark:glow_shroom",
    "the_vault:pylon",
    "the_vault:coin_pile",
    "the_vault:vault_bronze",
    "the_vault:vault_silver",
    "the_vault:vault_gold",
    "the_vault:vault_platinum",
    "architects_palette:pipe",
    "decorative_blocks:brazier",
    "decorative_blocks:soul_brazier",
    "decorative_blocks:stone_pillar",
    "decorative_blocks:chandelier",
    "decorative_blocks:soul_chandelier",
    "supplementaries:book_pile",
    "supplementaries:book_pile_horizontal",
    "supplementaries:rope",
    "supplementaries:rope_knot",
    "supplementaries:goblet",
    "supplementaries:stick",
    "supplementaries:jar",
    "supplementaries:sack",
    "supplementaries:statue",
    "supplementaries:crank",
    "supplementaries:doormat",
    "supplementaries:pedestal",
    "supplementaries:gunpowder",
    "cookingforblockheads:spice_rack",
];

/// The ids of Tropicraft's `TropicraftFlower` enum: every one is a `TropicsFlowerBlock extends FlowerBlock`
/// with properties copied from POPPY (noCollission, Material.PLANT), so no support face and no collision.
pub fn is_tropicraft_flower(id: &str) -> bool {
    const FLOWERS: &[&str] = &[
        "acai_vine", "anemone", "bromeliad", "canna", "commelina_diffusa", "crocosmia", "croton", "dracaena",
        "tropical_fern", "foliage", "magic_mushroom", "orange_anthurium", "orchid", "pathos", "red_anthurium",
    ];
    FLOWERS.contains(&id)
}

/// isFaceSturdy(UP) for blocks verified against their decompiled classes, or `None` to fall through to
/// the name heuristics. Beyond the two exact lists: layer blocks (`minecraft:snow`, `ecologics:moss_layer`
/// extends `SnowLayerBlock`, `supplementaries:ash`) have support shape `SHAPE_BY_LAYER[layers]`, full only
/// at 8 layers; `auxiliaryblocks:*_gelatin` (`GelatinBlock`, collision 1..15), `quark:*_corundum_cluster`,
/// Quark / Every Compat hedges (`HedgeBlock extends FenceBlock`), Architect's Palette nubs, Supplementaries
/// flags / sconces / candle holders, Macaw's parapets (half-width top) and Decorative Blocks seats never
/// are; Decorative Blocks `lattice` / `bar_panel` extend `TrapDoorBlock` (full only closed, top half);
/// Decorative Blocks supports (`SupportBlock`, also Every Compat's `db/` ones) are full on top only with
/// `up=true, horizontal=big` or with both faces hidden (a full cube).
fn verified_sturdy_top(name: &str, props: &HashMap<String, String>) -> Option<bool> {
    let full;
    let name = if name.contains(':') {
        name
    } else {
        full = format!("minecraft:{name}");
        full.as_str()
    };
    let p = |k: &str| props.get(k).map(String::as_str);
    if matches!(name, "minecraft:snow" | "ecologics:moss_layer" | "supplementaries:ash") {
        return Some(p("layers") == Some("8"));
    }
    if STURDY_EXACT.contains(&name) || name.ends_with("_bookshelf") {
        return Some(true);
    }
    if NOT_STURDY_EXACT.contains(&name) {
        return Some(false);
    }
    let (ns, id) = name.split_once(':').unwrap_or(("minecraft", name));
    match ns {
        "tropicraft" if is_tropicraft_flower(id) => Some(false),
        "auxiliaryblocks" if id.ends_with("_gelatin") => Some(false),
        "quark" if id.ends_with("_corundum_cluster") || id.ends_with("_hedge") => Some(false),
        "everycomp" if id.starts_with("q/") && id.ends_with("_hedge") => Some(false),
        "architects_palette" if id.ends_with("_nub") => Some(false),
        "supplementaries" if id.starts_with("flag_") || id.starts_with("sconce") || id.starts_with("candle_holder") => Some(false),
        "mcwwindows" if id.ends_with("_parapet") => Some(false),
        "decorative_blocks" if id.ends_with("_seat") => Some(false),
        "decorative_blocks" if id == "lattice" || id == "bar_panel" => Some(p("open") != Some("true") && p("half") == Some("top")),
        "decorative_blocks" | "everycomp" if id.ends_with("_support") && (ns == "decorative_blocks" || id.starts_with("db/")) => {
            let (h, v) = (p("horizontal"), p("vertical"));
            Some((p("up") == Some("true") && h == Some("big")) || (h == Some("hidden") && v == Some("hidden")))
        }
        _ => None,
    }
}

/// Parses a blockstate string ("namespace:block" or "namespace:block[k=v,k2=v2]") into its block
/// name and property map. Used to resolve a jigsaw block's `final_state`, which is stored as such a
/// string in the structure NBT.
pub fn parse_blockstate(s: &str) -> (String, HashMap<String, String>) {
    let mut props = HashMap::new();
    let name = match s.find('[') {
        Some(br) => {
            let inner = s[br..].trim_start_matches('[').trim_end_matches(']');
            for kv in inner.split(',') {
                if let Some((k, v)) = kv.split_once('=') {
                    props.insert(k.trim().to_string(), v.trim().to_string());
                }
            }
            s[..br].to_string()
        }
        None => s.to_string(),
    };
    (name, props)
}

/// Classifies a blockstate string into `(is_solid, is_liquid, is_non_sturdy_solid)` for the voxel
/// grid - used for jigsaw `final_state` resolution (the block the game actually leaves behind).
/// Air variants are non-solid; water/lava are solid-but-liquid; everything else is solid, flagged
/// non-sturdy iff its top face can't support a chest (`is_sturdy_top`).
pub fn classify_blockstate(s: &str) -> (bool, bool, bool) {
    let (name, props) = parse_blockstate(s);
    if name == "minecraft:air" || name == "minecraft:cave_air" || name == "minecraft:void_air" || name.is_empty() {
        return (false, false, false);
    }
    let is_liquid = name == "minecraft:water" || name == "minecraft:lava";
    (true, is_liquid, !is_sturdy_top(&name, &props))
}
