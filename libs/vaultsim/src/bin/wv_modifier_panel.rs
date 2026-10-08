use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Instant;

use rand::rngs::SmallRng;
use rand::SeedableRng;
use rayon::prelude::*;
use serde::Serialize;

use wv_chest_sim::assemble::{self, ChestSpot};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::decorator;
use wv_chest_sim::strongbox;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::transform::Rotation;

const CELL_SIZE: i32 = 47;

struct Args {
    gen_root: String,
    chest_type: &'static str,
    vault_level: u32,
    trials_per_room: u32,
    max_bonus: u32,
    max_cascade: u32,
    output: String,
    /// If set, skips the Monte Carlo sweep entirely and just re-renders the HTML surface (with
    /// the current --budget-step) from a CSV this same tool already wrote - the grid's own
    /// max bonus/cascade is inferred from the file's contents, not from --max-bonus/--max-cascade.
    from_csv: Option<String>,
    budget_step: u32,
    /// Exponent applied to each budget line's own 0..1 peak-normalized value before coloring -
    /// 1 = linear white->red, higher = only points very near that line's own max read as red.
    color_power: f64,
    /// Emits one HTML render per entry, each clipped to bonus,cascade in 0..=N - lets one sweep
    /// produce both a "zoomed in" and a "full range" view without resimulating anything.
    render_ranges: Vec<u32>,
}

fn static_chest_type(s: &str) -> &'static str {
    match s {
        "wooden_chest" => "wooden_chest",
        "living_chest" => "living_chest",
        "ornate_chest" => "ornate_chest",
        "gilded_chest" => "gilded_chest",
        other => {
            eprintln!("[panel][FALLBACK] unknown --chest-type {other}; using gilded_chest");
            "gilded_chest"
        }
    }
}

fn get_flag<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(String::as_str)
}

fn parse_args() -> Args {
    let raw: Vec<String> = std::env::args().collect();
    let max_bonus = get_flag(&raw, "--max-bonus").and_then(|s| s.parse().ok()).unwrap_or(200);
    let max_cascade = get_flag(&raw, "--max-cascade").and_then(|s| s.parse().ok()).unwrap_or(200);
    let render_ranges: Vec<u32> = match get_flag(&raw, "--render-ranges") {
        Some(s) => s.split(',').filter_map(|p| p.trim().parse().ok()).collect(),
        None => vec![max_bonus.max(max_cascade)],
    };
    Args {
        gen_root: get_flag(&raw, "--gen-root")
            .map(str::to_string)
            .unwrap_or_else(|| wv_chest_sim::paths::gen_root()),
        chest_type: static_chest_type(get_flag(&raw, "--chest-type").unwrap_or("gilded_chest")),
        vault_level: get_flag(&raw, "--vault-level").and_then(|s| s.parse().ok()).unwrap_or(50),
        trials_per_room: get_flag(&raw, "--trials-per-room").and_then(|s| s.parse().ok()).unwrap_or(30),
        max_bonus,
        max_cascade,
        output: get_flag(&raw, "--output")
            .map(str::to_string)
            .unwrap_or_else(|| wv_chest_sim::paths::out_path("wv-modifier-panel")),
        from_csv: get_flag(&raw, "--from-csv").map(str::to_string),
        budget_step: get_flag(&raw, "--budget-step").and_then(|s| s.parse().ok()).unwrap_or(10),
        color_power: get_flag(&raw, "--color-power").and_then(|s| s.parse().ok()).unwrap_or(5.0),
        render_ranges,
    }
}

/// Same uniform-random nonzero grid-cell picker used by main.rs/lib.rs (each binary keeps its
/// own copy rather than sharing it through the lib, matching the existing convention).
fn random_nonzero_region(rng: &mut impl rand::Rng) -> (i32, i32) {
    loop {
        let gx = rng.gen_range(-500..500);
        let gz = rng.gen_range(-500..500);
        if (gx, gz) != (0, 0) {
            return (gx, gz);
        }
    }
}

