// Measure, scoped to the COMMON rooms the web app actually simulates, how many decorator
// floor-candidate positions the sturdy-classifier fix removes - i.e. how many spots a chest could
// previously (wrongly) be floored on top of a non-full block (cactus / campfire / skull / coin pile
// / bed / door / ...). A "floor candidate" here is an air cell whose block directly below is a
// solid, non-liquid, non-chest floor - exactly what decorator_add/cascade require. We classify each
// candidate's floor block under the OLD rule and the NEW rule and report the delta + a breakdown by
// block, so the count impact is isolated to THIS change (not conflated with anything else).
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::PathBuf;
use std::rc::Rc;

use rand::rngs::SmallRng;
use rand::SeedableRng;

use wv_chest_sim::assemble;
use wv_chest_sim::assets::FsAssetSource;
use wv_chest_sim::data::DataSource;
use wv_chest_sim::structure::Structure;
use wv_chest_sim::sturdy::is_sturdy_top; // NEW rule

// Verbatim copy of is_sturdy_top as it was BEFORE this fix (no light/azalea/bed/leaves guards, old
// substring list with "iron_bars"/"coral_fan" and none of the 2-tall/thin additions). Props are
// irrelevant for every block whose classification actually changed, so we pass an empty map.
fn old_is_sturdy_top(name: &str, props: &HashMap<String, String>) -> bool {
    let n = name.strip_prefix("minecraft:").unwrap_or(name);
    if n == "air" || n == "cave_air" || n == "void_air" || n == "water" || n == "lava" {
        return false;
    }
    if n.ends_with("_slab") || n == "slab" {
        return matches!(props.get("type").map(String::as_str), Some("top") | Some("double"));
    }
    if n.ends_with("_stairs") {
        return props.get("half").map(|h| h == "top").unwrap_or(false);
    }
    if n.ends_with("_trapdoor") {
        let closed = props.get("open").map(|o| o == "false").unwrap_or(true);
        let top = props.get("half").map(|h| h == "top").unwrap_or(false);
        return closed && top;
    }
    if n.ends_with("_block") {
        return true;
    }
    const NON_STURDY: &[&str] = &[
        "fence", "wall", "pane", "iron_bars", "chain", "ladder", "scaffolding",
        "carpet", "snow", "torch", "lantern", "rail", "lever", "button", "tripwire",
        "pressure_plate", "sign", "banner", "candle", "cobweb", "pointed_dripstone",
        "amethyst_cluster", "_bud", "dripleaf", "lily_pad", "sea_pickle", "coral_fan",
        "sapling", "sprouts", "roots", "fungus", "bush", "vine", "lichen", "blossom",
        "grass", "fern", "flower", "tulip", "orchid", "dandelion", "poppy", "allium",
        "azure_bluet", "oxeye_daisy", "cornflower", "lily_of_the_valley", "wither_rose",
        "lilac", "rose_bush", "peony", "sunflower", "petals", "sugar_cane", "bamboo",
        "kelp", "seagrass", "mushroom", "wart", "hanging_roots", "nether_sprouts",
    ];
    for pat in NON_STURDY {
        if n.contains(pat) {
            return false;
        }
    }
    true
}

// Reconstruct the block name from the assembler's name-trace label.
// Labels: "placeholder:TYPE", "jigsaw->FINAL_STATE", or a raw block name.
fn label_to_name(label: &str) -> String {
    if let Some(t) = label.strip_prefix("placeholder:") {
        // The old code recorded any non-gate/pylon placeholder as a solid STURDY cube; the new code
        // keeps only ore/doors sturdy. Represent each with a synthetic name the two rules disagree
        // on appropriately - easiest is to just return a sentinel handled by the caller.
        return format!("@placeholder:{t}");
    }
    if let Some(fs) = label.strip_prefix("jigsaw->") {
        // final_state is a blockstate string; strip any [props].
        return fs.split('[').next().unwrap_or(fs).to_string();
    }
    label.to_string()
}

fn old_sturdy_for(name: &str) -> bool {
    if let Some(t) = name.strip_prefix("@placeholder:") {
        // Old behaviour: only gate/pylon were air (never solid here); everything else solid+sturdy.
        return !matches!(t, "gate" | "pylon");
    }
    old_is_sturdy_top(name, &HashMap::new())
}

fn new_sturdy_for(name: &str) -> bool {
    if let Some(t) = name.strip_prefix("@placeholder:") {
        return matches!(t, "ore" | "treasure_door" | "dungeon_door");
    }
    is_sturdy_top(name, &HashMap::new())
}

fn main() {
    let gen_root = wv_chest_sim::paths::gen_root();
    let rooms_dir = format!("{gen_root}\\structures\\vault\\rooms\\common");
    let mut files: Vec<PathBuf> = std::fs::read_dir(&rooms_dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "nbt").unwrap_or(false))
        .collect();
    files.sort();

    let trials = 40usize;
    let mut sum_old = 0u64;
    let mut sum_new = 0u64;
    let mut realizations = 0u64;
    // block name -> how many floor candidates it lost (old-sturdy, now non-sturdy)
    let mut removed_by_block: BTreeMap<String, u64> = BTreeMap::new();

    for path in &files {
        let assets = FsAssetSource::new(gen_root.clone());
        let data = DataSource::new(&assets);
        let mut rng = SmallRng::from_entropy();
        let Ok(root) = Structure::load(path) else { continue };
        let root = Rc::new(root);
        for _ in 0..trials {
            assemble::enable_name_trace();
            let work = assemble::assemble(&root, &data, &mut rng, 10);
            let names = assemble::take_name_trace();
            let chests: HashSet<(i32, i32, i32)> = work.chests.iter().map(|c| c.pos).collect();
            let grid = &work.solid;
            let (sx, sy, sz) = grid.size();
            realizations += 1;
            for x in 0..sx {
                for y in 1..sy {
                    for z in 0..sz {
                        let p = (x, y, z);
                        if grid.is_solid(p) {
                            continue; // candidate cell must be air (the chest's own cell)
                        }
                        let b = (x, y - 1, z);
                        if !grid.is_solid(b) || work.liquid.contains(&b) || chests.contains(&b) {
                            continue; // floor must be a solid, non-liquid, non-chest block
                        }
                        let Some(label) = names.get(&b) else { continue };
                        let nm = label_to_name(label);
                        let o = old_sturdy_for(&nm);
                        let n = new_sturdy_for(&nm);
                        if o {
                            sum_old += 1;
                        }
                        if n {
                            sum_new += 1;
                        }
                        if o && !n {
                            *removed_by_block.entry(nm).or_default() += 1;
                        }
                    }
                }
            }
        }
    }

    let old_per = sum_old as f64 / realizations as f64;
    let new_per = sum_new as f64 / realizations as f64;
    println!("Common rooms: {}  realizations: {}", files.len(), realizations);
    println!("Avg decorator floor-candidate capacity / room:");
    println!("   OLD rule: {old_per:8.1}");
    println!("   NEW rule: {new_per:8.1}");
    println!("   removed : {:8.1}  ({:.1}% of old capacity)", old_per - new_per, 100.0 * (old_per - new_per) / old_per);
    println!("\nRemoved floor candidates by floor-block type (avg / room), the artifact sources:");
    let mut v: Vec<_> = removed_by_block.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1));
    for (nm, c) in v.into_iter().take(40) {
        println!("   {:8.2}  {}", c as f64 / realizations as f64, nm);
    }
}
