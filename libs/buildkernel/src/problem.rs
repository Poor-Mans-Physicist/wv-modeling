//! Flattened context exported by model/kernel.py, resolved into index tables the evaluator reads directly.

use serde::Deserialize;
use std::collections::HashMap;

#[derive(Deserialize, Clone)]
pub struct AffixRec {
    pub k: u8,
    pub a: usize,
    pub v: f64,
    pub g: usize,
    pub key: usize,
    pub lc: f64,
    pub amp: i32,
}

#[derive(Deserialize, Clone)]
pub struct EtchRec {
    pub num: Option<f64>,
    pub truthy: bool,
    pub q: Vec<usize>,
    pub tl: Option<(usize, f64)>,
    pub stacking: bool,
}

#[derive(Deserialize, Clone)]
pub struct LvlKey {
    pub all: bool,
    pub hits: Vec<usize>,
}

#[derive(Deserialize, Clone)]
pub struct TalKey {
    pub all: bool,
    pub talent: i64,
}

#[derive(Deserialize, Clone)]
pub struct GearType {
    pub name: String,
    pub pre: usize,
    pub suf: usize,
    pub prefix: Vec<usize>,
    pub suffix: Vec<usize>,
    pub seal: Vec<usize>,
    pub unusual: Vec<usize>,
    pub implicit_groups: Vec<Vec<usize>>,
    pub etchings: Vec<usize>,
}

#[derive(Deserialize, Clone)]
#[serde(tag = "t")]
pub enum TrinketEff {
    #[serde(rename = "add")]
    Add { a: usize, v: f64 },
    #[serde(rename = "vanilla")]
    Vanilla { a: usize, v: f64, op: i32 },
    #[serde(rename = "lvl")]
    Lvl { key: usize, v: f64 },
    #[serde(rename = "dice")]
    Dice { lo: f64, hi: f64 },
    #[serde(rename = "luck")]
    Luck { n: i32 },
}

#[derive(Deserialize, Clone)]
pub struct Trinket {
    pub color: usize,
    pub effects: Vec<TrinketEff>,
}

#[derive(Deserialize, Clone)]
pub struct CharmMod {
    pub a: usize,
    pub v: f64,
    pub g: usize,
}

#[derive(Deserialize, Clone)]
pub struct God {
    pub mods: Vec<CharmMod>,
}

#[derive(Deserialize, Clone)]
pub struct DeckOpt {
    pub card: usize,
    pub a: usize,
    pub c: f64,
}

#[derive(Deserialize, Clone)]
pub struct TalentTier {
    #[serde(rename = "type")]
    pub ttype: u8,
    pub a: i64,
    pub extra: bool,
    pub v: f64,
    pub max_stacks: f64,
    pub di: Option<f64>,
    pub th: f64,
    pub dp: Option<f64>,
    pub pdd: Option<f64>,
    pub mhp: Option<f64>,
    pub ahe: Option<f64>,
    pub mmp: Option<f64>,
}

#[derive(Deserialize, Clone)]
pub struct Talent {
    pub max: usize,
    pub cost: Vec<f64>,
    pub tiers: Vec<TalentTier>,
}

#[derive(Deserialize, Clone)]
pub struct SpecRaw {
    pub id: String,
    pub skill: usize,
    pub max: usize,
    pub cost: Vec<f64>,
    pub cfg: HashMap<String, Vec<HashMap<String, f64>>>,
    pub holes: HashMap<String, Vec<(usize, usize)>>,
    pub baseline_skill: bool,
}

#[derive(Deserialize, Clone)]
pub struct GreedNode {
    pub parents: Vec<usize>,
    pub free: bool,
    pub sp: f64,
    pub entries: Vec<(usize, f64)>,
}

#[derive(Deserialize, Clone)]
pub struct Prestige {
    pub adds: Vec<(usize, f64)>,
    pub masterful: bool,
    pub berserk: bool,
}

#[derive(Deserialize, Clone)]
pub struct Caps {
    pub lucky: f64,
    pub aoe: f64,
}

