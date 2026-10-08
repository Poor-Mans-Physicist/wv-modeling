//! 4-room line solved PER ROOM, stitched by simple visual hallways. Flight-penalty × bail sweep.
//!
//! Every room has 4 gate doorways (one per wall, all at y=24 — verified from the gate placeholders).
//! The line runs along Z: each room's -Z gate (23,24,0) is its entrance, its +Z gate (23,24,46) is
//! its exit. The solver runs **independently on each room** (entrance gate → exit gate, on the room's
//! own grid) and knows nothing about hallways. Rooms are placed a few blocks apart; a hallway is just
//! a short floor strip + a gray travel line from one room's red exit to the next room's green entrance.
//! Common rooms only. Writes 20 renders routes/line_f<mult>_b<bail>.html (local three.min.js).
//!
//! Run: cargo run --release --bin wv_line [target] [bonus] [cascade] [seed]

use std::collections::HashSet;
use std::path::Path;
use std::rc::Rc;
use std::time::Instant;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::decorator;
use wv_chest_sim::pathfind::WalkGraph;
use wv_chest_sim::route::{plan_route, render_html};
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;
use wv_chest_sim::voxel::{self, VoxelGrid};

const CELL: i32 = 47;
const GAP: i32 = 6; // hallway length between rooms (blocks)
const MAX_DEPTH: i32 = 10;
const ENT: (i32, i32, i32) = (23, 24, 0); // room-local -Z gate (entrance)
const EXI: (i32, i32, i32) = (23, 24, CELL - 1); // room-local +Z gate (exit), z=46
type P = (i32, i32, i32);

fn static_target(s: &str) -> &'static str {
    match s {
        "wooden_chest" => "wooden_chest",
        "gilded_chest" => "gilded_chest",
        "living_chest" => "living_chest",
        "ornate_chest" => "ornate_chest",
        o => panic!("unknown chest type {o:?}"),
    }
}
fn random_nonzero_region(rng: &mut impl Rng) -> (i32, i32) {
    loop {
        let g = (rng.gen_range(-500..500), rng.gen_range(-500..500));
        if g != (0, 0) {
            return g;
        }
    }
}
fn assemble_cell(path: &Path, data: &DataSource, rng: &mut SmallRng, add: u32, cascade: u32, target: &'static str) -> (VoxelGrid, Vec<P>) {
    let root = Rc::new(Structure::load(path).unwrap());
    let mut asm = assemble::assemble(&root, data, rng, MAX_DEPTH);
    let mut extra: Vec<ChestSpot> = Vec::new();
    if add > 0 || cascade > 0 {
        let region = random_nonzero_region(rng);
        let rot = Rotation::random(rng);
        let mut occ: HashSet<P> = asm.chests.iter().map(|c| c.pos).collect();
        for _ in 0..add {
            extra.extend(decorator::decorator_add_pass(&mut asm.solid, &mut occ, &asm.liquid, &asm.non_sturdy, region, rot, CELL, 8, true, target, rng));
        }
        let src: Vec<ChestSpot> = asm.chests.iter().cloned().chain(extra.iter().cloned()).collect();
        for _ in 0..cascade {
            extra.extend(decorator::decorator_cascade_pass(&mut asm.solid, &mut asm.liquid, &mut occ, &asm.non_sturdy, &src, region, rot, CELL, 0.25, target, rng));
        }
    }
    let pts: Vec<P> = asm.chests.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos).chain(extra.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos)).collect();
    (asm.solid, pts)
}
fn y_range(solid: &VoxelGrid) -> i32 {
    let g = WalkGraph::build(solid);
    if g.nodes.is_empty() {
        return 0;
    }
    let ys: Vec<i32> = g.nodes.iter().map(|p| p.1).collect();
    ys.iter().max().unwrap() - ys.iter().min().unwrap()
}

struct Room {
    solid: VoxelGrid,
    chests: Vec<P>,
    zoff: i32,
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let target = static_target(a.get(1).map(String::as_str).unwrap_or("gilded_chest"));
    let add: u32 = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(55);
    let cascade: u32 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(55);
    let seed: u64 = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(1);

    let gen_root = wv_chest_sim::paths::gen_root();
    let rooms_dir = format!(r"{gen_root}\structures\vault\rooms\common");
    let assets = FsAssetSource::new(&gen_root);
    let data = DataSource::new(&assets);

    let pool = ["cliffs1", "cliffs2", "cliffs3", "cliffs4", "bee2", "ore3", "mustard3", "lakes2", "rainbow_forest1", "pirate2"];
    let mut ranked: Vec<(&str, i32)> = pool
        .iter()
        .map(|&name| {
            let mut rng = SmallRng::seed_from_u64(seed.wrapping_add(name.len() as u64));
            let (solid, _) = assemble_cell(Path::new(&format!(r"{rooms_dir}\{name}.nbt")), &data, &mut rng, 0, 0, target);
            (name, y_range(&solid))
        })
        .collect();
    ranked.sort_by_key(|x| -x.1);
    let m = ranked.len();
    let picks = [ranked[0].0, ranked[m - 1].0, ranked[1].0, ranked[m - 2].0];
    println!("room verticality {ranked:?}\n4-room line order: {picks:?}\n");

