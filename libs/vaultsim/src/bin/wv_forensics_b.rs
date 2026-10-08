//! Cell-level forensics: assembles rooms exactly like `wv_vault_grid matched` (same RNG stream,
//! palettes and forced rotation/mirror) while recording the final block state of every cell.
//!
//! `wv_forensics_b trace --jobs jobs.tsv --k 4 --out-dir DIR [--level 475] [--jobs-limit N]`
//!   Writes `DIR/names.tsv` (id, final state key, class) and, per job, `DIR/job_<i>.bin.gz`: K
//!   consecutive little-endian u16 arrays of 47*47*47 name ids indexed ((rx*47 + y)*47 + rz) in the
//!   region-relative world frame (rx = world x - region x*47, y = world y - 9). `DIR/index.jsonl`
//!   holds per (job, rep) the template chests, the bonus/cascade chests (same frame) and N/clumpiness
//!   for checking parity with `matched`.
use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Write;
use std::sync::Mutex;

use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;

use wv_chest_sim::assemble;
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::palette::{Cell, PaletteLibrary};
use wv_chest_sim::schedule::{self, Bonus, Cascade};
use wv_chest_sim::transform::Rotation;

type P = (i32, i32, i32);
const CELL: i32 = 47;

const TARGET: &str = "living_chest";

struct Names {
    ids: HashMap<String, u16>,
    list: Vec<(String, String)>,
}

impl Names {
    fn id(&mut self, key: &str, class: &str) -> u16 {
        if let Some(i) = self.ids.get(key) {
            return *i;
        }
        let i = self.list.len() as u16;
        self.list.push((key.to_string(), class.to_string()));
        self.ids.insert(key.to_string(), i);
        i
    }
}

fn flag<'a>(a: &'a [String], name: &str) -> Option<&'a str> {
    a.iter().position(|x| x == name).and_then(|i| a.get(i + 1)).map(String::as_str)
}

