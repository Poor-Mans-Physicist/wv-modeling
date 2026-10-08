use rand::Rng;
use std::collections::HashSet;
use std::rc::Rc;

use crate::data::DataSource;
use crate::structure::{BlockEntry, BlockSpec, JigsawInfo, Structure};
use crate::transform::{choose_rotation, Direction, Transform};
use crate::voxel::VoxelGrid;

const CHEST_TYPES: [&str; 4] = ["wooden_chest", "gilded_chest", "living_chest", "ornate_chest"];

// Opt-in diagnostic: when enabled, paste_blocks records the resolved label of the final block at
// every world position (jigsaw->final_state, placeholder:type, or the raw block name). Off by
// default - the per-block work is gated on a single thread-local read per structure, so the Monte
// Carlo paths pay essentially nothing. Used by the wv_floattrace diagnostic to identify what the
// "floating blocks with chests" actually are.
thread_local! {
    static NAME_TRACE: std::cell::RefCell<Option<std::collections::HashMap<(i32, i32, i32), String>>> =
        const { std::cell::RefCell::new(None) };
}

pub fn enable_name_trace() {
    NAME_TRACE.with(|t| *t.borrow_mut() = Some(std::collections::HashMap::new()));
}

pub fn take_name_trace() -> std::collections::HashMap<(i32, i32, i32), String> {
    NAME_TRACE.with(|t| t.borrow_mut().take().unwrap_or_default())
}

/// Every tile `assemble_themed` pasted while tracing, in paste order: room-local position, processor
/// chain id (see `chains`), the input block state key the chain ran on, and the sampled cell class.
#[derive(Default)]
pub struct ThemedTrace {
    pub entries: Vec<((i32, i32, i32), usize, String, crate::palette::Cell)>,
    pub chains: std::collections::HashMap<usize, crate::palette::ChainId>,
}

thread_local! {
    static THEMED_TRACE: std::cell::RefCell<Option<ThemedTrace>> = const { std::cell::RefCell::new(None) };
}

/// Starts recording every tile `assemble_themed` pastes on this thread. Recording never touches the
/// RNG, so traced and untraced assemblies are identical.
pub fn enable_themed_trace() {
    THEMED_TRACE.with(|t| *t.borrow_mut() = Some(ThemedTrace::default()));
}

/// Stops recording and returns the pastes since `enable_themed_trace` (later entries overwrite
/// earlier ones at the same position, as the pastes did).
pub fn take_themed_trace() -> ThemedTrace {
    THEMED_TRACE.with(|t| t.borrow_mut().take().unwrap_or_default())
}

fn classify_chest(type_str: &str) -> Option<&'static str> {
    CHEST_TYPES.iter().find(|ct| type_str.starts_with(**ct)).copied()
}

/// Resolves a `the_vault:placeholder[type=X]` marker to the `(is_solid, is_liquid, is_non_sturdy)`
/// the live world actually leaves once its tile-processor runs - the raw placeholder block never
/// persists. Returns `None` for non-placeholder blocks (they use their own palette entry instead).
///
/// The correctness point the user caught: a placeholder is NOT a normal full cube, so an
/// unrecognized one must NOT be recorded as a solid *sturdy* floor. Doing so let the decorator floor
/// a chest on top of coin piles / presents / pedestals / discovery markers - "floating chests on a
/// single block, mid-room." Verified against `palettes/generic/*_placeholder.json`:
///   - `gate` → air; `pylon` → 80% air / 20% thin pylon marker          => air (non-solid)
///   - `ore`  → `vault_stone` / `ore_*[type=vault_stone]` full cubes      => solid + sturdy
///   - `treasure_door`/`dungeon_door` → mostly `minecraft:stone` (a wall) => solid + sturdy
///   - `*_chest*` → a chest (its own top face is NOT sturdy)              => solid + non-sturdy
///   - `coin_stacks(_waterlogged)`/`present`/`vendor_pedestal`/
///     `dungeon_discoverable` and any future/unknown marker → a real
///     non-full decoration (coin pile, present, pedestal, altar)          => solid + non-sturdy
/// Blocks the vault's theme palette (`palettes/universal_*.json`) ALWAYS swaps to a non-solid block
/// (`minecraft:air` or the invisible, no-collision `minecraft:light` source) in EVERY theme - so the
/// raw template block never persists in the live world and must NOT be recorded solid. Verified by
/// parsing all 148 universal palettes: among otherwise-solid full cubes, only `minecraft:glass` is
/// unconditional (-> `minecraft:light[level=10..15]`); it's used purely as a light-source marker.
/// Recording it solid made it a phantom "detached floating glass cube" (~34 per rainbow_forest room,
/// air on all 6 sides) that chests then floored on top of and cascaded beneath - the user could see
/// straight through these in-game. (The colored-wool / shroomlight / warped_wart_block / twisted_
/// fence markers are deliberately NOT included: those are theme-DEPENDENT - air in some themes, a
/// real solid block like osseous_bricks in others - so leaving them as the template block is the
/// correct theme-agnostic default, and unlike glass they're never fully detached.)
fn is_universal_air_marker(name: &str) -> bool {
    name == "minecraft:glass"
}

