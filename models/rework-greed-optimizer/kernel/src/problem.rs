//! Flattened context exported by model/kernel.py, resolved into index tables the evaluator reads directly.

use serde::de::IgnoredAny;
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
pub struct EtchRec {
    #[serde(rename = "id")]
    _id: IgnoredAny,
    pub num: Option<f64>,
    pub dur: Option<f64>,
    pub truthy: bool,
    pub q: Vec<usize>,
    pub tl: Option<(usize, f64)>,
    pub stacking: bool,
    /// Lucky Reset: the spec indices its ability pool names (abilities.reset_pool).
    #[serde(default)]
    pub pool: Vec<usize>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct LvlKey {
    pub all: bool,
    pub hits: Vec<usize>,
    #[serde(rename = "name")]
    _name: IgnoredAny,
    #[serde(rename = "ok_any")]
    _ok_any: IgnoredAny,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct TalKey {
    pub all: bool,
    pub talent: i64,
    #[serde(rename = "name")]
    _name: IgnoredAny,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
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
#[serde(tag = "t", deny_unknown_fields)]
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
#[serde(deny_unknown_fields)]
pub struct Trinket {
    #[serde(rename = "id")]
    _id: IgnoredAny,
    pub color: usize,
    pub effects: Vec<TrinketEff>,
    pub regen: i64,
    /// Carapace: meets the effect-negation requirement.
    #[serde(default)]
    pub negation: bool,
    /// Immortal Seal (evaluate.immortal_seal): chance per hit taken and immunity ticks.
    #[serde(default)]
    pub seal: Option<(f64, f64)>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct CharmMod {
    pub a: usize,
    pub v: f64,
    pub g: usize,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct God {
    #[serde(rename = "name")]
    _name: IgnoredAny,
    pub mods: Vec<CharmMod>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct DeckOpt {
    pub card: usize,
    pub a: usize,
    pub c: f64,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
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
    pub cdd: Option<f64>,
    pub ch: Option<f64>,
    /// Stacking talents: durationTicks of the shared stack timer.
    #[serde(default)]
    pub dur: Option<f64>,
    /// Blizzard (cast_on_hit): cast probability and the durationTicks of the Frost Nova tier it casts.
    #[serde(default)]
    pub pr: Option<f64>,
    #[serde(default)]
    pub nd: Option<f64>,
    /// Last Stand (low_health_resistance): damage reduction below the health threshold.
    #[serde(default)]
    pub dr: Option<f64>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Talent {
    #[serde(rename = "id")]
    _id: IgnoredAny,
    pub max: usize,
    pub cost: Vec<f64>,
    pub tiers: Vec<TalentTier>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct SpecRaw {
    pub id: String,
    pub skill: usize,
    pub max: usize,
    pub cost: Vec<f64>,
    pub cfg: HashMap<String, Vec<HashMap<String, f64>>>,
    pub holes: HashMap<String, Vec<(usize, usize)>>,
    pub baseline_skill: bool,
    pub pd_ok: bool,
    /// Spec table facts (model/spec_overlay.py via kernel.py _spec_facts): leech and spectral rules (RULE_*), lucky
    /// hits, Power Dump held channel, cast-debuff class, fang table (FANG_*), reach rule (REACH_*) and the config key
    /// holding the reach radius.
    pub leech: u8,
    pub lucky: bool,
    pub pd_hold: bool,
    pub spectral: u8,
    pub cast_debuff: u8,
    pub fang: u8,
    pub reach: u8,
    #[serde(default)]
    pub reach_key: Option<String>,
    /// Rework: hits neither set nor respect i-frames (abilities.free_iframes).
    pub free_iframe: bool,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct GreedNode {
    #[serde(rename = "id")]
    _id: IgnoredAny,
    pub parents: Vec<usize>,
    pub free: bool,
    pub sp: f64,
    pub entries: Vec<(usize, f64)>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Prestige {
    pub adds: Vec<(usize, f64)>,
    pub masterful: bool,
    pub berserk: bool,
    pub regen: i64,
    /// Swiftness Amplified movement multiplier (1 when the stage does not reach it).
    #[serde(default = "one")]
    pub swiftness: f64,
}

fn one() -> f64 {
    1.0
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Caps {
    pub lucky: f64,
    pub aoe: f64,
}

/// Hyperboss health H, damage per hit D and player-damage multiplier M on c = c0 + k * step (model/hyper.py grid).
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct BossGrid {
    pub c0: f64,
    pub step: f64,
    #[serde(rename = "H")]
    pub h: Vec<f64>,
    /// Raw attack damage; the kernel solves survival on DF / DS and keeps this only for the export shape.
    #[allow(dead_code)]
    #[serde(rename = "D")]
    pub d: Vec<f64>,
    #[serde(rename = "M")]
    pub m: Vec<f64>,
    /// Survival hit: crit hit, and the as-shipped hit with the attack-damage cap (BOSS-DMG-CAP).
    #[serde(rename = "DF")]
    pub df: Vec<f64>,
    #[serde(rename = "DS")]
    pub ds: Vec<f64>,
    /// ln(kill_time_s x M), ln DF and ln DS per grid point (filled at load for damage_cycle and survival_cycle).
    #[serde(skip)]
    pub ln_tm: Vec<f64>,
    #[serde(skip)]
    pub ln_df: Vec<f64>,
    #[serde(skip)]
    pub ln_ds: Vec<f64>,
}

impl BossGrid {
    fn with_logs(mut self, kill_time_s: f64) -> BossGrid {
        self.ln_tm = self.m.iter().map(|&m| (kill_time_s * m).ln()).collect();
        self.ln_df = self.df.iter().map(|&x| x.ln()).collect();
        self.ln_ds = self.ds.iter().map(|&x| x.ln()).collect();
        self
    }
}

/// Skill gate of one talent: points spent on other talents, required talents, either-of groups, lock-outs.
/// A dep of -1 names a talent the model does not offer (never met).
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Gate {
    pub t: usize,
    pub spent: f64,
    pub deps: Vec<i64>,
    pub either: Vec<Vec<usize>>,
    pub locked: Vec<usize>,
}

/// Per-ability multiplier key: specs it applies to (the spec itself or its skill).
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct AmodKey {
    pub hits: Vec<usize>,
    #[serde(rename = "name")]
    _name: IgnoredAny,
}

/// A unique: its fixed affixes (indices into the affix table), which of them are explicits a seal can replace,
/// its gear type and its slot.
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Unique {
    #[serde(rename = "id")]
    _id: IgnoredAny,
    pub ty: usize,
    pub slot: usize,
    pub affixes: Vec<usize>,
    pub explicit: Vec<usize>,
    pub ancient: bool,
    pub regen: i64,
}

/// One god tree's local graph (model/gods.Tree): node types 0 root / 1 stat / 2 minor / 3 major, effect per node.
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct GodTreeRaw {
    pub off: usize,
    pub n: usize,
    pub ty: Vec<u8>,
    pub eff: Vec<usize>,
    pub usable: Vec<bool>,
    pub adj: Vec<Vec<usize>>,
    pub roots: Vec<usize>,
}

/// A god node effect: plain gear attribute(s) (ga) or a custom handler by name with its numeric params.
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct GodEffect {
    pub god: usize,
    pub ty: u8,
    pub ga: bool,
    pub a: i64,
    pub a2: i64,
    pub values: Vec<f64>,
    pub minor_node: i64,
    pub pious: bool,
    pub handler: String,
    pub params: HashMap<String, f64>,
    pub adds: Vec<(usize, f64)>,
    pub node: usize,
}

/// Greed-rework god data (model/kernel.py _god_doc).
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct GodData {
    pub trees: Vec<GodTreeRaw>,
    pub effects: Vec<GodEffect>,
    pub cum: Vec<f64>,
    pub max_defined: usize,
    pub past_max: f64,
    pub p1: usize,
    pub p_to: usize,
    pub p_past: usize,
    pub mts_levels: Vec<usize>,
    pub rep: usize,
    pub rep_per_point: usize,
    pub pool: f64,
    pub rank_gate: usize,
    pub max_level: usize,
    pub charm_to_god: Vec<usize>,
    pub time_lock_dynamic: bool,
    pub plushie_type: i64,
    pub staff_type: i64,
    pub tb_effect: i64,
    pub names: Vec<String>,
    pub fury: Vec<[f64; 4]>,
    pub utility_key: i64,
    #[serde(skip)]
    pub ix: crate::gods::GfxIx,
    #[serde(skip)]
    pub fast: crate::gods::GodFast,
    #[serde(skip)]
    pub gp: crate::gods::GfxPar,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct CtxRaw {
    pub contract: Contract,
    pub attrs: Vec<String>,
    #[serde(rename = "n_groups")]
    _n_groups: IgnoredAny,
    #[serde(rename = "hyper")]
    _hyper: IgnoredAny,
    pub affixes: Vec<AffixRec>,
    pub etchings: Vec<EtchRec>,
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
    pub designs: Vec<crate::deck::DesignDoc>,
    pub talents: Vec<Talent>,
    pub gates: Vec<Gate>,
    pub amod_keys: Vec<AmodKey>,
    pub uniques: Vec<Unique>,
    pub unique_seals: Vec<usize>,
    pub unique_max: usize,
    pub unique_seals_max: usize,
    pub heal_specs: Vec<usize>,
    pub specs: Vec<SpecRaw>,
    /// Better Combat combo multipliers per melee weapon (spec_overlay.WEAPON_COMBOS).
    pub weapon_combos: HashMap<String, Vec<f64>>,
    pub buff_specs: Vec<usize>,
    pub sec_specs: Vec<usize>,
    pub greed: Vec<GreedNode>,
    pub greed_budget: usize,
    pub prestige: Prestige,
    pub knobs: HashMap<String, f64>,
    /// evaluate.dice_z of the dice_percentile knob.
    pub dice_z: f64,
    pub caps: Caps,
    pub boss_grid: BossGrid,
    pub seals: usize,
    pub unusual: usize,
    pub mode_bugged: bool,
    pub talent_names: Vec<String>,
    pub spec_names: Vec<String>,
    pub godtree: Option<GodData>,
    pub ancient_max: usize,
    pub clouds: Vec<Cloud>,
    pub echo: EchoTable,
    pub fang_table: FangTable,
    #[serde(default)]
    pub echo_reverb: EchoTable,
    pub boots_mj: i64,
    pub feather: i64,
    pub boots_slot: usize,
}

/// Name lists the index-coupled tables share with model/kernel.py (contract_lists); checked in Problem::new.
#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Contract {
    pub affix_kinds: Vec<String>,
    pub flag_keys: Vec<String>,
    pub special_keys: Vec<String>,
    pub talent_types: HashMap<String, u8>,
    pub amod_kinds: HashMap<String, u8>,
    pub proc_kinds: Vec<String>,
    pub cloud_kinds: HashMap<String, u8>,
    pub etch_queries: Vec<String>,
    pub cfg_keys: Vec<String>,
    pub bug_ids: Vec<String>,
    pub rule_codes: HashMap<String, u8>,
    pub cast_debuff_classes: Vec<(String, String, u8)>,
    pub knobs: Vec<String>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct Cloud {
    pub chance: f64,
    pub ticks: f64,
    pub effects: Vec<(usize, f64)>,
    /// Strongest minecraft:poison amplifier + 1 (0 = no poison).
    #[serde(default)]
    pub poison: f64,
    /// Strongest Chilled level (amplifier + 1; 0 = none).
    pub chill: i64,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
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
    flatLifeHealed = "flatLifeHealed", additionalThornsDamagePercent = "additionalThornsDamagePercent", stackDuration = "stackDuration",
    baseAmplitude = "baseAmplitude", amplitudeScaleChance = "amplitudeScaleChance", baseEffectDuration = "baseEffectDuration",
    speedPercentAdded = "speedPercentAdded", totemHealthPerSecond = "totemHealthPerSecond", healthPoints = "healthPoints",
    percentageDamageAbsorbed = "percentageDamageAbsorbed", manaPerDamageScalar = "manaPerDamageScalar",
    baseManaDrainPerTick = "baseManaDrainPerTick", manaDrainRampPerSecond = "manaDrainRampPerSecond",
    maxManaDrainPerTick = "maxManaDrainPerTick", attackIntervalTicks = "attackIntervalTicks", aoeRadius = "aoeRadius",
    aoeDamageMultiplier = "aoeDamageMultiplier", slowDuration = "slowDuration",
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
    /// buffs.pd_eligible: instant-mana or held channel (Power Dump can apply when cast while sneaking).
    pub pd_ok: bool,
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
    PyramidScheme, Divinity, SneakyNinja,
    ApTotem, StrikeDelay, BlizzardEffect, BlizzardDamage, LuckyNova, LuckyReset, QuakeRecast, Greedball, Reverb,
    BlockingMana, ScatterMana, PoisonMana, ColossusTitan, NovaLowMana,
}

pub const ETQ_NAMES: &[(&str, Etq)] = &[
    ("etching_nova_recast", Etq::NovaRecast), ("etching_smite_echo", Etq::SmiteEcho),
    ("etching_arcane_pierce", Etq::ArcanePierce), ("etching_lightning_orb_triple_damage", Etq::OrbTriple),
    ("etching_ice_bolt_lucky", Etq::IceBoltLucky), ("etching_ice_bolt_multicast", Etq::IceBoltMulticast),
    ("etching_extra_piercing_javelin", Etq::ExtraPiercingJavelin), ("ravenous_fangs", Etq::RavenousFangs),
    ("etching_life_tap_extra_damage", Etq::LifeTapExtra), ("etching_shield_bash_damage", Etq::ShieldBashDamage),
    ("woldsvaults:fireball_volley_mitosis", Etq::Mitosis), ("etching_totem_mob_damage_ad", Etq::TotemMobAd),
    ("etching_lucky_vulnerable", Etq::LuckyVulnerable), ("etching_totem_player_damage_effect", Etq::TotemPlayerDamageEffect),
    ("etching_rampage_lucky_hit", Etq::RampageLucky), ("woldsvaults:additional_stacking_stacks", Etq::PyramidScheme),
    ("woldsvaults:divinity", Etq::Divinity), ("woldsvaults:sneaky_getaway_ninja", Etq::SneakyNinja),
    ("etching_totem_player_damage_ap", Etq::ApTotem),
    ("etching_lightning_strike_delay", Etq::StrikeDelay), ("etching_storm_arrow_blizzard_effect", Etq::BlizzardEffect),
    ("etching_storm_arrow_blizzard_damage", Etq::BlizzardDamage), ("etching_lucky_frost_nova", Etq::LuckyNova),
    ("etching_lucky_cooldown_reset", Etq::LuckyReset), ("etching_earthquake_recast_delay", Etq::QuakeRecast),
    ("woldsvaults:fireball_greedball", Etq::Greedball), ("woldsvaults:reverberation", Etq::Reverb),
    ("etching_blocking_mana", Etq::BlockingMana), ("etching_scatter_mana", Etq::ScatterMana),
    ("etching_poison_mana", Etq::PoisonMana), ("woldsvaults:colossus_titan_resistance", Etq::ColossusTitan),
    ("etching_nova_low_mana_damage", Etq::NovaLowMana),
];
pub const N_ETQ: usize = 32;
const _: () = assert!(N_ETQ <= 32, "Etching.mask holds at most 32 queries");

#[derive(Clone)]
pub struct Etching {
    pub num: Option<f64>,
    /// The etching's "duration" field (Lucky Vulnerable, Totem damage effect), seconds.
    pub dur: Option<f64>,
    pub truthy: bool,
    pub q: [bool; N_ETQ],
    /// q as a bit set (bit i = query i).
    pub mask: u32,
    pub tl: Option<(usize, f64)>,
    pub stacking: bool,
    pub pool: Vec<usize>,
}

#[derive(Clone, Copy, PartialEq)]
pub enum Bug { IceboltDi2 = 0, EchoFlagloss, ExecGear, OrderFlatadd, FangResidual, VulnOff1, VolleyBounceExplode, IceboltT12Hole, OffhandStaff, ManaCap, BossDmgCap }
pub const BUG_NAMES: &[&str] = &["ICEBOLT-DI2", "ECHO-FLAGLOSS", "EXEC-GEAR", "ORDER-FLATADD", "FANG-RESIDUAL", "VULN-OFF1",
                                 "VOLLEY-BOUNCE-EXPLODE", "ICEBOLT-T12-HOLE", "OFFHAND-STAFF", "MANA-CAP", "BOSS-DMG-CAP"];

/// Talents the evaluator reads by name (None when the stage does not offer them).
#[derive(Clone)]
pub struct NamedTalents {
    pub berserking: Option<usize>,
    pub depleted: Option<usize>,
    pub fatal_strike: Option<usize>,
    pub execution_strike: Option<usize>,
    pub fanged_strike: Option<usize>,
    pub arcane_strike: Option<usize>,
    pub ethereal: Option<usize>,
    pub cleave: Option<usize>,
    pub mana_steal: Option<usize>,
    pub life_steal: Option<usize>,
    pub executioner: Option<usize>,
    pub hexbreaker: Option<usize>,
    pub lightning_damage: Option<usize>,
    pub last_stand: Option<usize>,
    pub blizzard: Option<usize>,
}

#[derive(Clone)]
pub struct Knobs {
    pub god_banked_damage_taken: f64,
    pub god_surrounded_mobs: f64,
    pub god_vault_minutes: f64,
    pub god_sneaky_effects: f64,
    pub power_dump_sqrt_ref: f64,
    pub spectral_ap_per_stack: f64,
    pub pd_hold_drain: f64,
    pub pd_sneak_window_s: f64,
    pub sneak_avoid_factor: f64,
    pub kill_time_s: f64,
    pub survive_hits: f64,
    pub execution_cycle_cap: f64,
    pub pack_size: f64,
    pub pack_radius: f64,
    pub base_skill_points: f64,
    pub uptime_low_hp: f64,
    pub uptime_low_mana: f64,
    pub mana_state_heuristic: bool,
    pub kills_per_s: f64,
    pub concentrate_effects_stripped: f64,
    pub score_swings: bool,
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
    pub min_move_speed: f64,
    pub main_damage_share_min: f64,
    pub max_secondaries: usize,
    pub secondary_min_value: f64,
    pub hit_interval_s: f64,
    pub survival_risk: f64,
    pub avoid_lo: f64,
    pub avoid_hi: f64,
    pub speed_lo: f64,
    pub speed_hi: f64,
    pub ranged_bonus: f64,
    pub ranged_reach: f64,
    pub castle_uptime_lo: f64,
    pub castle_uptime_hi: f64,
    pub hp_state_heuristic: bool,
    pub dice_z: f64,
    pub maw_pull_factor: f64,
    pub hyper_boss_crit_mult: f64,
    pub oneshot_protection: bool,
    pub oneshot_heal_fraction: f64,
    pub oneshot_extra_cycles: f64,
    pub frozen_orb_additive: bool,
    pub steel_inferno_boss_efficiency: f64,
    pub steel_inferno_pack_mean: f64,
    pub storm_boss_occupancy: f64,
    pub ultimate_shield_scalar_mult: f64,
    pub effect_negation_required: bool,
    pub rang_travel_blocks: f64,
    pub taunt_amp_half_above: f64,
    pub echo_inherits_rampage: bool,
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
    "the_vault:generic.mana_regen", "the_vault:movement_speed", "the_vault:item_quantity", "the_vault:item_rarity",
    "the_vault:knockback_resistance", "the_vault:health_boost", "the_vault:reaving_damage", "the_vault:hexing_chance",
    "the_vault:burning_hit_chance", "the_vault:effect_cloud_chance_additive", "the_vault:thorns_damage_flat_percentile",
    "the_vault:velocity", "the_vault:piercing", "the_vault:returning_damage", "the_vault:ricochet",
    "the_vault:splitting_javelins",
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
    v_attack_damage, v_max_health, v_mana_max, v_mana_regen, movement_speed, item_quantity, item_rarity,
    knockback_resistance, health_boost, reaving_damage, hexing_chance, burning_hit_chance, effect_cloud_chance_additive,
    thorns_damage_flat_percentile, velocity, piercing, returning_damage, ricochet, splitting_javelins,
}

#[derive(Clone)]
pub enum FamKind {
    Melee { combo_avg: f64, weapon: String },
    Ability { main: usize },
    /// Vaultarang (families.RangedFamily): thrown from range, no weave, no secondaries.
    Rang,
}

#[derive(Clone)]
pub struct Family {
    pub id: String,
    pub kind: FamKind,
    pub mainhands: Vec<usize>,
    pub baseline: Vec<(usize, usize)>,
}

/// Spec identities the evaluator dispatches on, resolved once at load (string matches per call were a hot cost).
#[allow(non_camel_case_types)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Sp {
    Other,
    Arcane_Base,
    Arcane_Prism,
    Arcane_Rail,
    Battle_Cry_Base,
    Battle_Cry_Lucky_Strike,
    Battle_Cry_Spectral_Strike,
    Chain_Lightning_Base,
    Chain_Lightning_Charged_Bolts,
    Chain_Lightning_Orbs,
    Colossus_Base,
    Concentrate_Base,
    Dash_Damage,
    Earthquake_Base,
    Earthquake_Singularity,
    Earthquake_Tremor,
    Fangs_Base,
    Fangs_Maw,
    Fireball_Base,
    Fireball_Fireshot,
    Fireball_Volley,
    Grenade_Base,
    Grenade_Sticky,
    Ice_Bolt_Base,
    Implode_Life_Tap,
    Javelin_Base,
    Javelin_Piercing,
    Javelin_Scatter,
    Mana_Shield_Implode,
    Necromancy_Archer,
    Necromancy_Base,
    Necromancy_Golem,
    Nova_Base,
    Nova_Dot,
    Nova_Slow,
    Rampage_Base,
    Rampage_Berserker,
    Rampage_Bloodlust,
    Rampage_Instinct,
    Shard_Blizzard,
    Shell_Porcupine,
    Shield_Bash,
    Shield_Bash_Battering_Ram,
    Shield_Bash_Earthshatter,
    Smite_Archon,
    Smite_Base,
    Smite_Blast_Wave,
    Storm_Arrow_Base,
    Taunt_Base,
    Totem_Mana_Regen,
    Totem_Mob_Damage,
    Totem_Player_Damage,
    Toxic_Grenade,
    Empower_Base,
    Empower_Slowness_Aura,
    Sneaky_Getaway,
    Totem_Base,
    Mana_Shield_Base,
    UltimateShield_Base,
}

impl Sp {
    pub fn from_name(s: &str) -> Option<Sp> {
        Some(match s {
            "Arcane_Base" => Sp::Arcane_Base,
            "Arcane_Prism" => Sp::Arcane_Prism,
            "Arcane_Rail" => Sp::Arcane_Rail,
            "Battle_Cry_Base" => Sp::Battle_Cry_Base,
            "Battle_Cry_Lucky_Strike" => Sp::Battle_Cry_Lucky_Strike,
            "Battle_Cry_Spectral_Strike" => Sp::Battle_Cry_Spectral_Strike,
            "Chain_Lightning_Base" => Sp::Chain_Lightning_Base,
            "Chain_Lightning_Charged_Bolts" => Sp::Chain_Lightning_Charged_Bolts,
            "Chain_Lightning_Orbs" => Sp::Chain_Lightning_Orbs,
            "Colossus_Base" => Sp::Colossus_Base,
            "Concentrate_Base" => Sp::Concentrate_Base,
            "Dash_Damage" => Sp::Dash_Damage,
            "Earthquake_Base" => Sp::Earthquake_Base,
            "Earthquake_Singularity" => Sp::Earthquake_Singularity,
            "Earthquake_Tremor" => Sp::Earthquake_Tremor,
            "Fangs_Base" => Sp::Fangs_Base,
            "Fangs_Maw" => Sp::Fangs_Maw,
            "Fireball_Base" => Sp::Fireball_Base,
            "Fireball_Fireshot" => Sp::Fireball_Fireshot,
            "Fireball_Volley" => Sp::Fireball_Volley,
            "Grenade_Base" => Sp::Grenade_Base,
            "Grenade_Sticky" => Sp::Grenade_Sticky,
            "Ice_Bolt_Base" => Sp::Ice_Bolt_Base,
            "Implode_Life_Tap" => Sp::Implode_Life_Tap,
            "Javelin_Base" => Sp::Javelin_Base,
            "Javelin_Piercing" => Sp::Javelin_Piercing,
            "Javelin_Scatter" => Sp::Javelin_Scatter,
            "Mana_Shield_Implode" => Sp::Mana_Shield_Implode,
            "Necromancy_Archer" => Sp::Necromancy_Archer,
            "Necromancy_Base" => Sp::Necromancy_Base,
            "Nova_Base" => Sp::Nova_Base,
            "Nova_Dot" => Sp::Nova_Dot,
            "Nova_Slow" => Sp::Nova_Slow,
            "Rampage_Base" => Sp::Rampage_Base,
            "Rampage_Berserker" => Sp::Rampage_Berserker,
            "Rampage_Bloodlust" => Sp::Rampage_Bloodlust,
            "Rampage_Instinct" => Sp::Rampage_Instinct,
            "Shard_Blizzard" => Sp::Shard_Blizzard,
            "Shell_Porcupine" => Sp::Shell_Porcupine,
            "Shield_Bash" => Sp::Shield_Bash,
            "Shield_Bash_Battering_Ram" => Sp::Shield_Bash_Battering_Ram,
            "Shield_Bash_Earthshatter" => Sp::Shield_Bash_Earthshatter,
            "Smite_Archon" => Sp::Smite_Archon,
            "Smite_Base" => Sp::Smite_Base,
            "Smite_Blast_Wave" => Sp::Smite_Blast_Wave,
            "Storm_Arrow_Base" => Sp::Storm_Arrow_Base,
            "Taunt_Base" => Sp::Taunt_Base,
            "Totem_Mana_Regen" => Sp::Totem_Mana_Regen,
            "Totem_Mob_Damage" => Sp::Totem_Mob_Damage,
            "Totem_Player_Damage" => Sp::Totem_Player_Damage,
            "Toxic_Grenade" => Sp::Toxic_Grenade,
            "Necromancy_Golem" => Sp::Necromancy_Golem,
            "Empower_Base" => Sp::Empower_Base,
            "Empower_Slowness_Aura" => Sp::Empower_Slowness_Aura,
            "Sneaky_Getaway" => Sp::Sneaky_Getaway,
            "Totem_Base" => Sp::Totem_Base,
            "Mana_Shield_Base" => Sp::Mana_Shield_Base,
            "UltimateShield_Base" => Sp::UltimateShield_Base,
            _ => return None,
        })
    }
}

/// data/fang_hits.json (tools/fang_geometry.py): raw fang hits per cast on a uniform radius grid.
#[derive(Deserialize, Clone, Default)]
pub struct FangTable {
    pub step: f64,
    pub wall: Vec<f64>,
    pub maw: Vec<f64>,
}

/// data/echo_table.json flattened: v[((n - 1) * na + i) * nq + j] = V(a_i, q_j, n).
#[derive(Deserialize, Clone, Default)]
#[serde(deny_unknown_fields)]
pub struct EchoTable {
    pub n_max: usize,
    pub na: usize,
    pub nq: usize,
    pub v: Vec<f64>,
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
    /// Rework deck menus: the stage's designs (empty = the fixed layout in `deck`).
    pub designs: Vec<crate::deck::Design>,
    pub talents: Vec<Talent>,
    pub gates: Vec<Gate>,
    /// amod_hit[key][spec]: the per-ability multiplier key applies to the spec.
    pub amod_hit: Vec<Vec<bool>>,
    pub uniques: Vec<Unique>,
    pub unique_seals: Vec<usize>,
    pub unique_max: usize,
    pub unique_seals_max: usize,
    pub heal_specs: Vec<usize>,
    /// Spec table rules per spec: leech (RULE_*), lucky hits, Power Dump held channel, Spectral Strike (RULE_*), fang
    /// hits per cast.
    pub leech_spec: Vec<u8>,
    pub lucky_spec: Vec<bool>,
    pub spec_pd_hold: Vec<bool>,
    /// Per spec: hits neither set nor respect i-frames (rework Fireball and Volley).
    pub spec_free: Vec<bool>,
    pub spec_spectral: Vec<u8>,
    /// Fang table per spec (FANG_*), reach rule (REACH_*) and the Cfg index of its reach radius.
    pub spec_fang: Vec<u8>,
    pub spec_reach: Vec<(u8, usize)>,
    pub fang_table: FangTable,
    /// Talents whose effect depends on the HP state (evaluate.hp_state_talents: types 9, 11 and 12).
    pub hp_talents: Vec<bool>,
    pub specs: Vec<Spec>,
    pub buff_specs: Vec<usize>,
    /// Damage specs a build may learn next to its main one (families.secondary_specs order).
    pub sec_specs: Vec<usize>,
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
    /// Cast-debuff class per spec (buffs.cast_debuffs, kernel.py CAST_DEBUFF_CLASSES): 0 none, 1 Totem of Hatred mark,
    /// 2 Hungry Maw Slowness, 3 Necromancy curse, 4 Hypothermia (Shard Blizzard / Frost Nova).
    pub spec_cls: Vec<u8>,
    /// Combo average per melee weapon name, and per gear type index (None for types without a combo).
    pub weapon_combo: HashMap<String, f64>,
    pub type_combo: Vec<Option<f64>>,
    /// Spec identity per spec index (Sp::Other for specs no rule names) and the Mana Shield Implode spec.
    pub spec_sp: Vec<Sp>,
    pub implode_spec: Option<usize>,
    /// Hit lanes that differ (eval.rs LANE_HP / LANE_EXEC): 4, or 1 with god trees, where execution is a threshold
    /// and every lane of a hit is the same number (hit_core computes lane 0 and copies it).
    pub lanes: usize,
    pub fam: Family,
    pub godtree: Option<GodData>,
    pub ancient_max: usize,
    /// Effect-cloud affixes (affix kind 9 points here): trigger chance, cloud ticks, (kind, effect ticks).
    pub clouds: Vec<Cloud>,
    /// Echo pulse table (tools/echo_table.py; procs.echo_value).
    pub echo: EchoTable,
    /// Reverberating Echo table (tools/echo_table.py --reverb; empty when the file has none).
    pub echo_reverb: EchoTable,
    /// Multi-jump rule: affix index of the boots' Multi Jump implicit and trinket index of the Prismatic Feather (-1 = off).
    pub boots_mj: i64,
    pub feather: i64,
    pub boots_slot: usize,
}

fn knob(m: &HashMap<String, f64>, k: &str) -> f64 {
    assert!(KNOB_NAMES.contains(&k), "knob {} is read but not listed in KNOB_NAMES (kernel/src/problem.rs)", k);
    *m.get(k).unwrap_or_else(|| panic!("knob {} missing from export", k))
}

/// Knobs the kernel reads (every knob() call must name one; the `contract` op prints them).
pub const KNOB_NAMES: &[&str] = &[
    "god_banked_damage_taken", "god_surrounded_mobs", "god_vault_minutes", "god_sneaky_effects",
    "power_dump_sqrt_ref", "spectral_ap_per_stack", "pd_hold_drain", "kill_time_s", "survive_hits",
    "execution_cycle_cap", "pack_size", "pack_radius", "base_skill_points", "uptime_low_hp", "uptime_low_mana",
    "max_swings_per_s", "weave_window_s", "rampage_refresh_regen", "rampage_refresh_cdr", "rampage_refresh_uptime",
    "mitosis_multiplier", "volley_bounces", "volley_iframe_hits_per_cast", "smite_targets_in_range",
    "boss_shield_downtime", "min_mana_regen", "min_cooldown_reduction", "min_move_speed", "main_damage_share_min",
    "max_secondaries", "secondary_min_value", "hit_interval_s", "oneshot_protection", "oneshot_heal_fraction",
    "oneshot_extra_cycles", "frozen_orb_additive", "avoid_lo", "avoid_hi", "speed_lo",
    "speed_hi", "castle_uptime_lo", "castle_uptime_hi", "survival_risk", "ranged_bonus", "ranged_reach",
    "hp_state_heuristic", "dice_percentile", "fang_boss_distance", "maw_pull_factor", "hyper_boss_crit_mult",
    "kills_per_s", "concentrate_effects_stripped", "secondary_flag_share", "score_swings", "mana_state_heuristic",
    "effect_negation_required", "rang_travel_blocks", "ultimate_shield_scalar_mult", "taunt_amp_half_above",
    "echo_inherits_rampage", "steel_inferno_boss_efficiency", "steel_inferno_pack_mean", "storm_boss_occupancy", "pd_sneak_window_s", "sneak_avoid_factor",
];

/// AffixRec.k codes by name (build.rs matches on the codes).
pub const AFFIX_KIND_NAMES: &[&str] = &["add", "ability_level", "talent_level", "luck", "none", "flag", "ability_mod", "special",
                                        "arcane_nova", "cloud", "on_damage", "steel_inferno"];
/// Presence flags by AffixRec.key of a flag affix (build.rs stats).
pub const FLAG_NAMES: &[&str] = &["lucky_thorns", "safer_space", "bloodthirst", "javelin_implode", "javelin_multi_hit",
                                  "storm_tracking"];
/// Stats.special slots.
pub const SPECIAL_NAMES: &[&str] = &["frost_nova_vulnerability", "fireball_recast", "frost_nova_on_damage", "javelin_implode_damage"];
/// TalentTier.ttype codes by talent type.
pub const TALENT_TYPE_NAMES: &[(&str, u8)] = &[
    ("gear_attribute", 1), ("high_health_gear_attribute", 11), ("stack_on_hit_talent", 2), ("stacking_gear_attribute", 3),
    ("mind_meld", 4), ("damage_lucky_hit", 5), ("execution_lucky_hit", 6), ("low_mana_healing_efficiency", 7),
    ("health_leech_lucky_hit", 8), ("low_health_resistance", 9), ("cast_on_hit", 10), ("low_health_damage", 12),
];
/// Per-ability multiplier kinds (Stats.amods).
pub const AMOD_KIND_NAMES: &[(&str, u8)] = &[("cooldown", 0), ("mana", 1)];
/// Debuff kinds (procs.py KIND_NAMES) and the procs.rs code of each.
pub const PROC_KIND_NAMES: &[(&str, u8)] = &[
    ("Vulnerable", crate::procs::VULNERABLE), ("Hypothermia", crate::procs::HYPOTHERMIA), ("Slowness", crate::procs::SLOWNESS),
    ("Poison", crate::procs::POISON), ("Wither", crate::procs::WITHER), ("Weakness", crate::procs::WEAKNESS),
    ("Bad Luck", crate::procs::UNLUCK), ("Fear", crate::procs::FEAR), ("Burn", crate::procs::BURN),
    ("Necromancy curse", crate::procs::CURSE), ("Totem of Hatred mark", crate::procs::MARK),
    ("Chilled", crate::procs::CHILLED),
];
/// Effect-cloud effect ids and their debuff kind (procs.CLOUD_KINDS).
pub const CLOUD_KIND_NAMES: &[(&str, u8)] = &[
    ("minecraft:poison", crate::procs::POISON), ("minecraft:wither", crate::procs::WITHER),
    ("minecraft:weakness", crate::procs::WEAKNESS), ("minecraft:slowness", crate::procs::SLOWNESS),
    ("minecraft:unluck", crate::procs::UNLUCK), ("the_vault:chilled", crate::procs::CHILLED),
];
/// Spec table rule codes (kernel.py RULE_CODES).
pub const RULE_NAMES: &[(&str, u8)] = &[("no", 0), ("yes", RULE_YES), ("lucky_bolt", RULE_LUCKY_BOLT)];
/// Cast-debuff classes (kernel.py CAST_DEBUFF_CLASSES; buffs.rs cast_debuffs matches on the codes).
pub const CAST_DEBUFF_NAMES: &[(&str, &str, u8)] = &[
    ("Totem of Hatred mark", "totem_interval", 1), ("Slowness", "maw_slowness", 2), ("Necromancy curse", "always", 3),
    ("Hypothermia", "share", 4),
];
/// Buff specs buffs.rs dispatches on: a buff spec outside this list would silently do nothing.
pub const BUFF_SPS: &[Sp] = &[
    Sp::Taunt_Base, Sp::Totem_Player_Damage, Sp::Rampage_Base, Sp::Rampage_Berserker, Sp::Rampage_Bloodlust,
    Sp::Rampage_Instinct, Sp::Battle_Cry_Base, Sp::Battle_Cry_Spectral_Strike, Sp::Battle_Cry_Lucky_Strike, Sp::Colossus_Base,
    Sp::Concentrate_Base, Sp::Totem_Mana_Regen, Sp::Shell_Porcupine, Sp::Nova_Slow, Sp::Empower_Base,
    Sp::Empower_Slowness_Aura, Sp::Sneaky_Getaway, Sp::Totem_Base, Sp::Mana_Shield_Base, Sp::UltimateShield_Base,
];

fn pairs(v: &[(&str, u8)]) -> Vec<(String, u8)> {
    let mut x: Vec<(String, u8)> = v.iter().map(|(n, c)| (n.to_string(), *c)).collect();
    x.sort();
    x
}

fn map_pairs(m: &HashMap<String, u8>) -> Vec<(String, u8)> {
    let mut x: Vec<(String, u8)> = m.iter().map(|(n, c)| (n.clone(), *c)).collect();
    x.sort();
    x
}

fn names(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

impl Contract {
    /// Every list must equal the kernel's; the kernel's knobs must be exported. Panics with every difference.
    pub fn check(&self, exported_knobs: &HashMap<String, f64>) {
        let mut errs = Vec::new();
        let mut eq = |what: &str, py: String, rs: String| {
            if py != rs {
                errs.push(format!("{}: model/kernel.py {} vs kernel {}", what, py, rs));
            }
        };
        eq("affix kinds", format!("{:?}", self.affix_kinds), format!("{:?}", names(AFFIX_KIND_NAMES)));
        eq("FLAG_KEYS", format!("{:?}", self.flag_keys), format!("{:?}", names(FLAG_NAMES)));
        eq("SPECIAL_KEYS", format!("{:?}", self.special_keys), format!("{:?}", names(SPECIAL_NAMES)));
        eq("TALENT_TYPES", format!("{:?}", map_pairs(&self.talent_types)), format!("{:?}", pairs(TALENT_TYPE_NAMES)));
        eq("AMOD_KINDS", format!("{:?}", map_pairs(&self.amod_kinds)), format!("{:?}", pairs(AMOD_KIND_NAMES)));
        let kinds: Vec<(String, u8)> = self.proc_kinds.iter().enumerate().map(|(i, n)| (n.clone(), i as u8)).collect();
        let mut kinds_rs: Vec<(String, u8)> = PROC_KIND_NAMES.iter().map(|(n, c)| (n.to_string(), *c)).collect();
        kinds_rs.sort_by_key(|x| x.1);
        eq("proc kinds", format!("{:?}", kinds), format!("{:?}", kinds_rs));
        eq("CLOUD_KINDS", format!("{:?}", map_pairs(&self.cloud_kinds)), format!("{:?}", pairs(CLOUD_KIND_NAMES)));
        let mut q = self.etch_queries.clone();
        q.sort();
        let mut q_rs: Vec<String> = ETQ_NAMES.iter().map(|(n, _)| n.to_string()).collect();
        q_rs.sort();
        eq("ETCH_QUERIES", format!("{:?}", q), format!("{:?}", q_rs));
        eq("CFG_KEYS", format!("{:?}", self.cfg_keys), format!("{:?}", names(KEY_NAMES)));
        eq("BUG_IDS", format!("{:?}", self.bug_ids), format!("{:?}", names(BUG_NAMES)));
        eq("RULE_CODES", format!("{:?}", map_pairs(&self.rule_codes)), format!("{:?}", pairs(RULE_NAMES)));
        let mut cd = self.cast_debuff_classes.clone();
        cd.sort();
        let mut cd_rs: Vec<(String, String, u8)> = CAST_DEBUFF_NAMES.iter().map(|(a, b, c)| (a.to_string(), b.to_string(), *c)).collect();
        cd_rs.sort();
        eq("CAST_DEBUFF_CLASSES", format!("{:?}", cd), format!("{:?}", cd_rs));
        let missing: Vec<&str> = KNOB_NAMES.iter().copied().filter(|k| !exported_knobs.contains_key(*k) || !self.knobs.iter().any(|x| x == k)).collect();
        if !missing.is_empty() {
            errs.push(format!("knobs the kernel reads but model/kernel.py does not export: {:?}", missing));
        }
        assert!(errs.is_empty(), "JSON contract out of sync with model/kernel.py:\n{}", errs.join("\n"));
    }
}

/// The kernel's side of the contract (the `contract` op): the same keys as kernel.contract_lists().
pub fn contract_json() -> serde_json::Value {
    let obj = |v: &[(&str, u8)]| -> serde_json::Value { v.iter().map(|(n, c)| (n.to_string(), serde_json::json!(c))).collect::<serde_json::Map<_, _>>().into() };
    let mut kinds: Vec<(&str, u8)> = PROC_KIND_NAMES.to_vec();
    kinds.sort_by_key(|x| x.1);
    serde_json::json!({
        "affix_kinds": AFFIX_KIND_NAMES, "flag_keys": FLAG_NAMES, "special_keys": SPECIAL_NAMES,
        "talent_types": obj(TALENT_TYPE_NAMES), "amod_kinds": obj(AMOD_KIND_NAMES),
        "proc_kinds": kinds.iter().map(|x| x.0).collect::<Vec<_>>(), "cloud_kinds": obj(CLOUD_KIND_NAMES),
        "etch_queries": ETQ_NAMES.iter().map(|x| x.0).collect::<Vec<_>>(), "cfg_keys": KEY_NAMES, "bug_ids": BUG_NAMES,
        "rule_codes": obj(RULE_NAMES),
        "cast_debuff_classes": CAST_DEBUFF_NAMES.iter().map(|(a, b, c)| serde_json::json!([a, b, c])).collect::<Vec<_>>(),
        "knobs": KNOB_NAMES,
        "buff_specs": BUFF_SPS.iter().map(|x| format!("{:?}", x)).collect::<Vec<_>>(),
    })
}

/// Spec table rule codes (kernel.py RULE_CODES): applies, or applies only with the Lucky Bolt etching.
pub const RULE_YES: u8 = 1;
pub const RULE_LUCKY_BOLT: u8 = 2;
/// Fang tables (kernel.py FANG_CODES) and reach rules (kernel.py REACH_CODES).
pub const FANG_WALL: u8 = 1;
pub const FANG_MAW: u8 = 2;
pub const REACH_PROJECTILE: u8 = 1;
pub const REACH_SELF_RADIUS: u8 = 2;
pub const REACH_ZERO: u8 = 3;
pub const REACH_FANG_LINE: u8 = 4;
/// Talent tier types (kernel.py TALENT_TYPES) that read the HP state.
pub const TT_LOW_HEALTH_RES: u8 = 9;
pub const TT_HIGH_HEALTH: u8 = 11;
pub const TT_LOW_HEALTH_DMG: u8 = 12;

fn make_family(combos: &HashMap<String, f64>, fam: &FamilyRaw) -> Family {
    let kind = if fam.kind == "melee" {
        let combo_avg = *combos.get(&fam.weapon).unwrap_or_else(|| panic!("unknown melee weapon {}", fam.weapon));
        FamKind::Melee { combo_avg, weapon: fam.weapon.clone() }
    } else if fam.kind == "rang" {
        FamKind::Rang
    } else {
        FamKind::Ability { main: fam.main as usize }
    };
    Family { id: fam.id.clone(), kind, mainhands: fam.mainhands.clone(), baseline: fam.baseline.clone() }
}

fn combo_avg(c: &[f64]) -> f64 {
    c.iter().sum::<f64>() / c.len() as f64
}

impl Problem {
    pub fn new(raw: CtxRaw, fam: FamilyRaw) -> Problem {
        raw.contract.check(&raw.knobs);
        use crate::build::*;
        let cap = |what: &str, n: usize, max: usize, constant: &str| {
            assert!(n <= max, "{} has {} entries, kernel capacity is {}: raise {} in kernel/src/build.rs", what, n, max, constant);
        };
        cap("attribute table", raw.attrs.len() + 1, MAX_ATTRS, "MAX_ATTRS");
        cap("talents", raw.talents.len(), MAX_TALENTS, "MAX_TALENTS");
        cap("ability specs", raw.specs.len(), MAX_SPECS, "MAX_SPECS");
        cap("greed nodes", raw.greed.len(), MAX_GREED, "MAX_GREED");
        cap("deck slots", raw.deck.len(), MAX_DECK, "MAX_DECK");
        for d in &raw.designs {
            cap("design slots", d.slots.len(), MAX_DECK, "MAX_DECK");
            assert!(d.t.len() == d.slots.len() && d.color.len() == d.slots.len() && d.opts.len() == d.slots.len()
                    && d.seed.len() == d.slots.len(), "deck design: per-slot lists differ in length");
            assert!(!d.csets.is_empty(), "deck design without a core set");
            for cs in &d.csets {
                assert!(cs.nslots.len() == d.slots.len(), "deck design: nitwit slot list length");
            }
            for (o, s) in d.opts.iter().zip(&d.seed) {
                assert!(!o.is_empty() && *s < o.len(), "deck design: empty slot options or seed out of range");
            }
        }
        for t in &raw.types {
            cap("implicit groups", t.implicit_groups.len(), MAX_IMPLICITS, "MAX_IMPLICITS");
            cap("prefixes", t.pre, MAX_SIDE, "MAX_SIDE");
            cap("suffixes", t.suf, MAX_SIDE, "MAX_SIDE");
            cap("prefix pool", t.prefix.len(), crate::search::MAX_POOL, "MAX_POOL (search.rs)");
            cap("suffix pool", t.suffix.len(), crate::search::MAX_POOL, "MAX_POOL (search.rs)");
        }
        for g in &raw.gods {
            cap("charm mod pool", g.mods.len(), crate::search::MAX_CHARM_POOL, "MAX_CHARM_POOL (search.rs)");
        }
        cap("trinket slots", 2 * raw.trinket_slots.iter().sum::<usize>(), MAX_TRINKETS, "MAX_TRINKETS");
        cap("charm mods", raw.charm_prefixes, MAX_MODS, "MAX_MODS");
        for t in &raw.talents {
            assert!(t.max < 256, "talent tier count must fit u8");
        }
        let attr_idx: HashMap<&str, usize> = raw.attrs.iter().enumerate().map(|(i, s)| (s.as_str(), i)).collect();
        let dummy = raw.attrs.len();
        let at = AttrIds { ids: DERIVED_ATTRS.iter().map(|n| *attr_idx.get(n).unwrap_or(&dummy)).collect() };
        let qmap: Vec<Etq> = raw.contract.etch_queries.iter().map(|n| {
            ETQ_NAMES.iter().find(|(s, _)| s == n).unwrap_or_else(|| panic!("unknown etching query {}", n)).1
        }).collect();
        let etchings = raw.etchings.iter().map(|e| {
            let mut q = [false; N_ETQ];
            let mut mask = 0u32;
            for &i in &e.q {
                q[qmap[i] as usize] = true;
                mask |= 1 << qmap[i] as u32;
            }
            Etching { num: e.num, dur: e.dur, truthy: e.truthy, q, mask, tl: e.tl, stacking: e.stacking, pool: e.pool.clone() }
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
            Spec { id: s.id.clone(), skill: s.skill, max: s.max, cost: s.cost.clone(), cfg: c, baseline_skill: s.baseline_skill, pd_ok: s.pd_ok }
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
            execution_strike: tid("Execution_Strike"), fanged_strike: tid("Fanged_Strike"), arcane_strike: tid("Arcane_Strike"), ethereal: tid("Ethereal"),
            cleave: tid("Cleave"), mana_steal: tid("Mana_Steal"), life_steal: tid("Life_Steal"), executioner: tid("Executioner"),
            hexbreaker: tid("Hexbreaker"), lightning_damage: tid("Lightning_Damage"), last_stand: tid("Last_Stand"),
            blizzard: tid("Blizzard"),
        };
        let k = &raw.knobs;
        let knobs = Knobs {
            god_banked_damage_taken: knob(k, "god_banked_damage_taken"), god_surrounded_mobs: knob(k, "god_surrounded_mobs"),
            god_vault_minutes: knob(k, "god_vault_minutes"), god_sneaky_effects: knob(k, "god_sneaky_effects"), power_dump_sqrt_ref: knob(k, "power_dump_sqrt_ref"),
            spectral_ap_per_stack: knob(k, "spectral_ap_per_stack"), pd_hold_drain: knob(k, "pd_hold_drain"),
            pd_sneak_window_s: knob(k, "pd_sneak_window_s"), sneak_avoid_factor: knob(k, "sneak_avoid_factor"),
            kill_time_s: knob(k, "kill_time_s"), survive_hits: knob(k, "survive_hits"),
            execution_cycle_cap: knob(k, "execution_cycle_cap"), pack_size: knob(k, "pack_size"),
            pack_radius: knob(k, "pack_radius"), base_skill_points: knob(k, "base_skill_points"),
            uptime_low_hp: knob(k, "uptime_low_hp"), uptime_low_mana: knob(k, "uptime_low_mana"),
            mana_state_heuristic: knob(k, "mana_state_heuristic") != 0.0, kills_per_s: knob(k, "kills_per_s"),
            concentrate_effects_stripped: knob(k, "concentrate_effects_stripped"), score_swings: knob(k, "score_swings") != 0.0,
            max_swings_per_s: knob(k, "max_swings_per_s"),
            weave_window_s: knob(k, "weave_window_s"),
            rampage_refresh_regen: knob(k, "rampage_refresh_regen"), rampage_refresh_cdr: knob(k, "rampage_refresh_cdr"),
            rampage_refresh_uptime: knob(k, "rampage_refresh_uptime"), mitosis_multiplier: knob(k, "mitosis_multiplier"),
            volley_bounces: knob(k, "volley_bounces"), volley_iframe_hits_per_cast: knob(k, "volley_iframe_hits_per_cast"),
            smite_targets_in_range: knob(k, "smite_targets_in_range"), boss_shield_downtime: knob(k, "boss_shield_downtime"),
            min_mana_regen: knob(k, "min_mana_regen"), min_cooldown_reduction: knob(k, "min_cooldown_reduction"),
            min_move_speed: knob(k, "min_move_speed"), main_damage_share_min: knob(k, "main_damage_share_min"), max_secondaries: knob(k, "max_secondaries") as usize, secondary_min_value: knob(k, "secondary_min_value"),
            hit_interval_s: knob(k, "hit_interval_s"), oneshot_protection: knob(k, "oneshot_protection") != 0.0,
            oneshot_heal_fraction: knob(k, "oneshot_heal_fraction"), oneshot_extra_cycles: knob(k, "oneshot_extra_cycles"),
            frozen_orb_additive: knob(k, "frozen_orb_additive") != 0.0,
            survival_risk: knob(k, "survival_risk"), avoid_lo: knob(k, "avoid_lo"), avoid_hi: knob(k, "avoid_hi"),
            speed_lo: knob(k, "speed_lo"), speed_hi: knob(k, "speed_hi"), ranged_bonus: knob(k, "ranged_bonus"),
            ranged_reach: knob(k, "ranged_reach"), castle_uptime_lo: knob(k, "castle_uptime_lo"),
            castle_uptime_hi: knob(k, "castle_uptime_hi"), hp_state_heuristic: knob(k, "hp_state_heuristic") != 0.0,
            dice_z: raw.dice_z, maw_pull_factor: knob(k, "maw_pull_factor"),
            hyper_boss_crit_mult: knob(k, "hyper_boss_crit_mult"),
            steel_inferno_boss_efficiency: knob(k, "steel_inferno_boss_efficiency"),
            steel_inferno_pack_mean: knob(k, "steel_inferno_pack_mean"),
            storm_boss_occupancy: knob(k, "storm_boss_occupancy"),
            ultimate_shield_scalar_mult: knob(k, "ultimate_shield_scalar_mult"),
            effect_negation_required: knob(k, "effect_negation_required") != 0.0, rang_travel_blocks: knob(k, "rang_travel_blocks"),
            taunt_amp_half_above: knob(k, "taunt_amp_half_above"), echo_inherits_rampage: knob(k, "echo_inherits_rampage") != 0.0,
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
        let spec_cls = raw.specs.iter().map(|s| s.cast_debuff).collect();
        let leech_spec = raw.specs.iter().map(|s| s.leech).collect();
        let lucky_spec = raw.specs.iter().map(|s| s.lucky).collect();
        let spec_pd_hold = raw.specs.iter().map(|s| s.pd_hold).collect();
        let spec_free = raw.specs.iter().map(|s| s.free_iframe).collect();
        let spec_spectral = raw.specs.iter().map(|s| s.spectral).collect();
        let spec_fang = raw.specs.iter().map(|s| s.fang).collect();
        let spec_reach = raw.specs.iter().map(|s| {
            let key = match &s.reach_key {
                Some(n) => KEY_NAMES.iter().position(|k| k == n).unwrap_or_else(|| panic!("reach key {} is not a config key", n)),
                None => 0,
            };
            (s.reach, key)
        }).collect();
        let hp_talents = raw.talents.iter().map(|t| t.tiers.iter().any(|x| matches!(x.ttype, TT_HIGH_HEALTH | TT_LOW_HEALTH_RES | TT_LOW_HEALTH_DMG))).collect();
        let weapon_combo: HashMap<String, f64> = raw.weapon_combos.iter().map(|(w, c)| (w.clone(), combo_avg(c))).collect();
        let type_combo = raw.types.iter().map(|t| weapon_combo.get(&t.name).copied()).collect();
        let spec_sp: Vec<Sp> = raw.specs.iter().map(|s| match Sp::from_name(&s.id) {
            Some(x) => x,
            None if s.baseline_skill => Sp::Other,
            None => panic!("spec {} has no kernel Sp variant: add it to problem.rs (and its model.rs or buffs.rs arm)", s.id),
        }).collect();
        for &b in &raw.buff_specs {
            assert!(BUFF_SPS.contains(&spec_sp[b]), "buff spec {} has no buffs.rs arm listed in BUFF_SPS", raw.specs[b].id);
        }
        let implode_spec = spec_sp.iter().position(|&x| x == Sp::Mana_Shield_Implode);
        let family = make_family(&weapon_combo, &fam);
        let grid = raw.boss_grid.with_logs(knobs.kill_time_s);
        Problem {
            affix_kind: raw.affixes.iter().map(|a| a.k).collect(), affixes: raw.affixes, etchings, lvl_keys: raw.lvl_keys, lvl_hit,
            tal_keys: raw.tal_keys, types: raw.types, offhand_types: raw.offhand_types, necklace_type: raw.necklace_type,
            armor_types: raw.armor_types, trinkets: raw.trinkets, trinket_slots: raw.trinket_slots,
            trinket_fusion: raw.trinket_fusion, gods: raw.gods, charm_prefixes: raw.charm_prefixes, deck: raw.deck,
            designs: raw.designs.into_iter().map(crate::deck::Design::new).collect(),
            talents: raw.talents, gates: raw.gates, amod_hit, uniques: raw.uniques, unique_seals: raw.unique_seals,
            unique_max: raw.unique_max, unique_seals_max: raw.unique_seals_max, heal_specs: raw.heal_specs,
            leech_spec, lucky_spec, spec_pd_hold, spec_free, spec_spectral, spec_fang, spec_reach, fang_table: raw.fang_table, hp_talents, specs,
            buff_specs: raw.buff_specs, sec_specs: raw.sec_specs, greed: raw.greed, greed_children, greed_budget: raw.greed_budget,
            prestige: raw.prestige, knobs, caps: raw.caps, grid, seals: raw.seals, unusual: raw.unusual,
            mode_bugged: raw.mode_bugged, at, nt, talent_names: raw.talent_names, spec_names: raw.spec_names, spec_by_name, spec_cls, weapon_combo, type_combo, spec_sp, implode_spec,
            lanes: if raw.godtree.is_some() { 1 } else { 4 }, fam: family, godtree: raw.godtree.map(|mut g| {
                g.ix = crate::gods::GfxIx::new(&g.names);
                g.fast = crate::gods::GodFast::new(&g);
                g.gp = crate::gods::GfxPar::new(&g);
                g
            }),
            ancient_max: raw.ancient_max, clouds: raw.clouds, echo: raw.echo, echo_reverb: raw.echo_reverb, boots_mj: raw.boots_mj, feather: raw.feather, boots_slot: raw.boots_slot,
        }
    }

    /// Same context with another family (batch jobs share one parsed context).
    pub fn with_family(&self, fam: &FamilyRaw) -> Problem {
        let mut p = self.clone();
        p.fam = make_family(&p.weapon_combo, fam);
        p
    }

    /// Combo average of the melee weapon of gear type `ty` (woven swings).
    pub fn type_combo_avg(&self, ty: usize) -> f64 {
        self.type_combo[ty].unwrap_or_else(|| panic!("gear type {} has no melee combo", self.types[ty].name))
    }

    pub fn is_melee(&self) -> bool {
        matches!(self.fam.kind, FamKind::Melee { .. })
    }
}
