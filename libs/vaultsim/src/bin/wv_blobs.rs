//! Chain-miner "blob" analysis for the Routerunner routing model.
//!
//! In a one-type farm vault only ONE chest block matters; chain miner clears connected
//! components of that exact block, where two chests are chain-adjacent iff they differ by
//! <= RANGE on every axis (Chebyshev metric — verified from woldsvaults ChainBreakHandler.areaDig),
//! and one trigger clears up to CAP chests of a component. Strongboxes are a separate block and
//! are excluded. This measures the blob structure those rules produce across a Bonus/Cascade
//! density sweep: blob-size distribution, largest blobs, how often the 32-cap bites,
//! triggers/room, and blob spatial extent.
//!
//! Run: cargo run --release --bin wv_blobs [target_chest_type] [trials/room] [gen_root] [rooms_dir]

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Instant;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::decorator;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;

const CELL_SIZE: i32 = 47;
const RANGE: i32 = 6; // chain miner max-level range (Chebyshev half-extent)
const CAP: u32 = 32; // chain miner max-level blockLimit (hard cap, no AoE)
const BUCKET: i32 = RANGE + 1; // spatial-hash cell; > RANGE so true neighbours differ <=1 bucket/axis
const MAX_DEPTH: i32 = 10; // matches the validated baseline census (depth 0 vs 10 agree in this pack)

// (bonus stacks, cascade stacks) density sweep — baseline -> ~1k chests
const LEVELS: [(u32, u32); 4] = [(0, 0), (20, 20), (40, 40), (55, 55)];

const HIST_BOUNDS: [u32; 9] = [1, 3, 8, 16, 32, 64, 128, 256, 512];
const HIST_LABELS: [&str; 10] =
    ["1", "2-3", "4-8", "9-16", "17-32", "33-64", "65-128", "129-256", "257-512", "513+"];

fn hist_bucket(s: u32) -> usize {
    for (i, &b) in HIST_BOUNDS.iter().enumerate() {
        if s <= b {
            return i;
        }
    }
    HIST_BOUNDS.len()
}

#[derive(Default, Clone)]
struct LevelStats {
    trials: u64,
    sum_n: u64,            // target chests
    sum_blobs: u64,        // connected components
    sum_triggers: u64,     // Σ ⌈size/CAP⌉
    sum_comp_gt_cap: u64,  // components larger than CAP
    sum_max_size: u64,     // per-trial largest blob (chests)
    max_max_size: u32,     // global largest blob seen
    sum_largest_extent: u64, // per-trial largest blob's max bbox dimension (blocks)
    max_largest_extent: i32,
    blob_hist: [u64; 10],  // blobs per size bucket
    chest_hist: [u64; 10], // chests living in blobs of each size bucket
}

impl LevelStats {
    fn merge(&mut self, o: &LevelStats) {
        self.trials += o.trials;
        self.sum_n += o.sum_n;
        self.sum_blobs += o.sum_blobs;
        self.sum_triggers += o.sum_triggers;
        self.sum_comp_gt_cap += o.sum_comp_gt_cap;
        self.sum_max_size += o.sum_max_size;
        self.max_max_size = self.max_max_size.max(o.max_max_size);
        self.sum_largest_extent += o.sum_largest_extent;
        self.max_largest_extent = self.max_largest_extent.max(o.max_largest_extent);
        for i in 0..10 {
            self.blob_hist[i] += o.blob_hist[i];
            self.chest_hist[i] += o.chest_hist[i];
        }
    }
}

struct UnionFind {
    parent: Vec<u32>,
    rank: Vec<u8>,
}
impl UnionFind {
    fn new(n: usize) -> Self {
        UnionFind { parent: (0..n as u32).collect(), rank: vec![0; n] }
    }
    fn find(&mut self, x: u32) -> u32 {
        let mut r = x;
        while self.parent[r as usize] != r {
            r = self.parent[r as usize];
        }
        let mut c = x;
        while self.parent[c as usize] != r {
            let nx = self.parent[c as usize];
            self.parent[c as usize] = r;
            c = nx;
        }
        r
    }
    fn union(&mut self, a: u32, b: u32) {
        let (ra, rb) = (self.find(a), self.find(b));
        if ra == rb {
            return;
        }
        let (hi, lo) = if self.rank[ra as usize] < self.rank[rb as usize] { (rb, ra) } else { (ra, rb) };
        self.parent[lo as usize] = hi;
        if self.rank[hi as usize] == self.rank[lo as usize] {
            self.rank[hi as usize] += 1;
        }
    }
}

