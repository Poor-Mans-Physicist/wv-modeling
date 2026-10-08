//! Theme palettes: the tile-processor chains the_vault 3.21.6 runs over every template tile.
//!
//! Source (decompiled 3.21.6): `JigsawTemplate.of` configurators add, in order, the jigsaw and
//! structure-void processors and then every processor of each palette listed on the pool entry.
//! Child pieces stream their tiles through a COPY of the parent's settings plus their own
//! configurator, so a decor piece's tiles see the room's theme palette first and the piece's own
//! pool-entry palettes after it. Processors (`ProcessorAdapter`):
//! - `weighted_target`: if the tile matches `target`, `fillInto` a weighted-random output.
//! - `bernoulli_weighted_target`: if it matches, success pool with probability p, else failure pool.
//! - `leveled`: the entry with the highest level <= vault level.
//! - `placeholder` (`VaultLootTileProcessor`): matches `the_vault:placeholder[type=T]`, then the
//!   level entry as a bernoulli (map palettes: 94 chest : 5 strongbox : 1 enigma at every level).
//! - `reference`: inlines the referenced palette's processors.
//! - `spawner`, `spawner_element`, `template_stack_tile`, `template_stack_spawner` only touch
//!   spawners / lootable ores, which stay solid either way; they are skipped (logged once).
//!
//! Rather than rolling every processor per block, a chain is enumerated once per input block state
//! into a small distribution over final cell classes (weights are exact; the game's per-position
//! RNG is not reproduced), cached, and sampled per block.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use rand::Rng;
use serde_json::Value;

use crate::assets::{res_to_rel_path, AssetSource};
use crate::sturdy;

/// A block state as the processors see it: block id plus the properties that were specified.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct State {
    pub name: String,
    pub props: Vec<(String, String)>,
}

impl State {
    pub fn parse(s: &str) -> State {
        let s = s.split('{').next().unwrap_or(s);
        let (name, props) = sturdy::parse_blockstate(s);
        let name = normalize(&name);
        let mut props: Vec<(String, String)> = props.into_iter().collect();
        props.sort();
        State { name, props }
    }

    fn prop(&self, k: &str) -> Option<&str> {
        self.props.iter().find(|(pk, _)| pk == k).map(|(_, v)| v.as_str())
    }

    fn fill_from(&self, out: &State) -> State {
        let mut next = self.clone();
        if !out.name.is_empty() {
            next.name = out.name.clone();
        }
        for (k, v) in &out.props {
            match next.props.iter_mut().find(|(pk, _)| pk == k) {
                Some(slot) => slot.1 = v.clone(),
                None => next.props.push((k.clone(), v.clone())),
            }
        }
        next.props.sort();
        next
    }

    fn key(&self) -> String {
        if self.props.is_empty() {
            return self.name.clone();
        }
        let inner: Vec<String> = self.props.iter().map(|(k, v)| format!("{k}={v}")).collect();
        format!("{}[{}]", self.name, inner.join(","))
    }
}

fn normalize(name: &str) -> String {
    if name.is_empty() || name.contains(':') {
        name.to_string()
    } else {
        format!("minecraft:{name}")
    }
}

#[derive(Clone, Debug)]
enum Pred {
    Block(State),
    Any(Vec<Pred>),
    Never,
}

impl Pred {
    fn test(&self, s: &State) -> bool {
        match self {
            Pred::Never => false,
            Pred::Any(list) => list.iter().any(|p| p.test(s)),
            Pred::Block(p) => p.name == s.name && p.props.iter().all(|(k, v)| s.prop(k) == Some(v.as_str())),
        }
    }
}

#[derive(Clone, Debug)]
enum Rule {
    Weighted { pred: Pred, outs: Vec<(f64, State)> },
    Bernoulli { pred: Pred, p: f64, success: Vec<(f64, State)>, failure: Vec<(f64, State)> },
    Placeholder { ty: String, p: f64, success: Vec<(f64, State)>, failure: Vec<(f64, State)> },
}

/// What a template cell becomes in the live room, as far as chest placement and movement care.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Cell {
    /// isAir(): a valid bonus/cascade target.
    Air,
    /// Any other block. `passable` = no collision (plants, light, torches, ...): not a chest target,
    /// not a floor, but walkable. Liquids are passable and cascade targets.
    Block { liquid: bool, non_sturdy: bool, passable: bool },
    /// A resolved chest of `ty`; `strongbox` = the placeholder rolled the strongbox upgrade.
    Chest { ty: &'static str, strongbox: bool },
    /// Enigma chest or any other chest-like block that is neither the target nor a cascade source.
    OtherChest,
    Gate,
}

