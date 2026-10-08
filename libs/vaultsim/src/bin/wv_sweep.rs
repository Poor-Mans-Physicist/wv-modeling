//! Coverage-sweep prototype for Routerunner (2026-06-29).
//!
//! At farm density the target chests are ~one room-filling blob (see wv_blobs), so the within-room
//! problem is NOT inter-hotspot TSP — it's a value-weighted sweep over the density field: hit dense
//! pockets, skip dead zones, bail when marginal value drops (MVT). Chain miner is deterministic, so
//! we pick each trigger by greedy maximum-coverage and simulate the *exact* cleared set.
//!
//! This measures the coverage curve (chests cleared vs. breaks vs. travel distance) for:
//!   - GREEDY: pick the next break maximizing (deterministic clear size) / (travel distance)  [the A+B vs A+C rule]
//!   - NEAREST: pick the nearest remaining chest (naive baseline)
//! and reports where the marginal value collapses (the bail point) and how front-loaded the value is.
//!
//! NOTE: distances are Euclidean (no obstacle routing yet) — a first proxy; obstacle-aware JPS is the next layer.
//! Run: cargo run --release --bin wv_sweep [target] [bonus] [cascade] [rooms]

use std::collections::{HashMap, HashSet, VecDeque};
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
const RANGE: i32 = 6;
const CAP: usize = 32;
const BUCKET: i32 = RANGE + 1;
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
fn cheb(a: P, b: P) -> i32 {
    (a.0 - b.0).abs().max((a.1 - b.1).abs()).max((a.2 - b.2).abs())
}
fn manh(a: P, b: P) -> i32 {
    (a.0 - b.0).abs() + (a.1 - b.1).abs() + (a.2 - b.2).abs()
}
fn dist(a: P, b: P) -> f64 {
    let (dx, dy, dz) = ((a.0 - b.0) as f64, (a.1 - b.1) as f64, (a.2 - b.2) as f64);
    (dx * dx + dy * dy + dz * dz).sqrt()
}
fn key(p: P) -> P {
    (p.0.div_euclid(BUCKET), p.1.div_euclid(BUCKET), p.2.div_euclid(BUCKET))
}

/// Assemble one realization and return (target-block positions, gate positions).
fn gen_room(
    root: &Rc<Structure>,
    data: &DataSource,
    rng: &mut SmallRng,
    target: &'static str,
    add: u32,
    cascade: u32,
) -> (Vec<P>, Vec<P>) {
    let mut asm = assemble::assemble(root, data, rng, MAX_DEPTH);
    let region = random_nonzero_region(rng);
    let rot = Rotation::random(rng);
    let mut occ: HashSet<P> = asm.chests.iter().map(|c| c.pos).collect();
    let mut extra: Vec<ChestSpot> = Vec::new();
    for _ in 0..add {
        let a = decorator::decorator_add_pass(
            &mut asm.solid, &mut occ, &asm.liquid, &asm.non_sturdy, region, rot, CELL_SIZE, 8, true, target, rng,
        );
        extra.extend(a);
    }
    let sources: Vec<ChestSpot> = asm.chests.iter().cloned().chain(extra.iter().cloned()).collect();
    for _ in 0..cascade {
        let c = decorator::decorator_cascade_pass(
            &mut asm.solid, &mut asm.liquid, &mut occ, &asm.non_sturdy, &sources, region, rot, CELL_SIZE, 0.25, target, rng,
        );
        extra.extend(c);
    }
    let mut pts: Vec<P> = asm.chests.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos).collect();
    pts.extend(extra.iter().filter(|c| c.chest_type == target && !c.is_strongbox).map(|c| c.pos));
    (pts, asm.gates.clone())
}

fn build_buckets(pts: &[P]) -> HashMap<P, Vec<u32>> {
    let mut b: HashMap<P, Vec<u32>> = HashMap::new();
    for (i, p) in pts.iter().enumerate() {
        b.entry(key(*p)).or_default().push(i as u32);
    }
    b
}

