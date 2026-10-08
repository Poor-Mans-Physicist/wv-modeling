//! Single-room route + viewer (thin wrapper over `wv_chest_sim::route`).
//! Run: cargo run --release --bin wv_route [target] [bonus] [cascade] [bail] [seed] [flight_mult]
//! Writes routes/route.html (references local routes/three.min.js).

use std::collections::HashSet;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::route::{self, plan_route, render_html};
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;
use wv_chest_sim::{chain, decorator, voxel};

const CELL_SIZE: i32 = 47;
const MAX_DEPTH: i32 = 10;
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

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let target = static_target(a.get(1).map(String::as_str).unwrap_or("gilded_chest"));
    let add: u32 = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(55);
    let cascade: u32 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(55);
    let bail: f64 = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(3.0);
    let seed: Option<u64> = a.get(5).and_then(|s| s.parse().ok());
    let flight_mult: f64 = a.get(6).and_then(|s| s.parse().ok()).unwrap_or(3.0);

    let gen_root = wv_chest_sim::paths::gen_root();
    let rooms_dir = format!(r"{gen_root}\structures\vault\rooms\common");
    let mut files: Vec<_> = std::fs::read_dir(&rooms_dir).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false)).collect();
    files.sort();
    let assets = FsAssetSource::new(&gen_root);
    let data = DataSource::new(&assets);
    let mut rng = match seed {
        Some(s) => SmallRng::seed_from_u64(s),
        None => SmallRng::from_entropy(),
    };

    let room_path = files[rng.gen_range(0..files.len())].clone();
    let room_name = room_path.file_name().unwrap().to_string_lossy().to_string();
    let root = Rc::new(Structure::load(&room_path).unwrap());
    let mut asm = assemble::assemble(&root, &data, &mut rng, MAX_DEPTH);
    let region = random_nonzero_region(&mut rng);
    let rot = Rotation::random(&mut rng);
    let mut occ: HashSet<P> = asm.chests.iter().map(|c| c.pos).collect();
    let mut extra: Vec<ChestSpot> = Vec::new();
    for _ in 0..add {
        extra.extend(decorator::decorator_add_pass(&mut asm.solid, &mut occ, &asm.liquid, &asm.non_sturdy, region, rot, CELL_SIZE, 8, true, target, &mut rng));
    }
    let sources: Vec<ChestSpot> = asm.chests.iter().cloned().chain(extra.iter().cloned()).collect();
    for _ in 0..cascade {
        extra.extend(decorator::decorator_cascade_pass(&mut asm.solid, &mut asm.liquid, &mut occ, &asm.non_sturdy, &sources, region, rot, CELL_SIZE, 0.25, target, &mut rng));
    }
    let pts: Vec<P> = asm.chests.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos).chain(extra.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos)).collect();
    let entrance = asm.gates.first().copied().unwrap_or((23, 12, 23));
    let exit = if asm.gates.len() >= 2 {
        asm.gates[1..].iter().copied().max_by(|x, y| route::euclid(entrance, *x).partial_cmp(&route::euclid(entrance, *y)).unwrap()).unwrap()
    } else {
        entrance
    };

    // bail-sweep guidance
    let buckets = chain::build_buckets(&pts);
    println!("bail sweep (chests/block -> breaks, coverage):");
    for &b in &[0.6, 1.5, 3.0, 5.0, 8.0] {
        let (br, col, _) = route::select(&pts, &buckets, entrance, b);
        println!("   bail {b:>4}: {:3} breaks, {:.0}%", br.len(), 100.0 * col as f64 / pts.len().max(1) as f64);
    }

    let plan = plan_route(&asm.solid, &pts, entrance, exit, flight_mult, bail);
    let pts_set: HashSet<P> = pts.iter().copied().collect();
    let (boxes, _) = voxel::merge_terrain_boxes(&asm.solid, 2, &pts_set);
    let hud = format!(
        "{room_name} &nbsp; {target} +{add}/+{cascade}<br>chests {} &nbsp; breaks {} &nbsp; collected {} ({:.0}%)<br>walk {:.0} blk + fly {:.0} blk (mult x{flight_mult})<br>bail@{bail}/blk &nbsp; solid-crossings {}<br>drag=orbit wheel=zoom",
        pts.len(), plan.breaks.len(), plan.collected, 100.0 * plan.collected as f64 / pts.len().max(1) as f64, plan.walk_b, plan.fly_b, plan.fly_solid,
    );
    let markers = [(entrance, 'i'), (exit, 'o')];
    let html = render_html(asm.solid.size(), &boxes, &pts, &plan.state, &plan.segments, &plan.order, &markers, &hud, "three.min.js");
    std::fs::write(wv_chest_sim::paths::out_path("route.html"), &html).unwrap();
    println!("\n{room_name}: {} {target}, {} breaks, {} ({:.0}%); walk {:.0} + fly {:.0} blk; solid-crossings {} -> routes\\route.html", pts.len(), plan.breaks.len(), plan.collected, 100.0 * plan.collected as f64 / pts.len().max(1) as f64, plan.walk_b, plan.fly_b, plan.fly_solid);
}
