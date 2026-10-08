//! Export a freshly-assembled room to JSON for the Java (Routerunner) solver preview — NO solving here.
//! The Java solver (`com.routerunner.solver`, the real one shipped in the mod) reads this and plans the
//! route, so the preview and the mod never duplicate solver logic. Run:
//!   cargo run --release --bin wv_export_room -- [target] [add] [cascade] [seed] [out.json]
//! JSON schema:
//!   {"name","target","size":[x,y,z],
//!    "air":[{"min":[..],"max":[..]}],      // lossless cover of carved AIR cells (solid-by-default grid)
//!    "terrain":[{"min":[..],"max":[..]}],  // render mesh (merge_terrain_boxes) — viewer passthrough only
//!    "chests":[{"pos":[..],"type":".."}],
//!    "gates":[[x,y,z],..]}

use std::collections::HashSet;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;
use wv_chest_sim::voxel::{self, VoxelGrid};
use wv_chest_sim::decorator;

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

/// Baseline (avg target chests, avg saturation-fraction) for a (bonus, cascade) combo, from the
/// precomputed modifier panel. This is the per-vault-normalized richness reference — a room with more
/// chests / higher saturation than its combo's average is "rich". Clamps to the panel's 0..200 range.
fn lookup_panel(bonus: u32, cascade: u32) -> (f64, f64) {
    let prefix = format!("{},{},", bonus.min(200), cascade.min(200));
    for p in ["wv-modifier-panel.csv".to_string(), wv_chest_sim::paths::out_path("wv-modifier-panel.csv")] {
        if let Ok(txt) = std::fs::read_to_string(p) {
            for line in txt.lines() {
                if let Some(rest) = line.strip_prefix(&prefix) {
                    let f: Vec<&str> = rest.split(',').collect();
                    if f.len() >= 2 {
                        return (f[0].parse().unwrap_or(0.0), f[1].parse().unwrap_or(0.0));
                    }
                }
            }
            eprintln!("[wv_export_room] no panel row for {prefix} — richness baseline unavailable");
            return (0.0, 0.0);
        }
    }
    eprintln!("[wv_export_room] wv-modifier-panel.csv not found — richness baseline unavailable");
    (0.0, 0.0)
}

/// Lossless per-Y-layer greedy-rectangle cover of the carved AIR cells (in-bounds only). The Java side
/// fills the grid solid then carves these back to air, reproducing the exact solid/air field the solver
/// walks — unlike `merge_terrain_boxes`, which is a downsampled, exposed-surface-only RENDER mesh.
fn air_boxes(solid: &VoxelGrid) -> Vec<(P, P)> {
    let (sx, sy, sz) = solid.size();
    let mut boxes = Vec::new();
    for y in 0..sy {
        let mut remaining: HashSet<(i32, i32)> = HashSet::new();
        for x in 0..sx {
            for z in 0..sz {
                if !solid.is_solid((x, y, z)) {
                    remaining.insert((x, z));
                }
            }
        }
        let mut sorted: Vec<(i32, i32)> = remaining.iter().copied().collect();
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
            boxes.push(((x0, y, z0), (x1, y, z1)));
        }
    }
    boxes
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let target = static_target(a.get(1).map(String::as_str).unwrap_or("gilded_chest"));
    let add: u32 = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(55);
    let cascade: u32 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(55);
    let seed: Option<u64> = a.get(4).and_then(|s| s.parse().ok());
    let out = a.get(5).cloned().unwrap_or_else(|| wv_chest_sim::paths::out_path("room.json"));

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
    // Chest-slot capacity is a fixed pre-modifier property → count it on the BASE room, before add/cascade.
    let base_positions: HashSet<P> = asm.chests.iter().map(|c| c.pos).collect();
    let slots = decorator::count_chest_slots(&asm.solid, &asm.liquid, &base_positions, &asm.non_sturdy);
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

    let all_chests: Vec<&ChestSpot> = asm.chests.iter().chain(extra.iter()).filter(|c| !c.is_strongbox).collect();
    let size = asm.solid.size();
    let air = air_boxes(&asm.solid);
    let pts_set: HashSet<P> = all_chests.iter().map(|c| c.pos).collect();
    let (terrain, _) = voxel::merge_terrain_boxes(&asm.solid, 2, &pts_set);

    let tcount = all_chests.iter().filter(|c| c.chest_type == target).count();
    let (base_avg, base_sat) = lookup_panel(add, cascade);
    let room_sat = if slots > 0 { tcount as f64 / slots as f64 } else { 0.0 };
    let rich_count = if base_avg > 0.0 { tcount as f64 / base_avg } else { 0.0 };
    let rich_density = if base_sat > 0.0 { room_sat / base_sat } else { 0.0 };

    let a3 = |p: P| format!("[{},{},{}]", p.0, p.1, p.2);
    let boxes_json = |bs: &[(P, P)]| bs.iter().map(|(mn, mx)| format!(r#"{{"min":{},"max":{}}}"#, a3(*mn), a3(*mx))).collect::<Vec<_>>().join(",");
    let chests_json = all_chests.iter().map(|c| format!(r#"{{"pos":{},"type":"{}"}}"#, a3(c.pos), c.chest_type)).collect::<Vec<_>>().join(",");
    let gates_json = asm.gates.iter().map(|g| a3(*g)).collect::<Vec<_>>().join(",");
    let json = format!(
        r#"{{"name":"{}","target":"{}","size":{},"slots":{},"bonus":{},"cascade":{},"baselineAvgChests":{:.4},"baselineSat":{:.6},"air":[{}],"terrain":[{}],"chests":[{}],"gates":[{}]}}"#,
        room_name, target, a3(size), slots, add, cascade, base_avg, base_sat, boxes_json(&air), boxes_json(&terrain), chests_json, gates_json,
    );
    std::fs::write(&out, &json).unwrap();
    println!(
        "{room_name}: {tcount} {target} (avg {base_avg:.0} @ +{add}/+{cascade}); richness {rich_count:.2}x count / {rich_density:.2}x density; slots {slots}, air {} terrain {} -> {out}",
        air.len(), terrain.len(),
    );
}