/// One room file's contribution to the global (bonus, cascade) -> avg_chests grid.
struct RoomAccum {
    /// sum_target[b][c] = sum, across every trial this room ran, of the target-type chest count
    /// after exactly b stacked decorator_add passes and c stacked decorator_cascade passes.
    sum_target: Vec<Vec<u64>>,
    /// Sum of each trial's baseline saturation_number (chest-slot capacity) - a fixed property
    /// of one assembled room, independent of bonus/cascade, so it has no [b][c] axes of its own.
    sum_saturation: u64,
    trials: u64,
}

/// Runs `trials_per_room` independent trials of this room, and for each trial walks the entire
/// bonus x cascade grid by *stepping* rather than restarting: decorator_add_pass and
/// decorator_cascade_pass are each "apply one more independent pass" functions that don't care
/// how many calls came before or will come after, so calling either one N times in a row and
/// recording the chest count after every call gives the exact value for every count 0..N in a
/// single pass - no need to reassemble the room or recompute from scratch per grid cell.
///
/// For a fixed bonus level b, all `max_cascade` cascade steps share one fixed source list
/// (baseline + add chests through level b, computed once - matches apply_modifiers/run_export's
/// existing "cascade_sources computed once, after every add pass" sequencing), so the cascade
/// sweep for level b runs on a throwaway clone of the grid/liquid/chest_positions: cascade's
/// mutations must never leak into bonus level b+1's starting state, only the add pass's do.
fn run_room_sweep(path: &Path, args: &Args) -> RoomAccum {
    let nb = (args.max_bonus + 1) as usize;
    let nc = (args.max_cascade + 1) as usize;
    let mut sum_target = vec![vec![0u64; nc]; nb];
    let mut sum_saturation = 0u64;

    let root_structure = match Structure::load(path) {
        Ok(s) => Rc::new(s),
        Err(e) => {
            eprintln!("[wv-modifier-panel] WARNING: failed to load {path:?}: {e}");
            return RoomAccum { sum_target, sum_saturation, trials: 0 };
        }
    };

    let assets = FsAssetSource::new(args.gen_root.clone());
    let data = DataSource::new(&assets);
    let mut rng = SmallRng::from_entropy();
    let start = Instant::now();

    for _ in 0..args.trials_per_room {
        let mut work = assemble::assemble(&root_structure, &data, &mut rng, 10);
        strongbox::apply_strongbox_rolls(&mut work.chests, args.vault_level, &mut rng);

        let region = random_nonzero_region(&mut rng);
        let rotation = Rotation::random(&mut rng);

        let mut chest_positions: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
        // Computed once on the untouched baseline - a room's fixed chest-slot capacity, the same
        // denominator for every (bonus, cascade) cell this trial contributes to.
        let saturation_number = decorator::count_chest_slots(&work.solid, &work.liquid, &chest_positions, &work.non_sturdy);
        sum_saturation += saturation_number as u64;

        let baseline_target_count =
            work.chests.iter().filter(|c| c.chest_type == args.chest_type).count() as u64;
        let mut extra_from_add: Vec<ChestSpot> = Vec::new();

        for b in 0..=args.max_bonus {
            if b > 0 {
                let mut added = decorator::decorator_add_pass(
                    &mut work.solid,
                    &mut chest_positions,
                    &work.liquid,
                    &work.non_sturdy,
                    region,
                    rotation,
                    CELL_SIZE,
                    8,
                    true,
                    args.chest_type,
                    &mut rng,
                );
                strongbox::apply_strongbox_rolls(&mut added, args.vault_level, &mut rng);
                extra_from_add.extend(added);
            }

            // All of extra_from_add is already args.chest_type by construction (decorator_add_pass
            // was called with that exact type), so no filter is needed here - only the baseline
            // census mixes chest types.
            let base_count = baseline_target_count + extra_from_add.len() as u64;
            sum_target[b as usize][0] += base_count;

            if args.max_cascade == 0 {
                continue;
            }
            let cascade_sources: Vec<ChestSpot> =
                work.chests.iter().cloned().chain(extra_from_add.iter().cloned()).collect();

            let mut cgrid = work.solid.clone();
            let mut cliquid = work.liquid.clone();
            let mut cpositions = chest_positions.clone();
            let mut cascade_total = 0u64;

            for c in 1..=args.max_cascade {
                let cascaded = decorator::decorator_cascade_pass(
                    &mut cgrid,
                    &mut cliquid,
                    &mut cpositions,
                    &work.non_sturdy,
                    &cascade_sources,
                    region,
                    rotation,
                    CELL_SIZE,
                    0.25,
                    args.chest_type,
                    &mut rng,
                );
                // Same reasoning as extra_from_add above - cascaded chests are already filtered
                // to args.chest_type by decorator_cascade_pass's own chest_type_filter argument.
                cascade_total += cascaded.len() as u64;
                sum_target[b as usize][c as usize] += base_count + cascade_total;
            }
        }
    }

    eprintln!(
        "[wv-modifier-panel] {} done in {:.2?} ({} trials)",
        path.file_name().unwrap_or_default().to_string_lossy(),
        start.elapsed(),
        args.trials_per_room
    );
    RoomAccum { sum_target, sum_saturation, trials: args.trials_per_room as u64 }
}