/// Hyperboss health H, damage per hit D and player-damage multiplier M on c = c0 + k * step (model/hyper.py grid).
#[derive(Deserialize, Clone)]
pub struct BossGrid {
    pub c0: f64,
    pub step: f64,
    #[serde(rename = "H")]
    pub h: Vec<f64>,
    #[serde(rename = "D")]
    pub d: Vec<f64>,
    #[serde(rename = "M")]
    pub m: Vec<f64>,
}

/// Skill gate of one talent: points spent on other talents, required talents, either-of groups, lock-outs.
/// A dep of -1 names a talent the model does not offer (never met).
#[derive(Deserialize, Clone)]
pub struct Gate {
    pub t: usize,
    pub spent: f64,
    pub deps: Vec<i64>,
    pub either: Vec<Vec<usize>>,
    pub locked: Vec<usize>,
}

/// Per-ability multiplier key: specs it applies to (the spec itself or its skill).
#[derive(Deserialize, Clone)]
pub struct AmodKey {
    pub hits: Vec<usize>,
}

/// A unique: its fixed affixes (indices into the affix table), which of them are explicits a seal can replace,
/// its gear type and its slot.
#[derive(Deserialize, Clone)]
pub struct Unique {
    pub ty: usize,
    pub slot: usize,
    pub affixes: Vec<usize>,
    pub explicit: Vec<usize>,
}

#[derive(Deserialize, Clone)]
pub struct CtxRaw {
    pub attrs: Vec<String>,
    pub affixes: Vec<AffixRec>,
    pub etchings: Vec<EtchRec>,
    pub etch_queries: Vec<String>,
    pub cfg_keys: Vec<String>,
    pub bug_ids: Vec<String>,
    pub lvl_keys: Vec<LvlKey>,
    pub tal_keys: Vec<TalKey>,
    pub types: Vec<GearType>,
    pub offhand_types: Vec<usize>,
    pub necklace_type: usize,
    pub armor_types: Vec<usize>,
    pub trinkets: Vec<Trinket>,
    pub trinket_slots: Vec<usize>,
    pub trinket_fusion: bool,
    pub gods: Vec<God>,
    pub charm_prefixes: usize,
    pub deck: Vec<Vec<DeckOpt>>,
    pub talents: Vec<Talent>,
    pub gates: Vec<Gate>,
    pub amod_keys: Vec<AmodKey>,
    pub uniques: Vec<Unique>,
    pub unique_seals: Vec<usize>,
    pub unique_max: usize,
    pub unique_seals_max: usize,
    pub heal_specs: Vec<usize>,
    pub leech_spec: Vec<bool>,
    pub lucky_spec: Vec<bool>,
    pub specs: Vec<SpecRaw>,
    pub buff_specs: Vec<usize>,
    pub greed: Vec<GreedNode>,
    pub greed_budget: usize,
    pub prestige: Prestige,
    pub knobs: HashMap<String, f64>,
    pub caps: Caps,
    pub boss_grid: BossGrid,
    pub seals: usize,
    pub unusual: usize,
    pub mode_bugged: bool,
    pub talent_names: Vec<String>,
    pub spec_names: Vec<String>,
}

#[derive(Deserialize, Clone)]
pub struct FamilyRaw {
    pub id: String,
    pub kind: String,
    #[serde(default)]
    pub weapon: String,
    pub main: i64,
    pub mainhands: Vec<usize>,
    pub baseline: Vec<(usize, usize)>,
}

macro_rules! keys {
    ($($name:ident = $s:literal),* $(,)?) => {
        #[allow(non_camel_case_types, dead_code)]
        #[derive(Clone, Copy)]
        pub enum K { $($name),* }
        pub const KEY_NAMES: &[&str] = &[$($s),*];
    };
}

