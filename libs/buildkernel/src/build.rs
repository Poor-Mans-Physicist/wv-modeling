//! Build representation and stat assembly (model/build.py, model/stats.py).
//!
//! Builds are fixed-size `Copy` values so a candidate costs one memcpy. Additive stat totals (`Lin`) are kept for the
//! current build and patched for a candidate by diffing the two builds (delta scoring); everything non-additive
//! (levels, luck, dice, vanilla multipliers, talents) is recomputed from the build each time.

use crate::problem::*;
use serde::{Deserialize, Serialize};

pub const N_SLOTS: usize = 7;
pub const MAINHAND: usize = 4;
pub const OFFHAND: usize = 5;
pub const NECKLACE: usize = 6;

pub const MAX_ATTRS: usize = 128;
pub const MAX_TALENTS: usize = 64;
pub const MAX_SPECS: usize = 96;
pub const MAX_GREED: usize = 512;
pub const MAX_DECK: usize = 64;
pub const MAX_IMPLICITS: usize = 8;
pub const MAX_SIDE: usize = 6;
pub const MAX_TRINKETS: usize = 16;
pub const MAX_MODS: usize = 8;
pub const MAX_LEVELS: usize = 24;
pub const MAX_UNIQUE_AFFIXES: usize = 16;
pub const MAX_AMODS: usize = 12;

/// Inline list of small indices; unused cells stay 0 so derived equality compares contents only.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Small<const N: usize> {
    len: u8,
    v: [u16; N],
}