fn classify_placeholder(spec: &BlockSpec) -> Option<(bool, bool, bool)> {
    if spec.name != "the_vault:placeholder" {
        return None;
    }
    let t = spec.properties.get("type").map(String::as_str).unwrap_or("");
    Some(match t {
        "gate" | "pylon" => (false, false, false),
        "ore" | "treasure_door" | "dungeon_door" => (true, false, false),
        _ if classify_chest(t).is_some() => (true, false, true),
        _ => (true, false, true),
    })
}

#[derive(Debug, Clone)]
pub struct ChestSpot {
    pub pos: (i32, i32, i32),
    pub chest_type: &'static str,
    // Set by a separate post-pass (see strongbox.rs), never inline here - matches the real
    // game's actual sequencing, where the upgrade roll happens at placeholder-resolution time
    // for baseline/decorator_add chests but never at all for decorator_cascade copies (cascade
    // duplicates a block-entity directly, bypassing that roll pipeline entirely - see
    // MECHANICS_NOTES.md). Always false at construction time everywhere in this module.
    pub is_strongbox: bool,
}

#[derive(Clone)]
pub struct AssemblyResult {
    pub chests: Vec<ChestSpot>,
    pub solid: VoxelGrid,
    // Sparse, room-local positions currently holding water/lava. Tracked separately from `solid`
    // (which treats liquid as solid, exactly matching decorator_add's strict "must be true air"
    // rule) because DecoratorCascadeModifier's own target check is air-OR-liquid, not air-only -
    // see decorator::decorator_cascade_pass for where this is actually consumed.
    pub liquid: HashSet<(i32, i32, i32)>,
    // Room-local positions of every placed solid block whose UP face is NOT sturdy (bottom slabs,
    // fences, walls, panes, plants, carpets, torches, ...). The decorator floor checks consult this
    // so a chest is never floored on a block the real game's isFaceSturdy(UP) test would reject -
    // fixes the previous "any solid block is a valid floor" overcount (chests floating on fences/
    // slabs/plants). Fixed at assembly time; decorators never add to it (their own chests are
    // tracked separately and excluded as floors via chest_positions).
    pub non_sturdy: HashSet<(i32, i32, i32)>,
    // World-space positions of every "the_vault:placeholder[type=gate]" block this room's own
    // template carries - the room-to-tunnel doorway markers (see MECHANICS_NOTES.md "Room
    // entrance/exit positions"). Most common rooms have 4 (one per cardinal wall); some
    // standalone/dead-end rooms have none. Detected per-template rather than hardcoded, since a
    // couple of rooms deviate from the otherwise-invariant position rule.
    pub gates: Vec<(i32, i32, i32)>,
    // Cells holding a block without collision (plants, light, torches, liquids, ...): not chest
    // targets or floors, but walkable. Only consumed by movement-grid exports.
    pub passable: HashSet<(i32, i32, i32)>,
    pub pieces_placed: u32,
    pub duds: u32, // jigsaw points where a pool roll happened but no compatible connector was found
}

pub fn assemble(root: &Rc<Structure>, data: &DataSource, rng: &mut impl Rng, max_depth: i32) -> AssemblyResult {
    let mut result = AssemblyResult {
        chests: Vec::new(),
        solid: VoxelGrid::new(root.size),
        liquid: HashSet::new(),
        non_sturdy: HashSet::new(),
        gates: Vec::new(),
        passable: HashSet::new(),
        pieces_placed: 1,
        duds: 0,
    };
    process(root, Transform::identity(), data, rng, max_depth, &mut result);
    result
}

