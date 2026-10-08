// Diagnostic: find "floating" solid blocks in baseline-assembled common rooms (solid cell with AIR
// directly below it) and report what block they actually are, via the assembler's name trace. The
// user reports mid-room floating blocks with chests on top that don't exist in-game; this tells us
// whether they're real terrain (cave ledges/overhangs - expected) or some marker/placeholder the
// sim is wrongly keeping solid (the bug).
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::SeedableRng;

use wv_chest_sim::assemble;
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::structure::Structure;

fn main() {
    let gen_root = wv_chest_sim::paths::gen_root();
    let rooms_dir = format!("{gen_root}\\structures\\vault\\rooms\\common");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    files.sort();

    let trials = 5;
    // label -> count of floating solid cells that are "isolated" (>=3 horizontal air neighbors)
    let mut isolated: HashMap<String, u64> = HashMap::new();
    // label -> count of floating solid cells that have a (baseline) chest directly on top
    let mut with_chest: HashMap<String, u64> = HashMap::new();

    for path in &files {
        let assets = FsAssetSource::new(gen_root.clone());
        let data = DataSource::new(&assets);
        let mut rng = SmallRng::from_entropy();
        let Ok(root) = Structure::load(path) else { continue };
        let root = Rc::new(root);
        for _ in 0..trials {
            assemble::enable_name_trace();
            let work = assemble::assemble(&root, &data, &mut rng, 10);
            let names = assemble::take_name_trace();
            let chests: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
            let grid = &work.solid;
            let (sx, sy, sz) = grid.size();
            for x in 0..sx {
                for y in 1..sy {
                    for z in 0..sz {
                        let p = (x, y, z);
                        if !grid.is_solid(p) {
                            continue;
                        }
                        if grid.is_solid((x, y - 1, z)) {
                            continue; // supported from below - not floating
                        }
                        let label = names.get(&p).cloned().unwrap_or_else(|| "<untraced>".to_string());
                        if chests.contains(&(x, y + 1, z)) {
                            *with_chest.entry(label.clone()).or_default() += 1;
                        }
                        let horiz_air = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)]
                            .iter()
                            .filter(|&&(dx, dy, dz)| !grid.is_solid((x + dx, y + dy, z + dz)))
                            .count();
                        if horiz_air >= 3 {
                            *isolated.entry(label).or_default() += 1;
                        }
                    }
                }
            }
        }
    }

    // Print a few example vertical columns under floating chest+floor cases, to see the support gap.
    {
        let path = &files[0];
        let assets = FsAssetSource::new(gen_root.clone());
        let data = DataSource::new(&assets);
        let mut rng = SmallRng::from_entropy();
        if let Ok(root) = Structure::load(path) {
            let root = Rc::new(root);
            let mut printed = 0;
            'outer: for _ in 0..20 {
                assemble::enable_name_trace();
                let work = assemble::assemble(&root, &data, &mut rng, 10);
                let names = assemble::take_name_trace();
                let chests: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
                let grid = &work.solid;
                for c in &work.chests {
                    let (x, y, z) = c.pos;
                    let floor = (x, y - 1, z);
                    if grid.is_solid(floor) && !grid.is_solid((x, y - 2, z)) {
                        println!("\n[{}] chest {:?} ({}) on FLOATING floor - column top->down:", path.file_name().unwrap().to_string_lossy(), c.pos, c.chest_type);
                        for dy in (-4i32..=1).rev() {
                            let p = (x, y + dy, z);
                            let lbl = names.get(&p).map(String::as_str).unwrap_or(if grid.is_solid(p) { "<solid,untraced>" } else { "<air>" });
                            let kind = if chests.contains(&p) { "CHEST" } else if grid.is_solid(p) { "solid" } else { "air" };
                            println!("   y{:+}  {:6}  {}", dy, kind, lbl);
                        }
                        printed += 1;
                        if printed >= 8 { break 'outer; }
                    }
                }
            }
        }
    }

    let dump = |title: &str, m: &HashMap<String, u64>| {
        println!("\n=== {title} (total across {} rooms x {trials} trials) ===", files.len());
        let mut v: Vec<_> = m.iter().collect();
        v.sort_by(|a, b| b.1.cmp(a.1));
        for (label, count) in v.into_iter().take(30) {
            println!("{count:7}  {label}");
        }
    };
    dump("ISOLATED floating solid blocks (>=3 horizontal air neighbors), by block", &isolated);
    dump("Floating solid blocks with a chest directly on top, by floor block", &with_chest);
}