pub type Dist = Vec<(f64, Cell)>;

pub fn sample(dist: &Dist, rng: &mut impl Rng) -> Cell {
    if dist.len() == 1 {
        return dist[0].1;
    }
    let total: f64 = dist.iter().map(|d| d.0).sum();
    let mut r = rng.r#gen::<f64>() * total;
    for (w, c) in dist {
        if r < *w {
            return *c;
        }
        r -= w;
    }
    dist[dist.len() - 1].1
}

const CHEST_TYPES: [&str; 4] = ["wooden_chest", "gilded_chest", "living_chest", "ornate_chest"];

fn static_chest(ty: &str) -> Option<&'static str> {
    CHEST_TYPES.iter().find(|c| ty.starts_with(**c)).copied()
}

/// Heuristic list of no-collision blocks (Material without blocksMotion). Only used for the
/// movement grid and for `light`, which is not isAir() (Blocks.LIGHT, mcdec Blocks:650).
pub fn is_passable(name: &str) -> bool {
    let n = name.split(':').nth(1).unwrap_or(name);
    if let Some(verified) = verified_passable(name) {
        return verified;
    }
    if n.ends_with("_block") || n.ends_with("_leaves") || n.ends_with("_log") || n.ends_with("_planks") || n.ends_with("_stem")
        || n.ends_with("_wood") || n.ends_with("_wall") || n.ends_with("_slab") || n.ends_with("_stairs")
    {
        return false;
    }
    if matches!(n, "light" | "snow" | "cobweb" | "tripwire" | "lever" | "ladder" | "scaffolding" | "dead_bush" | "sugar_cane") {
        return true;
    }
    const PASS: &[&str] = &[
        "grass", "fern", "flower", "tulip", "orchid", "dandelion", "poppy", "allium", "bluet", "daisy", "cornflower",
        "lily_of_the_valley", "wither_rose", "lilac", "rose_bush", "peony", "sunflower", "sapling", "vine", "lichen",
        "blossom", "roots", "sprouts", "fungus", "mushroom", "kelp", "seagrass", "torch", "button", "pressure_plate",
        "rail", "sign", "banner", "carpet", "berry_bush", "azalea", "petals",
    ];
    PASS.iter().any(|p| n.contains(p))
}

/// `!Material.blocksMotion()` (what Routerunner's room grid records) for blocks checked against their
/// decompiled registration, or `None` to fall through to the name heuristics. Open: vanilla FIRE,
/// DECORATION, PLANT and WATER_PLANT blocks the heuristics miss (fire, soul_fire, redstone_wire,
/// tripwire_hook, candles, skulls and heads, flower pots, sea_pickle, crops), Tropicraft flowers (copy
/// of POPPY), Ecologics coconut_seedling / coconut / moss_layer (PLANT), seashell (DECORATION),
/// surface_moss (REPLACEABLE_PLANT), quark:glow_shroom (copy of RED_MUSHROOM), supplementaries:ash
/// (TOP_SNOW) and gunpowder (copy of REDSTONE_WIRE), Macaw's parapets and Decorative Blocks chandeliers
/// (DECORATION). Blocking: vanilla signs, banners and pressure plates (WOOD / NETHER_WOOD / STONE / METAL
/// despite having no collision), architects_palette:entrails (VEGETABLE), Architect's Palette railings
/// (copies of planks, also Every Compat's `ap/` ones) and bookshelves.
fn verified_passable(name: &str) -> Option<bool> {
    let (ns, id) = name.split_once(':').unwrap_or(("minecraft", name));
    match ns {
        "minecraft" => {
            if matches!(id, "fire" | "soul_fire" | "redstone_wire" | "tripwire_hook" | "sea_pickle" | "flower_pot" | "wheat" | "carrots" | "potatoes" | "beetroots")
                || id.starts_with("potted_")
                || id.ends_with("_skull")
                || (id.ends_with("_head") && id != "piston_head")
                || (id.ends_with("candle") && !id.ends_with("candle_cake"))
            {
                return Some(true);
            }
            if id.ends_with("_sign") || id.ends_with("_banner") || id.ends_with("_pressure_plate") {
                return Some(false);
            }
        }
        "tropicraft" if crate::sturdy::is_tropicraft_flower(id) => return Some(true),
        "ecologics" if matches!(id, "coconut_seedling" | "coconut" | "moss_layer" | "seashell" | "surface_moss") => return Some(true),
        "quark" if id == "glow_shroom" => return Some(true),
        "supplementaries" if id == "ash" || id == "gunpowder" => return Some(true),
        "mcwwindows" if id.ends_with("_parapet") => return Some(true),
        "decorative_blocks" if id == "chandelier" || id == "soul_chandelier" => return Some(true),
        "architects_palette" if id == "entrails" || id.ends_with("_railing") => return Some(false),
        "everycomp" if id.starts_with("ap/") && id.ends_with("_railing") => return Some(false),
        _ => {}
    }
    if id.ends_with("_bookshelf") {
        return Some(false);
    }
    None
}