fn paste_blocks(structure: &Structure, to_root: &Transform, result: &mut AssemblyResult) {
    let tracing = NAME_TRACE.with(|t| t.borrow().is_some());
    let mut passable_cache: Vec<Option<bool>> = vec![None; structure.palette.len()];
    for b in &structure.blocks {
        let Some(spec) = structure.palette.get(b.state) else { continue };
        let world_pos = to_root.apply(b.pos);

        if tracing {
            let label = if spec.name == "minecraft:jigsaw" {
                format!("jigsaw->{}", b.jigsaw.as_ref().map(|j| j.final_state.as_str()).unwrap_or("?"))
            } else if spec.name == "the_vault:placeholder" {
                format!("placeholder:{}", spec.properties.get("type").map(String::as_str).unwrap_or("?"))
            } else {
                spec.name.clone()
            };
            NAME_TRACE.with(|t| {
                if let Some(m) = t.borrow_mut().as_mut() {
                    m.insert(world_pos, label);
                }
            });
        }

        if spec.name == "the_vault:placeholder" {
            if let Some(t) = spec.properties.get("type") {
                if let Some(chest_type) = classify_chest(t) {
                    result.chests.push(ChestSpot {
                        pos: world_pos,
                        chest_type,
                        is_strongbox: false,
                    });
                } else if t == "gate" {
                    result.gates.push(world_pos);
                }
            }
        }

        // Confirmed empirically (see SPEC.md): this pack never uses minecraft:structure_void
        // anywhere in its room/decor structures, so every placed piece simply overwrites whatever
        // was at its footprint - no transparency/void handling needed.
        //
        // A `minecraft:jigsaw` marker never persists in the live world - the game swaps it for its
        // `final_state` (usually air, sometimes terrain) once generation finishes. Record THAT
        // block in the voxel grid, not a solid jigsaw cube, otherwise every connector (decor anchors
        // especially, whose final_state is air) leaves a phantom floating solid block that chests
        // then wrongly floor on. `the_vault:placeholder` markers are resolved the same way (via
        // classify_placeholder) to whatever the tile processor leaves. Everything else uses its own
        // palette entry, with sturdiness taken from the per-palette flag precomputed in
        // Structure::parse (the substring scan is too expensive to run per block in this hot path).
        let (is_solid, is_liquid_block, is_non_sturdy) = match &b.jigsaw {
            Some(j) => crate::sturdy::classify_blockstate(&j.final_state),
            None => match classify_placeholder(spec) {
                Some(c) => c,
                None if is_universal_air_marker(&spec.name) => (false, false, false),
                None => (
                    spec.name != "minecraft:air",
                    spec.name == "minecraft:water" || spec.name == "minecraft:lava",
                    structure.palette_non_sturdy.get(b.state).copied().unwrap_or(false),
                ),
            },
        };
        result.solid.set(world_pos, is_solid);
        if is_liquid_block {
            result.liquid.insert(world_pos);
        } else {
            result.liquid.remove(&world_pos);
        }
        // Last write wins (decor overwrites terrain): a cell that becomes air or a full cube must be
        // cleared from the non-sturdy set, not left stale.
        if is_non_sturdy {
            result.non_sturdy.insert(world_pos);
        } else {
            result.non_sturdy.remove(&world_pos);
        }
        let passable = is_liquid_block
            || match &b.jigsaw {
                Some(j) => crate::palette::is_passable(&crate::sturdy::parse_blockstate(&j.final_state).0),
                None => *passable_cache[b.state].get_or_insert_with(|| is_solid && crate::palette::is_passable(&spec.name)),
            };
        if is_solid && passable {
            result.passable.insert(world_pos);
        } else {
            result.passable.remove(&world_pos);
        }
    }
}

fn find_matching_connector<'a>(
    child: &'a Structure,
    target_name: &str,
    parent_facing: Direction,
    parent_side: Direction,
    rng: &mut impl Rng,
) -> Option<(&'a BlockEntry, &'a JigsawInfo)> {
    let mut chosen = None;
    let mut count: u32 = 0;
    for (cb, cj) in child.jigsaw_blocks() {
        if cj.name != target_name {
            continue;
        }
        let child_facing = Direction::parse(&cj.front);
        if child_facing.is_vertical() != parent_facing.is_vertical() {
            continue;
        }
        if parent_facing.is_vertical() {
            let child_side = Direction::parse(&cj.side);
            if child_side != parent_side {
                continue;
            }
        }
        count += 1;
        if rng.gen_range(0..count) == 0 {
            chosen = Some((cb, cj));
        }
    }
    chosen
}

