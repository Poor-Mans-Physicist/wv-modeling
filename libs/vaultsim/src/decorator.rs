use rand::Rng;
use std::collections::HashSet;

use crate::assemble::ChestSpot;
use crate::transform::{Rotation, Transform};
use crate::voxel::VoxelGrid;

/// World-space room footprint is always a fixed 47x47 box anchored at a multiple of 47
/// (GridGenerator.CELL_X/CELL_Z, verified earlier), regardless of the room's own internal
/// rotation - rotation happens around the box's own center, which maps a 47-wide square onto
/// itself. So chunk overlap only depends on the box origin, never on rotation choice.
pub fn chunks_overlapped(origin_x: i32, origin_z: i32, size: i32) -> Vec<(i32, i32)> {
    let mut chunks = Vec::new();
    let min_cx = origin_x.div_euclid(16);
    let max_cx = (origin_x + size - 1).div_euclid(16);
    let min_cz = origin_z.div_euclid(16);
    let max_cz = (origin_z + size - 1).div_euclid(16);
    for cx in min_cx..=max_cx {
        for cz in min_cz..=max_cz {
            chunks.push((cx, cz));
        }
    }
    chunks
}

/// Builds the transform mapping a room's own local block coordinates to absolute world
/// coordinates, exactly matching VaultGridLayout.getRoom(): rotate the content around the
/// room's own center (cellSize/2, _, cellSize/2), then translate so the room's local origin
/// lands at (region_x*cellSize, 9, region_z*cellSize). The +9 Y offset and the cellSize=47
/// anchor are both verified directly from VaultGridLayout.java/RegionPos.java, not assumed.
/// Mirror (also applied by the real game, randomly NONE/FRONT_BACK) is deliberately not
/// modeled - it doesn't change per-room chest *counts* or chunk-overlap statistics (mirroring
/// a square room around its own center preserves its footprint and interior volume exactly),
/// it would only matter for knowing the exact mirrored position of one specific chest, which
/// isn't needed for any of the count-based modeling done so far.
pub fn room_world_transform(region: (i32, i32), rotation: Rotation, cell_size: i32) -> Transform {
    let pivot = (cell_size / 2, 0, cell_size / 2);
    let offset = (region.0 * cell_size, 9, region.1 * cell_size);
    Transform::for_attachment(rotation, pivot, offset)
}

/// Faithful port of DecoratorAddModifier.initServer's per-attempt logic. Mutates `grid` so
/// repeated calls (simulating multiple stacked decorator_add modifiers) naturally see each
/// other's placements as solid obstacles, exactly like the real game's live world state -
/// this is also what makes "can't place into an already-occupied slot" fall out for free,
/// since baseline POI chests are already marked solid in `grid` by the assembly pass.
///
/// `chest_positions` must be pre-seeded with every existing chest position (baseline plus any
/// earlier add/cascade passes this trial) and is updated as new chests are placed - see the
/// floor-check note below for why this can't just be derived from `grid` alone.
///
/// `region` is the room's own grid cell index (NOT a chunk coordinate - see RegionPos/
/// VaultGridLayout notes). Region (0,0) is the vault's starting room and is always fully
/// exempt (DecoratorAddModifier checks `data.getRegion().x==0 && z==0`, which - since that
/// check uses the same `region` value for every chunk the room spans - skips the *entire*
/// room, not just one chunk of it).
#[allow(clippy::too_many_arguments)]
pub fn decorator_add_pass(
    grid: &mut VoxelGrid,
    chest_positions: &mut HashSet<(i32, i32, i32)>,
    liquid: &HashSet<(i32, i32, i32)>,
    non_sturdy: &HashSet<(i32, i32, i32)>,
    region: (i32, i32),
    rotation: Rotation,
    cell_size: i32,
    attempts_per_chunk: u32,
    require_conditions: bool,
    chest_type: &'static str,
    rng: &mut impl Rng,
) -> Vec<ChestSpot> {
    let mut placed = Vec::new();
    if region == (0, 0) {
        return placed;
    }

    let outer = room_world_transform(region, rotation, cell_size);
    let inverse = outer.inverse();

    let origin_x = region.0 * cell_size;
    let origin_z = region.1 * cell_size;
    let chunks = chunks_overlapped(origin_x, origin_z, cell_size);

    for (cx, cz) in chunks {
        for _ in 0..attempts_per_chunk {
            let world_x = rng.gen_range(cx * 16..cx * 16 + 16);
            let world_z = rng.gen_range(cz * 16..cz * 16 + 16);
            let world_y = rng.gen_range(0..64);

            let local = inverse.apply((world_x, world_y, world_z));

            if grid.is_solid(local) {
                continue;
            }
            if require_conditions {
                let above = (local.0, local.1 + 1, local.2);
                let below = (local.0, local.1 - 1, local.2);
                // Real check is `isFaceSturdy(below, UP)` (verified directly in
                // DecoratorAddModifier's decompiled bytecode), not a blanket solidity test - a
                // chest's hitbox doesn't fill its own top face, so a chest is never a sturdy
                // floor, and neither is a liquid surface, a slab/fence/plant, etc. `grid` alone
                // can't distinguish those from ordinary solid terrain (all read as solid), hence
                // the separate `chest_positions` / `liquid` / `non_sturdy` checks.
                if grid.is_solid(above)
                    || !grid.is_solid(below)
                    || liquid.contains(&below)
                    || non_sturdy.contains(&below)
                    || chest_positions.contains(&below)
                {
                    continue;
                }
            }

            grid.set(local, true);
            chest_positions.insert(local);
            placed.push(ChestSpot {
                pos: local,
                chest_type,
                is_strongbox: false,
            });
        }
    }

    placed
}