/// Runs the full sweep across every room file and reduces all of their accumulators into one
/// (bonus, cascade) -> avg_chests grid, plus the matching saturation_percent grid.
fn run_sweep(args: &Args) -> (Vec<Vec<f64>>, Vec<Vec<f64>>, u64) {
    let rooms_dir = format!("{}\\structures\\vault\\rooms\\common", args.gen_root);
    let mut room_files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir)
        .unwrap_or_else(|e| panic!("failed to read rooms dir {rooms_dir}: {e}"))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    room_files.sort();

    println!(
        "[wv-modifier-panel] {} rooms x {} trials, chest_type={} vault_level={} grid={}x{}",
        room_files.len(),
        args.trials_per_room,
        args.chest_type,
        args.vault_level,
        args.max_bonus + 1,
        args.max_cascade + 1
    );

    let accums: Vec<RoomAccum> = room_files.par_iter().map(|p| run_room_sweep(p, args)).collect();

    let nb = (args.max_bonus + 1) as usize;
    let nc = (args.max_cascade + 1) as usize;
    let mut total_target = vec![vec![0u64; nc]; nb];
    let mut total_saturation = 0u64;
    let mut total_trials = 0u64;
    for acc in &accums {
        for b in 0..nb {
            for c in 0..nc {
                total_target[b][c] += acc.sum_target[b][c];
            }
        }
        total_saturation += acc.sum_saturation;
        total_trials += acc.trials;
    }

    let avg: Vec<Vec<f64>> = total_target
        .iter()
        .map(|row| row.iter().map(|&t| t as f64 / total_trials.max(1) as f64).collect())
        .collect();
    let sat_pct: Vec<Vec<f64>> = total_target
        .iter()
        .map(|row| row.iter().map(|&t| t as f64 / total_saturation.max(1) as f64).collect())
        .collect();
    (avg, sat_pct, total_trials)
}

fn write_csv(output: &str, avg: &[Vec<f64>], sat_pct: &[Vec<f64>]) -> String {
    let mut csv = String::from("bonus,cascade,avg_chests,saturation_percent\n");
    for (b, row) in avg.iter().enumerate() {
        for (c, &a) in row.iter().enumerate() {
            csv.push_str(&format!("{b},{c},{a:.4},{:.6}\n", sat_pct[b][c]));
        }
    }
    let path = format!("{output}.csv");
    std::fs::write(&path, &csv).unwrap_or_else(|e| panic!("failed to write {path}: {e}"));
    path
}

/// Parses a CSV this same tool wrote earlier, inferring the grid's max bonus/cascade directly
/// from the data rather than trusting --max-bonus/--max-cascade (which may not match the file
/// that's actually being loaded).
fn read_avg_grid(path: &str) -> (Vec<Vec<f64>>, u32, u32) {
    let content = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("failed to read {path}: {e}"));
    let mut cells: Vec<(u32, u32, f64)> = Vec::new();
    let mut max_b = 0u32;
    let mut max_c = 0u32;
    for line in content.lines().skip(1) {
        if line.trim().is_empty() {
            continue;
        }
        let mut parts = line.split(',');
        let b: u32 = parts.next().and_then(|s| s.parse().ok()).unwrap_or_else(|| panic!("bad row: {line}"));
        let c: u32 = parts.next().and_then(|s| s.parse().ok()).unwrap_or_else(|| panic!("bad row: {line}"));
        let avg: f64 = parts.next().and_then(|s| s.parse().ok()).unwrap_or_else(|| panic!("bad row: {line}"));
        max_b = max_b.max(b);
        max_c = max_c.max(c);
        cells.push((b, c, avg));
    }
    let mut grid = vec![vec![0.0f64; (max_c + 1) as usize]; (max_b + 1) as usize];
    for (b, c, avg) in cells {
        grid[b as usize][c as usize] = avg;
    }
    (grid, max_b, max_c)
}