keys! {
    cooldownTicks = "cooldownTicks", manaCost = "manaCost", percentAbilityPowerDealt = "percentAbilityPowerDealt",
    percentAbilityPowerDealtMin = "percentAbilityPowerDealtMin", percentAbilityPowerDealtMax = "percentAbilityPowerDealtMax",
    radius = "radius", intervalTicks = "intervalTicks", manaCostPerSecond = "manaCostPerSecond",
    additionalManaPerBolt = "additionalManaPerBolt", durationTicks = "durationTicks", maxTargets = "maxTargets",
    chainRange = "chainRange", boltCount = "boltCount", fullDamageHitsPerTarget = "fullDamageHitsPerTarget",
    repeatHitDamageMultiplier = "repeatHitDamageMultiplier", cloudDuration = "cloudDuration",
    percentAttackDamageDealt = "percentAttackDamageDealt", damagePerBolt = "damagePerBolt", damagePerShard = "damagePerShard",
    piercing = "piercing", numberOfJavelins = "numberOfJavelins", shockCount = "shockCount",
    shockIntervalTicks = "shockIntervalTicks", summonCap = "summonCap", attackDamagePercentPerDash = "attackDamagePercentPerDash",
    damageMultiplier = "damageMultiplier", baseDamage = "baseDamage", percentManaDealt = "percentManaDealt",
    percentHealthDrained = "percentHealthDrained", damagePerHealth = "damagePerHealth",
    blockChanceDamageScalar = "blockChanceDamageScalar", thornsDamageScalar = "thornsDamageScalar",
    totemPercentDamagePerInterval = "totemPercentDamagePerInterval", totemDurationTicks = "totemDurationTicks",
    totemDamageIntervalTicks = "totemDamageIntervalTicks", totemEffectRadius = "totemEffectRadius",
    poisonTicks = "poisonTicks", durationSeconds = "durationSeconds", totemPlayerDamagePercent = "totemPlayerDamagePercent",
    totemManaRegenPercent = "totemManaRegenPercent", manaRampPerSecond = "manaRampPerSecond", damageIncrease = "damageIncrease",
    luckyHitChance = "luckyHitChance", maxStacksTotal = "maxStacksTotal", attackDamagePerStack = "attackDamagePerStack",
    abilityPowerPerStack = "abilityPowerPerStack", luckyHitChancePerStack = "luckyHitChancePerStack",
    maxStacksUsedPerHit = "maxStacksUsedPerHit", amplifier = "amplifier", additionalResistance = "additionalResistance",
    flatLifeHealed = "flatLifeHealed", additionalThornsDamagePercent = "additionalThornsDamagePercent",
}

/// One merged tier config: Some(value) for keys present in the Python dict.
#[derive(Clone)]
pub struct Cfg {
    pub v: Vec<Option<f64>>,
    pub hole: Option<usize>,
}

impl Cfg {
    pub fn get(&self, k: K) -> Option<f64> {
        self.v[k as usize]
    }
    pub fn req(&self, k: K) -> Result<f64, String> {
        self.v[k as usize].ok_or_else(|| format!("KeyError {}", KEY_NAMES[k as usize]))
    }
    pub fn has(&self, k: K) -> bool {
        self.v[k as usize].is_some()
    }
}

#[derive(Clone)]
pub struct Spec {
    pub id: String,
    pub skill: usize,
    pub max: usize,
    pub cost: Vec<f64>,
    pub cfg: [Vec<Cfg>; 2],
    pub baseline_skill: bool,
}

impl Spec {
    pub fn n_tiers(&self) -> usize {
        self.cfg[0].len()
    }
}

#[derive(Clone, Copy, PartialEq)]
pub enum Etq {
    NovaRecast, SmiteEcho, ArcanePierce, OrbTriple, IceBoltLucky, IceBoltMulticast, ExtraPiercingJavelin, RavenousFangs,
    LifeTapExtra, ShieldBashDamage, Mitosis, TotemMobAd, LuckyVulnerable, TotemPlayerDamageEffect, RampageLucky,
}