/// Faithful port of DecoratorCascadeModifier.onGenerate's per-source-tile loop. Unlike
/// decorator_add, this is NOT exempt at region (0,0) (verified directly: DecoratorCascadeModifier
/// registers its TEMPLATE_GENERATION.POST handler with no region check at all, unlike
/// DecoratorAddModifier's explicit `region.x==0 && z==0` early return). It also runs at event
/// priority -100 vs decorator_add's default 0, and Event.java's `getListeners()` sorts priorities
/// *descending* (`Collections.reverseOrder()`), so decorator_add always fires first for the same
/// chunk - meaning decorator_add's own additions are already in the world (and thus valid cascade
/// sources) by the time cascade runs. `sources` should be passed accordingly: baseline POI chests
/// plus whatever an earlier decorator_add_pass call already added this trial.
///
/// Each source rolls `chance` via the exact stochastic-rounding loop from the decompiled
/// bytecode (`for (p = chance; p > 0 && rng.nextFloat() < p; p -= 1)`) - this is NOT the same as
/// a guaranteed `floor(chance)` copies, since each iteration's *attempt* can still fail to find a
/// valid spot (silently wasted, no retry), so realized yield is always <= the roll count.
#[allow(clippy::too_many_arguments)]
pub fn decorator_cascade_pass(
    grid: &mut VoxelGrid,
    liquid: &mut HashSet<(i32, i32, i32)>,
    chest_positions: &mut HashSet<(i32, i32, i32)>,
    non_sturdy: &HashSet<(i32, i32, i32)>,
    sources: &[ChestSpot],
    region: (i32, i32),
    rotation: Rotation,
    cell_size: i32,
    chance: f32,
    chest_type_filter: &'static str,
    rng: &mut impl Rng,
) -> Vec<ChestSpot> {
    let mut placed = Vec::new();
    let outer = room_world_transform(region, rotation, cell_size);
    let inverse = outer.inverse();

    for source in sources {
        // A strongbox is a genuinely distinct block id in the real game, which no cascade
        // modifier's filter ever names - confirmed it can never be a cascade source (or target).
        // The simulator's `chest_type` field doesn't carry that distinction on its own (it's the
        // same "gilded_chest" either way), so `is_strongbox` is checked explicitly here.
        if source.chest_type != chest_type_filter || source.is_strongbox {
            continue;
        }
        let origin = outer.apply(source.pos);
        let chunk = (origin.0.div_euclid(16), origin.2.div_euclid(16));

        let mut p = chance;
        while p > 0.0 && rng.r#gen::<f32>() < p {
            if let Some(local) = find_cascade_spot(grid, liquid, chest_positions, non_sturdy, &inverse, origin, chunk, rng) {
                grid.set(local, true);
                liquid.remove(&local);
                chest_positions.insert(local);
                placed.push(ChestSpot {
                    pos: local,
                    chest_type: chest_type_filter,
                    is_strongbox: false,
                });
            }
            p -= 1.0;
        }
    }

    placed
}

