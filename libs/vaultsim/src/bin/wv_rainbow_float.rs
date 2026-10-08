// Find DETACHED floating solid blocks in the bare assembled room (no decorator passes at all):
// a solid cell with AIR directly above AND directly below it. The user reports these exist near the
// center of rainbow1 at mid Y and that in real gameplay those positions are just air - i.e. the sim
// is recording a solid block the game doesn't. Identify exactly what each one is via the assembler
// name-trace (raw block name / "jigsaw->final_state" / "placeholder:type"), so we can see whether
// it's a real block, a mis-resolved jigsaw marker, or a placeholder.
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::SeedableRng;

use wv_chest_sim::assemble;
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::structure::Structure;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let room_name = args.get(1).map(String::as_str).unwrap_or("rainbow1");
    let depth: i32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(10);

    let gen_root = wv_chest_sim::paths::gen_root();
    let room = format!("{gen_root}\\structures\\vault\\rooms\\common\\{room_name}.nbt");
    let assets = FsAssetSource::new(gen_root.clone());
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();
    let root = Rc::new(Structure::load(&PathBuf::from(&room)).unwrap());

    let trials = 8;
    // label -> (vertically-isolated count, fully-isolated count [all 6 neighbours air])
    let mut by_label: HashMap<String, (u64, u64)> = HashMap::new();
    let mut printed_examples = 0;

    for t in 0..trials {
        assemble::enable_name_trace();
        let work = assemble::assemble(&root, &data, &mut rng, depth);
        let names = assemble::take_name_trace();
        let grid = &work.solid;
        let (sx, sy, sz) = grid.size();

        for x in 1..sx - 1 {
            for y in 1..sy - 1 {
                for z in 1..sz - 1 {
                    let p = (x, y, z);
                    if !grid.is_solid(p) {
                        continue;
                    }
                    let air_up = !grid.is_solid((x, y + 1, z));
                    let air_dn = !grid.is_solid((x, y - 1, z));
                    if !(air_up && air_dn) {
                        continue; // need air directly above AND below
                    }
                    let air_sides = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)]
                        .iter()
                        .all(|&(dx, dy, dz)| !grid.is_solid((x + dx, y + dy, z + dz)));
                    let label = names.get(&p).cloned().unwrap_or_else(|| "<solid,untraced>".to_string());
                    let e = by_label.entry(label.clone()).or_default();
                    e.0 += 1;
                    if air_sides {
                        e.1 += 1;
                    }

                    // Print a few concrete fully-isolated examples near the room center on trial 0.
                    let near_center = (x - sx / 2).abs() <= 12 && (z - sz / 2).abs() <= 12;
                    if t == 0 && air_sides && near_center && printed_examples < 25 {
                        println!(
                            "  isolated solid @ local ({:2},{:2},{:2})  [center=({},{}), midY≈{}]  ->  {}",
                            x, y, z, sx / 2, sz / 2, sy / 2, label
                        );
                        printed_examples += 1;
                    }
                }
            }
        }
    }

    println!("\n=== {room_name}  (depth={depth}, {trials} trials) ===");
    println!("Detached solid blocks (air directly above AND below), by identity:");
    println!("{:>10} {:>10}   label", "vert-iso", "fully-iso");
    let mut v: Vec<_> = by_label.into_iter().collect();
    v.sort_by(|a, b| b.1 .0.cmp(&a.1 .0));
    for (label, (vert, full)) in v {
        println!(
            "{:>10.1} {:>10.1}   {}",
            vert as f64 / trials as f64,
            full as f64 / trials as f64,
            label
        );
    }
    println!("(counts are avg per assembled room; 'fully-iso' = all 6 neighbours air)");
}