pub const ETQ_NAMES: &[(&str, Etq)] = &[
    ("etching_nova_recast", Etq::NovaRecast), ("etching_smite_echo", Etq::SmiteEcho),
    ("etching_arcane_pierce", Etq::ArcanePierce), ("etching_lightning_orb_triple_damage", Etq::OrbTriple),
    ("etching_ice_bolt_lucky", Etq::IceBoltLucky), ("etching_ice_bolt_multicast", Etq::IceBoltMulticast),
    ("etching_extra_piercing_javelin", Etq::ExtraPiercingJavelin), ("ravenous_fangs", Etq::RavenousFangs),
    ("etching_life_tap_extra_damage", Etq::LifeTapExtra), ("etching_shield_bash_damage", Etq::ShieldBashDamage),
    ("woldsvaults:fireball_volley_mitosis", Etq::Mitosis), ("etching_totem_mob_damage_ad", Etq::TotemMobAd),
    ("etching_lucky_vulnerable", Etq::LuckyVulnerable), ("etching_totem_player_damage_effect", Etq::TotemPlayerDamageEffect),
    ("etching_rampage_lucky_hit", Etq::RampageLucky),
];
pub const N_ETQ: usize = 15;

#[derive(Clone)]
pub struct Etching {
    pub num: Option<f64>,
    pub truthy: bool,
    pub q: [bool; N_ETQ],
    pub tl: Option<(usize, f64)>,
    pub stacking: bool,
}

#[derive(Clone, Copy, PartialEq)]
pub enum Bug { IceboltDi2 = 0, EchoFlagloss, ExecGear, OrderFlatadd, FangResidual, VulnOff1, VolleyBounceExplode, IceboltT12Hole, OffhandStaff, ManaCap }
pub const BUG_NAMES: &[&str] = &["ICEBOLT-DI2", "ECHO-FLAGLOSS", "EXEC-GEAR", "ORDER-FLATADD", "FANG-RESIDUAL", "VULN-OFF1",
                                 "VOLLEY-BOUNCE-EXPLODE", "ICEBOLT-T12-HOLE", "OFFHAND-STAFF", "MANA-CAP"];

/// Talents the evaluator reads by name (None when the stage does not offer them).
#[derive(Clone)]
pub struct NamedTalents {
    pub berserking: Option<usize>,
    pub depleted: Option<usize>,
    pub fatal_strike: Option<usize>,
    pub execution_strike: Option<usize>,
    pub fanged_strike: Option<usize>,
    pub arcane_strike: Option<usize>,
    pub cleave: Option<usize>,
    pub mana_steal: Option<usize>,
    pub life_steal: Option<usize>,
    pub executioner: Option<usize>,
    pub hexbreaker: Option<usize>,
    pub lightning_damage: Option<usize>,
}

#[derive(Clone)]
pub struct Knobs {
    pub kill_time_s: f64,
    pub survive_hits: f64,
    pub execution_cycle_cap: f64,
    pub pack_size: f64,
    pub pack_radius: f64,
    pub base_skill_points: f64,
    pub uptime_low_hp: f64,
    pub uptime_low_mana: f64,
    pub uptime_kill_stacks: f64,
    pub uptime_target_debuffed: f64,
    pub concentrate_empower_amp: f64,
    pub max_swings_per_s: f64,
    pub weave_window_s: f64,
    pub rampage_refresh_regen: f64,
    pub rampage_refresh_cdr: f64,
    pub rampage_refresh_uptime: f64,
    pub mitosis_multiplier: f64,
    pub volley_bounces: f64,
    pub volley_iframe_hits_per_cast: f64,
    pub smite_targets_in_range: f64,
    pub boss_shield_downtime: f64,
    pub min_mana_regen: f64,
    pub min_cooldown_reduction: f64,
    pub hit_interval_s: f64,
    pub oneshot_protection: bool,
    pub oneshot_heal_fraction: f64,
    pub oneshot_extra_cycles: f64,
    pub castle_bastion_uptime: f64,
}

/// Attribute slots derive() reads.
#[derive(Clone)]
pub struct AttrIds {
    pub ids: Vec<usize>,
}