/// Remaining target chests within Chebyshev<=RANGE of pts[idx] (excluding idx).
fn neighbors(idx: usize, pts: &[P], buckets: &HashMap<P, Vec<u32>>, remaining: &[bool]) -> Vec<usize> {
    let p = pts[idx];
    let b = key(p);
    let mut out = Vec::new();
    for dx in -1..=1 {
        for dy in -1..=1 {
            for dz in -1..=1 {
                if let Some(m) = buckets.get(&(b.0 + dx, b.1 + dy, b.2 + dz)) {
                    for &j in m {
                        let j = j as usize;
                        if remaining[j] && j != idx && cheb(p, pts[j]) <= RANGE {
                            out.push(j);
                        }
                    }
                }
            }
        }
    }
    out
}

/// Deterministic chain clear from `start`: faithful BFS port of ChainBreakHandler.areaDig —
/// FIFO queue, each node scans its Chebyshev<=RANGE box nearest-first (Manhattan), cap CAP.
fn clear_from(start: usize, pts: &[P], buckets: &HashMap<P, Vec<u32>>, remaining: &[bool]) -> Vec<usize> {
    let mut trav: Vec<usize> = Vec::new();
    let mut inset: HashSet<usize> = HashSet::new();
    let mut q: VecDeque<usize> = VecDeque::new();
    q.push_back(start);
    'outer: while let Some(head) = q.pop_front() {
        let mut cand = neighbors(head, pts, buckets, remaining);
        cand.push(head); // the head's own cell (distance 0) is part of its scan box
        cand.sort_by_key(|&j| manh(pts[j], pts[head]));
        for j in cand {
            if trav.len() >= CAP {
                break 'outer;
            }
            if inset.contains(&j) || !remaining[j] {
                continue;
            }
            trav.push(j);
            inset.insert(j);
            q.push_back(j);
        }
    }
    trav
}

#[derive(Clone, Copy, PartialEq)]
enum Strategy {
    Greedy,  // maximize (clear size proxy) / travel distance
    Nearest, // minimize travel distance (naive)
}

/// One sweep over a room; returns per-break (cumulative chests cleared, cumulative travel distance).
fn sweep(pts: &[P], start: P, strat: Strategy) -> Vec<(usize, f64)> {
    let n = pts.len();
    let buckets = build_buckets(pts);
    let mut remaining = vec![true; n];
    let mut left = n;
    let mut cur = start;
    let mut cum_chests = 0usize;
    let mut cum_dist = 0.0f64;
    let mut curve = Vec::new();

    while left > 0 {
        // pick the next break
        let mut best = usize::MAX;
        let mut best_score = f64::NEG_INFINITY;
        for idx in 0..n {
            if !remaining[idx] {
                continue;
            }
            let d = dist(cur, pts[idx]).max(0.5);
            let score = match strat {
                Strategy::Nearest => -d,
                Strategy::Greedy => {
                    let v = (neighbors(idx, pts, &buckets, &remaining).len() + 1).min(CAP) as f64;
                    v / d
                }
            };
            if score > best_score {
                best_score = score;
                best = idx;
            }
        }
        let cleared = clear_from(best, pts, &buckets, &remaining);
        for &j in &cleared {
            remaining[j] = false;
        }
        left -= cleared.len();
        cum_dist += dist(cur, pts[best]);
        cum_chests += cleared.len();
        cur = pts[best];
        curve.push((cum_chests, cum_dist));
    }
    curve
}

