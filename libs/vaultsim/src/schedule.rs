//! The chest modifiers applied in the order the_vault 3.21.6 actually runs them (decompiled
//! `GridGenerator.generate(Vault, ServerLevelAccessor, ChunkPos)`, `DecoratorAddModifier`,
//! `DecoratorCascadeModifier`), replacing the one-round-per-room-chunk passes in `decorator.rs`.
//!
//! Per chunk, every region overlapping it is placed in x-then-z order, and each placement fires
//! TEMPLATE_GENERATION POST for that (region, chunk):
//! - every decorator_add listener makes its attempts over the WHOLE chunk, not just the region just
//!   placed (skipped for NONE cells and region (0,0); Wold's Vaults forces the room-type whitelist
//!   to pass). Attempts landing in a room placed earlier in the chunk are valid there.
//! - then every decorator_cascade modifier processes every non-duped target tile in the chunk. A tile
//!   is tagged with the "population" phase the first time and is NOT skipped by later population
//!   events, so it cascades again at every later event in its chunk. One modifier runs all of its
//!   stacks per source in sequence, drawing from a candidate list (valid cells of the source's
//!   +/-3 cube, clipped to the chunk) without replacement and re-checking each draw.
//!
//! Rooms sit at even/even regions (tunnel_span 1). Their east/south neighbours are tunnels (real
//! templates, so their events add and cascade) and their diagonal neighbours are NONE cells (their
//! events cascade only). A room's chests in chunks it shares with a later region therefore get
//! extra add attempts and extra cascade rounds. Against six recent living vaults (45/45, 30/30,
//! 51/74 stacks), this schedule moved simulated chest counts per template from 0.65-0.82 of real
//! to 0.87-0.97, and clumpiness from 0.42-0.69 to 0.76-0.89, inside the vault-to-vault spread of
//! identical crystals (0.87-1.14 N, 0.80-1.39 clumpiness).

use std::collections::HashSet;

use rand::Rng;

use crate::assemble::{AssemblyResult, ChestSpot};
use crate::decorator::{chunks_overlapped, room_world_transform};
use crate::strongbox;
use crate::transform::{Rotation, Transform};
use crate::voxel::VoxelGrid;

type P = (i32, i32, i32);

/// A random room region: even/even (a ROOM cell with tunnel_span 1), never the start room (0,0).
pub fn random_room_region(rng: &mut impl Rng) -> (i32, i32) {
    loop {
        let g = (2 * rng.gen_range(-250..250), 2 * rng.gen_range(-250..250));
        if g != (0, 0) {
            return g;
        }
    }
}

/// One cascade modifier: how many stacks of it the crystal has and its per-stack chance.
#[derive(Clone, Copy, Debug)]
pub struct Cascade {
    pub stacks: u32,
    pub chance: f32,
}

struct World<'a> {
    grid: &'a mut VoxelGrid,
    liquid: &'a mut HashSet<P>,
    non_sturdy: &'a HashSet<P>,
    occ: &'a mut HashSet<P>,
}

impl World<'_> {
    fn sturdy_floor(&self, local: P) -> bool {
        let below = (local.0, local.1 - 1, local.2);
        self.grid.is_solid(below) && !self.liquid.contains(&below) && !self.non_sturdy.contains(&below) && !self.occ.contains(&below)
    }

    fn is_air(&self, local: P) -> bool {
        !self.grid.is_solid(local) && !self.liquid.contains(&local)
    }

    fn cascade_valid(&self, local: P) -> bool {
        (!self.grid.is_solid(local) || self.liquid.contains(&local)) && self.sturdy_floor(local)
    }
}

struct Tile {
    spot: ChestSpot,
    duped: bool,
}

/// Apply `add_stacks` decorator_add modifiers (`add_attempts` attempts per chunk each) and the
/// `cascades` to a room at `region` (even/even), in the game's event order. Bonus chests roll the
/// strongbox upgrade when `vault_level` is given. Returns every chest placed (bonus and cascade);
/// `asm.solid`/`asm.liquid` are updated in place.
#[allow(clippy::too_many_arguments)]
pub fn apply_schedule(
    asm: &mut AssemblyResult,
    region: (i32, i32),
    rotation: Rotation,
    cell_size: i32,
    target: &'static str,
    add_stacks: u32,
    add_attempts: u32,
    cascades: &[Cascade],
    vault_level: Option<u32>,
    rng: &mut impl Rng,
) -> Vec<ChestSpot> {
    let bonus = match vault_level {
        Some(level) => Bonus::Level(level),
        None => Bonus::Plain,
    };
    apply_schedule_with(asm, region, rotation, cell_size, target, add_stacks, add_attempts, cascades, bonus, rng)
}

/// How a Bonus X output placeholder resolves.
#[derive(Clone, Copy)]
pub enum Bonus<'d> {
    /// Always the plain target chest.
    Plain,
    /// `strongbox::strongbox_chance` for this vault level (non-map palettes).
    Level(u32),
    /// The room chain's resolved placeholder distribution (`PaletteLibrary::bonus_dist`): chest,
    /// strongbox, enigma or air. Strongboxes and enigma chests occupy the cell and never cascade.
    Dist(&'d crate::palette::Dist),
}