#[derive(Serialize)]
struct BudgetLine {
    budget: u32,
    x: Vec<u32>,
    y: Vec<u32>,
    z: Vec<f64>,
}

/// Traces the diagonal bonus+cascade=budget line across the surface for every `step`-spaced
/// budget level (always including the absolute max budget exactly, even if it falls off-step) -
/// since bonus and cascade cost the same one point per stack, "what's the best split of N
/// points" is exactly the question of where the peak sits along one of these lines.
fn build_budget_lines(avg: &[Vec<f64>], max_bonus: u32, max_cascade: u32, step: u32) -> Vec<BudgetLine> {
    let total_max = max_bonus + max_cascade;
    let mut budgets: Vec<u32> = (0..=total_max).step_by(step.max(1) as usize).collect();
    if budgets.last() != Some(&total_max) {
        budgets.push(total_max);
    }
    budgets
        .into_iter()
        .map(|budget| {
            let lo = budget.saturating_sub(max_cascade);
            let hi = budget.min(max_bonus);
            let mut x = Vec::new();
            let mut y = Vec::new();
            let mut z = Vec::new();
            for b in lo..=hi {
                let c = budget - b;
                x.push(c);
                y.push(b);
                z.push(avg[b as usize][c as usize]);
            }
            BudgetLine { budget, x, y, z }
        })
        .collect()
}

