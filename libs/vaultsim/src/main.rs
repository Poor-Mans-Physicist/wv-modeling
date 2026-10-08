use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Instant;

use rand::rngs::SmallRng;
use rand::SeedableRng;
use rayon::prelude::*;

use wv_chest_sim::assemble;
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::schedule;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;

const CELL_SIZE: i32 = 47;
const CHEST_TYPES: [&str; 4] = ["wooden_chest", "gilded_chest", "living_chest", "ornate_chest"];

/// Everything one room contributes to the printed report. Computed entirely inside one
/// `run_room` call so it can be handed to rayon as a single parallel unit of work.
struct RoomReport {
    name: String,
    mean_total: f64,
    by_type: HashMap<&'static str, f64>,
    pieces_placed: u64,
    duds: u64,
    add_gilded: f64,
    cascade_alone_gilded: f64,
    combined_gilded: f64,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("export") {
        return run_export(&args[2..]);
    }
    let gen_root = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| wv_chest_sim::paths::gen_root());
    let rooms_dir = args
        .get(2)
        .cloned()
        .unwrap_or_else(|| format!("{gen_root}\\structures\\vault\\rooms\\common"));
    let trials: u32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(2000);
    let max_depth: i32 = args.get(4).and_then(|s| s.parse().ok()).unwrap_or(10);

    let mut room_files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir)
        .unwrap_or_else(|e| panic!("failed to read rooms dir {rooms_dir}: {e}"))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    room_files.sort();

    let start = Instant::now();

    // One DataSource + SmallRng per room, constructed inside run_room rather than shared: a
    // shared DataSource isn't an option without rewriting its caching layer (data.rs uses
    // Rc<_>/RefCell<_> internally, neither of which is Sync), and a shared RNG can't be handed
    // out as &mut to multiple threads at once regardless. Each room's own cache warms once and
    // is reused across all of that room's own trials, so this costs at most one redundant
    // disk read/parse per distinct decor file per room - not per trial - which is what actually
    // makes "every room gets its own core" safe to do with zero locking anywhere.
    let reports: Vec<RoomReport> = room_files
        .par_iter()
        .map(|path| run_room(path, &gen_root, trials, max_depth))
        .collect();

    let elapsed = start.elapsed();

    let summarize = |label: &str, vals: &[f64]| {
        let avg = vals.iter().sum::<f64>() / vals.len() as f64;
        let min = vals.iter().cloned().fold(f64::INFINITY, f64::min);
        let max = vals.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        println!("{label:24} avg={avg:.3} min={min:.3} max={max:.3}");
    };

    println!("=== phase 1: baseline POI chest census ===");
    let mut total_pieces: u64 = 0;
    let mut total_duds: u64 = 0;
    let mut grand_totals: Vec<f64> = Vec::new();
    let mut grand_by_type: HashMap<&str, Vec<f64>> = HashMap::new();
    for r in &reports {
        println!(
            "{:24} mean_total={:7.3} wooden={:6.3} gilded={:6.3} living={:6.3} ornate={:6.3}",
            r.name,
            r.mean_total,
            r.by_type.get("wooden_chest").copied().unwrap_or(0.0),
            r.by_type.get("gilded_chest").copied().unwrap_or(0.0),
            r.by_type.get("living_chest").copied().unwrap_or(0.0),
            r.by_type.get("ornate_chest").copied().unwrap_or(0.0),
        );
        grand_totals.push(r.mean_total);
        for ct in CHEST_TYPES {
            grand_by_type.entry(ct).or_default().push(r.by_type.get(ct).copied().unwrap_or(0.0));
        }
        total_pieces += r.pieces_placed;
        total_duds += r.duds;
    }

    println!();
    println!(
        "=== summary across {} rooms, {} trials each, max_depth={} ===",
        reports.len(),
        trials,
        max_depth
    );
    summarize("E[total chests/room]", &grand_totals);
    for ct in CHEST_TYPES {
        summarize(&format!("E[{ct}/room]"), &grand_by_type[ct]);
    }
    let dud_rate = 100.0 * total_duds as f64 / (total_pieces as f64 + total_duds as f64).max(1.0);
    println!(
        "pieces placed total={total_pieces}, duds (pool rolled real but no compatible connector)={total_duds} ({dud_rate:.2}% of attempts)"
    );

    println!();
    println!("=== phase 2: incremental yield of one 'the_vault:gilded' Bonus Gilded modifier ===");
    println!("(attemptsPerChunk=8, requireConditions=true, roomTypeWhitelist bypassed by woldsvaults in this pack)");
    let mut grand_add: Vec<f64> = Vec::new();
    for r in &reports {
        println!("{:24} +gilded={:.3}", r.name, r.add_gilded);
        grand_add.push(r.add_gilded);
    }
    summarize("E[+gilded chests/room from one Bonus Gilded]", &grand_add);

    println!();
    println!("=== phase 3: decorator_cascade ('the_vault:gilded_cascade', chance=0.25) ===");
    println!("(duplicates existing gilded chests into a same-chunk-restricted 7x7x7 search cube around each source;");
    println!(" decorator_add runs first - priority 0 beats cascade's -100 - so its additions are valid cascade sources too)");
    let mut grand_cascade_alone: Vec<f64> = Vec::new();
    let mut grand_combined: Vec<f64> = Vec::new();
    for r in &reports {
        println!(
            "{:24} cascade_alone={:.3} combined_with_bonus_gilded={:.3}",
            r.name, r.cascade_alone_gilded, r.combined_gilded
        );
        grand_cascade_alone.push(r.cascade_alone_gilded);
        grand_combined.push(r.combined_gilded);
    }
    summarize("E[+gilded from Gilded Cascade alone]", &grand_cascade_alone);
    summarize("E[+gilded total, Bonus Gilded + Cascade]", &grand_combined);

    println!();
    println!("(n rooms = {}) elapsed: {elapsed:.2?}", reports.len());
}