    // assemble the 4 rooms once; gates are already air openings so no carving is needed
    let stride = CELL + GAP;
    let mut rooms: Vec<Room> = Vec::new();
    for (ri, &name) in picks.iter().enumerate() {
        let mut rng = SmallRng::seed_from_u64(seed.wrapping_mul(131).wrapping_add(ri as u64));
        let (solid, chests) = assemble_cell(Path::new(&format!(r"{rooms_dir}\{name}.nbt")), &data, &mut rng, add, cascade, target);
        rooms.push(Room { solid, chests, zoff: ri as i32 * stride });
    }
    let width = (rooms.len() as i32 - 1) * stride + CELL;

    // composite the visual terrain: rooms at their z-offset + a short floor strip in each hallway
    let mut big = VoxelGrid::new((CELL, CELL, width));
    for room in &rooms {
        for x in 0..CELL {
            for y in 0..CELL {
                for z in 0..CELL {
                    big.set((x, y, room.zoff + z), room.solid.is_solid((x, y, z)));
                }
            }
        }
    }
    for ri in 0..rooms.len() - 1 {
        let z0 = rooms[ri].zoff + CELL; // first hallway block
        for z in z0..z0 + GAP {
            for x in 21..=25 {
                for y in 22..=23 {
                    big.set((x, y, z), true); // hallway floor strip (gate is at y=24)
                }
            }
        }
    }

    let all_chests_line: Vec<P> = rooms.iter().flat_map(|r| r.chests.iter().map(|c| (c.0, c.1, c.2 + r.zoff))).collect();
    let pts_set: HashSet<P> = all_chests_line.iter().copied().collect();
    let (boxes, _) = voxel::merge_terrain_boxes(&big, 3, &pts_set);
    println!("line {width} long, {} {target} across {} rooms (gates at y=24)\n", all_chests_line.len(), rooms.len());

    let mults = [1.0, 1.5, 2.0, 2.5, 3.0];
    let bails = [0.6, 1.5, 3.0, 5.0];
    println!(" flight x bail -> breaks, coverage, walk/fly blocks (per-room solved):");
    for &mult in &mults {
        for &bail in &bails {
            let t = Instant::now();
            let mut segments: Vec<(char, Vec<P>)> = Vec::new();
            let mut chest_render: Vec<P> = Vec::new();
            let mut state_render: Vec<&str> = Vec::new();
            let mut order_render: Vec<P> = Vec::new();
            let mut markers: Vec<(P, char)> = Vec::new();
            let (mut tb, mut tc, mut wb, mut fb) = (0usize, 0usize, 0.0f64, 0.0f64);
            let mut prev_exit: Option<P> = None;
            for room in &rooms {
                let z = room.zoff;
                let off = |p: P| (p.0, p.1, p.2 + z);
                let plan = plan_route(&room.solid, &room.chests, ENT, EXI, mult, bail);
                if let Some(pe) = prev_exit {
                    segments.push(('h', vec![pe, off(ENT)])); // gray hallway travel line (not solved)
                }
                for (mm, path) in &plan.segments {
                    segments.push((*mm, path.iter().map(|p| off(*p)).collect()));
                }
                for p in &plan.order {
                    order_render.push(off(*p));
                }
                for (i, c) in room.chests.iter().enumerate() {
                    chest_render.push(off(*c));
                    state_render.push(plan.state[i]);
                }
                markers.push((off(ENT), 'i')); // green entrance
                markers.push((off(EXI), 'o')); // red exit
                tb += plan.breaks.len();
                tc += plan.collected;
                wb += plan.walk_b;
                fb += plan.fly_b;
                prev_exit = Some(off(EXI));
            }
            let cov = 100.0 * tc as f64 / all_chests_line.len().max(1) as f64;
            let hud = format!(
                "4-room line (solved per room) &nbsp; {target} +{add}/+{cascade}<br><b>FLIGHT x{mult}</b> &nbsp; <b>BAIL {bail}/blk</b><br>chests {} &nbsp; breaks {tb} &nbsp; collected {tc} ({cov:.0}%)<br>walk {wb:.0} + fly {fb:.0} blk &nbsp; green=in red=out gray=hallway<br>drag=orbit wheel=zoom",
                all_chests_line.len(),
            );
            let html = render_html((CELL, CELL, width), &boxes, &chest_render, &state_render, &segments, &order_render, &markers, &hud, "three.min.js");
            std::fs::write(wv_chest_sim::paths::out_path(&format!("line_f{mult}_b{bail}.html")), &html).unwrap();
            println!("  f{mult} b{bail}: {tb:3} breaks, {cov:3.0}% cov, walk {wb:4.0} + fly {fb:4.0} blk  ({:.1}s)", t.elapsed().as_secs_f64());
        }
    }
    println!("\nwrote 20 renders -> {}", wv_chest_sim::paths::out_path("line_f<mult>_b<bail>.html"));
}
