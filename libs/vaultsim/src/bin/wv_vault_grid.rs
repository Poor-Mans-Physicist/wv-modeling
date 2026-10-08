//! Themed vault simulation: rooms drawn from a real theme's room pool, assembled with the theme's
//! palettes (marker blocks, plants, chest placeholders with strongbox/enigma rolls), mirrored and
//! rotated like `VaultGridLayout.getRoom`, then Bonus/cascade applied with the 3.21.6 schedule.
//!
//! `wv_vault_grid grid  --theme desert --size 50 --bonus 51 --cascade 74 [--moc 0] [--level 475]
//!                      [--seed 1] --out grid.json [--rooms rooms.jsonl]`
//!   A size x size block of room cells centred on the start room (region (0,0)); cell (i, j) is
//!   region (2(i - size/2), 2(j - size/2)). Rooms that are not common rooms (challenge / omega /
//!   gateway, ~9 % of map pools) are recorded as special and not simulated.
//! `wv_vault_grid matched --jobs jobs.tsv --k 8 --out out.jsonl [--level 475]`
//!   jobs.tsv columns: tag template rx rz bonus cascade moc theme. The room's palette is drawn from
//!   the theme's common pool entries for that template; K independent realisations per job.
//! `wv_vault_grid cells --cells cells.tsv --per 32 --out rooms.jsonl [--themes beach,cave,desert,nether,void]
//!                      [--align-x w0,w2,..,w14] [--align-z ...] [--seed 1] [--level 475] [--crn]`
//!   --chest living|gilded|ornate (any mode): the chest type generated and measured (default living).
//!   --crn: room k of every cell uses the same seed (same theme, alignment, template and base room), so cells differ
//!   only by their stacks (common random numbers).
//!   cells.tsv columns: bonus cascade. For every cell, `per` lane_cli room records: each room draws a
//!   theme uniformly from `--themes`, a common room from that theme's pool, and a region whose chunk
//!   alignment on each axis is drawn from the given weights (offsets 0, 2, .., 14; uniform if absent).
use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Write;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;

use wv_chest_sim::assemble::{self, AssemblyResult};
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::palette::PaletteLibrary;
use wv_chest_sim::schedule::{self, Bonus, Cascade};
use wv_chest_sim::transform::Rotation;

type P = (i32, i32, i32);
const CELL: i32 = 47;

static TARGET_CELL: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// The chest type rooms are generated and measured for: `--chest living|gilded|ornate` (default living).
fn target() -> &'static str {
    TARGET_CELL.get().map(String::as_str).unwrap_or("living_chest")
}

#[derive(Clone, Copy)]
struct Cfg {
    bonus: u32,
    cascade: u32,
    moc: u32,
    level: u32,
}

struct RoomOut {
    n: usize,
    clump: f64,
    maxcomp: usize,
    strongbox: usize,
    enigma: usize,
    slots: usize,
    other_types: String,
    world_targets: Vec<P>,
    world_others: Vec<(P, String)>,
    mirrored: bool,
    rot: u8,
    asm: AssemblyResult,
    targets: Vec<P>,
}

fn flag<'a>(a: &'a [String], name: &str) -> Option<&'a str> {
    a.iter().position(|x| x == name).and_then(|i| a.get(i + 1)).map(String::as_str)
}

fn components(pts: &[P]) -> Vec<usize> {
    let set: HashSet<P> = pts.iter().copied().collect();
    let mut seen: HashSet<P> = HashSet::new();
    let mut sizes = Vec::new();
    for &p in pts {
        if !seen.insert(p) {
            continue;
        }
        let mut q = VecDeque::from([p]);
        let mut n = 0;
        while let Some((x, y, z)) = q.pop_front() {
            n += 1;
            for dx in -1..=1 {
                for dy in -1..=1 {
                    for dz in -1..=1 {
                        let c = (x + dx, y + dy, z + dz);
                        if set.contains(&c) && seen.insert(c) {
                            q.push_back(c);
                        }
                    }
                }
            }
        }
        sizes.push(n);
    }
    sizes
}