fn tile_seed(seed: u64, rx: i32, rz: i32) -> u64 {
    let mut h = seed ^ 0x9E37_79B9_7F4A_7C15;
    for v in [rx as i64 as u64, rz as i64 as u64] {
        h = (h ^ v).wrapping_mul(0x100_0000_01B3).rotate_left(29) ^ 0xD6E8_FEB8_6659_FD93;
    }
    h
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

fn class_str(c: Cell) -> String {
    match c {
        Cell::Air => "air".into(),
        Cell::Gate => "gate".into(),
        Cell::Block { liquid, non_sturdy, passable } => {
            format!("block{}{}{}", if liquid { "+liquid" } else { "" }, if non_sturdy { "+nonsturdy" } else { "" }, if passable { "+passable" } else { "" })
        }
        Cell::Chest { ty, strongbox } => format!("chest:{ty}{}", if strongbox { ":strongbox" } else { "" }),
        Cell::OtherChest => "otherchest".into(),
    }
}

/// Picks the final state key for a traced cell: the chain's named outcomes restricted to the sampled
/// class, weighted as the processors weight them.
fn pick_name(named: &[(f64, String, Cell)], cell: Cell, rng: &mut SmallRng) -> String {
    let pool: Vec<&(f64, String, Cell)> = named.iter().filter(|e| e.2 == cell).collect();
    if pool.is_empty() {
        eprintln!("[forensics] fallback: no named outcome for class {cell:?}; recorded as <unknown>");
        return "<unknown>".into();
    }
    let total: f64 = pool.iter().map(|e| e.0).sum();
    let mut r = rng.r#gen::<f64>() * total;
    for e in &pool {
        if r < e.0 {
            return e.1.clone();
        }
        r -= e.0;
    }
    pool[pool.len() - 1].1.clone()
}

fn run_trace(a: &[String]) {
    let jobs_path = flag(a, "--jobs").expect("--jobs required");
    let k: u64 = flag(a, "--k").and_then(|s| s.parse().ok()).unwrap_or(4);
    let level: u32 = flag(a, "--level").and_then(|s| s.parse().ok()).unwrap_or(475);
    let dir = flag(a, "--out-dir").expect("--out-dir required").to_string();
    let limit: usize = flag(a, "--jobs-limit").and_then(|s| s.parse().ok()).unwrap_or(usize::MAX);
    std::fs::create_dir_all(&dir).expect("create out dir");
    let text = std::fs::read_to_string(jobs_path).expect("read jobs");
    let jobs: Vec<Vec<String>> =
        text.lines().filter(|l| !l.trim().is_empty()).take(limit).map(|l| l.split('\t').map(str::to_string).collect()).collect();
    let assets: &'static FsAssetSource = Box::leak(Box::new(FsAssetSource::new(&*wv_chest_sim::paths::gen_root())));
    let names = Mutex::new(Names { ids: HashMap::from([("<unset>".to_string(), 0u16)]), list: vec![("<unset>".into(), "unset".into())] });
    let started = std::time::Instant::now();
    let index: Vec<String> = (0..jobs.len())
        .into_par_iter()
        .map_init(
            || (DataSource::new(assets), PaletteLibrary::new(assets, level), HashMap::<String, Vec<(f64, String, Vec<String>)>>::new()),
            |(data, lib, leaves), ji| {
                let f = &jobs[ji];
                let (tag, template, rx, rz) = (&f[0], &f[1], f[2].parse::<i32>().unwrap(), f[3].parse::<i32>().unwrap());
                let (bonus, cascade, moc): (u32, u32, u32) = (f[4].parse().unwrap(), f[5].parse().unwrap(), f[6].parse().unwrap());
                let theme = &f[7];
                let pool = format!("the_vault:map/rooms/{theme}_common_rooms");
                let cands: Vec<(f64, String, Vec<String>)> = leaves
                    .entry(pool.clone())
                    .or_insert_with(|| data.pool_leaves(&pool))
                    .iter()
                    .filter(|(_, t, _)| t.ends_with(&format!("/{template}")))
                    .cloned()
                    .collect();
                let mut buf: Vec<u8> = Vec::new();
                let mut lines = Vec::new();
                let mut name_cache: HashMap<(usize, String, String), Vec<(f64, String, Cell)>> = HashMap::new();
                for rep in 0..k {
                    let mut rng = SmallRng::seed_from_u64(tile_seed(0xABCD ^ rep, rx, rz) ^ (ji as u64).wrapping_mul(0x9E37_79B9));
                    let mut side = SmallRng::seed_from_u64(0x5EED_F0E5 ^ (ji as u64) << 8 ^ rep);
                    if cands.is_empty() {
                        eprintln!("[forensics] fallback: {template} not in {pool}; job {tag} skipped");
                        lines.push(format!(r#"{{"job":{ji},"tag":"{tag}","rep":{rep},"error":"not in pool"}}"#));
                        buf.extend(std::iter::repeat_n(0u8, (CELL * CELL * CELL * 2) as usize));
                        continue;
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
                    let Some(root) = data.get_structure(&pick.1) else {
                        eprintln!("[forensics] fallback: {} failed to load; job {tag} skipped", pick.1);
                        lines.push(format!(r#"{{"job":{ji},"tag":"{tag}","rep":{rep},"error":"load"}}"#));
                        buf.extend(std::iter::repeat_n(0u8, (CELL * CELL * CELL * 2) as usize));
                        continue;
                    };
                    assemble::enable_themed_trace();
                    let (mut asm, chain) = assemble::assemble_themed(&root, &pick.2, data, lib, &mut rng, 10);
                    let trace = assemble::take_themed_trace();
                    let mut mirrored = rng.gen_bool(0.5);
                    let mut rot = Rotation::random(&mut rng);
                    if let (Some(deg), Some(m)) = (f.get(8), f.get(9)) {
                        rot = match deg.trim() {
                            "0" => Rotation::NONE,
                            "90" => Rotation::CW90,
                            "180" => Rotation::CW180,
                            _ => Rotation::CCW90,
                        };
                        mirrored = m.trim() == "yes" || m.trim() == "1" || m.trim() == "true";
                    }
                    let mut grid: HashMap<P, (usize, &str, Cell)> = HashMap::with_capacity(trace.entries.len());
                    for (pos, cid, input, cell) in &trace.entries {
                        grid.insert(*pos, (*cid, input.as_str(), *cell));
                    }
                    let template_chests: Vec<(P, String)> =
                        asm.chests.iter().map(|c| (c.pos, format!("{}{}", c.chest_type, if c.is_strongbox { "_strongbox" } else { "" }))).collect();
                    if mirrored {
                        assemble::mirror_x(&mut asm);
                    }
                    let dist = lib.bonus_dist(&chain, TARGET);
                    let cascades = [Cascade { stacks: cascade, chance: 0.25 }, Cascade { stacks: 15 * moc, chance: 0.01 }];
                    let placed = schedule::apply_schedule_with(&mut asm, (rx, rz), rot, CELL, TARGET, bonus, 8, &cascades, Bonus::Dist(&dist), &mut rng);
                    let outer = wv_chest_sim::decorator::room_world_transform((rx, rz), rot, CELL);
                    let mir = |p: P| if mirrored { (CELL - 1 - p.0, p.1, p.2) } else { p };
                    let rel = |p: P| {
                        let w = outer.apply(p);
                        (w.0 - rx * CELL, w.1 - 9, w.2 - rz * CELL)
                    };
                    let mut ids = vec![0u16; (CELL * CELL * CELL) as usize];
                    for (pos, (cid, input, cell)) in &grid {
                        let w = rel(mir(*pos));
                        if w.0 < 0 || w.0 >= CELL || w.1 < 0 || w.1 >= CELL || w.2 < 0 || w.2 >= CELL {
                            continue;
                        }
                        let ck = (*cid, input.to_string(), format!("{cell:?}"));
                        let named = name_cache.entry(ck).or_insert_with(|| lib.resolve_named(&trace.chains[cid], input));
                        let key = pick_name(named, *cell, &mut side);
                        let id = names.lock().unwrap().id(&key, &class_str(*cell));
                        ids[((w.0 * CELL + w.1) * CELL + w.2) as usize] = id;
                    }
                    for v in &ids {
                        buf.extend_from_slice(&v.to_le_bytes());
                    }
                    let all: Vec<_> = asm.chests.iter().chain(placed.iter()).cloned().collect();
                    let (sx, _, sz) = asm.solid.size();
                    let inside = |p: P| p.0 >= 0 && p.0 < sx && p.2 >= 0 && p.2 < sz;
                    let targets: Vec<P> = all.iter().filter(|c| c.chest_type == TARGET && !c.is_strongbox && inside(c.pos)).map(|c| c.pos).collect();
                    let sizes = components(&targets);
                    let n = targets.len();
                    let clump = if n == 0 { 0.0 } else { sizes.iter().map(|s| (s * s) as f64).sum::<f64>() / n as f64 };
                    let tc: Vec<String> = template_chests.iter().map(|(p, t)| format!(r#"[{},{},{},"{t}"]"#, rel(mir(*p)).0, rel(mir(*p)).1, rel(mir(*p)).2)).collect();
                    let pc: Vec<String> = placed
                        .iter()
                        .map(|c| {
                            let w = rel(c.pos);
                            format!(r#"[{},{},{},"{}{}"]"#, w.0, w.1, w.2, c.chest_type, if c.is_strongbox { "_strongbox" } else { "" })
                        })
                        .collect();
                    lines.push(format!(
                        r#"{{"job":{ji},"tag":"{tag}","template":"{template}","rep":{rep},"palette":"{}","mirror":{mirrored},"rot":{},"n":{n},"clump":{clump:.2},"template_chests":[{}],"placed":[{}]}}"#,
                        pick.2.last().cloned().unwrap_or_default(),
                        [Rotation::NONE, Rotation::CW90, Rotation::CW180, Rotation::CCW90].iter().position(|x| *x == rot).unwrap_or(0),
                        tc.join(","),
                        pc.join(",")
                    ));
                }
                let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
                enc.write_all(&buf).unwrap();
                std::fs::write(format!("{dir}/job_{ji}.bin.gz"), enc.finish().unwrap()).expect("write job file");
                lines.join("\n")
            },
        )
        .collect();
    std::fs::write(format!("{dir}/index.jsonl"), index.join("\n")).expect("write index");
    let names = names.into_inner().unwrap();
    let mut f = std::io::BufWriter::new(std::fs::File::create(format!("{dir}/names.tsv")).unwrap());
    for (i, (key, class)) in names.list.iter().enumerate() {
        writeln!(f, "{i}\t{key}\t{class}").unwrap();
    }
    eprintln!("[forensics] {} jobs x {k} in {:.1?} -> {dir}", jobs.len(), started.elapsed());
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a.get(1).map(String::as_str) {
        Some("trace") => run_trace(&a),
        _ => eprintln!("usage: wv_forensics_b trace ... (see the file header)"),
    }
}