fn process(structure: &Structure, to_root: Transform, data: &DataSource, rng: &mut impl Rng, depth: i32, result: &mut AssemblyResult) {
    paste_blocks(structure, &to_root, result);

    if depth < 0 {
        return;
    }

    for (block, jig) in structure.jigsaw_blocks() {
        let Some(picked_template) = data.sample_pool(&jig.pool, rng) else {
            continue;
        };
        let Some(child) = data.get_structure(&picked_template) else {
            continue;
        };

        let parent_facing = Direction::parse(&jig.front);
        let parent_side = Direction::parse(&jig.side);
        let parent_rollable = jig.joint == "rollable";

        let Some((target_block, target_jig)) =
            find_matching_connector(&child, &jig.target, parent_facing, parent_side, rng)
        else {
            result.duds += 1;
            continue;
        };

        let child_facing = Direction::parse(&target_jig.front);
        let child_side = Direction::parse(&target_jig.side);
        let child_rollable = target_jig.joint == "rollable";

        let rotation = choose_rotation(
            child_facing,
            child_side,
            child_rollable,
            parent_facing,
            parent_side,
            parent_rollable,
            rng,
        );

        let diff = (
            block.pos.0 - target_block.pos.0,
            block.pos.1 - target_block.pos.1,
            block.pos.2 - target_block.pos.2,
        );
        let facing_vec = parent_facing.vector();
        let offset = (diff.0 + facing_vec.0, diff.1 + facing_vec.1, diff.2 + facing_vec.2);

        let local_step = Transform::for_attachment(rotation, target_block.pos, offset);
        let child_to_root = to_root.then(&local_step);

        result.pieces_placed += 1;
        process(&child, child_to_root, data, rng, depth - 1, result);
    }
}

/// `assemble`, with the room's theme palettes applied the way the game does: every tile of the root
/// runs through `root_palettes` (the pool entry's palettes, e.g. `the_vault:map/universal_desert`),
/// every decor piece through its own pool-entry palettes first and then its parent's chain. Template chest
/// placeholders resolve through the chain's placeholder processor (strongbox / enigma / chest / air)
/// instead of always becoming a plain chest. Returns the root chain for resolving Bonus X outputs.
pub fn assemble_themed(
    root: &Rc<Structure>,
    root_palettes: &[String],
    data: &DataSource,
    lib: &crate::palette::PaletteLibrary,
    rng: &mut impl Rng,
    max_depth: i32,
) -> (AssemblyResult, crate::palette::ChainId) {
    let mut result = AssemblyResult {
        chests: Vec::new(),
        solid: VoxelGrid::new(root.size),
        liquid: HashSet::new(),
        non_sturdy: HashSet::new(),
        gates: Vec::new(),
        passable: HashSet::new(),
        pieces_placed: 1,
        duds: 0,
    };
    let chain = lib.chain(root_palettes);
    process_themed(root, Transform::identity(), &chain, data, lib, rng, max_depth, &mut result);
    (result, chain)
}