/// Distance & break-count at which the curve first reaches `frac` of total chests.
fn milestone(curve: &[(usize, f64)], total: usize, frac: f64) -> (usize, f64) {
    let target = (frac * total as f64).ceil() as usize;
    for (i, &(c, d)) in curve.iter().enumerate() {
        if c >= target {
            return (i + 1, d);
        }
    }
    curve.last().map(|&(_, d)| (curve.len(), d)).unwrap_or((0, 0.0))
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let target = static_target(a.get(1).map(String::as_str).unwrap_or("gilded_chest"));
    let add: u32 = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(55);
    let cascade: u32 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(55);
    let rooms: usize = a.get(4).and_then(|s| s.parse().ok()).unwrap_or(8);

    let gen_root = wv_chest_sim::paths::gen_root();
    let rooms_dir = format!(r"{gen_root}\structures\vault\rooms\common");
    let mut files: Vec<_> = std::fs::read_dir(&rooms_dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    files.sort();

    let assets = FsAssetSource::new(&gen_root);
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();

    println!("target={target}  Bonus+{add}/Cascade+{cascade}  rooms={rooms}  (Euclidean distance, no obstacles yet)\n");

    let fracs = [0.5, 0.7, 0.9, 1.0];
    // accumulators: [strategy][frac] -> (sum breaks, sum dist); plus totals
    let mut acc: [[(f64, f64); 4]; 2] = [[(0.0, 0.0); 4]; 2];
    let mut sum_total = 0.0;
    let mut sum_full_dist = [0.0f64; 2];
    let mut sum_frontload = [0.0f64; 2]; // % chests collected within first half of greedy's full distance
    let mut counted = 0;

    for _ in 0..rooms {
        let f = &files[rng.gen_range(0..files.len())];
        let root = Rc::new(Structure::load(f).unwrap());
        let (pts, gates) = gen_room(&root, &data, &mut rng, target, add, cascade);
        if pts.len() < 20 {
            continue; // skip near-empty draws
        }
        let start = gates.first().copied().unwrap_or((23, 10, 23));
        let total = pts.len();
        sum_total += total as f64;
        counted += 1;

        for (si, strat) in [Strategy::Greedy, Strategy::Nearest].into_iter().enumerate() {
            let curve = sweep(&pts, start, strat);
            let full_dist = curve.last().map(|&(_, d)| d).unwrap_or(0.0);
            sum_full_dist[si] += full_dist;
            for (fi, &fr) in fracs.iter().enumerate() {
                let (b, d) = milestone(&curve, total, fr);
                acc[si][fi].0 += b as f64;
                acc[si][fi].1 += d;
            }
            // front-load: chests collected by the time we've travelled half the full distance
            let half = full_dist * 0.5;
            let got = curve.iter().take_while(|&&(_, d)| d <= half).last().map(|&(c, _)| c).unwrap_or(0);
            sum_frontload[si] += 100.0 * got as f64 / total as f64;
        }
    }

    let r = counted.max(1) as f64;
    println!("rooms counted: {counted}   avg target chests/room: {:.0}\n", sum_total / r);

    for (si, name) in ["GREEDY  (clear/dist — the A+B vs A+C rule)", "NEAREST (naive nearest-chest)"].iter().enumerate() {
        println!("== {name} ==");
        println!("  full clear: {:.1} breaks-worth, {:.0} blocks travelled", acc[si][3].0 / r, sum_full_dist[si] / r);
        println!("  coverage milestones (avg breaks | avg blocks | marginal chests/block in segment):");
        let mut prev_d = 0.0;
        let mut prev_c = 0.0;
        for (fi, &fr) in fracs.iter().enumerate() {
            let breaks = acc[si][fi].0 / r;
            let d = acc[si][fi].1 / r;
            let chests_here = fr * sum_total / r;
            let seg_d = (d - prev_d).max(1e-6);
            let marginal = (chests_here - prev_c) / seg_d;
            println!("     {:>3.0}%: {breaks:5.1} breaks | {d:6.0} blk | {marginal:5.2} chests/blk", fr * 100.0);
            prev_d = d;
            prev_c = chests_here;
        }
        println!("  value front-loading: {:.0}% of chests collected in the first half of the travel distance", sum_frontload[si] / r);
        println!();
    }
}