fn is_liquid_name(name: &str) -> bool {
    let n = name.split(':').nth(1).unwrap_or(name);
    n == "water" || n == "lava" || n.ends_with("_water") || n == "honey" || n == "chocolate" || n.ends_with("_liquid")
        || n.ends_with("_flow") || n.ends_with("_fluid")
}

/// Final classification of a processed state (see `Cell`).
pub fn classify(s: &State) -> Cell {
    let n = s.name.as_str();
    if n.is_empty() || n == "minecraft:air" || n == "minecraft:cave_air" || n == "minecraft:void_air" {
        return Cell::Air;
    }
    if n == "the_vault:placeholder" {
        let t = s.prop("type").unwrap_or("");
        if let Some(ty) = static_chest(t) {
            return Cell::Chest { ty, strongbox: false };
        }
        return match t {
            "gate" | "pylon" => Cell::Air,
            "ore" | "treasure_door" | "dungeon_door" => Cell::Block { liquid: false, non_sturdy: false, passable: false },
            _ => Cell::Block { liquid: false, non_sturdy: true, passable: false },
        };
    }
    if let Some(id) = n.strip_prefix("the_vault:") {
        if let Some(base) = id.strip_suffix("_strongbox") {
            if let Some(ty) = static_chest(&format!("{base}_chest")) {
                return Cell::Chest { ty, strongbox: true };
            }
            return Cell::OtherChest;
        }
        if let Some(ty) = static_chest(id) {
            if !id.contains("placeable") {
                return Cell::Chest { ty, strongbox: false };
            }
        }
        if id.ends_with("_chest") {
            return Cell::OtherChest;
        }
    }
    if is_liquid_name(n) {
        return Cell::Block { liquid: true, non_sturdy: true, passable: true };
    }
    let props: HashMap<String, String> = s.props.iter().cloned().collect();
    let non_sturdy = !sturdy::is_sturdy_top(n, &props);
    Cell::Block { liquid: false, non_sturdy, passable: is_passable(n) }
}

/// Loads, compiles and caches palettes and processor chains. One per worker thread.
pub struct PaletteLibrary<'a> {
    assets: &'a dyn AssetSource,
    level: u32,
    compiled: RefCell<HashMap<String, Rc<Vec<Rule>>>>,
    chains: RefCell<HashMap<Vec<String>, usize>>,
    chain_rules: RefCell<Vec<Rc<Vec<Rule>>>>,
    dists: RefCell<HashMap<(usize, String), Rc<Dist>>>,
    skipped: RefCell<HashSet<String>>,
}

/// A compiled processor chain (a list of palettes applied in order).
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ChainId(pub usize, pub Vec<String>);

impl<'a> PaletteLibrary<'a> {
    pub fn new(assets: &'a dyn AssetSource, vault_level: u32) -> Self {
        PaletteLibrary {
            assets,
            level: vault_level,
            compiled: RefCell::new(HashMap::new()),
            chains: RefCell::new(HashMap::new()),
            chain_rules: RefCell::new(Vec::new()),
            dists: RefCell::new(HashMap::new()),
            skipped: RefCell::new(HashSet::new()),
        }
    }

    fn warn_once(&self, what: String) {
        if self.skipped.borrow_mut().insert(what.clone()) {
            static SEEN: std::sync::Mutex<Option<HashSet<String>>> = std::sync::Mutex::new(None);
            let mut g = SEEN.lock().unwrap_or_else(|e| e.into_inner());
            if g.get_or_insert_with(HashSet::new).insert(what.clone()) {
                eprintln!("[palette] fallback: {what}");
            }
        }
    }