pub const DERIVED_ATTRS: &[&str] = &[
    "the_vault:attack_damage", "the_vault:attack_speed", "the_vault:attack_speed_percent", "the_vault:ability_power",
    "the_vault:ability_power_percent", "the_vault:ability_power_percentile", "the_vault:damage_increase",
    "the_vault:lucky_hit_chance", "the_vault:lucky_hit_chance_percentile", "the_vault:jester_lucky_hit_chance_percentile",
    "the_vault:area_of_effect", "the_vault:health", "the_vault:health_percentile", "the_vault:armor",
    "the_vault:armor_percentile", "the_vault:resistance_cap", "the_vault:resistance", "the_vault:block_cap",
    "the_vault:block", "the_vault:dodge_percent", "the_vault:mana_additive", "the_vault:mana_additive_percentile",
    "the_vault:mana_regen", "the_vault:cooldown_reduction", "the_vault:cooldown_reduction_percentile",
    "the_vault:cooldown_reduction_cap", "the_vault:echoing_chance", "the_vault:echoing_damage",
    "the_vault:execution_damage", "the_vault:on_hit_chain", "the_vault:on_hit_aoe", "the_vault:double_hit_chance",
    "the_vault:relentless_strike", "the_vault:third_attack", "the_vault:effect_duration",
    "the_vault:critical_hit_mitigation", "the_vault:ability_cooldown_skip", "the_vault:thorns_damage_flat",
    "the_vault:healing_effectiveness", "the_vault:leech", "the_vault:on_kill_heal", "the_vault:thorns_damage",
    "the_vault:thorns_scaling_damage", "the_vault:ap_scaling_damage", "the_vault:castle_bastion",
    "minecraft:generic.attack_damage", "minecraft:generic.max_health", "the_vault:generic.mana_max",
    "the_vault:generic.mana_regen",
];

/// Positions in DERIVED_ATTRS (some are listed only to keep the order).
#[allow(non_camel_case_types, dead_code)]
#[derive(Clone, Copy)]
pub enum A {
    attack_damage, attack_speed, attack_speed_percent, ability_power, ability_power_percent, ability_power_percentile,
    damage_increase, lucky_hit_chance, lucky_hit_chance_percentile, jester_lucky_hit_chance_percentile, area_of_effect,
    health, health_percentile, armor, armor_percentile, resistance_cap, resistance, block_cap, block, dodge_percent,
    mana_additive, mana_additive_percentile, mana_regen, cooldown_reduction, cooldown_reduction_percentile,
    cooldown_reduction_cap, echoing_chance, echoing_damage, execution_damage, on_hit_chain, on_hit_aoe, double_hit_chance,
    relentless_strike, third_attack, effect_duration, critical_hit_mitigation, ability_cooldown_skip, thorns_damage_flat,
    healing_effectiveness, leech, on_kill_heal, thorns_damage, thorns_scaling_damage, ap_scaling_damage, castle_bastion,
    v_attack_damage, v_max_health, v_mana_max, v_mana_regen,
}

#[derive(Clone)]
pub enum FamKind {
    Melee { combo_avg: f64, weapon: String },
    Ability { main: usize },
}

#[derive(Clone)]
pub struct Family {
    pub id: String,
    pub kind: FamKind,
    pub mainhands: Vec<usize>,
    pub baseline: Vec<(usize, usize)>,
}

