// Reproduce the user's screenshot case (mustard4, ~1000 gilded) and identify the "floating blocks"
// that end up with a gilded chest ON TOP and another gilded chest DIRECTLY UNDER them. Cascade can
// place a chest into an air pocket directly below a solid block (its target check is air-or-liquid +
// sturdy floor below, with NO "block above must be air" condition - unlike decorator_add), so any
// isolated solid block can get a chest cascaded beneath it and another floored on top. This dumps
// what those middle blocks actually are (via the assembler name-trace), so we can say definitively
// whether they're real terrain/decor or a sim artifact.
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::decorator;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;

const CELL_SIZE: i32 = 47;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let add_count: u32 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(60);
    let cascade_count: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(60);
    let map_pct: f32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(300.0);

    let gen_root = wv_chest_sim::paths::gen_root();
    let room = format!("{gen_root}\\structures\\vault\\rooms\\common\\mustard4.nbt");
    let chest_type = "gilded_chest";

    let assets = FsAssetSource::new(gen_root.clone());
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();
    let root = Rc::new(Structure::load(&PathBuf::from(&room)).unwrap());

    let trials = 10;
    let mut total = 0u64;
    let mut floating = 0u64; // chest whose floor block has air directly below the block
    let mut sandwich = 0u64; // chest / block / chest, vertically
    let mut by_block: HashMap<String, u64> = HashMap::new();
    let mut sandwich_by_block: HashMap<String, u64> = HashMap::new();

    for _ in 0..trials {
        assemble::enable_name_trace();
        let mut work = assemble::assemble(&root, &data, &mut rng, 10);
        let names = assemble::take_name_trace();

        // --- replicate apply_modifiers (positions only; strongbox doesn't move anything) ---
        let region = loop {
            let r = (rng.gen_range(-3..=3), rng.gen_range(-3..=3));
            if r != (0, 0) {
                break r;
            }
        };
        let rotation = Rotation::random(&mut rng);
        let mut chest_positions: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
        let mut extra: Vec<ChestSpot> = Vec::new();
        for _ in 0..add_count {
            let added = decorator::decorator_add_pass(
                &mut work.solid, &mut chest_positions, &work.liquid, &work.non_sturdy,
                region, rotation, CELL_SIZE, 8, true, chest_type, &mut rng,
            );
            extra.extend(added);
        }
        let sources: Vec<ChestSpot> = work.chests.iter().cloned().chain(extra.iter().cloned()).collect();
        for _ in 0..cascade_count {
            let c = decorator::decorator_cascade_pass(
                &mut work.solid, &mut work.liquid, &mut chest_positions, &work.non_sturdy,
                &sources, region, rotation, CELL_SIZE, 0.25, chest_type, &mut rng,
            );
            extra.extend(c);
        }
        if map_pct > 0.0 {
            let c = decorator::decorator_cascade_pass(
                &mut work.solid, &mut work.liquid, &mut chest_positions, &work.non_sturdy,
                &sources, region, rotation, CELL_SIZE, map_pct / 100.0, chest_type, &mut rng,
            );
            extra.extend(c);
        }
        let final_chests: Vec<ChestSpot> = work.chests.iter().cloned().chain(extra).collect();
        let cpos: HashSet<(i32, i32, i32)> = final_chests.iter().map(|c| c.pos).collect();
        let grid = &work.solid;

        for c in &final_chests {
            if c.chest_type != chest_type {
                continue;
            }
            total += 1;
            let (x, y, z) = c.pos;
            let b = (x, y - 1, z); // floor block under this chest
            if !grid.is_solid(b) || cpos.contains(&b) {
                continue; // floor must be a NON-chest solid block
            }
            let label = names.get(&b).cloned().unwrap_or_else(|| "<solid,untraced>".to_string());
            let under_b = (x, y - 2, z);
            if !grid.is_solid(under_b) {
                floating += 1; // the floor block is itself floating (air directly beneath it)
                *by_block.entry(label.clone()).or_default() += 1;
            }
            if cpos.contains(&under_b) {
                sandwich += 1; // chest directly below the floor block too -> chest/block/chest
                *sandwich_by_block.entry(label.clone()).or_default() += 1;
            }
        }
    }

    println!("mustard4  add={add_count} cascade={cascade_count} map={map_pct}%  trials={trials}");
    println!("avg gilded / room                                    : {:.0}", total as f64 / trials as f64);
    println!("avg gilded on a FLOATING floor-block (air below block): {:.1}", floating as f64 / trials as f64);
    println!("avg chest/block/chest SANDWICHES                     : {:.1}", sandwich as f64 / trials as f64);

    let dump = |t: &str, m: &HashMap<String, u64>| {
        println!("\n{t} (avg/room):");
        let mut v: Vec<_> = m.iter().collect();
        v.sort_by(|a, b| b.1.cmp(a.1));
        for (k, c) in v.into_iter().take(25) {
            println!("  {:6.2}  {}", *c as f64 / trials as f64, k);
        }
    };
    dump("Floating floor-block identity (chest on top, block has air under it)", &by_block);
    dump("Sandwich middle-block identity (chest above AND chest below the block)", &sandwich_by_block);
}