    fn palette(&self, id: &str, depth: u32) -> Rc<Vec<Rule>> {
        if let Some(p) = self.compiled.borrow().get(id) {
            return p.clone();
        }
        let mut rules = Vec::new();
        if depth > 16 {
            self.warn_once(format!("palette reference depth limit hit at {id}, rest of chain ignored"));
        } else {
            match self.assets.read(&res_to_rel_path(id, "palettes", "json")) {
                Some(bytes) => match serde_json::from_slice::<Value>(&bytes) {
                    Ok(v) => {
                        if let Some(list) = v.get("tile_processors").and_then(Value::as_array) {
                            for p in list {
                                self.compile(p, &mut rules, depth);
                            }
                        }
                    }
                    Err(e) => self.warn_once(format!("palette {id} failed to parse ({e}); treated as empty")),
                },
                None => self.warn_once(format!("palette {id} not found; treated as empty")),
            }
        }
        let rc = Rc::new(rules);
        self.compiled.borrow_mut().insert(id.to_string(), rc.clone());
        rc
    }

    fn pred(&self, v: Option<&Value>) -> Pred {
        match v {
            Some(Value::String(s)) if s.starts_with('@') || s.starts_with('#') => {
                self.warn_once(format!("group/tag predicate {s} not evaluated; never matches"));
                Pred::Never
            }
            Some(Value::String(s)) => Pred::Block(State::parse(s)),
            Some(Value::Object(o)) => self.pred(o.get("block")),
            Some(Value::Array(a)) => Pred::Any(a.iter().map(|v| self.pred(Some(v))).collect()),
            other => {
                self.warn_once(format!("unsupported predicate {other:?}; never matches"));
                Pred::Never
            }
        }
    }

    fn outputs(v: Option<&Value>) -> Vec<(f64, State)> {
        match v {
            Some(Value::Object(o)) => o.iter().map(|(k, w)| (w.as_f64().unwrap_or(0.0), State::parse(k))).collect(),
            Some(Value::Array(a)) => a
                .iter()
                .map(|e| {
                    let b = e.get("block").and_then(Value::as_str).unwrap_or("");
                    (e.get("weight").and_then(Value::as_f64).unwrap_or(0.0), State::parse(b))
                })
                .collect(),
            _ => Vec::new(),
        }
    }