#[derive(Clone)]
pub struct Problem {
    pub affixes: Vec<AffixRec>,
    /// AffixRec.k packed densely for the hot loops.
    pub affix_kind: Vec<u8>,
    pub etchings: Vec<Etching>,
    pub lvl_keys: Vec<LvlKey>,
    pub lvl_hit: Vec<Vec<bool>>,
    pub tal_keys: Vec<TalKey>,
    pub types: Vec<GearType>,
    pub offhand_types: Vec<usize>,
    pub necklace_type: usize,
    pub armor_types: Vec<usize>,
    pub trinkets: Vec<Trinket>,
    pub trinket_slots: Vec<usize>,
    pub trinket_fusion: bool,
    pub gods: Vec<God>,
    pub charm_prefixes: usize,
    pub deck: Vec<Vec<DeckOpt>>,
    pub talents: Vec<Talent>,
    pub gates: Vec<Gate>,
    /// amod_hit[key][spec]: the per-ability multiplier key applies to the spec.
    pub amod_hit: Vec<Vec<bool>>,
    pub uniques: Vec<Unique>,
    pub unique_seals: Vec<usize>,
    pub unique_max: usize,
    pub unique_seals_max: usize,
    pub heal_specs: Vec<usize>,
    pub leech_spec: Vec<bool>,
    pub lucky_spec: Vec<bool>,
    pub specs: Vec<Spec>,
    pub buff_specs: Vec<usize>,
    pub greed: Vec<GreedNode>,
    pub greed_children: Vec<Vec<usize>>,
    pub greed_budget: usize,
    pub prestige: Prestige,
    pub knobs: Knobs,
    pub caps: Caps,
    pub grid: BossGrid,
    pub seals: usize,
    pub unusual: usize,
    pub mode_bugged: bool,
    pub at: AttrIds,
    pub nt: NamedTalents,
    pub talent_names: Vec<String>,
    pub spec_names: Vec<String>,
    pub spec_by_name: HashMap<String, usize>,
    pub fam: Family,
}

fn knob(m: &HashMap<String, f64>, k: &str) -> f64 {
    *m.get(k).unwrap_or_else(|| panic!("knob {} missing from export", k))
}

fn make_family(_specs: &[Spec], fam: &FamilyRaw) -> Family {
    let kind = if fam.kind == "melee" {
        FamKind::Melee { combo_avg: combo_avg(&fam.weapon), weapon: fam.weapon.clone() }
    } else {
        FamKind::Ability { main: fam.main as usize }
    };
    Family { id: fam.id.clone(), kind, mainhands: fam.mainhands.clone(), baseline: fam.baseline.clone() }
}

pub fn combo_avg(weapon: &str) -> f64 {
    let c: &[f64] = match weapon {
        "sword" => &[1.0, 1.0, 1.25],
        "axe" => &[1.0, 1.0],
        "battlestaff" => &[0.8, 1.0, 1.2, 1.4, 0.8, 0.8],
        "trident" => &[1.0],
        _ => panic!("unknown melee weapon {}", weapon),
    };
    c.iter().sum::<f64>() / c.len() as f64
}