const HTML_TEMPLATE: &str = r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>wv-modifier-panel</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body { margin: 0; background: #14151a; color: #e8e8ec; font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
  #plot { width: 100vw; height: 92vh; }
  #info { padding: 10px 16px; font-size: 13px; color: #9a9ca8; }
</style>
</head>
<body>
<div id="info">__INFO__</div>
<div id="plot"></div>
<script>
  const z = __Z__;
  const x = __X__;
  const y = __Y__;
  const budgetLines = __LINES__;

  const surface = {
    type: 'surface',
    x: x,
    y: y,
    z: z,
    colorscale: 'Viridis',
    contours: { z: { show: true, usecolormap: true, project: { z: true } } },
  };

  const COLOR_POWER = __POWER__;
  const lineTraces = budgetLines.map((bl, i) => {
    const zMin = Math.min(...bl.z);
    const zMax = Math.max(...bl.z);
    const range = (zMax - zMin) || 1;
    // Raise the 0..1 peak-normalized value to COLOR_POWER before coloring - at 1 (linear) the
    // whole line fades evenly, higher powers squash everything below the peak toward 0 (white),
    // so only points genuinely close to that line's own max read as red.
    const colorVals = bl.z.map((zv) => Math.pow((zv - zMin) / range, COLOR_POWER));
    return {
      type: 'scatter3d',
      mode: 'lines',
      x: bl.x,
      y: bl.y,
      z: bl.z,
      line: {
        color: colorVals,
        colorscale: [[0, '#ffffff'], [1, '#ff0000']],
        cmin: 0,
        cmax: 1,
        width: 6,
      },
      hovertemplate: 'bonus=%{y}<br>cascade=%{x}<br>avg=%{z:.2f}<extra>budget ' + bl.budget + '</extra>',
      legendgroup: 'budget',
      showlegend: i === 0,
      name: 'bonus+cascade = const (red = that line\'s own peak)',
    };
  });

  Plotly.newPlot('plot', [surface, ...lineTraces], {
    scene: {
      xaxis: { title: 'cascade (stacked)' },
      yaxis: { title: 'bonus (stacked)' },
      zaxis: { title: 'avg target chests / room' },
    },
    margin: { l: 0, r: 0, t: 10, b: 0 },
    paper_bgcolor: '#14151a',
    plot_bgcolor: '#14151a',
    font: { color: '#e8e8ec' },
    legend: { x: 0.02, y: 0.98 },
  }, {responsive: true});
</script>
</body>
</html>
"#;

/// Clips `avg` down to bonus,cascade in 0..=max_b/0..=max_c, capping at whatever's actually
/// available rather than panicking if a render range was requested larger than the real data.
fn clip_grid(avg: &[Vec<f64>], max_b: u32, max_c: u32) -> Vec<Vec<f64>> {
    let avail_b = avg.len().saturating_sub(1);
    let avail_c = avg.first().map(|r| r.len().saturating_sub(1)).unwrap_or(0);
    if max_b as usize > avail_b || max_c as usize > avail_c {
        eprintln!(
            "[wv-modifier-panel] WARNING: requested render range {max_b}x{max_c} exceeds available data \
             ({avail_b}x{avail_c}) - clipping to what's available"
        );
    }
    let nb = (max_b as usize + 1).min(avg.len());
    avg.iter()
        .take(nb)
        .map(|row| {
            let nc = (max_c as usize + 1).min(row.len());
            row[..nc].to_vec()
        })
        .collect()
}

/// Renders one HTML view clipped to bonus,cascade in 0..=render_max. When exactly one range was
/// requested and it covers the full swept grid, keeps the plain `<output>.html` name (matches
/// the original single-output behavior); otherwise suffixes with `_<bonus>x<cascade>` so e.g.
/// `--render-ranges 100,200` produces two clearly distinct files from one sweep.
fn write_html(
    args: &Args,
    full_avg: &[Vec<f64>],
    full_max_bonus: u32,
    full_max_cascade: u32,
    info: &str,
    render_max: u32,
) -> String {
    let clipped = clip_grid(full_avg, render_max, render_max);
    let max_bonus = (clipped.len() - 1) as u32;
    let max_cascade = (clipped[0].len() - 1) as u32;
    let x: Vec<u32> = (0..=max_cascade).collect();
    let y: Vec<u32> = (0..=max_bonus).collect();
    let budget_lines = build_budget_lines(&clipped, max_bonus, max_cascade, args.budget_step);
    let html = HTML_TEMPLATE
        .replace("__INFO__", info)
        .replace("__Z__", &serde_json::to_string(&clipped).unwrap())
        .replace("__X__", &serde_json::to_string(&x).unwrap())
        .replace("__Y__", &serde_json::to_string(&y).unwrap())
        .replace("__LINES__", &serde_json::to_string(&budget_lines).unwrap())
        .replace("__POWER__", &args.color_power.to_string());
    let is_single_full_render =
        args.render_ranges.len() == 1 && max_bonus == full_max_bonus && max_cascade == full_max_cascade;
    let path = if is_single_full_render {
        format!("{}.html", args.output)
    } else {
        format!("{}_{}x{}.html", args.output, max_bonus, max_cascade)
    };
    std::fs::write(&path, &html).unwrap_or_else(|e| panic!("failed to write {path}: {e}"));
    path
}

fn main() {
    let args = parse_args();

    let (avg, max_bonus, max_cascade, info) = if let Some(csv_path) = &args.from_csv {
        let (grid, mb, mc) = read_avg_grid(csv_path);
        println!("[wv-modifier-panel] regenerating HTML from {csv_path} ({}x{} grid, no resimulation)", mb + 1, mc + 1);
        let info = format!("loaded from {csv_path} - drag to rotate, scroll to zoom");
        (grid, mb, mc, info)
    } else {
        let start = Instant::now();
        let (avg, sat_pct, total_trials) = run_sweep(&args);
        let elapsed = start.elapsed();
        let csv_path = write_csv(&args.output, &avg, &sat_pct);
        println!(
            "[wv-modifier-panel] wrote {csv_path} ({} cells, {total_trials} total trials) in {elapsed:.2?}",
            avg.len() * avg[0].len()
        );
        let info = format!(
            "chest_type={} vault_level={} trials/room={} ({total_trials} total trials) - drag to rotate, scroll to zoom",
            args.chest_type, args.vault_level, args.trials_per_room
        );
        (avg, args.max_bonus, args.max_cascade, info)
    };

    for &range in &args.render_ranges {
        let html_path = write_html(&args, &avg, max_bonus, max_cascade, &info, range);
        println!("[wv-modifier-panel] wrote {html_path}");
    }
}
