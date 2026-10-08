use std::collections::{BTreeMap, HashMap, HashSet};

/// Solid/air voxel storage for one assembled room, in the room's own local frame.
/// The root room structure is always an exact dense `size`-shaped cube (verified: every
/// sampled room file is a fully-dense 47^3 box with no sparse regions), so the hot path -
/// pasting that ~100k-block base, then querying it many times per decorator_add attempt -
/// uses a flat indexed Vec rather than a HashMap. Decor pieces are pasted on top via the same
/// `set` call; the rare case where a piece's geometry lands outside the nominal box (never
/// directly observed, but not proven impossible) falls back to a small overflow map instead
/// of panicking or silently dropping data.
///
/// Absent-from-everything positions default to **solid**, not air - confirmed directly from
/// `data/the_vault/dimension/vault.json` (`"generator":{"type":"minecraft:flat","settings":
/// {"layers":[{"block":"the_vault:vault_bedrock","height":64}]}}`) plus `dimension_type/vault.json`
/// (`"min_y":0`): the vault dimension is a flat world with solid `vault_bedrock` filling world Y
/// 0-63 *before* any room/tunnel content is pasted in - exactly the same range decorator_add's
/// own `y = random.nextInt(64)` samples. A room occupies world Y 9-55, entirely inside that
/// band, so anything the room/decor pieces never explicitly carved into air (including the open
/// space directly above a room's own ceiling) is real solid bedrock, not void. An earlier version
/// of this model defaulted absent positions to air on the mistaken assumption that the vault
/// dimension was a true void outside placed structures (conflating it with `SkyVaultsChunkGenerator`,
/// a different, unrelated generator never actually wired to the vault dimension) - that bug let
/// decorator_add/cascade wrongly place chests "floating" above room ceilings, contradicted by
/// direct gameplay observation (treasure-goggles x-ray, well over 100 blocks range, never once
/// showing a chest above a ceiling) and now fixed.
#[derive(Clone)]
pub struct VoxelGrid {
    size: (i32, i32, i32),
    dense: Vec<bool>,
    overflow: HashMap<(i32, i32, i32), bool>,
}

impl VoxelGrid {
    pub fn new(size: (i32, i32, i32)) -> VoxelGrid {
        let len = (size.0.max(0) as usize) * (size.1.max(0) as usize) * (size.2.max(0) as usize);
        VoxelGrid {
            size,
            dense: vec![false; len],
            overflow: HashMap::new(),
        }
    }

    fn in_bounds(&self, p: (i32, i32, i32)) -> bool {
        p.0 >= 0 && p.0 < self.size.0 && p.1 >= 0 && p.1 < self.size.1 && p.2 >= 0 && p.2 < self.size.2
    }

    fn index(&self, p: (i32, i32, i32)) -> usize {
        ((p.0 * self.size.1 + p.1) * self.size.2 + p.2) as usize
    }

    /// Always records the explicit state for out-of-bounds positions (both solid and air), never
    /// removing - the default below is now solid, so an explicit air write from a decor piece
    /// must stay recorded, not fall back to "absent".
    pub fn set(&mut self, p: (i32, i32, i32), solid: bool) {
        if self.in_bounds(p) {
            let idx = self.index(p);
            self.dense[idx] = solid;
        } else {
            self.overflow.insert(p, solid);
        }
    }

    /// Out-of-bounds and never-explicitly-touched defaults to **solid** (vault_bedrock) - see the
    /// struct doc comment for the dimension-config verification behind this.
    pub fn is_solid(&self, p: (i32, i32, i32)) -> bool {
        if self.in_bounds(p) {
            self.dense[self.index(p)]
        } else {
            *self.overflow.get(&p).unwrap_or(&true)
        }
    }

    pub fn size(&self) -> (i32, i32, i32) {
        self.size
    }

    /// Scans downward from (and including) `y` at the given (x,z) column for the first solid
    /// cell - used to find a doorway's actual walkable floor from its gate marker's Y, since the
    /// real offset varies by room theme (confirmed: cliffs1 floor at gate_Y-1, arcade at gate_Y-3
    /// - see MECHANICS_NOTES.md). Stops at `min_y` to avoid scanning forever on a room with no floor
    /// under that column at all (shouldn't happen for a real gate, but never loops infinitely).
    pub fn floor_y_below(&self, x: i32, z: i32, start_y: i32, min_y: i32) -> i32 {
        let mut y = start_y;
        while y > min_y && !self.is_solid((x, y, z)) {
            y -= 1;
        }
        y
    }
}