fn cheb(a: (i32, i32, i32), b: (i32, i32, i32)) -> i32 {
    (a.0 - b.0).abs().max((a.1 - b.1).abs()).max((a.2 - b.2).abs())
}

/// Connected-component root id per point, under Chebyshev<=RANGE, via spatial hashing.
fn components(pts: &[(i32, i32, i32)]) -> Vec<u32> {
    let n = pts.len();
    let mut uf = UnionFind::new(n);
    let mut buckets: HashMap<(i32, i32, i32), Vec<u32>> = HashMap::new();
    for (i, p) in pts.iter().enumerate() {
        let key = (p.0.div_euclid(BUCKET), p.1.div_euclid(BUCKET), p.2.div_euclid(BUCKET));
        buckets.entry(key).or_default().push(i as u32);
    }
    for (i, p) in pts.iter().enumerate() {
        let base = (p.0.div_euclid(BUCKET), p.1.div_euclid(BUCKET), p.2.div_euclid(BUCKET));
        for dx in -1..=1 {
            for dy in -1..=1 {
                for dz in -1..=1 {
                    if let Some(members) = buckets.get(&(base.0 + dx, base.1 + dy, base.2 + dz)) {
                        for &j in members {
                            if j as usize > i && cheb(*p, pts[j as usize]) <= RANGE {
                                uf.union(i as u32, j);
                            }
                        }
                    }
                }
            }
        }
    }
    (0..n as u32).map(|i| uf.find(i)).collect()
}

fn random_nonzero_region(rng: &mut impl Rng) -> (i32, i32) {
    loop {
        let gx = rng.gen_range(-500..500);
        let gz = rng.gen_range(-500..500);
        if (gx, gz) != (0, 0) {
            return (gx, gz);
        }
    }
}

fn static_target(s: &str) -> &'static str {
    match s {
        "wooden_chest" => "wooden_chest",
        "gilded_chest" => "gilded_chest",
        "living_chest" => "living_chest",
        "ornate_chest" => "ornate_chest",
        other => panic!("unknown chest type {other:?} (expected wooden_chest|gilded_chest|living_chest|ornate_chest)"),
    }
}

fn run_room_level(path: &Path, gen_root: &str, target: &'static str, add: u32, cascade: u32, trials: u32) -> LevelStats {
    let assets = FsAssetSource::new(gen_root);
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();
    let root = match Structure::load(path) {
        Ok(s) => Rc::new(s),
        Err(e) => {
            eprintln!("[wv_blobs] failed to load {path:?}: {e}");
            return LevelStats::default();
        }
    };

    let mut st = LevelStats::default();
    for _ in 0..trials {
        let mut asm = assemble::assemble(&root, &data, &mut rng, MAX_DEPTH);
        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);
        let mut occ: HashSet<(i32, i32, i32)> = asm.chests.iter().map(|c| c.pos).collect();

        // Bonus (decorator_add) passes first — priority 0 beats cascade's -100 — then cascade
        // over the fixed baseline+bonus source pool (cascade copies don't seed further cascades).
        let mut extra: Vec<ChestSpot> = Vec::new();
        for _ in 0..add {
            let a = decorator::decorator_add_pass(
                &mut asm.solid, &mut occ, &asm.liquid, &asm.non_sturdy,
                region, rotation, CELL_SIZE, 8, true, target, &mut rng,
            );
            extra.extend(a);
        }
        let sources: Vec<ChestSpot> = asm.chests.iter().cloned().chain(extra.iter().cloned()).collect();
        for _ in 0..cascade {
            let c = decorator::decorator_cascade_pass(
                &mut asm.solid, &mut asm.liquid, &mut occ, &asm.non_sturdy, &sources,
                region, rotation, CELL_SIZE, 0.25, target, &mut rng,
            );
            extra.extend(c);
        }

        // Exact target block only, strongboxes excluded (separate block — chain miner ignores them).
        let mut pts: Vec<(i32, i32, i32)> = asm
            .chests
            .iter()
            .filter(|c| c.chest_type == target && !c.is_strongbox)
            .map(|c| c.pos)
            .collect();
        pts.extend(
            extra.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos),
        );

        let roots = components(&pts);
        // size + bbox per component
        let mut comp: HashMap<u32, (u32, i32, i32, i32, i32, i32, i32)> = HashMap::new();
        for (i, &r) in roots.iter().enumerate() {
            let p = pts[i];
            let e = comp.entry(r).or_insert((0, p.0, p.1, p.2, p.0, p.1, p.2));
            e.0 += 1;
            e.1 = e.1.min(p.0); e.2 = e.2.min(p.1); e.3 = e.3.min(p.2);
            e.4 = e.4.max(p.0); e.5 = e.5.max(p.1); e.6 = e.6.max(p.2);
        }

        st.trials += 1;
        st.sum_n += pts.len() as u64;
        st.sum_blobs += comp.len() as u64;
        let mut max_size = 0u32;
        let mut extent_of_largest = 0i32;
        for &(cnt, mnx, mny, mnz, mxx, mxy, mxz) in comp.values() {
            let b = hist_bucket(cnt);
            st.blob_hist[b] += 1;
            st.chest_hist[b] += cnt as u64;
            st.sum_triggers += cnt.div_ceil(CAP) as u64;
            if cnt > CAP {
                st.sum_comp_gt_cap += 1;
            }
            if cnt > max_size {
                max_size = cnt;
                extent_of_largest = (mxx - mnx).max(mxy - mny).max(mxz - mnz);
            }
        }
        st.sum_max_size += max_size as u64;
        st.max_max_size = st.max_max_size.max(max_size);
        st.sum_largest_extent += extent_of_largest as u64;
        st.max_largest_extent = st.max_largest_extent.max(extent_of_largest);
    }
    st
}