impl<const N: usize> Small<N> {
    pub const fn new() -> Self {
        Small { len: 0, v: [0; N] }
    }
    pub fn from_slice(s: &[usize]) -> Self {
        let mut x = Self::new();
        for &e in s {
            x.push(e);
        }
        x
    }
    #[inline]
    pub fn len(&self) -> usize {
        self.len as usize
    }
    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    #[inline]
    pub fn get(&self, i: usize) -> usize {
        debug_assert!(i < self.len());
        self.v[i] as usize
    }
    #[inline]
    pub fn set(&mut self, i: usize, x: usize) {
        debug_assert!(i < self.len());
        self.v[i] = x as u16;
    }
    pub fn push(&mut self, x: usize) {
        assert!(self.len() < N, "inline list full (capacity {}); raise the MAX_ constant in kernel/src/build.rs", N);
        self.v[self.len()] = x as u16;
        self.len += 1;
    }
    pub fn pop(&mut self) {
        if self.len > 0 {
            self.len -= 1;
            self.v[self.len()] = 0;
        }
    }
    pub fn remove(&mut self, i: usize) {
        for j in i..self.len() - 1 {
            self.v[j] = self.v[j + 1];
        }
        self.pop();
    }
    #[inline]
    pub fn contains(&self, x: usize) -> bool {
        self.v[..self.len()].iter().any(|&e| e as usize == x)
    }
    #[inline]
    pub fn iter(&self) -> impl Iterator<Item = usize> + '_ {
        self.v[..self.len()].iter().map(|&e| e as usize)
    }
    pub fn to_vec(&self) -> Vec<usize> {
        self.iter().collect()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Bits {
    w: [u64; MAX_GREED / 64],
}

impl Bits {
    pub const fn new() -> Self {
        Bits { w: [0; MAX_GREED / 64] }
    }
    #[inline]
    pub fn has(&self, i: usize) -> bool {
        self.w[i >> 6] >> (i & 63) & 1 == 1
    }
    #[inline]
    pub fn set(&mut self, i: usize) {
        self.w[i >> 6] |= 1 << (i & 63);
    }
    #[inline]
    pub fn clear(&mut self, i: usize) {
        self.w[i >> 6] &= !(1 << (i & 63));
    }
    #[inline]
    pub fn count(&self) -> usize {
        self.w.iter().map(|x| x.count_ones() as usize).sum()
    }
    pub fn iter(&self) -> impl Iterator<Item = usize> + '_ {
        self.w.iter().enumerate().flat_map(|(k, &x)| {
            let mut x = x;
            std::iter::from_fn(move || {
                if x == 0 {
                    None
                } else {
                    let t = x.trailing_zeros() as usize;
                    x &= x - 1;
                    Some(k * 64 + t)
                }
            })
        })
    }
    /// Bits set in exactly one of the two sets: (only in self, only in other).
    pub fn diff(&self, o: &Bits) -> (Bits, Bits) {
        let mut a = Bits::new();
        let mut b = Bits::new();
        for k in 0..self.w.len() {
            a.w[k] = self.w[k] & !o.w[k];
            b.w[k] = o.w[k] & !self.w[k];
        }
        (a, b)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Item {
    pub ty: u16,
    pub implicits: Small<MAX_IMPLICITS>,
    pub prefixes: Small<MAX_SIDE>,
    pub suffixes: Small<MAX_SIDE>,
    pub seal: Option<u16>,
    pub unusual: Option<u16>,
    pub etching: Option<u16>,
    /// Unique worn in place of the normal piece (index into Problem.uniques); the normal piece's affixes stay stored.
    pub unique: Option<u16>,
    /// Unique explicit replaced by its seal (index into Unique.affixes).
    pub udrop: Option<u8>,
    /// The normal piece's seal, etching and unusual while a unique is worn (model/search.py equip_unique).
    pub shadow: [Option<u16>; 3],
}

impl Item {
    pub fn new(ty: usize) -> Item {
        Item {
            ty: ty as u16, implicits: Small::new(), prefixes: Small::new(), suffixes: Small::new(), seal: None, unusual: None,
            etching: None, unique: None, udrop: None, shadow: [None; 3],
        }
    }
    #[inline]
    pub fn ty(&self) -> usize {
        self.ty as usize
    }
    /// Affixes in model/build.py order: implicits, prefixes, suffixes, seal, unusual (a unique: its affixes minus the
    /// sealed-over one, then the seal).
    #[inline]
    pub fn for_affixes<F: FnMut(usize)>(&self, p: &Problem, mut f: F) {
        if let Some(u) = self.unique {
            let drop = self.udrop.map(|x| x as usize);
            for (i, &a) in p.uniques[u as usize].affixes.iter().enumerate() {
                if Some(i) != drop {
                    f(a);
                }
            }
            if let Some(a) = self.seal {
                f(a as usize);
            }
            return;
        }
        for a in self.implicits.iter() {
            f(a);
        }
        for a in self.prefixes.iter() {
            f(a);
        }
        for a in self.suffixes.iter() {
            f(a);
        }
        if let Some(a) = self.seal {
            f(a as usize);
        }
        if let Some(a) = self.unusual {
            f(a as usize);
        }
    }
    pub fn side(&self, side: usize) -> &Small<MAX_SIDE> {
        if side == 0 { &self.prefixes } else { &self.suffixes }
    }
    pub fn side_mut(&mut self, side: usize) -> &mut Small<MAX_SIDE> {
        if side == 0 { &mut self.prefixes } else { &mut self.suffixes }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Build {
    pub items: [Item; N_SLOTS],
    pub trinkets: Small<MAX_TRINKETS>,
    pub god: u16,
    pub mods: Small<MAX_MODS>,
    /// Option index within each deck slot (not the card id).
    pub deck: Small<MAX_DECK>,
    pub talents: [u8; MAX_TALENTS],
    pub abilities: [u8; MAX_SPECS],
    pub greed: Bits,
    pub bugs: u32,
    /// Ability builds only: melee swings woven between casts.
    pub weave: bool,
}

/// JSON shape shared with model/kernel.py (card ids in the deck, plain lists elsewhere).
#[derive(Clone, Serialize, Deserialize)]
pub struct ItemDoc {
    #[serde(rename = "type")]
    pub ty: usize,
    pub implicits: Vec<usize>,
    pub prefixes: Vec<usize>,
    pub suffixes: Vec<usize>,
    pub seal: Option<usize>,
    pub unusual: Option<usize>,
    pub etching: Option<usize>,
    #[serde(default)]
    pub unique: Option<usize>,
    #[serde(default)]
    pub udrop: Option<usize>,
    #[serde(default)]
    pub shadow: Option<[Option<usize>; 3]>,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct BuildDoc {
    pub items: Vec<ItemDoc>,
    pub trinkets: Vec<usize>,
    pub god: usize,
    pub mods: Vec<usize>,
    pub deck: Vec<usize>,
    pub talents: Vec<usize>,
    pub abilities: Vec<usize>,
    pub greed: Vec<usize>,
    pub bugs: u32,
    #[serde(default)]
    pub weave: bool,
}

impl Build {
    pub fn empty() -> Build {
        Build {
            items: [Item::new(0); N_SLOTS], trinkets: Small::new(), god: 0, mods: Small::new(), deck: Small::new(),
            talents: [0; MAX_TALENTS], abilities: [0; MAX_SPECS], greed: Bits::new(), bugs: 0, weave: false,
        }
    }

    pub fn from_doc(d: &BuildDoc, p: &Problem) -> Build {
        let mut b = Build::empty();
        for (k, it) in d.items.iter().enumerate() {
            b.items[k] = Item {
                ty: it.ty as u16, implicits: Small::from_slice(&it.implicits), prefixes: Small::from_slice(&it.prefixes),
                suffixes: Small::from_slice(&it.suffixes), seal: it.seal.map(|x| x as u16), unusual: it.unusual.map(|x| x as u16),
                etching: it.etching.map(|x| x as u16), unique: it.unique.map(|x| x as u16), udrop: it.udrop.map(|x| x as u8),
                shadow: it.shadow.map(|s| [s[0].map(|x| x as u16), s[1].map(|x| x as u16), s[2].map(|x| x as u16)]).unwrap_or([None; 3]),
            };
        }
        b.trinkets = Small::from_slice(&d.trinkets);
        b.god = d.god as u16;
        b.mods = Small::from_slice(&d.mods);
        for (i, &card) in d.deck.iter().enumerate() {
            b.deck.push(p.deck[i].iter().position(|o| o.card == card).expect("deck card not offered in its slot"));
        }
        for (t, &n) in d.talents.iter().enumerate() {
            b.talents[t] = n as u8;
        }
        for (s, &n) in d.abilities.iter().enumerate() {
            b.abilities[s] = n as u8;
        }
        for &g in &d.greed {
            b.greed.set(g);
        }
        b.bugs = d.bugs;
        b.weave = d.weave;
        b
    }

    pub fn to_doc(&self, p: &Problem) -> BuildDoc {
        BuildDoc {
            items: self.items.iter().map(|it| ItemDoc {
                ty: it.ty(), implicits: it.implicits.to_vec(), prefixes: it.prefixes.to_vec(), suffixes: it.suffixes.to_vec(),
                seal: it.seal.map(|x| x as usize), unusual: it.unusual.map(|x| x as usize), etching: it.etching.map(|x| x as usize),
                unique: it.unique.map(|x| x as usize), udrop: it.udrop.map(|x| x as usize),
                shadow: it.unique.map(|_| [it.shadow[0].map(|x| x as usize), it.shadow[1].map(|x| x as usize), it.shadow[2].map(|x| x as usize)]),
            }).collect(),
            trinkets: self.trinkets.to_vec(), god: self.god as usize, mods: self.mods.to_vec(),
            deck: self.deck.iter().enumerate().map(|(i, o)| p.deck[i][o].card).collect(),
            talents: self.talents[..p.talents.len()].iter().map(|&x| x as usize).collect(),
            abilities: self.abilities[..p.specs.len()].iter().map(|&x| x as usize).collect(),
            greed: self.greed.iter().collect(), bugs: self.bugs, weave: self.weave,
        }
    }

    #[inline]
    pub fn bug(&self, b: Bug) -> bool {
        self.bugs & (1 << (b as u32)) != 0
    }

    #[inline]
    pub fn tal(&self, t: usize) -> usize {
        self.talents[t] as usize
    }

    #[inline]
    pub fn abil(&self, s: usize) -> usize {
        self.abilities[s] as usize
    }

    pub fn skill_points_total(&self, p: &Problem) -> f64 {
        p.knobs.base_skill_points + self.greed.iter().map(|n| p.greed[n].sp).sum::<f64>()
    }

    pub fn skill_points_spent(&self, p: &Problem) -> f64 {
        let mut s = 0.0;
        for t in 0..p.talents.len() {
            let n = self.tal(t);
            if n > 0 {
                s += p.talents[t].cost[n.min(p.talents[t].cost.len() - 1)];
            }
        }
        for a in 0..p.specs.len() {
            let n = self.abil(a);
            if n > 0 {
                s += p.specs[a].cost[n.min(p.specs[a].cost.len() - 1)];
            }
        }
        s
    }

    pub fn points_ok(&self, p: &Problem) -> bool {
        self.skill_points_spent(p) <= self.skill_points_total(p)
    }

    /// First etching (slot order) matching the query: (value-or-True as a number, truthiness).
    pub fn etch(&self, p: &Problem, q: Etq) -> Option<(Option<f64>, bool)> {
        for it in &self.items {
            if let Some(e) = it.etching {
                let er = &p.etchings[e as usize];
                if er.q[q as usize] {
                    return Some((er.num, er.truthy));
                }
            }
        }
        None
    }

    /// Python `etch_value(...)` used in a truthy test followed by a numeric read.
    pub fn etch_num(&self, p: &Problem, q: Etq) -> Result<Option<f64>, String> {
        match self.etch(p, q) {
            Some((n, true)) => n.map(Some).ok_or_else(|| "etching value not numeric".to_string()),
            _ => Ok(None),
        }
    }
}

pub fn offhand_counts(b: &Build, p: &Problem) -> bool {
    !(!b.bug(Bug::OffhandStaff) && p.types[b.items[MAINHAND].ty()].name == "battlestaff")
}

/// Sum of every additive source (affixes of counted items, trinket adds, charm, deck, greed, prestige).
#[derive(Clone, Copy)]
pub struct Lin {
    pub v: [f64; MAX_ATTRS],
}

/// Attribute slots a delta update wrote (deduplicated lazily: duplicates only cost a repeated check).
struct Touched {
    n: usize,
    a: [u16; 96],
    overflow: bool,
}

impl Touched {
    #[inline]
    fn mark(&mut self, a: usize) {
        if self.n < self.a.len() {
            self.a[self.n] = a as u16;
            self.n += 1;
        } else {
            self.overflow = true;
        }
    }
}

#[inline]
fn item_lin(it: &Item, p: &Problem, v: &mut [f64; MAX_ATTRS], sign: f64, t: &mut Option<&mut Touched>) {
    it.for_affixes(p, |a| {
        if p.affix_kind[a] == 0 {
            let r = &p.affixes[a];
            v[r.a] += sign * r.v;
            if let Some(t) = t.as_deref_mut() {
                t.mark(r.a);
            }
        }
    });
}

#[inline]
fn trinkets_lin(b: &Build, p: &Problem, v: &mut [f64; MAX_ATTRS], sign: f64, t: &mut Option<&mut Touched>) {
    for tr in b.trinkets.iter() {
        for e in &p.trinkets[tr].effects {
            let (a, x) = match *e {
                TrinketEff::Add { a, v: x } => (a, x),
                TrinketEff::Vanilla { a, v: x, op } if op != 2 => (a, x),
                _ => continue,
            };
            v[a] += sign * x;
            if let Some(t) = t.as_deref_mut() {
                t.mark(a);
            }
        }
    }
}

#[inline]
fn charm_lin(b: &Build, p: &Problem, v: &mut [f64; MAX_ATTRS], sign: f64, t: &mut Option<&mut Touched>) {
    for m in b.mods.iter() {
        let cm = &p.gods[b.god as usize].mods[m];
        v[cm.a] += sign * cm.v;
        if let Some(t) = t.as_deref_mut() {
            t.mark(cm.a);
        }
    }
}

pub fn lin_full(b: &Build, p: &Problem) -> Lin {
    let mut v = [0.0; MAX_ATTRS];
    let off = offhand_counts(b, p);
    for (slot, it) in b.items.iter().enumerate() {
        if slot != OFFHAND || off {
            item_lin(it, p, &mut v, 1.0, &mut None);
        }
    }
    trinkets_lin(b, p, &mut v, 1.0, &mut None);
    charm_lin(b, p, &mut v, 1.0, &mut None);
    for (i, o) in b.deck.iter().enumerate() {
        let d = &p.deck[i][o];
        v[d.a] += d.c;
    }
    for n in b.greed.iter() {
        for &(a, x) in &p.greed[n].entries {
            v[a] += x;
        }
    }
    for &(a, x) in &p.prestige.adds {
        v[a] += x;
    }
    Lin { v }
}

/// Totals for `b` from the totals of `prev` (delta scoring): only components that differ are subtracted and re-added.
pub fn lin_delta(prev_b: &Build, prev: &Lin, b: &Build, p: &Problem) -> Lin {
    let mut out = *prev;
    let v = &mut out.v;
    let mut touched = Touched { n: 0, a: [0; 96], overflow: false };
    let mut t = Some(&mut touched);
    let (off0, off1) = (offhand_counts(prev_b, p), offhand_counts(b, p));
    for slot in 0..N_SLOTS {
        let c0 = slot != OFFHAND || off0;
        let c1 = slot != OFFHAND || off1;
        if c0 == c1 && prev_b.items[slot] == b.items[slot] {
            continue;
        }
        if c0 {
            item_lin(&prev_b.items[slot], p, v, -1.0, &mut t);
        }
        if c1 {
            item_lin(&b.items[slot], p, v, 1.0, &mut t);
        }
    }
    if prev_b.trinkets != b.trinkets {
        trinkets_lin(prev_b, p, v, -1.0, &mut t);
        trinkets_lin(b, p, v, 1.0, &mut t);
    }
    if prev_b.god != b.god || prev_b.mods != b.mods {
        charm_lin(prev_b, p, v, -1.0, &mut t);
        charm_lin(b, p, v, 1.0, &mut t);
    }
    if prev_b.deck != b.deck {
        for i in 0..b.deck.len() {
            let (o0, o1) = (prev_b.deck.get(i), b.deck.get(i));
            if o0 != o1 {
                let (d0, d1) = (&p.deck[i][o0], &p.deck[i][o1]);
                v[d0.a] -= d0.c;
                v[d1.a] += d1.c;
                touched.mark(d0.a);
                touched.mark(d1.a);
            }
        }
    }
    if prev_b.greed != b.greed {
        let (gone, new) = prev_b.greed.diff(&b.greed);
        for n in gone.iter() {
            for &(a, x) in &p.greed[n].entries {
                v[a] -= x;
                touched.mark(a);
            }
        }
        for n in new.iter() {
            for &(a, x) in &p.greed[n].entries {
                v[a] += x;
                touched.mark(a);
            }
        }
    }
    if touched.overflow {
        for x in v.iter_mut() {
            if x.abs() < 1e-12 {
                *x = 0.0;
            }
        }
    } else {
        for &a in &touched.a[..touched.n] {
            let x = &mut v[a as usize];
            if x.abs() < 1e-12 {
                *x = 0.0;
            }
        }
    }
    out
}

pub struct Stats {
    pub attr: [f64; MAX_ATTRS],
    pub van: [(u16, f64); 8],
    pub n_van: usize,
    pub ability_levels: [(u16, f64); MAX_LEVELS],
    pub n_al: usize,
    pub talent_levels: [(u16, f64); MAX_LEVELS],
    pub n_tl: usize,
    pub mind_meld: bool,
    pub masterful: bool,
    pub berserk_power: bool,
    pub dice: Option<(f64, f64)>,
    pub luck: i32,
    pub talent_tier: [u8; MAX_TALENTS],
    pub extra_stacks: f64,
    /// Per-ability multipliers in assembly order: (key, kind 0 cooldown / 1 mana, amount).
    pub amods: [(u16, u8, f64); MAX_AMODS],
    pub n_am: usize,
    pub lucky_thorns: bool,
    pub safer_space: bool,
    pub bloodthirst: bool,
    /// Summed ability special modifications: [frost nova vulnerability level, fireball recast chance].
    pub special: [f64; 2],
    pub methodical: f64,
}

impl Stats {
    #[inline]
    pub fn g(&self, p: &Problem, a: A) -> f64 {
        self.attr[p.at.ids[a as usize]]
    }
    #[inline]
    pub fn tier(&self, t: Option<usize>) -> usize {
        t.map(|i| self.talent_tier[i] as usize).unwrap_or(0)
    }
    #[inline]
    pub fn vanilla(&self, a: usize) -> f64 {
        let mut m = 1.0;
        for &(x, f) in &self.van[..self.n_van] {
            if x as usize == a {
                m *= f;
            }
        }
        m
    }
    pub fn ability_levels(&self) -> &[(u16, f64)] {
        &self.ability_levels[..self.n_al]
    }
    fn push_al(&mut self, k: usize, c: f64) {
        assert!(self.n_al < MAX_LEVELS, "too many ability-level sources; raise MAX_LEVELS");
        self.ability_levels[self.n_al] = (k as u16, c);
        self.n_al += 1;
    }
    pub fn amods(&self) -> &[(u16, u8, f64)] {
        &self.amods[..self.n_am]
    }
    fn push_am(&mut self, k: usize, kind: i32, v: f64) {
        assert!(self.n_am < MAX_AMODS, "too many per-ability multipliers; raise MAX_AMODS");
        self.amods[self.n_am] = (k as u16, kind as u8, v);
        self.n_am += 1;
    }
    fn push_tl(&mut self, k: usize, c: f64) {
        assert!(self.n_tl < MAX_LEVELS, "too many talent-level sources; raise MAX_LEVELS");
        self.talent_levels[self.n_tl] = (k as u16, c);
        self.n_tl += 1;
    }
}

pub struct Derived {
    pub attack_damage: f64,
    pub attack_speed: f64,
    pub ability_power: f64,
    pub damage_increase: f64,
    pub lucky_hit_chance: f64,
    pub aoe_multiplier: f64,
    pub health: f64,
    pub armor: f64,
    pub resistance: f64,
    pub resistance_cap: f64,
    pub block: f64,
    pub dodge: f64,
    pub mana_max: f64,
    pub mana_regen: f64,
    pub mana_regen_vt: f64,
    pub cooldown_reduction: f64,
    pub echo_chance: f64,
    pub echo_damage: f64,
    pub execution: f64,
    pub chain: f64,
    pub on_hit_aoe: f64,
    pub double_hit: f64,
    pub relentless: f64,
    pub third_attack: f64,
    pub effect_duration: f64,
    pub healing_effectiveness: f64,
    pub leech: f64,
    pub thorns_flat: f64,
    pub thorns_pct: f64,
    pub thorns_scaling: f64,
    pub ap_scaling: f64,
    pub ap_flat: f64,
    pub castle: f64,
}

#[derive(Default, Clone)]
pub struct Fallbacks {
    pub trinket_op: u64,
    pub ehp_zero: u64,
    pub eval_error: u64,
    pub holes_used: Vec<bool>,
    pub first_error: Option<String>,
}

impl Fallbacks {
    pub fn merge(&mut self, o: &Fallbacks) {
        self.trinket_op += o.trinket_op;
        self.ehp_zero += o.ehp_zero;
        self.eval_error += o.eval_error;
        if self.holes_used.len() < o.holes_used.len() {
            self.holes_used.resize(o.holes_used.len(), false);
        }
        for (i, &u) in o.holes_used.iter().enumerate() {
            self.holes_used[i] |= u;
        }
        if self.first_error.is_none() {
            self.first_error = o.first_error.clone();
        }
    }
}

/// Non-additive parts of assembly on top of the additive totals, then talents (model/build.py assemble + apply_talents).
pub fn stats(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks) -> Stats {
    let mut st = Stats {
        attr: lin.v, van: [(0, 1.0); 8], n_van: 0, ability_levels: [(0, 0.0); MAX_LEVELS], n_al: 0,
        talent_levels: [(0, 0.0); MAX_LEVELS], n_tl: 0, mind_meld: false, masterful: p.prestige.masterful,
        berserk_power: p.prestige.berserk, dice: None, luck: 0, talent_tier: [0; MAX_TALENTS], extra_stacks: 0.0,
        amods: [(0, 0, 0.0); MAX_AMODS], n_am: 0, lucky_thorns: false, safer_space: false, bloodthirst: false, special: [0.0; 2], methodical: 0.0,
    };
    let off = offhand_counts(b, p);
    for (slot, it) in b.items.iter().enumerate() {
        if slot == OFFHAND && !off {
            continue;
        }
        it.for_affixes(p, |a| {
            let k = p.affix_kind[a];
            if k == 0 || k == 4 {
                return;
            }
            let r = &p.affixes[a];
            match k {
                1 => st.push_al(r.key, r.lc),
                2 => st.push_tl(r.key, r.lc),
                5 => match r.key {
                    0 => st.lucky_thorns = true,
                    1 => st.safer_space = true,
                    _ => st.bloodthirst = true,
                },
                7 => st.special[r.key] += r.v,
                6 => st.push_am(r.key, r.amp, r.v),
                _ => st.luck = st.luck.max(r.amp),
            }
        });
        if let Some(e) = it.etching {
            if let Some((k, c)) = p.etchings[e as usize].tl {
                st.push_tl(k, c);
            }
        }
    }
    for t in b.trinkets.iter() {
        for e in &p.trinkets[t].effects {
            match *e {
                TrinketEff::Vanilla { a, v, op } => {
                    if op == 2 {
                        assert!(st.n_van < 8, "too many vanilla multipliers");
                        st.van[st.n_van] = (a as u16, 1.0 + v);
                        st.n_van += 1;
                    } else {
                        fb.trinket_op += 1;
                    }
                }
                TrinketEff::Lvl { key, v } => st.push_al(key, v),
                TrinketEff::Dice { lo, hi } => st.dice = Some((lo, hi)),
                TrinketEff::Luck { n } => st.luck = st.luck.max(n),
                TrinketEff::Add { .. } => {}
            }
        }
    }
    apply_talents(b, p, &mut st);
    st
}

pub fn talent_effective_tier(p: &Problem, st: &Stats, tid: usize, learned: usize) -> usize {
    if learned == 0 {
        return 0;
    }
    let n = p.talents[tid].tiers.len() as f64;
    let mut bonus = 0.0;
    for &(k, ch) in &st.talent_levels[..st.n_tl] {
        let tk = &p.tal_keys[k as usize];
        if tk.all || tk.talent == tid as i64 {
            bonus += ch;
        }
    }
    n.min(learned as f64 + bonus) as usize
}

fn apply_talents(b: &Build, p: &Problem, st: &mut Stats) {
    let mut stack = [0u8; MAX_TALENTS];
    for tid in 0..p.talents.len() {
        let learned = b.tal(tid);
        if learned == 0 {
            continue;
        }
        let eff = talent_effective_tier(p, st, tid, learned);
        let tier = &p.talents[tid].tiers[eff - 1];
        match tier.ttype {
            1 => {
                if tier.extra {
                    st.extra_stacks += tier.v;
                } else {
                    st.attr[tier.a as usize] += tier.v;
                }
            }
            2 | 3 => stack[tid] = eff as u8,
            4 => st.mind_meld = true,
            7 => st.methodical = tier.ahe.expect("Methodical tier without additionalHealingEfficiency") * p.knobs.uptime_low_mana,
            _ => {}
        }
        st.talent_tier[tid] = eff as u8;
    }
    let extra = st.extra_stacks;
    let melee = p.is_melee();
    for tid in 0..p.talents.len() {
        if stack[tid] == 0 {
            continue;
        }
        let tier = &p.talents[tid].tiers[stack[tid] as usize - 1];
        let stacks = tier.max_stacks + extra;
        let mut uptime = 1.0;
        if tier.ttype == 3 {
            uptime = p.knobs.uptime_kill_stacks;
        }
        if tier.ttype == 2 && !melee {
            uptime = 0.0;
        }
        if uptime > 0.0 {
            st.attr[tier.a as usize] += tier.v * stacks * uptime;
        }
    }
}

pub fn derive(st: &Stats, p: &Problem, mana_cap: bool) -> Derived {
    let g = |a: A| st.g(p, a);
    let van = |a: A| st.vanilla(p.at.ids[a as usize]);
    let attack_damage = (1.0 + g(A::attack_damage)) * van(A::v_attack_damage);
    let attack_speed = f64::max(0.1, (4.0 + g(A::attack_speed)) * (1.0 + g(A::attack_speed_percent)));
    let mut ap = g(A::ability_power);
    ap *= 1.0 + g(A::ability_power_percent);
    ap *= 1.0 + g(A::ability_power_percentile);
    let mut lucky = g(A::lucky_hit_chance) * (1.0 + g(A::lucky_hit_chance_percentile) + g(A::jester_lucky_hit_chance_percentile));
    let luck_mult = if st.luck > 0 { 1.15f64.powf(st.luck as f64) } else { 1.0 };
    if st.luck > 0 {
        lucky *= luck_mult;
    }
    let lucky_hit_chance = lucky.min(p.caps.lucky);
    let aoe_multiplier = f64::min(1.0 + g(A::area_of_effect), 1.0 + p.caps.aoe);
    let health = (20.0 + g(A::health)) * (1.0 + g(A::health_percentile)) * van(A::v_max_health);
    let armor = g(A::armor) * (1.0 + g(A::armor_percentile));
    let res_cap = f64::min(0.95, 0.5 + g(A::resistance_cap));
    let resistance = g(A::resistance).min(res_cap);
    let blk_cap = f64::min(0.95, 0.6 + g(A::block_cap));
    let mut blk = g(A::block);
    if st.luck > 0 {
        blk *= luck_mult;
    }
    let block = blk.min(blk_cap);
    let mut dodge = g(A::dodge_percent);
    if st.luck > 0 {
        dodge *= luck_mult;
    }
    let dodge = dodge.min(0.95);
    let mut mana_max = (100.0 + g(A::mana_additive)) * (1.0 + g(A::mana_additive_percentile)) * van(A::v_mana_max);
    if mana_cap {
        mana_max = mana_max.min(4096.0);
    }
    let mana_regen_vt = van(A::v_mana_regen);
    let mana_regen = 1.0 * (1.0 + g(A::mana_regen)) * mana_regen_vt;
    let mut cdr = g(A::cooldown_reduction) * (1.0 + g(A::cooldown_reduction_percentile));
    if st.mind_meld {
        cdr += (mana_max / 50.0 + 1e-9).floor() * 0.01;
    }
    let cdr_cap = f64::min(0.95, 0.8 + g(A::cooldown_reduction_cap));
    let bloodthirst = st.bloodthirst;
    let he = 1.0 + g(A::healing_effectiveness) + st.methodical;
    Derived {
        attack_damage, attack_speed, ability_power: ap, damage_increase: g(A::damage_increase), lucky_hit_chance,
        aoe_multiplier, health, armor, resistance, resistance_cap: res_cap, block, dodge, mana_max, mana_regen, mana_regen_vt,
        cooldown_reduction: cdr.min(cdr_cap), echo_chance: g(A::echoing_chance).min(1.0), echo_damage: g(A::echoing_damage),
        execution: g(A::execution_damage), chain: g(A::on_hit_chain), on_hit_aoe: g(A::on_hit_aoe),
        double_hit: g(A::double_hit_chance).min(1.0), relentless: g(A::relentless_strike), third_attack: g(A::third_attack),
        effect_duration: g(A::effect_duration),
        healing_effectiveness: if bloodthirst { 0.0 } else { he.max(0.0) }, leech: g(A::leech),
        thorns_flat: g(A::thorns_damage_flat), thorns_pct: g(A::thorns_damage), thorns_scaling: g(A::thorns_scaling_damage),
        ap_scaling: g(A::ap_scaling_damage), ap_flat: g(A::ability_power) * (1.0 + g(A::ability_power_percent)),
        castle: f64::min(0.5, 5.0 * g(A::castle_bastion)),
    }
}