fn rot_index(r: Rotation) -> u8 {
    [Rotation::NONE, Rotation::CW90, Rotation::CW180, Rotation::CCW90].iter().position(|x| *x == r).unwrap_or(0) as u8
}

/// Routerunner's slot definition on a movement grid (target chests open, other chests solid), over
/// the y window Routerunner snapshots: 4 below the lowest target to 5 above the highest.
fn slots(asm: &AssemblyResult, targets: &HashSet<P>, others: &HashSet<P>) -> usize {
    let (sx, sy, sz) = asm.solid.size();
    let (Some(lo), Some(hi)) = (targets.iter().map(|p| p.1).min(), targets.iter().map(|p| p.1).max()) else {
        return 0;
    };
    let y0 = (lo - 4).max(0);
    let y1 = (hi + 5).min(sy - 1);
    let blocks = |p: P| -> bool {
        if targets.contains(&p) {
            return false;
        }
        others.contains(&p) || (asm.solid.is_solid(p) && !asm.passable.contains(&p) && !asm.liquid.contains(&p))
    };
    let mut n = 0;
    for x in 0..sx {
        for z in 0..sz {
            for y in (y0 + 1)..=y1 {
                if !blocks((x, y, z)) && blocks((x, y - 1, z)) {
                    n += 1;
                }
            }
        }
    }
    n
}

fn sim_room(data: &DataSource, lib: &PaletteLibrary, template: &str, palettes: &[String], region: (i32, i32), cfg: Cfg, rng: &mut SmallRng) -> Option<RoomOut> {
    sim_room_forced(data, lib, template, palettes, region, cfg, None, rng)
}

