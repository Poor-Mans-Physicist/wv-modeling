// Diagnostic: census every distinct block / placeholder the simulator can paste, and report how
// `sturdy::is_sturdy_top` classifies it. Goal: find blocks the sim treats as a valid (solid +
// sturdy-top) chest FLOOR that the real game would NOT - i.e. 2-tall plants (cactus, bamboo...),
// non-full decor, and especially `the_vault:placeholder[type=X]` markers of a type the sim doesn't
// resolve to air/chest and therefore leaves as a phantom solid sturdy cube. The user reports
// single floating blocks with chests on them mid-room that don't exist in-game; if a decorator
// chest floors on a misclassified block, that's exactly the artifact.
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use wv_chest_sim::structure::Structure;
use wv_chest_sim::sturdy::is_sturdy_top;

fn walk_nbt(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() {
            walk_nbt(&p, out);
        } else if p.extension().map(|x| x == "nbt").unwrap_or(false) {
            out.push(p);
        }
    }
}

// Same handling assemble.rs applies, so the verdict matches what the sim actually does.
fn sim_disposition(name: &str, props: &BTreeMap<String, String>) -> &'static str {
    if name == "minecraft:air" {
        return "air";
    }
    if name == "minecraft:jigsaw" {
        return "jigsaw->final_state(per-block)";
    }
    if name == "the_vault:placeholder" {
        let t = props.get("type").map(String::as_str).unwrap_or("?");
        if t == "gate" || t == "pylon" {
            return "placeholder->air";
        }
        if ["wooden_chest", "gilded_chest", "living_chest", "ornate_chest"]
            .iter()
            .any(|c| t.starts_with(c))
        {
            return "placeholder->chest";
        }
        return "placeholder->SOLID(unhandled)";
    }
    "solid(block)"
}

fn main() {
    let gen_root = &*wv_chest_sim::paths::gen_root();
    let roots = [
        format!("{gen_root}\\structures\\vault\\rooms\\common"),
        format!("{gen_root}\\structures\\vault\\decor"),
    ];
    let mut files = Vec::new();
    for r in &roots {
        walk_nbt(Path::new(r), &mut files);
    }
    files.sort();
    files.dedup();
    eprintln!("scanning {} structure files", files.len());

    // (name, type-prop-for-placeholder) -> occurrence count across all files' BLOCK entries
    let mut block_counts: BTreeMap<String, u64> = BTreeMap::new();
    // placeholder type -> count
    let mut placeholder_counts: BTreeMap<String, u64> = BTreeMap::new();
    // name (collapsed over props) -> (sturdy_any, sturdy_all, count)
    let mut sturdy_by_name: BTreeMap<String, (bool, bool, u64)> = BTreeMap::new();

    for path in &files {
        let Ok(st) = Structure::load(path) else { continue };
        // Per-palette-index occurrence counts from the block list.
        let mut idx_count = vec![0u64; st.palette.len()];
        for b in &st.blocks {
            if b.state < idx_count.len() {
                idx_count[b.state] += 1;
            }
        }
        for (i, spec) in st.palette.iter().enumerate() {
            let n = idx_count[i];
            if n == 0 {
                continue;
            }
            let props: BTreeMap<String, String> =
                spec.properties.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
            let std_props: std::collections::HashMap<String, String> =
                spec.properties.clone();
            let sturdy = is_sturdy_top(&spec.name, &std_props);

            let disp = sim_disposition(&spec.name, &props);
            let key = format!("{:32} | {}", disp, spec.name);
            *block_counts.entry(key).or_default() += n;

            if spec.name == "the_vault:placeholder" {
                let t = spec.properties.get("type").cloned().unwrap_or_else(|| "?".into());
                *placeholder_counts.entry(t).or_default() += n;
            }

            let e = sturdy_by_name.entry(spec.name.clone()).or_insert((false, true, 0));
            e.0 |= sturdy;
            e.1 &= sturdy;
            e.2 += n;
        }
    }

    println!("\n=== ALL `the_vault:placeholder[type=X]` types found (X -> total block count) ===");
    println!("(types other than gate/pylon/<chest> are recorded as SOLID STURDY cubes by the sim)");
    for (t, c) in &placeholder_counts {
        let handled = t == "gate"
            || t == "pylon"
            || ["wooden_chest", "gilded_chest", "living_chest", "ornate_chest"]
                .iter()
                .any(|ch| t.starts_with(ch));
        println!("{:>9}  type={:30} {}", c, t, if handled { "(handled)" } else { "<-- UNHANDLED = solid sturdy" });
    }

    println!("\n=== Distinct block NAMES the sim classifies STURDY (a valid chest floor) ===");
    println!("(scan for anything that is NOT a real full cube: plants, 2-tall decor, thin decor, modded)");
    for (name, (sturdy_any, _sturdy_all, count)) in &sturdy_by_name {
        if *sturdy_any && name != "minecraft:air" {
            let modded = !name.starts_with("minecraft:");
            println!("{:>9}  {:50} {}", count, name, if modded { "[MODDED]" } else { "" });
        }
    }

    println!("\n=== Disposition summary (how each pasted block is recorded) ===");
    for (k, c) in &block_counts {
        println!("{:>9}  {}", c, k);
    }
}