/// Runs every phase's Monte Carlo loop for one room file. Self-contained (own DataSource, own
/// RNG, own loaded Structure) so it can run as one independent unit of parallel work - see the
/// comment above the `par_iter()` call in `main` for why nothing here is shared across rooms.
fn run_room(path: &Path, gen_root: &str, trials: u32, max_depth: i32) -> RoomReport {
    let name = path.file_name().unwrap().to_string_lossy().to_string();
    let assets = FsAssetSource::new(gen_root);
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();

    let root_structure = match Structure::load(path) {
        Ok(s) => Rc::new(s),
        Err(e) => {
            eprintln!("[main] WARNING: failed to load {path:?}: {e}");
            return RoomReport {
                name,
                mean_total: 0.0,
                by_type: HashMap::new(),
                pieces_placed: 0,
                duds: 0,
                add_gilded: 0.0,
                cascade_alone_gilded: 0.0,
                combined_gilded: 0.0,
            };
        }
    };

    // Phase 1: baseline POI census.
    let mut sum_total: u64 = 0;
    let mut sum_by_type: HashMap<&str, u64> = HashMap::new();
    let mut pieces_placed: u64 = 0;
    let mut duds: u64 = 0;
    for _ in 0..trials {
        let result = assemble::assemble(&root_structure, &data, &mut rng, max_depth);
        sum_total += result.chests.len() as u64;
        for c in &result.chests {
            *sum_by_type.entry(c.chest_type).or_insert(0) += 1;
        }
        pieces_placed += result.pieces_placed as u64;
        duds += result.duds as u64;
    }
    let mean_total = sum_total as f64 / trials as f64;
    let by_type: HashMap<&'static str, f64> = CHEST_TYPES
        .iter()
        .map(|&ct| (ct, *sum_by_type.get(ct).unwrap_or(&0) as f64 / trials as f64))
        .collect();

    // The remaining phases are heavier per-trial (each does a full fresh assembly plus at least
    // one spatial pass), so they're capped the same way phase 2 already was before phase 3 existed.
    let heavy_trials = trials.min(2000);

    // Phase 2: one Bonus Gilded (decorator_add) modifier, in isolation.
    let mut sum_added: u64 = 0;
    for _ in 0..heavy_trials {
        let mut assembly = assemble::assemble(&root_structure, &data, &mut rng, max_depth);
        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);
        let added = schedule::apply_schedule(&mut assembly, region, rotation, CELL_SIZE, "gilded_chest", 1, 8, &[], None, &mut rng);
        sum_added += added.len() as u64;
    }
    let add_gilded = sum_added as f64 / heavy_trials as f64;

    // Phase 3a: one Gilded Cascade modifier alone - sources are baseline POI chests only.
    // Region sampling deliberately reuses random_nonzero_region (same convention as phase 2,
    // for apples-to-apples comparability), even though cascade itself has no (0,0) exemption.
    let mut sum_cascade_alone: u64 = 0;
    for _ in 0..heavy_trials {
        let mut assembly = assemble::assemble(&root_structure, &data, &mut rng, max_depth);
        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);
        let cascade = [schedule::Cascade { stacks: 1, chance: 0.25 }];
        let cascaded = schedule::apply_schedule(&mut assembly, region, rotation, CELL_SIZE, "gilded_chest", 0, 8, &cascade, None, &mut rng);
        sum_cascade_alone += cascaded.len() as u64;
    }
    let cascade_alone_gilded = sum_cascade_alone as f64 / heavy_trials as f64;

    // Phase 3b: Bonus Gilded + Gilded Cascade stacked in the same trial, same region/rotation -
    // decorator_add runs first and its additions are folded into cascade's source list, exactly
    // matching the verified real-game ordering (see the priority note above run_room).
    let mut sum_combined: u64 = 0;
    for _ in 0..heavy_trials {
        let mut assembly = assemble::assemble(&root_structure, &data, &mut rng, max_depth);
        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);
        let cascade = [schedule::Cascade { stacks: 1, chance: 0.25 }];
        let placed = schedule::apply_schedule(&mut assembly, region, rotation, CELL_SIZE, "gilded_chest", 1, 8, &cascade, None, &mut rng);
        sum_combined += placed.len() as u64;
    }
    let combined_gilded = sum_combined as f64 / heavy_trials as f64;

    RoomReport {
        name,
        mean_total,
        by_type,
        pieces_placed,
        duds,
        add_gilded,
        cascade_alone_gilded,
        combined_gilded,
    }
}