impl Problem {
    pub fn new(raw: CtxRaw, fam: FamilyRaw) -> Problem {
        for (i, k) in KEY_NAMES.iter().enumerate() {
            assert!(raw.cfg_keys.get(i).map(|s| s.as_str()) == Some(*k), "cfg key list out of sync with model/kernel.py at {}", k);
        }
        assert_eq!(raw.cfg_keys.len(), KEY_NAMES.len(), "cfg key list length differs from model/kernel.py");
        for (i, b) in BUG_NAMES.iter().enumerate() {
            assert!(raw.bug_ids.get(i).map(|s| s.as_str()) == Some(*b), "bug id list out of sync at {}", b);
        }
        use crate::build::*;
        let cap = |what: &str, n: usize, max: usize, constant: &str| {
            assert!(n <= max, "{} has {} entries, kernel capacity is {}: raise {} in kernel/src/build.rs", what, n, max, constant);
        };
        cap("attribute table", raw.attrs.len() + 1, MAX_ATTRS, "MAX_ATTRS");
        cap("talents", raw.talents.len(), MAX_TALENTS, "MAX_TALENTS");
        cap("ability specs", raw.specs.len(), MAX_SPECS, "MAX_SPECS");
        cap("greed nodes", raw.greed.len(), MAX_GREED, "MAX_GREED");
        cap("deck slots", raw.deck.len(), MAX_DECK, "MAX_DECK");
        for t in &raw.types {
            cap("implicit groups", t.implicit_groups.len(), MAX_IMPLICITS, "MAX_IMPLICITS");
            cap("prefixes", t.pre, MAX_SIDE, "MAX_SIDE");
            cap("suffixes", t.suf, MAX_SIDE, "MAX_SIDE");
        }
        cap("trinket slots", 2 * raw.trinket_slots.iter().sum::<usize>(), MAX_TRINKETS, "MAX_TRINKETS");
        cap("charm mods", raw.charm_prefixes, MAX_MODS, "MAX_MODS");
        for t in &raw.talents {
            assert!(t.max < 256, "talent tier count must fit u8");
        }
        let attr_idx: HashMap<&str, usize> = raw.attrs.iter().enumerate().map(|(i, s)| (s.as_str(), i)).collect();
        let dummy = raw.attrs.len();
        let at = AttrIds { ids: DERIVED_ATTRS.iter().map(|n| *attr_idx.get(n).unwrap_or(&dummy)).collect() };
        let qmap: Vec<Etq> = raw.etch_queries.iter().map(|n| {
            ETQ_NAMES.iter().find(|(s, _)| s == n).unwrap_or_else(|| panic!("unknown etching query {}", n)).1
        }).collect();
        let etchings = raw.etchings.iter().map(|e| {
            let mut q = [false; N_ETQ];
            for &i in &e.q {
                q[qmap[i] as usize] = true;
            }
            Etching { num: e.num, truthy: e.truthy, q, tl: e.tl, stacking: e.stacking }
        }).collect();
        let specs: Vec<Spec> = raw.specs.iter().map(|s| {
            let mk = |variant: &str| -> Vec<Cfg> {
                let holes: HashMap<usize, usize> = s.holes.get(variant).cloned().unwrap_or_default().into_iter().collect();
                s.cfg[variant].iter().enumerate().map(|(i, m)| Cfg {
                    v: KEY_NAMES.iter().map(|k| m.get(*k).copied()).collect(),
                    hole: holes.get(&(i + 1)).copied(),
                }).collect()
            };
            let c = [mk("intended"), mk("bugged")];
            Spec { id: s.id.clone(), skill: s.skill, max: s.max, cost: s.cost.clone(), cfg: c, baseline_skill: s.baseline_skill }
        }).collect();
        let lvl_hit = raw.lvl_keys.iter().map(|k| {
            let mut v = vec![false; specs.len()];
            for &h in &k.hits {
                v[h] = true;
            }
            v
        }).collect();
        let tid = |n: &str| raw.talent_names.iter().position(|x| x == n);
        let nt = NamedTalents {
            berserking: tid("Berserking"), depleted: tid("Depleted"), fatal_strike: tid("Fatal_Strike"),
            execution_strike: tid("Execution_Strike"), fanged_strike: tid("Fanged_Strike"), arcane_strike: tid("Arcane_Strike"),
            cleave: tid("Cleave"), mana_steal: tid("Mana_Steal"), life_steal: tid("Life_Steal"), executioner: tid("Executioner"),
            hexbreaker: tid("Hexbreaker"), lightning_damage: tid("Lightning_Damage"),
        };
        let k = &raw.knobs;
        let knobs = Knobs {
            kill_time_s: knob(k, "kill_time_s"), survive_hits: knob(k, "survive_hits"),
            execution_cycle_cap: knob(k, "execution_cycle_cap"), pack_size: knob(k, "pack_size"),
            pack_radius: knob(k, "pack_radius"), base_skill_points: knob(k, "base_skill_points"),
            uptime_low_hp: knob(k, "uptime_low_hp"), uptime_low_mana: knob(k, "uptime_low_mana"),
            uptime_kill_stacks: knob(k, "uptime_kill_stacks"), uptime_target_debuffed: knob(k, "uptime_target_debuffed"),
            concentrate_empower_amp: knob(k, "concentrate_empower_amp"), max_swings_per_s: knob(k, "max_swings_per_s"),
            weave_window_s: knob(k, "weave_window_s"),
            rampage_refresh_regen: knob(k, "rampage_refresh_regen"), rampage_refresh_cdr: knob(k, "rampage_refresh_cdr"),
            rampage_refresh_uptime: knob(k, "rampage_refresh_uptime"), mitosis_multiplier: knob(k, "mitosis_multiplier"),
            volley_bounces: knob(k, "volley_bounces"), volley_iframe_hits_per_cast: knob(k, "volley_iframe_hits_per_cast"),
            smite_targets_in_range: knob(k, "smite_targets_in_range"), boss_shield_downtime: knob(k, "boss_shield_downtime"),
            min_mana_regen: knob(k, "min_mana_regen"), min_cooldown_reduction: knob(k, "min_cooldown_reduction"),
            hit_interval_s: knob(k, "hit_interval_s"), oneshot_protection: knob(k, "oneshot_protection") != 0.0,
            oneshot_heal_fraction: knob(k, "oneshot_heal_fraction"), oneshot_extra_cycles: knob(k, "oneshot_extra_cycles"),
            castle_bastion_uptime: knob(k, "castle_bastion_uptime"),
        };
        let amod_hit = raw.amod_keys.iter().map(|k| {
            let mut v = vec![false; specs.len()];
            for &h in &k.hits {
                v[h] = true;
            }
            v
        }).collect();
        for u in &raw.uniques {
            cap("unique affixes", u.affixes.len(), MAX_UNIQUE_AFFIXES, "MAX_UNIQUE_AFFIXES");
        }
        let mut greed_children = vec![Vec::new(); raw.greed.len()];
        for (i, n) in raw.greed.iter().enumerate() {
            for &p in &n.parents {
                greed_children[p].push(i);
            }
        }
        let spec_by_name = raw.spec_names.iter().enumerate().map(|(i, s)| (s.clone(), i)).collect();
        let family = make_family(&specs, &fam);
        Problem {
            affix_kind: raw.affixes.iter().map(|a| a.k).collect(), affixes: raw.affixes, etchings, lvl_keys: raw.lvl_keys, lvl_hit,
            tal_keys: raw.tal_keys, types: raw.types, offhand_types: raw.offhand_types, necklace_type: raw.necklace_type,
            armor_types: raw.armor_types, trinkets: raw.trinkets, trinket_slots: raw.trinket_slots,
            trinket_fusion: raw.trinket_fusion, gods: raw.gods, charm_prefixes: raw.charm_prefixes, deck: raw.deck,
            talents: raw.talents, gates: raw.gates, amod_hit, uniques: raw.uniques, unique_seals: raw.unique_seals,
            unique_max: raw.unique_max, unique_seals_max: raw.unique_seals_max, heal_specs: raw.heal_specs,
            leech_spec: raw.leech_spec, lucky_spec: raw.lucky_spec, specs,
            buff_specs: raw.buff_specs, greed: raw.greed, greed_children, greed_budget: raw.greed_budget,
            prestige: raw.prestige, knobs, caps: raw.caps, grid: raw.boss_grid, seals: raw.seals, unusual: raw.unusual,
            mode_bugged: raw.mode_bugged, at, nt, talent_names: raw.talent_names, spec_names: raw.spec_names, spec_by_name,
            fam: family,
        }
    }

    /// Same context with another family (batch jobs share one parsed context).
    pub fn with_family(&self, fam: &FamilyRaw) -> Problem {
        let mut p = self.clone();
        p.fam = make_family(&p.specs, fam);
        p
    }

    pub fn is_melee(&self) -> bool {
        matches!(self.fam.kind, FamKind::Melee { .. })
    }

    /// family_lucky(): melee and the two Fangs specs.
    pub fn family_lucky(&self) -> bool {
        match self.fam.kind {
            FamKind::Melee { .. } => true,
            FamKind::Ability { main } => {
                let n = &self.specs[main].id;
                n == "Fangs_Base" || n == "Fangs_Maw"
            }
        }
    }
}