fn print_level(add: u32, cascade: u32, st: &LevelStats, secs: f64) {
    let t = st.trials.max(1) as f64;
    let chests = st.sum_n as f64 / t;
    let blobs = st.sum_blobs as f64 / t;
    let mean_blob = if st.sum_blobs > 0 { st.sum_n as f64 / st.sum_blobs as f64 } else { 0.0 };
    let triggers = st.sum_triggers as f64 / t;
    let gt_cap = st.sum_comp_gt_cap as f64 / t;
    let mean_largest = st.sum_max_size as f64 / t;
    let mean_extent = st.sum_largest_extent as f64 / t;
    println!("===== Bonus +{add}, Cascade +{cascade}   ({secs:.1}s) =====");
    println!("  target chests/room ......... {chests:8.1}");
    println!("  blobs (hotspots)/room ...... {blobs:8.1}   mean blob size {mean_blob:6.1}");
    println!("  triggers/room (Σ⌈s/32⌉) .... {triggers:8.1}   blobs >32/room {gt_cap:6.2}");
    println!(
        "  largest blob/room .......... {mean_largest:8.1} chests (global max {})   extent ~{mean_extent:.0} blk (max {})",
        st.max_max_size, st.max_largest_extent
    );
    let tb = st.sum_blobs.max(1) as f64;
    let tc = st.sum_n.max(1) as f64;
    println!("  size distribution (share of blobs | share of target chests):");
    for i in 0..10 {
        if st.blob_hist[i] > 0 {
            let bp = 100.0 * st.blob_hist[i] as f64 / tb;
            let cp = 100.0 * st.chest_hist[i] as f64 / tc;
            println!("      {:>8}: {bp:5.1}% blobs | {cp:5.1}% chests", HIST_LABELS[i]);
        }
    }
    println!();
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let target_arg = args.get(1).cloned().unwrap_or_else(|| "gilded_chest".to_string());
    let target: &'static str = static_target(&target_arg);
    let trials: u32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(100);
    let gen_root = args
        .get(3)
        .cloned()
        .unwrap_or_else(|| wv_chest_sim::paths::gen_root());
    let rooms_dir = args
        .get(4)
        .cloned()
        .unwrap_or_else(|| format!(r"{gen_root}\structures\vault\rooms\common"));

    let mut room_files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir)
        .unwrap_or_else(|e| panic!("failed to read rooms dir {rooms_dir}: {e}"))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    room_files.sort();

    println!(
        "target = {target}   adjacency = Chebyshev<= {RANGE}   cap = {CAP}   trials/room = {trials}   rooms = {}",
        room_files.len()
    );
    println!("(a 'blob' = connected component of the target block = one spatial hotspot; strongboxes excluded)\n");

    for (add, cascade) in LEVELS {
        let start = Instant::now();
        let partials: Vec<LevelStats> = room_files
            .par_iter()
            .map(|p| run_room_level(p, &gen_root, &target, add, cascade, trials))
            .collect();
        let mut st = LevelStats::default();
        for p in &partials {
            st.merge(p);
        }
        print_level(add, cascade, &st, start.elapsed().as_secs_f64());
    }
}