/// Faithful port of DecoratorCascadeModifier.getValidPosition: full enumeration of the 7x7x7
/// (origin +/-3) cube in world space, clipped to candidates whose world chunk matches the
/// source's own chunk (`x>>4`/`z>>4` in the decompiled source - a signed arithmetic shift by 4,
/// which is exactly `div_euclid(16)` for both positive and negative coordinates) - a source
/// within 3 blocks of a chunk edge therefore has a correspondingly smaller real search volume,
/// exactly like the live game. Every valid candidate in the clipped cube is reservoir-sampled
/// with equal probability (the search never stops at the first hit), matching the decompiled
/// `random.nextInt(++index) != 0` pattern precisely.
///
/// Validity = target cell is air-or-liquid AND the cell below passes the real game's
/// `isFaceSturdy(below, UP)` check (verified directly in DecoratorCascadeModifier's decompiled
/// `getValidPosition` - it is not a blanket solidity test). Approximated here as solid-and-not-
/// liquid-and-not-a-chest: a water/lava surface isn't sturdy, and neither is an existing chest's
/// hitbox (it doesn't fill its own top face) - both are excluded explicitly since `grid` alone
/// only tracks solid-vs-air and can't tell either case apart from ordinary solid terrain.
#[allow(clippy::too_many_arguments)]
fn find_cascade_spot(
    grid: &VoxelGrid,
    liquid: &HashSet<(i32, i32, i32)>,
    chest_positions: &HashSet<(i32, i32, i32)>,
    non_sturdy: &HashSet<(i32, i32, i32)>,
    inverse: &Transform,
    origin: (i32, i32, i32),
    chunk: (i32, i32),
    rng: &mut impl Rng,
) -> Option<(i32, i32, i32)> {
    let mut index: u32 = 0;
    let mut result = None;
    for y in (origin.1 - 3)..=(origin.1 + 3) {
        for x in (origin.0 - 3)..=(origin.0 + 3) {
            for z in (origin.2 - 3)..=(origin.2 + 3) {
                if x.div_euclid(16) != chunk.0 || z.div_euclid(16) != chunk.1 {
                    continue;
                }
                let local = inverse.apply((x, y, z));
                let below = (local.0, local.1 - 1, local.2);
                let target_open = !grid.is_solid(local) || liquid.contains(&local);
                let floor_ok = grid.is_solid(below)
                    && !liquid.contains(&below)
                    && !non_sturdy.contains(&below)
                    && !chest_positions.contains(&below);
                if !target_open || !floor_ok {
                    continue;
                }
                index += 1;
                if rng.gen_range(0..index) == 0 {
                    result = Some(local);
                }
            }
        }
    }
    result
}

/// Counts every position within `grid`'s own bounds that could ever hold a chest - the same
/// air-or-liquid-with-a-sturdy-non-chest-floor validity rule `find_cascade_spot` uses, just
/// scanning the whole room instead of one source's local search cube. An existing chest's own
/// position always counts as one occupied slot unconditionally (even on the rare baseline
/// layouts where one chest's "floor" is another chest, which decorator placement itself could
/// never produce but a hand-built room template can) - this guarantees a chest is never missed
/// by the slot count, so `target_chest_count / saturation_number` can't mathematically exceed
/// 100% from that edge case. Meant to be computed once on a room's pre-modifier baseline state
/// (its result doesn't change with add/cascade counts - it's the room's fixed total capacity).
pub fn count_chest_slots(
    grid: &VoxelGrid,
    liquid: &HashSet<(i32, i32, i32)>,
    chest_positions: &HashSet<(i32, i32, i32)>,
    non_sturdy: &HashSet<(i32, i32, i32)>,
) -> usize {
    let (sx, sy, sz) = grid.size();
    let mut count = 0usize;
    for x in 0..sx {
        for y in 0..sy {
            for z in 0..sz {
                let p = (x, y, z);
                if chest_positions.contains(&p) {
                    count += 1;
                    continue;
                }
                let below = (x, y - 1, z);
                let target_open = !grid.is_solid(p) || liquid.contains(&p);
                let floor_ok = grid.is_solid(below)
                    && !liquid.contains(&below)
                    && !non_sturdy.contains(&below)
                    && !chest_positions.contains(&below);
                if target_open && floor_ok {
                    count += 1;
                }
            }
        }
    }
    count
}