/// `apply_schedule` with an explicit bonus-resolution mode.
#[allow(clippy::too_many_arguments)]
pub fn apply_schedule_with(
    asm: &mut AssemblyResult,
    region: (i32, i32),
    rotation: Rotation,
    cell_size: i32,
    target: &'static str,
    add_stacks: u32,
    add_attempts: u32,
    cascades: &[Cascade],
    bonus: Bonus,
    rng: &mut impl Rng,
) -> Vec<ChestSpot> {
    let outer = room_world_transform(region, rotation, cell_size);
    let inv = outer.inverse();
    let non_sturdy = asm.non_sturdy.clone();
    let mut occ: HashSet<P> = asm.chests.iter().map(|c| c.pos).collect();
    let mut tiles: Vec<Tile> = asm.chests.iter().map(|c| Tile { spot: c.clone(), duped: false }).collect();
    let baseline = tiles.len();
    {
        let mut w = World { grid: &mut asm.solid, liquid: &mut asm.liquid, non_sturdy: &non_sturdy, occ: &mut occ };
        for (cx, cz) in chunks_overlapped(region.0 * cell_size, region.1 * cell_size, cell_size) {
            let gx0 = (cx * 16).div_euclid(cell_size);
            let gx1 = (cx * 16 + 15).div_euclid(cell_size);
            let gz0 = (cz * 16).div_euclid(cell_size);
            let gz1 = (cz * 16 + 15).div_euclid(cell_size);
            let mut after = false;
            for gx in gx0..=gx1 {
                for gz in gz0..=gz1 {
                    if (gx, gz) == region {
                        after = true;
                    } else if !after {
                        continue;
                    }
                    let none_cell = gx.rem_euclid(2) == 1 && gz.rem_euclid(2) == 1;
                    if !none_cell && (gx, gz) != (0, 0) {
                        add_round(&mut w, &inv, (cx, cz), add_stacks, add_attempts, target, bonus, &mut tiles, rng);
                    }
                    cascade_round(&mut w, &outer, &inv, (cx, cz), cascades, target, &mut tiles, rng);
                }
            }
        }
    }
    tiles.into_iter().skip(baseline).map(|t| t.spot).collect()
}

#[allow(clippy::too_many_arguments)]
fn add_round(
    w: &mut World,
    inv: &Transform,
    chunk: (i32, i32),
    stacks: u32,
    attempts: u32,
    target: &'static str,
    bonus: Bonus,
    tiles: &mut Vec<Tile>,
    rng: &mut impl Rng,
) {
    for _ in 0..stacks {
        for _ in 0..attempts {
            let wx = rng.gen_range(chunk.0 * 16..chunk.0 * 16 + 16);
            let wz = rng.gen_range(chunk.1 * 16..chunk.1 * 16 + 16);
            let wy = rng.gen_range(0..64);
            let local = inv.apply((wx, wy, wz));
            let above = (local.0, local.1 + 1, local.2);
            if !w.is_air(local) || !w.is_air(above) || !w.sturdy_floor(local) {
                continue;
            }
            let mut spot = [ChestSpot { pos: local, chest_type: target, is_strongbox: false }];
            match bonus {
                Bonus::Plain => {}
                Bonus::Level(level) => strongbox::apply_strongbox_rolls(&mut spot, level, rng),
                Bonus::Dist(d) => match crate::palette::sample(d, rng) {
                    crate::palette::Cell::Chest { ty, strongbox } => {
                        spot[0].chest_type = ty;
                        spot[0].is_strongbox = strongbox;
                    }
                    crate::palette::Cell::OtherChest => spot[0].chest_type = "enigma_chest",
                    crate::palette::Cell::Air | crate::palette::Cell::Gate => continue,
                    crate::palette::Cell::Block { .. } => {
                        eprintln!("[schedule] fallback: bonus placeholder resolved to a plain block; placed as the target chest");
                    }
                },
            }
            w.grid.set(local, true);
            w.occ.insert(local);
            let [spot] = spot;
            tiles.push(Tile { spot, duped: false });
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn cascade_round(
    w: &mut World,
    outer: &Transform,
    inv: &Transform,
    chunk: (i32, i32),
    cascades: &[Cascade],
    target: &'static str,
    tiles: &mut Vec<Tile>,
    rng: &mut impl Rng,
) {
    let (x0, x1, z0, z1) = (chunk.0 * 16, chunk.0 * 16 + 15, chunk.1 * 16, chunk.1 * 16 + 15);
    for c in cascades {
        if c.stacks == 0 || c.chance <= 0.0 {
            continue;
        }
        let sources: Vec<P> = tiles
            .iter()
            .filter(|t| !t.duped && t.spot.chest_type == target && !t.spot.is_strongbox)
            .map(|t| outer.apply(t.spot.pos))
            .filter(|o| o.0 >= x0 && o.0 <= x1 && o.2 >= z0 && o.2 <= z1)
            .collect();
        for o in sources {
            let mut cand: Option<Vec<P>> = None;
            for _ in 0..c.stacks {
                let mut p = c.chance;
                while p > 0.0 && rng.r#gen::<f32>() < p {
                    p -= 1.0;
                    if cand.is_none() {
                        let mut v = Vec::new();
                        for y in o.1 - 3..=o.1 + 3 {
                            for x in (o.0 - 3).max(x0)..=(o.0 + 3).min(x1) {
                                for z in (o.2 - 3).max(z0)..=(o.2 + 3).min(z1) {
                                    let l = inv.apply((x, y, z));
                                    if w.cascade_valid(l) {
                                        v.push(l);
                                    }
                                }
                            }
                        }
                        cand = Some(v);
                    }
                    let list = cand.as_mut().unwrap();
                    let mut got = None;
                    while !list.is_empty() {
                        let k = rng.gen_range(0..list.len());
                        let l = list.swap_remove(k);
                        if w.cascade_valid(l) {
                            got = Some(l);
                            break;
                        }
                    }
                    if let Some(l) = got {
                        w.grid.set(l, true);
                        w.liquid.remove(&l);
                        w.occ.insert(l);
                        tiles.push(Tile { spot: ChestSpot { pos: l, chest_type: target, is_strongbox: false }, duped: true });
                    }
                }
            }
        }
    }
}
