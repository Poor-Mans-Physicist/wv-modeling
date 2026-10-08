// Non-invasive diagnostic: reproduce the wv-modifier-panel (bonus, cascade) sequencing for a
// single (b,c) point on every common room, then classify every PLACED gilded chest by:
//  (1) visible-from-above: air directly above (a chest with a solid block on top is buried), and
//  (2) reachable: adjacent to air that is flood-connected to the room's gate/entrance cavity.
//
// Goal: test whether the simulator's ~1590 at (66,66) is inflated by chests the real game places
// in isolated air pockets / ledges / sub-levels a player never traverses, which a "cleared the
// room" in-game count would never include. Does NOT modify any existing file.
use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::SeedableRng;
use rayon::prelude::*;

use wv_chest_sim::assemble::{self, AssemblyResult, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::decorator;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;
use wv_chest_sim::voxel::VoxelGrid;

const CELL_SIZE: i32 = 47;

fn random_nonzero_region(rng: &mut impl rand::Rng) -> (i32, i32) {
    loop {
        let gx = rng.gen_range(-500..500);
        let gz = rng.gen_range(-500..500);
        if (gx, gz) != (0, 0) {
            return (gx, gz);
        }
    }
}

#[derive(Default)]
struct Tally {
    total: f64,
    visible: f64,    // air directly above
    embedded: f64,   // solid directly above
    reachable: f64,  // adjacent to gate-connected open air
    isolated: f64,   // not connected to the explorable cavity
}

/// Flood-fill the set of OPEN (non-solid in the terrain grid) cells that are reachable from any
/// gate's interior, 6-connected. Returns the reachable-open-cell set. Terrain grid = pre-decorator
/// `solid` (baseline chests are a negligible handful). A cell is "open" if not solid OR is liquid
/// (a player can wade/see through water the same as air for spotting chests).
fn reachable_open_cells(grid: &VoxelGrid, liquid: &HashSet<(i32, i32, i32)>, gates: &[(i32, i32, i32)]) -> HashSet<(i32, i32, i32)> {
    let (sx, sy, sz) = grid.size();
    let is_open = |p: (i32, i32, i32)| !grid.is_solid(p) || liquid.contains(&p);
    let in_bounds = |p: (i32, i32, i32)| p.0 >= 0 && p.0 < sx && p.1 >= 0 && p.1 < sy && p.2 >= 0 && p.2 < sz;
    let mut seen: HashSet<(i32, i32, i32)> = HashSet::new();
    let mut q: VecDeque<(i32, i32, i32)> = VecDeque::new();

    // Seed from each gate marker and the open cells around it (the gate cell itself may be solid).
    for &g in gates {
        for dy in 0..3 {
            for &(dx, dz) in &[(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)] {
                let p = (g.0 + dx, g.1 + dy, g.2 + dz);
                if in_bounds(p) && is_open(p) && seen.insert(p) {
                    q.push_back(p);
                }
            }
        }
    }
    // Fallback: if there are no gates / nothing seeded, seed from the single largest open region
    // so a gate-less room still gets a meaningful "main cavity" rather than zero reachable.
    if q.is_empty() {
        if let Some(seed) = largest_open_seed(grid, liquid) {
            seen.insert(seed);
            q.push_back(seed);
        }
    }

    while let Some(p) = q.pop_front() {
        for &(dx, dy, dz) in &[(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)] {
            let n = (p.0 + dx, p.1 + dy, p.2 + dz);
            if in_bounds(n) && is_open(n) && !seen.contains(&n) {
                seen.insert(n);
                q.push_back(n);
            }
        }
    }
    seen
}

fn largest_open_seed(grid: &VoxelGrid, liquid: &HashSet<(i32, i32, i32)>) -> Option<(i32, i32, i32)> {
    let (sx, sy, sz) = grid.size();
    let is_open = |p: (i32, i32, i32)| !grid.is_solid(p) || liquid.contains(&p);
    let mut visited: HashSet<(i32, i32, i32)> = HashSet::new();
    let mut best: Option<((i32, i32, i32), usize)> = None;
    for x in 0..sx {
        for y in 0..sy {
            for z in 0..sz {
                let s = (x, y, z);
                if !is_open(s) || visited.contains(&s) {
                    continue;
                }
                let mut q = VecDeque::new();
                let mut size = 0usize;
                let root = s;
                visited.insert(s);
                q.push_back(s);
                while let Some(p) = q.pop_front() {
                    size += 1;
                    for &(dx, dy, dz) in &[(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)] {
                        let n = (p.0 + dx, p.1 + dy, p.2 + dz);
                        if n.0 >= 0 && n.0 < sx && n.1 >= 0 && n.1 < sy && n.2 >= 0 && n.2 < sz && is_open(n) && !visited.contains(&n) {
                            visited.insert(n);
                            q.push_back(n);
                        }
                    }
                }
                if best.map(|(_, b)| size > b).unwrap_or(true) {
                    best = Some((root, size));
                }
            }
        }
    }
    best.map(|(r, _)| r)
}

fn run_room(path: &PathBuf, gen_root: &str, bonus: u32, cascade: u32, trials: u32) -> (String, Tally, f64) {
    let name = path.file_name().unwrap().to_string_lossy().to_string();
    let assets = FsAssetSource::new(gen_root.to_string());
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();
    let root = match Structure::load(path) {
        Ok(s) => Rc::new(s),
        Err(_) => return (name, Tally::default(), 0.0),
    };

    let mut t = Tally::default();
    let mut sum_reachable_air = 0.0f64; // diagnostic: size of explorable cavity

    for _ in 0..trials {
        let work: AssemblyResult = assemble::assemble(&root, &data, &mut rng, 10);
        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);

        // Reachable cavity is a TERRAIN property - compute on the pristine assembled terrain,
        // before any decorator chest is added (baseline chests are a negligible handful).
        let reach = reachable_open_cells(&work.solid, &work.liquid, &work.gates);
        sum_reachable_air += reach.len() as f64;

        let mut solid = work.solid.clone();
        let mut liquid = work.liquid.clone();
        let mut chest_positions: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
        let baseline_gilded: Vec<ChestSpot> =
            work.chests.iter().filter(|c| c.chest_type == "gilded_chest").cloned().collect();

        let mut extra_add: Vec<ChestSpot> = Vec::new();
        for _ in 0..bonus {
            let added = decorator::decorator_add_pass(
                &mut solid, &mut chest_positions, &work.liquid, &work.non_sturdy, region, rotation, CELL_SIZE, 8, true, "gilded_chest", &mut rng,
            );
            extra_add.extend(added);
        }
        let cascade_sources: Vec<ChestSpot> =
            work.chests.iter().cloned().chain(extra_add.iter().cloned()).collect();
        let mut cgrid = solid.clone();
        let mut cliquid = liquid.clone();
        let mut cpositions = chest_positions.clone();
        let mut casc: Vec<ChestSpot> = Vec::new();
        for _ in 0..cascade {
            let c = decorator::decorator_cascade_pass(
                &mut cgrid, &mut cliquid, &mut cpositions, &work.non_sturdy, &cascade_sources, region, rotation, CELL_SIZE, 0.25, "gilded_chest", &mut rng,
            );
            casc.extend(c);
        }
        let _ = &mut liquid;

        for c in baseline_gilded.iter().chain(extra_add.iter()).chain(casc.iter()) {
            t.total += 1.0;
            let above = (c.pos.0, c.pos.1 + 1, c.pos.2);
            if cgrid.is_solid(above) { t.embedded += 1.0; } else { t.visible += 1.0; }
            // Reachable if any 6-neighbor cell is in the gate-connected open set.
            let neigh = [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)];
            let reachable = neigh.iter().any(|&(dx,dy,dz)| reach.contains(&(c.pos.0+dx, c.pos.1+dy, c.pos.2+dz)));
            if reachable { t.reachable += 1.0; } else { t.isolated += 1.0; }
        }
    }

    let n = trials as f64;
    t.total /= n; t.visible /= n; t.embedded /= n; t.reachable /= n; t.isolated /= n;
    (name, t, sum_reachable_air / n)
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let gen_root = wv_chest_sim::paths::gen_root();
    let bonus: u32 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(66);
    let cascade: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(66);
    let trials: u32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(10);

    let rooms_dir = format!("{gen_root}\\structures\\vault\\rooms\\common");
    let mut room_files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir).unwrap()
        .filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false)).collect();
    room_files.sort();

    println!("(bonus={bonus}, cascade={cascade}), {trials} trials/room, {} rooms\n", room_files.len());
    let results: Vec<(String, Tally, f64)> =
        room_files.par_iter().map(|p| run_room(p, &gen_root, bonus, cascade, trials)).collect();

    let mut g = Tally::default();
    let mut g_air = 0.0;
    for (name, t, air) in &results {
        println!("{name:22} total={:8.1} reachable={:8.1} isolated={:7.1} ({:4.1}% iso) | cavity_air={:.0}",
            t.total, t.reachable, t.isolated, 100.0 * t.isolated / t.total.max(1.0), air);
        g.total += t.total; g.visible += t.visible; g.embedded += t.embedded;
        g.reachable += t.reachable; g.isolated += t.isolated; g_air += air;
    }
    let n = results.len() as f64;
    println!("\n=== AVERAGE ACROSS {} ROOMS ===", results.len());
    println!("total placed gilded/room = {:.1}", g.total / n);
    println!("  reachable (player sees) = {:.1} ({:.1}%)", g.reachable / n, 100.0 * g.reachable / g.total);
    println!("  isolated (sealed/unseen)= {:.1} ({:.1}%)", g.isolated / n, 100.0 * g.isolated / g.total);
    println!("  visible-from-above      = {:.1} ({:.1}%)", g.visible / n, 100.0 * g.visible / g.total);
    println!("  explorable cavity cells = {:.0}", g_air / n);
}