/// A uniform-random ROOM cell (even/even with tunnel_span 1), never the start room (0,0), wide
/// enough that origin*47 mod 16 cycles through its full 16-phase period many times over - this
/// reproduces the true population distribution of chunk-overlap outcomes.
fn random_nonzero_region(rng: &mut impl rand::Rng) -> (i32, i32) {
    schedule::random_room_region(rng)
}

fn run_export(args: &[String]) {
    let room_path = args.first().cloned().unwrap_or_else(|| {
        panic!("usage: wv-chest-sim export <room.nbt> <output.json> [max_depth] [factor] [add_count] [cascade_count]")
    });
    let out_path = args.get(1).cloned().unwrap_or_else(|| "room_export.json".to_string());
    let max_depth: i32 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(10);
    let factor: i32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(3);
    // Number of stacked 'the_vault:gilded' Bonus Gilded (decorator_add) and 'the_vault:gilded_cascade'
    // Gilded Cascade (decorator_add, chance=0.25) modifier instances to apply to this one realization
    // before exporting - each behaves as a fully independent pass against shared world state, exactly
    // like N separate crystal modifier stacks (see Phase 2/3 findings on additive, non-compounding
    // stacking). Both default to 0 so plain `export` keeps its original baseline-only behavior.
    let add_count: u32 = args.get(4).and_then(|s| s.parse().ok()).unwrap_or(0);
    let cascade_count: u32 = args.get(5).and_then(|s| s.parse().ok()).unwrap_or(0);

    let gen_root = wv_chest_sim::paths::gen_root();
    let assets = FsAssetSource::new(gen_root);
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();

    let root_structure = Rc::new(Structure::load(std::path::Path::new(&room_path)).unwrap_or_else(|e| {
        panic!("failed to load {room_path}: {e}")
    }));
    let mut assembly = assemble::assemble(&root_structure, &data, &mut rng, max_depth);

    // The game's own event order (see schedule.rs): per chunk, every overlapping region's
    // placement runs every bonus modifier over the whole chunk and re-cascades the chunk's
    // non-duped chests.
    let region = random_nonzero_region(&mut rng);
    let rotation = Rotation::random(&mut rng);
    let cascade = [schedule::Cascade { stacks: cascade_count, chance: 0.25 }];
    let extra_chests = schedule::apply_schedule(&mut assembly, region, rotation, CELL_SIZE, "gilded_chest", add_count, 8, &cascade, None, &mut rng);
    if add_count > 0 || cascade_count > 0 {
        eprintln!(
            "[export] applied {add_count}x Bonus Gilded + {cascade_count}x Gilded Cascade: +{} gilded chests on top of {} baseline",
            extra_chests.len(),
            assembly.chests.len()
        );
    }

    let chest_positions: HashSet<(i32, i32, i32)> = assembly
        .chests
        .iter()
        .chain(extra_chests.iter())
        .map(|c| c.pos)
        .collect();

    // Natural-terrain rooms are too irregular for box-merging alone to help much (measured:
    // only ~2x on bee1.nbt). Downsample the *terrain* into factor^3 coarse cells first (occupied
    // if any constituent block is solid) - chests stay at full resolution throughout, only the
    // surrounding rock context is coarsened. Then exposed-surface + greedy 2D rectangle merge
    // runs on the much smaller coarse grid, and emitted boxes are scaled back up to room-local
    // block coordinates so they line up with the full-resolution chest positions. Shared with
    // the wv-web crate via wv_chest_sim::voxel::merge_terrain_boxes, not duplicated here.
    let size = assembly.solid.size();
    let (merged_boxes, exposed) = wv_chest_sim::voxel::merge_terrain_boxes(&assembly.solid, factor, &chest_positions);
    let boxes: Vec<String> = merged_boxes
        .iter()
        .map(|(min, max)| {
            format!(
                r#"{{"min":[{},{},{}],"max":[{},{},{}]}}"#,
                min.0, min.1, min.2, max.0, max.1, max.2
            )
        })
        .collect();

    let mut chests = Vec::new();
    for c in assembly.chests.iter().chain(extra_chests.iter()) {
        chests.push(format!(
            r#"{{"pos":[{},{},{}],"type":"{}","strongbox":{}}}"#,
            c.pos.0, c.pos.1, c.pos.2, c.chest_type, c.is_strongbox
        ));
    }

    let gates: Vec<String> = assembly
        .gates
        .iter()
        .map(|g| format!("[{},{},{}]", g.0, g.1, g.2))
        .collect();

    let json = format!(
        r#"{{"size":[{},{},{}],"voxels":[{}],"chests":[{}],"gates":[{}]}}"#,
        size.0,
        size.1,
        size.2,
        boxes.join(","),
        chests.join(","),
        gates.join(",")
    );
    std::fs::write(&out_path, &json).unwrap_or_else(|e| panic!("failed to write {out_path}: {e}"));
    println!(
        "exported {} merged boxes ({} raw exposed voxels) + {} chests to {out_path} ({} bytes)",
        boxes.len(),
        exposed,
        assembly.chests.len() + extra_chests.len(),
        json.len()
    );
}