/// Downsamples `grid`'s solid voxels into `factor`^3 coarse cells (occupied if any constituent
/// block is solid and not in `exclude` - typically the current chest position set, so the
/// terrain mesh doesn't visually swallow chest markers, and also means a cell touching a chest
/// is already "exposed" the same as one touching air), then merges exposed coarse cells into
/// 2D-per-layer greedy rectangles, scaled back into room-local block coordinates.
///
/// A coarse cell counts as "exposed" only if it has a neighbor *within the analyzed grid* that
/// isn't occupied - a neighbor that falls outside the grid's bounds does not count, since beyond
/// the room's own structure is more vault bedrock (see `VoxelGrid`'s doc comment), not air. That
/// means the room's outer hull (walls/floor/ceiling facing the surrounding bedrock) is never
/// rendered, only genuine interior surfaces touching real air pockets or chests are.
///
/// Returns the merged boxes as (min, max) pairs plus the raw exposed-cell count before merging.
/// Shared by the native CLI's `export` mode and the wasm crate's room renderer so both stay in
/// sync.
pub fn merge_terrain_boxes(
    grid: &VoxelGrid,
    factor: i32,
    exclude: &HashSet<(i32, i32, i32)>,
) -> (Vec<((i32, i32, i32), (i32, i32, i32))>, usize) {
    let size = grid.size();
    let coarse_dim = |n: i32| (n + factor - 1) / factor;
    let (cdx, cdy, cdz) = (coarse_dim(size.0), coarse_dim(size.1), coarse_dim(size.2));
    let mut coarse_occupied: HashSet<(i32, i32, i32)> = HashSet::new();
    for cx in 0..cdx {
        for cy in 0..cdy {
            for cz in 0..cdz {
                let mut any_solid = false;
                'scan: for x in (cx * factor)..((cx + 1) * factor).min(size.0) {
                    for y in (cy * factor)..((cy + 1) * factor).min(size.1) {
                        for z in (cz * factor)..((cz + 1) * factor).min(size.2) {
                            if grid.is_solid((x, y, z)) && !exclude.contains(&(x, y, z)) {
                                any_solid = true;
                                break 'scan;
                            }
                        }
                    }
                }
                if any_solid {
                    coarse_occupied.insert((cx, cy, cz));
                }
            }
        }
    }
    let is_coarse_occupied = |p: (i32, i32, i32)| coarse_occupied.contains(&p);
    let in_range =
        |p: (i32, i32, i32)| p.0 >= 0 && p.0 < cdx && p.1 >= 0 && p.1 < cdy && p.2 >= 0 && p.2 < cdz;
    let is_coarse_exposed = |p: (i32, i32, i32)| {
        is_coarse_occupied(p)
            && [
                (p.0 + 1, p.1, p.2),
                (p.0 - 1, p.1, p.2),
                (p.0, p.1 + 1, p.2),
                (p.0, p.1 - 1, p.2),
                (p.0, p.1, p.2 + 1),
                (p.0, p.1, p.2 - 1),
            ]
            .iter()
            .any(|&n| in_range(n) && !is_coarse_occupied(n))
    };

    let mut by_layer: BTreeMap<i32, HashSet<(i32, i32)>> = BTreeMap::new();
    for &(cx, cy, cz) in &coarse_occupied {
        if is_coarse_exposed((cx, cy, cz)) {
            by_layer.entry(cy).or_default().insert((cx, cz));
        }
    }

    let mut boxes = Vec::new();
    let mut exposed_count = 0usize;
    for (&y, cells) in &by_layer {
        exposed_count += cells.len();
        let mut remaining = cells.clone();
        let mut sorted: Vec<(i32, i32)> = cells.iter().copied().collect();
        sorted.sort_unstable_by_key(|&(x, z)| (z, x));
        for &(x0, z0) in &sorted {
            if !remaining.contains(&(x0, z0)) {
                continue;
            }
            let mut x1 = x0;
            while remaining.contains(&(x1 + 1, z0)) {
                x1 += 1;
            }
            let mut z1 = z0;
            'grow_z: loop {
                for x in x0..=x1 {
                    if !remaining.contains(&(x, z1 + 1)) {
                        break 'grow_z;
                    }
                }
                z1 += 1;
            }
            for z in z0..=z1 {
                for x in x0..=x1 {
                    remaining.remove(&(x, z));
                }
            }
            let min = (x0 * factor, y * factor, z0 * factor);
            let max = ((x1 + 1) * factor - 1, (y + 1) * factor - 1, (z1 + 1) * factor - 1);
            boxes.push((min, max));
        }
    }

    (boxes, exposed_count)
}