#[allow(clippy::too_many_arguments)]
fn sim_room_forced(
    data: &DataSource,
    lib: &PaletteLibrary,
    template: &str,
    palettes: &[String],
    region: (i32, i32),
    cfg: Cfg,
    force: Option<(bool, Rotation)>,
    rng: &mut SmallRng,
) -> Option<RoomOut> {
    let root = data.get_structure(template)?;
    let (mut asm, chain) = assemble::assemble_themed(&root, palettes, data, lib, rng, 10);
    let mut mirrored = rng.gen_bool(0.5);
    let mut rot = Rotation::random(rng);
    if let Some((m, r)) = force {
        mirrored = m;
        rot = r;
    }
    if mirrored {
        assemble::mirror_x(&mut asm);
    }
    let dist = lib.bonus_dist(&chain, target());
    let cascades = [Cascade { stacks: cfg.cascade, chance: 0.25 }, Cascade { stacks: 15 * cfg.moc, chance: 0.01 }];
    let placed = schedule::apply_schedule_with(&mut asm, region, rot, CELL, target(), cfg.bonus, 8, &cascades, Bonus::Dist(&dist), rng);
    let all: Vec<_> = asm.chests.iter().chain(placed.iter()).cloned().collect();
    let (sx, _, sz) = asm.solid.size();
    let inside = |p: P| p.0 >= 0 && p.0 < sx && p.2 >= 0 && p.2 < sz;
    let targets: Vec<P> = all.iter().filter(|c| c.chest_type == target() && !c.is_strongbox && inside(c.pos)).map(|c| c.pos).collect();
    let others: HashSet<P> = all.iter().filter(|c| !(c.chest_type == target() && !c.is_strongbox)).map(|c| c.pos).collect();
    let strongbox = all.iter().filter(|c| c.chest_type == target() && c.is_strongbox && inside(c.pos)).count();
    let enigma = all.iter().filter(|c| c.chest_type == "enigma_chest" && inside(c.pos)).count();
    let sizes = components(&targets);
    let n = targets.len();
    let clump = if n == 0 { 0.0 } else { sizes.iter().map(|s| (s * s) as f64).sum::<f64>() / n as f64 };
    let tset: HashSet<P> = targets.iter().copied().collect();
    let slots = slots(&asm, &tset, &others);
    let mut by: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for c in all.iter().filter(|c| inside(c.pos) && !(c.chest_type == target() && !c.is_strongbox)) {
        *by.entry(format!("{}{}", c.chest_type, if c.is_strongbox { "_strongbox" } else { "" })).or_insert(0) += 1;
    }
    let outer = wv_chest_sim::decorator::room_world_transform(region, rot, CELL);
    let rel = |p: P| {
        let w = outer.apply(p);
        (w.0 - region.0 * CELL, w.1, w.2 - region.1 * CELL)
    };
    let world_targets: Vec<P> = targets.iter().map(|p| rel(*p)).collect();
    let world_others: Vec<(P, String)> = all
        .iter()
        .filter(|c| inside(c.pos) && !(c.chest_type == target() && !c.is_strongbox))
        .map(|c| (rel(c.pos), format!("{}{}", c.chest_type, if c.is_strongbox { "_strongbox" } else { "" })))
        .collect();
    let other_types = by.iter().map(|(k, v)| format!(r#""{k}":{v}"#)).collect::<Vec<_>>().join(",");
    Some(RoomOut {
        n,
        clump,
        maxcomp: sizes.iter().copied().max().unwrap_or(0),
        strongbox,
        enigma,
        slots,
        other_types,
        world_targets,
        world_others,
        mirrored,
        rot: rot_index(rot),
        asm,
        targets,
    })
}

fn b64(bytes: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::with_capacity(bytes.len() * 4 / 3 + 4);
    for c in bytes.chunks(3) {
        let v = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        s.push(T[(v >> 18) as usize & 63] as char);
        s.push(T[(v >> 12) as usize & 63] as char);
        s.push(if c.len() > 1 { T[(v >> 6) as usize & 63] as char } else { '=' });
        s.push(if c.len() > 2 { T[v as usize & 63] as char } else { '=' });
    }
    s
}

/// A Routerunner/lane_cli room record: movement grid (walls, collidable decor and non-target chests
/// set; plants, light, liquids and target chests open) plus the target chest list, room-local.
fn room_record(key: &str, r: &RoomOut, cfg: Cfg, room: &str) -> String {
    let (sx, sy, sz) = r.asm.solid.size();
    let mut bits = vec![0u8; ((sx * sy * sz) as usize).div_ceil(8)];
    let tset: HashSet<P> = r.targets.iter().copied().collect();
    for x in 0..sx {
        for y in 0..sy {
            for z in 0..sz {
                let p = (x, y, z);
                if tset.contains(&p) {
                    continue;
                }
                if r.asm.solid.is_solid(p) && !r.asm.passable.contains(&p) && !r.asm.liquid.contains(&p) {
                    let i = ((x * sy + y) * sz + z) as usize;
                    bits[i >> 3] |= 1 << (i & 7);
                }
            }
        }
    }
    let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
    enc.write_all(&bits).unwrap();
    let gz = enc.finish().unwrap();
    let gates = &r.asm.gates;
    let (mut ent, mut ex) = ((0, 24, 23), (46, 24, 23));
    let mut best = -1i64;
    for a in gates {
        for b in gates {
            let d = ((a.0 - b.0) as i64).pow(2) + ((a.2 - b.2) as i64).pow(2);
            if d > best {
                best = d;
                ent = *a;
                ex = *b;
            }
        }
    }
    let clamp = |p: P| (p.0.clamp(0, sx - 1), p.1.clamp(1, sy - 2), p.2.clamp(0, sz - 1));
    let (e2, x2) = (clamp(ent), clamp(ex));
    let chests: Vec<String> = r.targets.iter().map(|p| format!("[{},{},{}]", p.0, p.1, p.2)).collect();
    let gate_list: Vec<String> = gates.iter().map(|g| format!("[{},{},{}]", g.0, g.1, g.2)).collect();
    format!(
        r#"{{"key":"{}","grid":{{"sx":{},"sy":{},"sz":{},"enc":"gzip+base64","solidZ":"{}"}},"chests":[{}],"entrance":[{},{},{}],"exit":[{},{},{}],"gates":[{}],"origin":[0,0,0],"chainRange":6,"chainLimit":32,"modes":["point"],"real":{{"sim":true,"b":{},"c":{},"room":"{}","n":{}}}}}"#,
        key, sx, sy, sz, b64(&gz), chests.join(","), e2.0, e2.1, e2.2, x2.0, x2.1, x2.2, gate_list.join(","), cfg.bonus, cfg.cascade, room, r.targets.len()
    )
}

fn assets() -> &'static FsAssetSource {
    Box::leak(Box::new(FsAssetSource::new(&*wv_chest_sim::paths::gen_root())))
}

fn tile_seed(seed: u64, rx: i32, rz: i32) -> u64 {
    let mut h = seed ^ 0x9E37_79B9_7F4A_7C15;
    for v in [rx as i64 as u64, rz as i64 as u64] {
        h = (h ^ v).wrapping_mul(0x100_0000_01B3).rotate_left(29) ^ 0xD6E8_FEB8_6659_FD93;
    }
    h
}

fn run_grid(a: &[String]) {
    let theme = flag(a, "--theme").unwrap_or("desert").to_string();
    let size: i32 = flag(a, "--size").and_then(|s| s.parse().ok()).unwrap_or(50);
    let seed: u64 = flag(a, "--seed").and_then(|s| s.parse().ok()).unwrap_or(1);
    let cfg = Cfg {
        bonus: flag(a, "--bonus").and_then(|s| s.parse().ok()).unwrap_or(51),
        cascade: flag(a, "--cascade").and_then(|s| s.parse().ok()).unwrap_or(74),
        moc: flag(a, "--moc").and_then(|s| s.parse().ok()).unwrap_or(0),
        level: flag(a, "--level").and_then(|s| s.parse().ok()).unwrap_or(475),
    };
    let out = flag(a, "--out").expect("--out required").to_string();
    let rooms_out = flag(a, "--rooms").map(str::to_string);
    let pool = format!("the_vault:map/rooms/{theme}_rooms");
    let assets = assets();
    let cells: Vec<(i32, i32)> = (0..size).flat_map(|j| (0..size).map(move |i| (i, j))).collect();
    let started = std::time::Instant::now();
    let results: Vec<(String, Option<String>)> = cells
        .par_iter()
        .map_init(
            || (DataSource::new(assets), PaletteLibrary::new(assets, cfg.level)),
            |(data, lib), &(i, j)| {
                let rx = 2 * (i - size / 2);
                let rz = 2 * (j - size / 2);
                let ax = (rx * CELL).rem_euclid(16);
                let az = (rz * CELL).rem_euclid(16);
                let head = format!(r#""i":{i},"j":{j},"rx":{rx},"rz":{rz},"ax":{ax},"az":{az}"#);
                if (rx, rz) == (0, 0) {
                    return (format!(r#"{{{head},"special":"start"}}"#), None);
                }
                let mut rng = SmallRng::seed_from_u64(tile_seed(seed, rx, rz));
                let Some((template, palettes)) = data.sample_pool_entry(&pool, &mut rng) else {
                    eprintln!("[grid] fallback: pool {pool} gave no entry at region ({rx},{rz}); tile left empty");
                    return (format!(r#"{{{head},"special":"none"}}"#), None);
                };
                let short = template.rsplit('/').next().unwrap_or(&template).to_string();
                if !template.contains("/rooms/common/") {
                    return (format!(r#"{{{head},"special":"{short}","template":"{template}"}}"#), None);
                }
                let pal = palettes.last().map(|p| p.rsplit('/').next().unwrap_or(p).to_string()).unwrap_or_default();
                match sim_room(data, lib, &template, &palettes, (rx, rz), cfg, &mut rng) {
                    Some(r) => {
                        let line = format!(
                            r#"{{{head},"template":"{short}","palette":"{pal}","n":{},"clump":{:.2},"maxcomp":{},"strongbox":{},"enigma":{},"slots":{},"mirror":{},"rot":{}}}"#,
                            r.n, r.clump, r.maxcomp, r.strongbox, r.enigma, r.slots, r.mirrored, r.rot
                        );
                        let rec = rooms_out.as_ref().map(|_| room_record(&format!("grid|{theme}|{rx},{rz}|{short}"), &r, cfg, &short));
                        (line, rec)
                    }
                    None => {
                        eprintln!("[grid] fallback: template {template} failed to load at ({rx},{rz}); tile left empty");
                        (format!(r#"{{{head},"special":"load-failed","template":"{template}"}}"#), None)
                    }
                }
            },
        )
        .collect();
    let tiles: Vec<&str> = results.iter().map(|r| r.0.as_str()).collect();
    let json = format!(
        r#"{{"theme":"{theme}","size":{size},"seed":{seed},"bonus":{},"cascade":{},"moc":{},"level":{},"tiles":[{}]}}"#,
        cfg.bonus,
        cfg.cascade,
        cfg.moc,
        cfg.level,
        tiles.join(",\n")
    );
    std::fs::write(&out, json).expect("write grid json");
    if let Some(path) = rooms_out {
        let mut f = std::io::BufWriter::new(std::fs::File::create(path).expect("create rooms file"));
        for r in results.iter().filter_map(|r| r.1.as_ref()) {
            writeln!(f, "{r}").unwrap();
        }
    }
    eprintln!("[grid] {theme}: {} cells in {:.1?} -> {out}", cells.len(), started.elapsed());
}

fn run_matched(a: &[String]) {
    let jobs_path = flag(a, "--jobs").expect("--jobs required");
    let k: u64 = flag(a, "--k").and_then(|s| s.parse().ok()).unwrap_or(8);
    let level: u32 = flag(a, "--level").and_then(|s| s.parse().ok()).unwrap_or(475);
    let out = flag(a, "--out").expect("--out required");
    let dump = a.iter().any(|x| x == "--dump");
    let text = std::fs::read_to_string(jobs_path).expect("read jobs");
    let jobs: Vec<Vec<String>> = text.lines().filter(|l| !l.trim().is_empty()).map(|l| l.split('\t').map(str::to_string).collect()).collect();
    let assets = assets();
    let work: Vec<(usize, u64)> = (0..jobs.len()).flat_map(|j| (0..k).map(move |r| (j, r))).collect();
    let started = std::time::Instant::now();
    let lines: Vec<String> = work
        .par_iter()
        .map_init(
            || (DataSource::new(assets), PaletteLibrary::new(assets, level), HashMap::<String, Vec<(f64, String, Vec<String>)>>::new()),
            |(data, lib, leaves), &(ji, rep)| {
                let f = &jobs[ji];
                let (tag, template, rx, rz) = (&f[0], &f[1], f[2].parse::<i32>().unwrap(), f[3].parse::<i32>().unwrap());
                let cfg = Cfg { bonus: f[4].parse().unwrap(), cascade: f[5].parse().unwrap(), moc: f[6].parse().unwrap(), level };
                let theme = &f[7];
                let pool = format!("the_vault:map/rooms/{theme}_common_rooms");
                let cands: Vec<(f64, String, Vec<String>)> = leaves
                    .entry(pool.clone())
                    .or_insert_with(|| data.pool_leaves(&pool))
                    .iter()
                    .filter(|(_, t, _)| t.ends_with(&format!("/{template}")))
                    .cloned()
                    .collect();
                let mut rng = SmallRng::seed_from_u64(tile_seed(0xABCD ^ rep, rx, rz) ^ (ji as u64).wrapping_mul(0x9E37_79B9));
                if cands.is_empty() {
                    eprintln!("[matched] fallback: {template} not in {pool}; job {tag} skipped");
                    return format!(r#"{{"tag":"{tag}","template":"{template}","error":"not in pool"}}"#);
                }
                let total: f64 = cands.iter().map(|c| c.0).sum();
                let mut roll = rng.r#gen::<f64>() * total;
                let mut pick = &cands[0];
                for c in &cands {
                    if roll < c.0 {
                        pick = c;
                        break;
                    }
                    roll -= c.0;
                }
                let force = match (f.get(8), f.get(9)) {
                    (Some(deg), Some(m)) => {
                        let r = match deg.trim() {
                            "0" => Rotation::NONE,
                            "90" => Rotation::CW90,
                            "180" => Rotation::CW180,
                            _ => Rotation::CCW90,
                        };
                        Some((m.trim() == "yes" || m.trim() == "1" || m.trim() == "true", r))
                    }
                    _ => None,
                };
                match sim_room_forced(data, lib, &pick.1, &pick.2, (rx, rz), cfg, force, &mut rng) {
                    Some(r) if dump => {
                        let t: Vec<String> = r.world_targets.iter().map(|p| format!("[{},{},{}]", p.0, p.1, p.2)).collect();
                        let o: Vec<String> = r.world_others.iter().map(|(p, k)| format!(r#"[{},{},{},"{}"]"#, p.0, p.1, p.2, k)).collect();
                        format!(
                            r#"{{"tag":"{tag}","template":"{template}","rx":{rx},"rz":{rz},"rep":{rep},"palette":"{}","n":{},"clump":{:.2},"slots":{},"chests":[{}],"others":[{}]}}"#,
                            pick.2.last().cloned().unwrap_or_default(), r.n, r.clump, r.slots, t.join(","), o.join(",")
                        )
                    }
                    Some(r) => format!(
                        r#"{{"tag":"{tag}","template":"{template}","rx":{rx},"rz":{rz},"rep":{rep},"palette":"{}","n":{},"clump":{:.2},"maxcomp":{},"strongbox":{},"enigma":{},"slots":{},"others":{{{}}}}}"#,
                        pick.2.last().cloned().unwrap_or_default(),
                        r.n, r.clump, r.maxcomp, r.strongbox, r.enigma, r.slots, r.other_types
                    ),
                    None => {
                        eprintln!("[matched] fallback: {template} failed to load; job {tag} skipped");
                        format!(r#"{{"tag":"{tag}","template":"{template}","error":"load"}}"#)
                    }
                }
            },
        )
        .collect();
    std::fs::write(out, lines.join("\n")).expect("write matched output");
    eprintln!("[matched] {} rooms in {:.1?} -> {out}", lines.len(), started.elapsed());
}

/// Eight alignment weights (offsets 0, 2, .., 14) from a comma list; uniform when absent or malformed.
fn align_weights(a: &[String], name: &str) -> Vec<f64> {
    match flag(a, name) {
        Some(s) => {
            let w: Vec<f64> = s.split(',').filter_map(|x| x.trim().parse().ok()).collect();
            if w.len() == 8 && w.iter().all(|x| *x >= 0.0) && w.iter().sum::<f64>() > 0.0 {
                w
            } else {
                eprintln!("[cells] fallback: {name} needs 8 non-negative weights; using uniform alignment");
                vec![1.0; 8]
            }
        }
        None => vec![1.0; 8],
    }
}

fn pick_weighted(w: &[f64], rng: &mut SmallRng) -> usize {
    let total: f64 = w.iter().sum();
    let mut roll = rng.r#gen::<f64>() * total;
    for (i, x) in w.iter().enumerate() {
        if roll < *x {
            return i;
        }
        roll -= x;
    }
    w.len() - 1
}

fn run_cells(a: &[String]) {
    let cells_path = flag(a, "--cells").expect("--cells required");
    let per: u32 = flag(a, "--per").and_then(|s| s.parse().ok()).unwrap_or(32);
    let seed: u64 = flag(a, "--seed").and_then(|s| s.parse().ok()).unwrap_or(1);
    let level: u32 = flag(a, "--level").and_then(|s| s.parse().ok()).unwrap_or(475);
    let out = flag(a, "--out").expect("--out required").to_string();
    let themes: Vec<String> = flag(a, "--themes").unwrap_or("beach,cave,desert,nether,void").split(',').map(|t| t.trim().to_string()).collect();
    let wx = align_weights(a, "--align-x");
    let wz = align_weights(a, "--align-z");
    let crn = a.iter().any(|x| x == "--crn");
    let text = std::fs::read_to_string(cells_path).expect("read cells");
    let cells: Vec<(u32, u32)> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            (f[0].parse().expect("bonus"), f[1].parse().expect("cascade"))
        })
        .collect();
    let assets = assets();
    let work: Vec<(usize, u32)> = (0..cells.len()).flat_map(|ci| (0..per).map(move |k| (ci, k))).collect();
    let started = std::time::Instant::now();
    let lines: Vec<Option<String>> = work
        .par_iter()
        .map_init(
            || (DataSource::new(assets), PaletteLibrary::new(assets, level), HashMap::<String, Vec<(f64, String, Vec<String>)>>::new()),
            |(data, lib, leaves), &(ci, k)| {
                let (b, c) = cells[ci];
                let cell_mix = if crn { 0 } else { ((b as u64) << 32 | c as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15) };
                let mut rng = SmallRng::seed_from_u64(tile_seed(seed ^ cell_mix, k as i32, 7));
                let theme = &themes[rng.gen_range(0..themes.len())];
                let ax = 2 * pick_weighted(&wx, &mut rng) as i32;
                let az = 2 * pick_weighted(&wz, &mut rng) as i32;
                let rx = (16 - ax).rem_euclid(16) + 16 * rng.gen_range(1..8);
                let rz = (16 - az).rem_euclid(16) + 16 * rng.gen_range(1..8);
                if (rx * CELL).rem_euclid(16) != ax || (rz * CELL).rem_euclid(16) != az {
                    eprintln!("[cells] alignment mismatch at ({rx},{rz}) for ({ax},{az}); room skipped");
                    return None;
                }
                let pool = format!("the_vault:map/rooms/{theme}_common_rooms");
                let cands = leaves.entry(pool.clone()).or_insert_with(|| data.pool_leaves(&pool)).clone();
                if cands.is_empty() {
                    eprintln!("[cells] fallback: pool {pool} has no leaves; room skipped");
                    return None;
                }
                let w: Vec<f64> = cands.iter().map(|x| x.0).collect();
                let pick = &cands[pick_weighted(&w, &mut rng)];
                let cfg = Cfg { bonus: b, cascade: c, moc: 0, level };
                let short = pick.1.rsplit('/').next().unwrap_or(&pick.1).to_string();
                match sim_room(data, lib, &pick.1, &pick.2, (rx, rz), cfg, &mut rng) {
                    Some(r) => {
                        let rec = room_record(&format!("cell|{b}|{c}|{k}|{theme}|{short}"), &r, cfg, &short);
                        let extra = format!(
                            r#""real":{{"theme":"{theme}","ax":{ax},"az":{az},"rx":{rx},"rz":{rz},"clump":{:.3},"maxcomp":{},"strongbox":{},"enigma":{},"slots":{},"#,
                            r.clump, r.maxcomp, r.strongbox, r.enigma, r.slots
                        );
                        Some(rec.replacen(r#""real":{"#, &extra, 1))
                    }
                    None => {
                        eprintln!("[cells] fallback: template {} failed to load; room skipped", pick.1);
                        None
                    }
                }
            },
        )
        .collect();
    let mut f = std::io::BufWriter::new(std::fs::File::create(&out).expect("create out"));
    let mut n = 0;
    for l in lines.iter().flatten() {
        writeln!(f, "{l}").unwrap();
        n += 1;
    }
    eprintln!("[cells] {n}/{} rooms for {} cells in {:.1?} -> {out}", work.len(), cells.len(), started.elapsed());
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    if let Some(i) = a.iter().position(|x| x == "--chest") {
        let t = a.get(i + 1).map(String::as_str).unwrap_or("living");
        let t = if t.ends_with("_chest") { t.to_string() } else { format!("{t}_chest") };
        if !["living_chest", "gilded_chest", "ornate_chest"].contains(&t.as_str()) {
            eprintln!("[chest] fallback: unknown chest type {t}; using living_chest");
        } else {
            TARGET_CELL.set(t).ok();
        }
    }
    match a.get(1).map(String::as_str) {
        Some("grid") => run_grid(&a),
        Some("matched") => run_matched(&a),
        Some("cells") => run_cells(&a),
        _ => eprintln!("usage: wv_vault_grid grid|matched|cells ... (see the file header)"),
    }
}