    fn pick_level<'v>(&self, levels: &'v [Value]) -> Option<&'v Value> {
        let mut best: Option<(i64, &Value)> = None;
        for e in levels {
            let l = e.get("level").and_then(Value::as_i64).unwrap_or(0);
            if l <= self.level as i64 && best.map(|(bl, _)| l >= bl).unwrap_or(true) {
                best = Some((l, e));
            }
        }
        best.map(|(_, e)| e)
    }

    fn compile(&self, p: &Value, out: &mut Vec<Rule>, depth: u32) {
        let ty = p.get("type").and_then(Value::as_str).unwrap_or("");
        match ty {
            "reference" => {
                if let Some(id) = p.get("id").and_then(Value::as_str) {
                    out.extend(self.palette(id, depth + 1).iter().cloned());
                } else {
                    self.warn_once("reference with a weighted pool; skipped".to_string());
                }
            }
            "weighted_target" => out.push(Rule::Weighted { pred: self.pred(p.get("target")), outs: Self::outputs(p.get("output")) }),
            "bernoulli_weighted_target" => out.push(Rule::Bernoulli {
                pred: match p.get("target") {
                    Some(t) => self.pred(Some(t)),
                    None => Pred::Never,
                },
                p: p.get("probability").and_then(Value::as_f64).unwrap_or(0.0),
                success: Self::outputs(p.get("success")),
                failure: Self::outputs(p.get("failure")),
            }),
            "leveled" => {
                if let Some(e) = p.get("levels").and_then(Value::as_array).and_then(|l| self.pick_level(l)) {
                    self.compile(e, out, depth);
                }
            }
            "placeholder" => {
                let target = p.get("target").and_then(Value::as_str).unwrap_or("").to_ascii_lowercase();
                if let Some(e) = p.get("levels").and_then(Value::as_array).and_then(|l| self.pick_level(l)) {
                    out.push(Rule::Placeholder {
                        ty: target,
                        p: e.get("probability").and_then(Value::as_f64).unwrap_or(0.0),
                        success: Self::outputs(e.get("success")),
                        failure: Self::outputs(e.get("failure")),
                    });
                }
            }
            other => self.warn_once(format!("processor type '{other}' skipped (does not change placement geometry)")),
        }
    }

    /// Chain for an ordered list of palette ids (the theme palette first, then decor palettes).
    pub fn chain(&self, palettes: &[String]) -> ChainId {
        if let Some(id) = self.chains.borrow().get(palettes) {
            return ChainId(*id, palettes.to_vec());
        }
        let mut rules = Vec::new();
        for p in palettes {
            rules.extend(self.palette(p, 0).iter().cloned());
        }
        let id = self.chain_rules.borrow().len();
        self.chain_rules.borrow_mut().push(Rc::new(rules));
        self.chains.borrow_mut().insert(palettes.to_vec(), id);
        ChainId(id, palettes.to_vec())
    }

    /// `parent` chain extended by a child piece's own pool-entry palettes.
    /// Chain for a jigsaw child: its own pool-entry palettes run FIRST, then the parent's chain. The game's
    /// child configurator (`JigsawTemplate.computeChildren`) prepends the entry's palette processors with
    /// `addProcessorAtBeginning` onto a copy of the parent's settings, so e.g. a decor piece's rarity
    /// highlighter thins its chest placeholders before the theme palette can resolve them into chests.
    pub fn extend(&self, parent: &ChainId, child: &[String]) -> ChainId {
        if child.is_empty() {
            return parent.clone();
        }
        let mut all: Vec<String> = child.to_vec();
        all.extend(parent.1.iter().cloned());
        self.chain(&all)
    }

    /// Exact distribution over final cells for `input` run through `chain`.
    pub fn resolve(&self, chain: &ChainId, input: &str) -> Rc<Dist> {
        let key = (chain.0, input.to_string());
        if let Some(d) = self.dists.borrow().get(&key) {
            return d.clone();
        }
        let states = self.final_states(chain, input);
        let mut dist: Dist = Vec::new();
        for (w, s) in states {
            let c = classify(&s);
            match dist.iter_mut().find(|(_, d)| *d == c) {
                Some(d) => d.0 += w,
                None => dist.push((w, c)),
            }
        }
        let rc = Rc::new(dist);
        self.dists.borrow_mut().insert(key, rc.clone());
        rc
    }

    /// Final block states (weight, state key, class) for `input` run through `chain`; the
    /// uncollapsed form of `resolve`, for tracing which block a cell becomes.
    pub fn resolve_named(&self, chain: &ChainId, input: &str) -> Vec<(f64, String, Cell)> {
        self.final_states(chain, input).into_iter().map(|(w, s)| (w, s.key(), classify(&s))).collect()
    }

    fn final_states(&self, chain: &ChainId, input: &str) -> Vec<(f64, State)> {
        let rules = self.chain_rules.borrow()[chain.0].clone();
        let mut states: Vec<(f64, State)> = vec![(1.0, State::parse(input))];
        for rule in rules.iter() {
            let mut next: Vec<(f64, State)> = Vec::with_capacity(states.len());
            for (w, s) in states {
                let split = |pool: &[(f64, State)], mass: f64, next: &mut Vec<(f64, State)>| {
                    let total: f64 = pool.iter().map(|o| o.0).sum();
                    if total <= 0.0 {
                        next.push((mass, s.clone()));
                        return;
                    }
                    for (ow, o) in pool {
                        if *ow > 0.0 {
                            next.push((mass * ow / total, s.fill_from(o)));
                        }
                    }
                };
                match rule {
                    Rule::Weighted { pred, outs } if pred.test(&s) => split(outs, w, &mut next),
                    Rule::Bernoulli { pred, p, success, failure } if pred.test(&s) => {
                        split(success, w * p.clamp(0.0, 1.0), &mut next);
                        if *p < 1.0 {
                            split(failure, w * (1.0 - p.clamp(0.0, 1.0)), &mut next);
                        }
                    }
                    Rule::Placeholder { ty, p, success, failure }
                        if s.name == "the_vault:placeholder" && s.prop("type") == Some(ty.as_str()) =>
                    {
                        split(success, w * p.clamp(0.0, 1.0), &mut next);
                        if *p < 1.0 {
                            split(failure, w * (1.0 - p.clamp(0.0, 1.0)), &mut next);
                        }
                    }
                    _ => next.push((w, s)),
                }
            }
            let mut merged: Vec<(f64, State)> = Vec::new();
            for (w, s) in next {
                match merged.iter_mut().find(|(_, m)| *m == s) {
                    Some(m) => m.0 += w,
                    None => merged.push((w, s)),
                }
            }
            states = merged;
        }
        states
    }

    /// Distribution of what a Bonus X output placeholder (`the_vault:placeholder[type=<ty>]`)
    /// becomes under a room's root chain: chest, strongbox, other chest (enigma) or air.
    pub fn bonus_dist(&self, chain: &ChainId, chest_type: &str) -> Rc<Dist> {
        self.resolve(chain, &format!("the_vault:placeholder[type={chest_type}]"))
    }

    pub fn state_key(name: &str, props: &HashMap<String, String>) -> String {
        let mut p: Vec<(String, String)> = props.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        p.sort();
        State { name: normalize(name), props: p }.key()
    }
}