fn paste_blocks_themed(
    structure: &Structure,
    to_root: &Transform,
    chain: &crate::palette::ChainId,
    lib: &crate::palette::PaletteLibrary,
    rng: &mut impl Rng,
    result: &mut AssemblyResult,
) {
    use crate::palette::{sample, Cell, PaletteLibrary};
    let mut cache: Vec<Option<Rc<crate::palette::Dist>>> = vec![None; structure.palette.len()];
    let tracing = THEMED_TRACE.with(|t| t.borrow().is_some());
    for b in &structure.blocks {
        let Some(spec) = structure.palette.get(b.state) else { continue };
        let pos = to_root.apply(b.pos);
        if spec.name == "the_vault:placeholder" && spec.properties.get("type").map(String::as_str) == Some("gate") {
            result.gates.push(pos);
        }
        let dist = match &b.jigsaw {
            Some(j) => lib.resolve(chain, &j.final_state),
            None => cache[b.state]
                .get_or_insert_with(|| lib.resolve(chain, &PaletteLibrary::state_key(&spec.name, &spec.properties)))
                .clone(),
        };
        let cell = sample(&dist, rng);
        if tracing {
            let input = match &b.jigsaw {
                Some(j) => j.final_state.clone(),
                None => PaletteLibrary::state_key(&spec.name, &spec.properties),
            };
            THEMED_TRACE.with(|t| {
                if let Some(v) = t.borrow_mut().as_mut() {
                    v.chains.entry(chain.0).or_insert_with(|| chain.clone());
                    v.entries.push((pos, chain.0, input, cell));
                }
            });
        }
        let (solid, liquid, non_sturdy, passable) = match cell {
            Cell::Air | Cell::Gate => (false, false, false, false),
            Cell::Block { liquid, non_sturdy, passable } => (true, liquid, non_sturdy, passable),
            Cell::Chest { ty, strongbox } => {
                result.chests.push(ChestSpot { pos, chest_type: ty, is_strongbox: strongbox });
                (true, false, true, false)
            }
            Cell::OtherChest => {
                result.chests.push(ChestSpot { pos, chest_type: "enigma_chest", is_strongbox: false });
                (true, false, true, false)
            }
        };
        result.solid.set(pos, solid);
        if liquid {
            result.liquid.insert(pos);
        } else {
            result.liquid.remove(&pos);
        }
        if non_sturdy {
            result.non_sturdy.insert(pos);
        } else {
            result.non_sturdy.remove(&pos);
        }
        if passable {
            result.passable.insert(pos);
        } else {
            result.passable.remove(&pos);
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn process_themed(
    structure: &Structure,
    to_root: Transform,
    chain: &crate::palette::ChainId,
    data: &DataSource,
    lib: &crate::palette::PaletteLibrary,
    rng: &mut impl Rng,
    depth: i32,
    result: &mut AssemblyResult,
) {
    paste_blocks_themed(structure, &to_root, chain, lib, rng, result);
    if depth < 0 {
        return;
    }
    for (block, jig) in structure.jigsaw_blocks() {
        let Some((picked_template, child_palettes)) = data.sample_pool_entry(&jig.pool, rng) else {
            continue;
        };
        let Some(child) = data.get_structure(&picked_template) else {
            continue;
        };
        let parent_facing = Direction::parse(&jig.front);
        let parent_side = Direction::parse(&jig.side);
        let parent_rollable = jig.joint == "rollable";
        let Some((target_block, target_jig)) = find_matching_connector(&child, &jig.target, parent_facing, parent_side, rng) else {
            result.duds += 1;
            continue;
        };
        let child_facing = Direction::parse(&target_jig.front);
        let child_side = Direction::parse(&target_jig.side);
        let child_rollable = target_jig.joint == "rollable";
        let rotation = choose_rotation(child_facing, child_side, child_rollable, parent_facing, parent_side, parent_rollable, rng);
        let diff = (block.pos.0 - target_block.pos.0, block.pos.1 - target_block.pos.1, block.pos.2 - target_block.pos.2);
        let facing_vec = parent_facing.vector();
        let offset = (diff.0 + facing_vec.0, diff.1 + facing_vec.1, diff.2 + facing_vec.2);
        let local_step = Transform::for_attachment(rotation, target_block.pos, offset);
        let child_to_root = to_root.then(&local_step);
        let child_chain = lib.extend(chain, &child_palettes);
        result.pieces_placed += 1;
        process_themed(&child, child_to_root, &child_chain, data, lib, rng, depth - 1, result);
    }
}

/// Mirrors an assembled room across x (x -> sx-1-x), the game's `Mirror.FRONT_BACK` about the cell
/// centre, applied before rotation exactly as `VaultGridLayout.getRoom` orders its processors.
pub fn mirror_x(asm: &mut AssemblyResult) {
    let (sx, sy, sz) = asm.solid.size();
    let m = |p: (i32, i32, i32)| (sx - 1 - p.0, p.1, p.2);
    let mut grid = VoxelGrid::new((sx, sy, sz));
    for x in 0..sx {
        for y in 0..sy {
            for z in 0..sz {
                grid.set(m((x, y, z)), asm.solid.is_solid((x, y, z)));
            }
        }
    }
    asm.solid = grid;
    asm.liquid = asm.liquid.iter().map(|p| m(*p)).collect();
    asm.non_sturdy = asm.non_sturdy.iter().map(|p| m(*p)).collect();
    asm.passable = asm.passable.iter().map(|p| m(*p)).collect();
    for c in &mut asm.chests {
        c.pos = m(c.pos);
    }
    for g in &mut asm.gates {
        *g = m(*g);
    }
}
