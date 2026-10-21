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

pub const MAX_ATTRS: usize = 80;
pub const MAX_TALENTS: usize = 64;
pub const MAX_SPECS: usize = 96;
/// Build::pd bit of the Starforge nova (spec bits stay below MAX_SPECS).
pub const PD_NOVA: u128 = 1u128 << 127;
pub const MAX_GREED: usize = 512;
pub const MAX_DECK: usize = 64;
pub const MAX_IMPLICITS: usize = 8;
pub const MAX_SIDE: usize = 6;
pub const MAX_TRINKETS: usize = 16;
pub const MAX_MODS: usize = 8;
pub const MAX_LEVELS: usize = 64;
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
    pub fn as_slice(&self) -> &[u16] {
        &self.v[..self.len as usize]
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

impl Default for Bits {
    fn default() -> Self {
        Bits::new()
    }
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
    #[inline]
    pub fn eq_masked(&self, o: &Bits, m: &Bits) -> bool {
        (0..self.w.len()).all(|k| (self.w[k] ^ o.w[k]) & m.w[k] == 0)
    }
    #[inline]
    pub fn and_count(&self, o: &Bits) -> usize {
        self.w.iter().zip(o.w.iter()).map(|(a, b)| (a & b).count_ones() as usize).sum()
    }
    #[inline]
    pub fn and3_count(&self, o: &Bits, q: &Bits) -> usize {
        let mut n = 0;
        for k in 0..self.w.len() {
            n += (self.w[k] & o.w[k] & q.w[k]).count_ones() as usize;
        }
        n
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
    #[inline]
    pub fn or_with(&mut self, o: &Bits) {
        for k in 0..self.w.len() {
            self.w[k] |= o.w[k];
        }
    }
    #[inline]
    pub fn and(&self, o: &Bits) -> Bits {
        let mut r = *self;
        for k in 0..r.w.len() {
            r.w[k] &= o.w[k];
        }
        r
    }
    #[inline]
    pub fn andnot(&self, o: &Bits) -> Bits {
        let mut r = *self;
        for k in 0..r.w.len() {
            r.w[k] &= !o.w[k];
        }
        r
    }
    #[inline]
    pub fn is_zero(&self) -> bool {
        self.w.iter().all(|&x| x == 0)
    }
    /// Index of the k-th set bit (k < count()).
    #[inline]
    pub fn select(&self, mut k: usize) -> usize {
        for (wi, &x) in self.w.iter().enumerate() {
            let c = x.count_ones() as usize;
            if k < c {
                let mut x = x;
                for _ in 0..k {
                    x &= x - 1;
                }
                return wi * 64 + x.trailing_zeros() as usize;
            }
            k -= c;
        }
        panic!("Bits::select out of range");
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
    /// Rework deck menus: chosen design, its core set, and the option index in each of the design's slots.
    pub design: u8,
    pub cset: u8,
    pub dk: Small<MAX_DECK>,
    pub talents: [u8; MAX_TALENTS],
    pub abilities: [u8; MAX_SPECS],
    pub greed: Bits,
    pub bugs: u32,
    /// Ability builds only: melee swings woven between casts.
    pub weave: bool,
    /// Power Dump rework: bit s = spec s is cast while sneaking; PD_NOVA = the Starforge nova procs while sneaking.
    pub pd: u128,
    /// Greed rework: god levels, owned god nodes (global index = tree offset + local), transfer slots (effect ids).
    pub glv: [u8; 4],
    pub gnodes: Bits,
    pub gmts: [[i16; 3]; 4],
}

#[derive(Clone, Serialize, Deserialize)]
pub struct GodStateDoc {
    pub levels: Vec<usize>,
    pub nodes: Vec<Vec<usize>>,
    pub mts: Vec<Vec<usize>>,
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
    #[serde(default)]
    pub pd: Vec<usize>,
    #[serde(default)]
    pub pd_nova: bool,
    #[serde(default)]
    pub gods: Option<GodStateDoc>,
    #[serde(default)]
    pub design: usize,
    #[serde(default)]
    pub cset: usize,
    #[serde(default)]
    pub dk: Vec<usize>,
}

impl Build {
    pub fn empty() -> Build {
        Build {
            items: [Item::new(0); N_SLOTS], trinkets: Small::new(), god: 0, mods: Small::new(), deck: Small::new(),
            design: 0, cset: 0, dk: Small::new(),
            talents: [0; MAX_TALENTS], abilities: [0; MAX_SPECS], greed: Bits::new(), bugs: 0, weave: false, pd: 0,
            glv: [0; 4], gnodes: Bits::new(), gmts: [[-1; 3]; 4],
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
        if !p.designs.is_empty() {
            assert!(d.design < p.designs.len() && d.cset < p.designs[d.design].doc.csets.len(), "build design out of range");
            assert!(d.dk.len() == p.designs[d.design].n, "build deck choice length != design slots");
            b.design = d.design as u8;
            b.cset = d.cset as u8;
            b.dk = Small::from_slice(&d.dk);
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
        for &s in &d.pd {
            b.pd |= 1u128 << s;
        }
        if d.pd_nova {
            b.pd |= PD_NOVA;
        }
        if let (Some(gd), Some(g)) = (&d.gods, &p.godtree) {
            for k in 0..4 {
                b.glv[k] = gd.levels[k] as u8;
                for &i in &gd.nodes[k] {
                    b.gnodes.set(g.trees[k].off + i);
                }
                for (s, &e) in gd.mts[k].iter().enumerate() {
                    b.gmts[k][s] = e as i16;
                }
            }
        }
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
            design: self.design as usize, cset: self.cset as usize, dk: self.dk.to_vec(),
            talents: self.talents[..p.talents.len()].iter().map(|&x| x as usize).collect(),
            abilities: self.abilities[..p.specs.len()].iter().map(|&x| x as usize).collect(),
            greed: self.greed.iter().collect(), bugs: self.bugs, weave: self.weave,
            pd: (0..p.specs.len()).filter(|&s| self.pd >> s & 1 == 1).collect(), pd_nova: self.pd & PD_NOVA != 0,
            gods: p.godtree.as_ref().map(|g| GodStateDoc {
                levels: self.glv.iter().map(|&x| x as usize).collect(),
                nodes: (0..4).map(|k| (0..g.trees[k].n).filter(|&i| self.gnodes.has(g.trees[k].off + i)).collect()).collect(),
                mts: (0..4).map(|k| self.gmts[k].iter().filter(|&&e| e >= 0).map(|&e| e as usize).collect()).collect(),
            }),
        }
    }

    /// OFFHAND-STAFF and VOLLEY-BOUNCE-EXPLODE are verified not to be bugs and are pinned to the in-game behaviour
    /// in every mode (model/damage.py NOT_BUGS).
    #[inline]
    pub fn bug(&self, b: Bug) -> bool {
        match b {
            Bug::OffhandStaff => false,
            Bug::VolleyBounceExplode => true,
            _ => self.bugs & (1 << (b as u32)) != 0,
        }
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

    /// Largest value of the etchings matching the query (abilities.etch_max: the strongest single copy).
    pub fn etch_max(&self, p: &Problem, q: Etq) -> Option<f64> {
        let mut best: Option<f64> = None;
        for it in &self.items {
            if let Some(e) = it.etching {
                let er = &p.etchings[e as usize];
                if er.q[q as usize] {
                    if let Some(v) = er.num {
                        if best.map_or(true, |x| v > x) {
                            best = Some(v);
                        }
                    }
                }
            }
        }
        best
    }
}

pub fn offhand_counts(b: &Build, p: &Problem) -> bool {
    !(!b.bug(Bug::OffhandStaff) && p.types[b.items[MAINHAND].ty()].name == "battlestaff")
}

/// Fixed-capacity list of Copy values on the stack: no allocation and no initialisation of the unused tail.
#[derive(Clone, Copy)]
pub(crate) struct Stack<T: Copy, const N: usize> {
    a: [std::mem::MaybeUninit<T>; N],
    n: usize,
}

impl<T: Copy, const N: usize> Stack<T, N> {
    #[inline]
    pub(crate) fn new() -> Self {
        Stack { a: [std::mem::MaybeUninit::uninit(); N], n: 0 }
    }
    #[inline]
    pub(crate) fn push(&mut self, x: T) {
        assert!(self.n < N, "fixed list over capacity {}", N);
        self.a[self.n] = std::mem::MaybeUninit::new(x);
        self.n += 1;
    }
    #[inline]
    pub(crate) fn clear(&mut self) {
        self.n = 0;
    }
}

impl<T: Copy, const N: usize> std::ops::Deref for Stack<T, N> {
    type Target = [T];
    #[inline]
    fn deref(&self) -> &[T] {
        unsafe { std::slice::from_raw_parts(self.a.as_ptr() as *const T, self.n) }
    }
}

impl<T: Copy, const N: usize> std::ops::DerefMut for Stack<T, N> {
    #[inline]
    fn deref_mut(&mut self) -> &mut [T] {
        unsafe { std::slice::from_raw_parts_mut(self.a.as_mut_ptr() as *mut T, self.n) }
    }
}

/// Sum of every additive source (affixes of counted items, trinket adds, charm, deck, greed, prestige).
#[derive(Clone, Copy)]
pub struct Lin {
    pub v: [f64; MAX_ATTRS],
    /// Rework: GodData::cache of the build's god state.
    pub gfx: Option<crate::gods::GodFx>,
    pub sss: f64,
    pub gkey: u64,
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
    let units = p.godtree.as_ref().map(|g| g.piety_units(b) + 1.0);
    for m in b.mods.iter() {
        let cm = &p.gods[b.god as usize].mods[m];
        v[cm.a] += sign * match units { Some(u) => cm.v * u, None => cm.v };
        if let Some(t) = t.as_deref_mut() {
            t.mark(cm.a);
        }
    }
}

/// The design deck's per-attribute totals (sum of term x card value over stat cards, slot order).
fn design_lin(b: &Build, p: &Problem, v: &mut [f64; MAX_ATTRS], sign: f64, t: &mut Option<&mut Touched>) {
    if p.designs.is_empty() || b.dk.is_empty() {
        return;
    }
    let d = &p.designs[b.design as usize];
    let choice = b.dk.as_slice();
    DECK_TERMS.with(|cell| {
        let mut terms = cell.borrow_mut();
        d.terms(b.cset as usize, choice, &mut terms);
        for (i, &o) in choice.iter().enumerate() {
            let c = d.card(i, o as usize);
            if terms[i] != 0.0 && c.a >= 0 {
                v[c.a as usize] += sign * terms[i] * c.v;
                if let Some(t) = t.as_deref_mut() {
                    t.mark(c.a as usize);
                }
            }
        }
    });
}

thread_local! {
    static DECK_TERMS: std::cell::RefCell<Vec<f64>> = std::cell::RefCell::new(Vec::with_capacity(MAX_DECK));
}

pub fn lin_full(b: &Build, p: &Problem) -> Lin {
    let mut v = [0.0; MAX_ATTRS];
    let off = offhand_counts(b, p);
    let ow = p.godtree.as_ref().map_or(1.0, |g| g.offhand_weight(b));
    for (slot, it) in b.items.iter().enumerate() {
        if slot != OFFHAND || off {
            item_lin(it, p, &mut v, if slot == OFFHAND { ow } else { 1.0 }, &mut None);
        }
    }
    trinkets_lin(b, p, &mut v, 1.0, &mut None);
    charm_lin(b, p, &mut v, 1.0, &mut None);
    for (i, o) in b.deck.iter().enumerate() {
        let d = &p.deck[i][o];
        v[d.a] += d.c;
    }
    design_lin(b, p, &mut v, 1.0, &mut None);
    for n in b.greed.iter() {
        for &(a, x) in &p.greed[n].entries {
            v[a] += x;
        }
    }
    for &(a, x) in &p.prestige.adds {
        v[a] += x;
    }
    let (mut gfx, mut sss, mut gkey) = (None, 0.0, 0);
    if let Some(g) = &p.godtree {
        let r = g.ranks(b);
        g.lin_r(&r, &mut v, 1.0);
        (gfx, sss) = g.cache(&r, &p.knobs);
        gkey = g.gfx_key(&r);
    }
    Lin { v, gfx, sss, gkey }
}

/// Totals for `b` from the totals of `prev` (delta scoring): only components that differ are subtracted and re-added.
/// The two most recent god rank tables, keyed on the god state ranks() reads (charm, nodes, minor trait slots), so a
/// god move does not recompute the current build's ranks. One per search, so every entry belongs to one problem.
#[derive(Default)]
pub struct RankCache {
    slots: [Option<(u16, Bits, [[i16; 3]; 4], crate::gods::Ranks)>; 2],
    next: usize,
}

impl RankCache {
    fn get(&mut self, g: &GodData, b: &Build, keep: Option<usize>) -> usize {
        for i in 0..2 {
            if let Some((god, nodes, mts, _)) = &self.slots[i] {
                if *god == b.god && *nodes == b.gnodes && *mts == b.gmts {
                    return i;
                }
            }
        }
        let i = if Some(self.next) == keep { 1 - self.next } else { self.next };
        self.next = 1 - i;
        self.slots[i] = Some((b.god, b.gnodes, b.gmts, g.ranks(b)));
        i
    }

    fn at(&self, i: usize) -> &crate::gods::Ranks {
        &self.slots[i].as_ref().unwrap().3
    }
}

pub fn lin_delta(prev_b: &Build, prev: &Lin, b: &Build, p: &Problem) -> Lin {
    lin_delta_c(prev_b, prev, b, p, None)
}

/// lin_delta with a search's rank cache.
pub fn lin_delta_c(prev_b: &Build, prev: &Lin, b: &Build, p: &Problem, cache: Option<&mut RankCache>) -> Lin {
    let mut out = *prev;
    let v = &mut out.v;
    let mut touched = Touched { n: 0, a: [0; 96], overflow: false };
    let mut t = Some(&mut touched);
    let (off0, off1) = (offhand_counts(prev_b, p), offhand_counts(b, p));
    let (ow0, ow1) = match &p.godtree {
        Some(g) => (g.offhand_weight(prev_b), g.offhand_weight(b)),
        None => (1.0, 1.0),
    };
    for slot in 0..N_SLOTS {
        let c0 = slot != OFFHAND || off0;
        let c1 = slot != OFFHAND || off1;
        let (w0, w1) = if slot == OFFHAND { (ow0, ow1) } else { (1.0, 1.0) };
        if c0 == c1 && w0 == w1 && prev_b.items[slot] == b.items[slot] {
            continue;
        }
        if c0 {
            item_lin(&prev_b.items[slot], p, v, -w0, &mut t);
        }
        if c1 {
            item_lin(&b.items[slot], p, v, w1, &mut t);
        }
    }
    if prev_b.trinkets != b.trinkets {
        trinkets_lin(prev_b, p, v, -1.0, &mut t);
        trinkets_lin(b, p, v, 1.0, &mut t);
    }
    let gods_changed = p.godtree.is_some()
        && (prev_b.glv != b.glv || prev_b.gnodes != b.gnodes || prev_b.gmts != b.gmts || prev_b.god != b.god);
    if prev_b.god != b.god || prev_b.mods != b.mods || gods_changed {
        charm_lin(prev_b, p, v, -1.0, &mut t);
        charm_lin(b, p, v, 1.0, &mut t);
    }
    let mut god_cache = None;
    if gods_changed {
        let g = p.godtree.as_ref().unwrap();
        let own;
        let (r0, r1) = match cache {
            Some(c) => {
                let i0 = c.get(g, prev_b, None);
                let i1 = c.get(g, b, Some(i0));
                (c.at(i0), c.at(i1))
            }
            None => {
                own = (g.ranks(prev_b), g.ranks(b));
                (&own.0, &own.1)
            }
        };
        g.lin_diff(r0, r1, v, &mut |a| touched.mark(a));
        let key = g.gfx_key(r1);
        let gfx = if key == prev.gkey { prev.gfx } else { Some(g.gfx(r1, &p.knobs)) };
        god_cache = Some((gfx, g.sss_of(r1), key));
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
    if !p.designs.is_empty() && (prev_b.design != b.design || prev_b.cset != b.cset || prev_b.dk != b.dk) {
        design_lin(prev_b, p, v, -1.0, &mut Some(&mut touched));
        design_lin(b, p, v, 1.0, &mut Some(&mut touched));
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
    if let Some((gfx, sss, key)) = god_cache {
        out.gfx = gfx;
        out.sss = sss;
        out.gkey = key;
    }
    out
}

pub struct Stats {
    pub gfx: Option<crate::gods::GodFx>,
    pub attr: [f64; MAX_ATTRS],
    pub van: Stack<(u16, f64), 8>,
    pub ability_levels: Stack<(u16, f64), MAX_LEVELS>,
    pub talent_levels: Stack<(u16, f64), MAX_LEVELS>,
    pub mind_meld: bool,
    pub masterful: bool,
    pub berserk_power: bool,
    pub dice: Option<(f64, f64)>,
    pub luck: i32,
    pub talent_tier: [u8; MAX_TALENTS],
    pub extra_stacks: f64,
    /// Per-ability multipliers in assembly order: (key, kind 0 cooldown / 1 mana, amount).
    pub amods: Stack<(u16, u8, f64), MAX_AMODS>,
    pub lucky_thorns: bool,
    pub javelin_implode: bool,
    pub javelin_multi_hit: bool,
    /// Stormcrown (rework): the Storm Arrow storm follows the boss.
    pub storm_tracking: bool,
    /// Steel Inferno (rework): summed centre bonus and the rolled radius multiplier (1 = none).
    pub steel_b: f64,
    pub steel_r: f64,
    /// Starforge arcane nova on the main hand: summed AP share and hits required.
    pub nova_pct: f64,
    pub nova_hits: f64,
    /// Effect-cloud affixes worn (indices into Problem.clouds).
    pub clouds: [u16; 6],
    pub n_clouds: usize,
    pub safer_space: bool,
    pub bloodthirst: bool,
    /// Summed ability special modifications: [frost nova vulnerability level, fireball recast chance].
    pub special: [f64; 4],
    pub methodical: f64,
    /// Per etching query: the etching of the first piece (slot order) that matches it, u16::MAX = none (Build::etch).
    pub etq: [u16; N_ETQ],
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
        for &(x, f) in self.van.iter() {
            if x as usize == a {
                m *= f;
            }
        }
        m
    }
    pub fn ability_levels(&self) -> &[(u16, f64)] {
        &self.ability_levels
    }
    fn push_al(&mut self, k: usize, c: f64) {
        assert!(self.ability_levels.len() < MAX_LEVELS, "too many ability-level sources; raise MAX_LEVELS");
        self.ability_levels.push((k as u16, c));
    }
    pub fn amods(&self) -> &[(u16, u8, f64)] {
        &self.amods
    }
    fn push_am(&mut self, k: usize, kind: i32, v: f64) {
        assert!(self.amods.len() < MAX_AMODS, "too many per-ability multipliers; raise MAX_AMODS");
        self.amods.push((k as u16, kind as u8, v));
    }
    fn push_tl(&mut self, k: usize, c: f64) {
        assert!(self.talent_levels.len() < MAX_LEVELS, "too many talent-level sources; raise MAX_LEVELS");
        self.talent_levels.push((k as u16, c));
    }
    #[inline]
    fn etching<'p>(&self, p: &'p Problem, q: Etq) -> Option<&'p Etching> {
        match self.etq[q as usize] {
            u16::MAX => None,
            e => Some(&p.etchings[e as usize]),
        }
    }
    /// Build::etch from the table.
    #[inline]
    pub fn etch(&self, p: &Problem, q: Etq) -> Option<(Option<f64>, bool)> {
        self.etching(p, q).map(|er| (er.num, er.truthy))
    }
    /// Build::etch_dur from the table.
    #[inline]
    pub fn etch_dur(&self, p: &Problem, q: Etq) -> Option<f64> {
        self.etching(p, q).and_then(|er| er.dur)
    }
    /// Build::etch_pool from the table.
    #[inline]
    pub fn etch_pool<'p>(&self, p: &'p Problem, q: Etq) -> Option<&'p [usize]> {
        self.etching(p, q).map(|er| &er.pool[..])
    }
    /// Build::etch_num from the table.
    #[inline]
    pub fn etch_num(&self, p: &Problem, q: Etq) -> Result<Option<f64>, String> {
        match self.etch(p, q) {
            Some((n, true)) => n.map(Some).ok_or_else(|| "etching value not numeric".to_string()),
            _ => Ok(None),
        }
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
    pub cooldown_skip: f64,
    pub echo_chance: f64,
    pub echo_damage: f64,
    pub execution: f64,
    pub reaving: f64,
    pub hexing: f64,
    pub burning: f64,
    pub cloud_chance: f64,
    pub chain: f64,
    pub on_hit_aoe: f64,
    pub double_hit: f64,
    pub relentless: f64,
    pub third_attack: f64,
    pub effect_duration: f64,
    pub healing_effectiveness: f64,
    /// Healing effectiveness with Methodical's bonus (below 20% mana); healing weights it by the family's u_low_mana.
    pub he_low_mana: f64,
    pub leech: f64,
    pub thorns_flat: f64,
    pub thorns_pct: f64,
    pub thorns_scaling: f64,
    pub ap_scaling: f64,
    pub ap_flat: f64,
    /// Flat ability power before every percentage (Spectral Strike's base).
    pub ap_base: f64,
    pub castle: f64,
    /// Castle Bastion before its speed-dependent uptime (evaluate.unique_effects).
    pub castle_max: f64,
    pub lucky_uncapped: f64,
    /// Arcane Cascade: lucky hits/s (evaluate_lin's fixed point) and cooldownDecrease.
    pub cascade_l: f64,
    pub cascade_c: f64,
    /// Lucky Reset etching: the spec whose cooldown lucky hits cut by 25% of its max (usize::MAX = none), its own lucky
    /// hits per cast and the other lucky hits per second (abilities.cooldown_s).
    pub reset_spec: usize,
    pub reset_self: f64,
    pub reset_l: f64,
    pub god_global: f64,
    /// Stable movement speed (stats.stable_move_speed).
    pub move_speed: f64,
    pub crit_mitigation: f64,
    /// HP-state shares (evaluate.hp_state; 1 and 0 before the second pass) and the derived-stat buffs written back
    /// before the family compute (Empower speed, Sneaky Getaway dodge).
    pub u_high: f64,
    pub u_low: f64,
    pub empower_speed: f64,
    pub sneaky_dodge: f64,
    /// Share of the fight spent sneaking for Power Dump (halves the speed avoidance, evaluate.attack_stream).
    pub sneak_share: f64,
    /// The attack stream (evaluate.attack_stream).
    pub ranged: bool,
    pub p_avoid: f64,
    pub p_seal: f64,
    pub p_land: f64,
    pub boss_hits_landed: f64,
    pub boss_hits_reaching: f64,
    pub god_stack: f64,
    pub god_true_rage: f64,
    pub god_archmage: bool,
    pub god_overcrit: f64,
    pub god_lucky_twice: bool,
}

#[derive(Default, Clone)]
pub struct Fallbacks {
    pub trinket_op: u64,
    pub ehp_zero: u64,
    pub eval_error: u64,
    pub echo_n_clamp: u64,
    pub sec_over_cap: u64,
    pub cascade_unconverged: u64,
    pub fang_radius_clamp: u64,
    pub survival_zero_mult: u64,
    pub reach_unknown: u64,
    pub lucky_nova_no_spec: u64,
    pub echo_reverb_missing: u64,
    /// initial_build found no legal god state (returned the last draw).
    pub init_illegal: u64,
    pub holes_used: Vec<bool>,
    /// Casts skipped per spec because their mana cost exceeds the bar (cast-unaffordable-<spec>).
    pub unaffordable: Vec<u64>,
    pub first_error: Option<String>,
}

impl Fallbacks {
    pub fn note_unaffordable(&mut self, spec: usize) {
        if self.unaffordable.len() <= spec {
            self.unaffordable.resize(spec + 1, 0);
        }
        self.unaffordable[spec] += 1;
    }

    pub fn merge(&mut self, o: &Fallbacks) {
        self.trinket_op += o.trinket_op;
        self.ehp_zero += o.ehp_zero;
        self.eval_error += o.eval_error;
        self.echo_n_clamp += o.echo_n_clamp;
        self.sec_over_cap += o.sec_over_cap;
        self.cascade_unconverged += o.cascade_unconverged;
        self.fang_radius_clamp += o.fang_radius_clamp;
        self.survival_zero_mult += o.survival_zero_mult;
        self.reach_unknown += o.reach_unknown;
        self.lucky_nova_no_spec += o.lucky_nova_no_spec;
        self.echo_reverb_missing += o.echo_reverb_missing;
        self.init_illegal += o.init_illegal;
        if self.holes_used.len() < o.holes_used.len() {
            self.holes_used.resize(o.holes_used.len(), false);
        }
        for (i, &u) in o.holes_used.iter().enumerate() {
            self.holes_used[i] |= u;
        }
        if self.unaffordable.len() < o.unaffordable.len() {
            self.unaffordable.resize(o.unaffordable.len(), 0);
        }
        for (i, &n) in o.unaffordable.iter().enumerate() {
            self.unaffordable[i] += n;
        }
        if self.first_error.is_none() {
            self.first_error = o.first_error.clone();
        }
    }
}

/// Non-additive parts of assembly on top of the additive totals, then talents (model/build.py assemble + apply_talents).
pub fn stats(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks) -> Stats {
    let mut st = Stats {
        gfx: lin.gfx,
        attr: lin.v, van: Stack::new(), ability_levels: Stack::new(),
        talent_levels: Stack::new(), mind_meld: false, masterful: p.prestige.masterful,
        berserk_power: p.prestige.berserk, dice: None, luck: 0, talent_tier: [0; MAX_TALENTS], extra_stacks: 0.0,
        amods: Stack::new(), lucky_thorns: false, javelin_implode: false, javelin_multi_hit: false, storm_tracking: false, steel_b: 0.0, steel_r: 1.0, nova_pct: 0.0, nova_hits: 0.0, clouds: [0; 6], n_clouds: 0, safer_space: false, bloodthirst: false, special: [0.0; 4], methodical: 0.0,
        etq: [u16::MAX; N_ETQ],
    };
    let mut seen = 0u32;
    for it in b.items.iter() {
        if let Some(e) = it.etching {
            let mut m = p.etchings[e as usize].mask & !seen;
            seen |= m;
            while m != 0 {
                st.etq[m.trailing_zeros() as usize] = e;
                m &= m - 1;
            }
        }
    }
    let off = offhand_counts(b, p);
    let ow = p.godtree.as_ref().map_or(1, |g| g.offhand_weight(b) as usize);
    for (slot, it) in b.items.iter().enumerate() {
        if slot == OFFHAND && !off {
            continue;
        }
        for _ in 0..(if slot == OFFHAND { ow } else { 1 }) {
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
                    2 => st.bloodthirst = true,
                    3 => st.javelin_implode = true,
                    4 => st.javelin_multi_hit = true,
                    5 => st.storm_tracking = true,
                    _ => unreachable!("flag key {} outside FLAG_NAMES (checked at load)", r.key),
                },
                7 => st.special[r.key] += r.v,
                11 => {
                    st.steel_b += r.v;
                    st.steel_r = r.lc;
                }
                10 => {
                    if slot < 4 {
                        st.special[r.key] += r.v;
                    }
                }
                9 => {
                    assert!(st.n_clouds < 6, "too many effect clouds");
                    st.clouds[st.n_clouds] = r.key as u16;
                    st.n_clouds += 1;
                }
                8 => {
                    if slot == MAINHAND {
                        st.nova_pct += r.v;
                        st.nova_hits = r.lc;
                    }
                }
                6 => st.push_am(r.key, r.amp, r.v),
                3 => st.luck = st.luck.max(r.amp),
                _ => unreachable!("affix kind {} outside AFFIX_KIND_NAMES (checked at load)", k),
            }
        });
        }
        if let Some(e) = it.etching {
            let er = &p.etchings[e as usize];
            if let Some((k, c)) = er.tl {
                st.push_tl(k, c);
            }
            if er.q[Etq::PyramidScheme as usize] && er.truthy {
                if let Some(v) = er.num {
                    st.extra_stacks += v;
                }
            }
        }
    }
    for t in b.trinkets.iter() {
        for e in &p.trinkets[t].effects {
            match *e {
                TrinketEff::Vanilla { a, v, op } => {
                    if op == 2 {
                        assert!(st.van.len() < 8, "too many vanilla multipliers");
                        st.van.push((a as u16, 1.0 + v));
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
    if let Some(g) = &p.godtree {
        st.extra_stacks += lin.sss;
        if let Some(fx) = st.gfx {
            if fx.utilized != 0.0 && g.utility_key >= 0 {
                st.push_al(g.utility_key as usize, fx.utilized);
            }
        }
    }
    if !p.designs.is_empty() && !b.dk.is_empty() {
        // One level source per key: a deck can hold many Knack / Arcane cards for the same talent or ability.
        let d = &p.designs[b.design as usize];
        let mut grants: Stack<(u8, usize, f64), MAX_DECK> = Stack::new();
        for (i, o) in b.dk.iter().enumerate() {
            let c = d.card(i, o);
            if c.gk == 0 {
                continue;
            }
            match grants.iter().position(|g| g.0 == c.gk && g.1 == c.key) {
                Some(j) => grants[j].2 += c.lv,
                None => grants.push((c.gk, c.key, c.lv)),
            }
        }
        for &(gk, key, lv) in grants.iter() {
            if gk == 1 { st.push_al(key, lv) } else { st.push_tl(key, lv) }
        }
    }
    apply_talents(b, p, &mut st);
    st
}

fn apply_talents(b: &Build, p: &Problem, st: &mut Stats) {
    let mut stack = [0u8; MAX_TALENTS];
    let mut bonus = [0.0f64; MAX_TALENTS];
    for &(k, ch) in st.talent_levels.iter() {
        let tk = &p.tal_keys[k as usize];
        if tk.all {
            for x in bonus[..p.talents.len()].iter_mut() {
                *x += ch;
            }
        } else if tk.talent >= 0 && (tk.talent as usize) < p.talents.len() {
            bonus[tk.talent as usize] += ch;
        }
    }
    for tid in 0..p.talents.len() {
        let learned = b.tal(tid);
        if learned == 0 {
            continue;
        }
        let eff = (p.talents[tid].tiers.len() as f64).min(learned as f64 + bonus[tid]) as usize;
        let tier = &p.talents[tid].tiers[eff - 1];
        match tier.ttype {
            1 | TT_HIGH_HEALTH => {
                if tier.extra {
                    st.extra_stacks += tier.v;
                } else {
                    st.attr[tier.a as usize] += tier.v;
                }
            }
            2 | 3 => stack[tid] = eff as u8,
            4 => st.mind_meld = true,
            7 => st.methodical = tier.ahe.expect("Methodical tier without additionalHealingEfficiency"),
            _ => {}
        }
        st.talent_tier[tid] = eff as u8;
    }
    let extra = st.extra_stacks;
    let hoard = st.gfx.map_or(1.0, |fx| fx.stack_hoarder);
    let ed = st.g(p, A::effect_duration);
    let hits = if stack[..p.talents.len()].iter().any(|&s| s != 0) { normal_hit_rate(b, p, st) } else { 0.0 };
    for tid in 0..p.talents.len() {
        if stack[tid] == 0 {
            continue;
        }
        let tier = &p.talents[tid].tiers[stack[tid] as usize - 1];
        let mut stacks = tier.max_stacks + extra;
        if let Some(fx) = st.gfx {
            if fx.super_stacker != 1.0 {
                stacks = (stacks * fx.super_stacker + 1e-9).floor();
            }
        }
        let rate = if tier.ttype == 3 { p.knobs.kills_per_s } else { hits };
        let n = rate * tier.dur.expect("stacking talent tier without durationTicks") * (1.0 + ed) * hoard / 20.0;
        let mut uptime = 1.0;
        if n < 1.0 - 1e-9 {
            stacks = 1.0;
            uptime = n;
        }
        if uptime > 0.0 {
            st.attr[tier.a as usize] += tier.v * stacks * uptime;
        }
    }
}

/// build.normal_hit_rate: normal weapon hits per second before stacking talents (melee families and weaving ability
/// builds swing; Grand Archmage cancels every swing).
fn normal_hit_rate(b: &Build, p: &Problem, st: &Stats) -> f64 {
    if st.gfx.map_or(false, |fx| fx.archmage) {
        return 0.0;
    }
    let aspd = f64::max(0.1, (4.0 + st.g(p, A::attack_speed)) * (1.0 + st.g(p, A::attack_speed_percent)));
    if matches!(p.fam.kind, crate::problem::FamKind::Rang) {
        let speed = 1.5 + st.g(p, A::velocity) * 0.325;
        let cd_ticks = (20.0 / aspd + 0.5 + 1e-9).floor();
        return 1.0 / (2.0 * p.knobs.rang_travel_blocks / (speed * 20.0) + cd_ticks / 20.0);
    }
    if !(p.is_melee() || b.weave) {
        return 0.0;
    }
    crate::families::bc_swings_per_s(aspd).min(p.knobs.max_swings_per_s)
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
    let luck_mult = if st.luck > 0 { crate::procs::luck_pow(st.luck) } else { 1.0 };
    if st.luck > 0 {
        lucky *= luck_mult;
    }
    if let Some(fx) = st.gfx {
        if fx.luckiest != 0.0 {
            lucky *= fx.luckiest;
        }
    }
    let lucky_hit_chance = lucky.min(p.caps.lucky).min(1.0);
    let aoe_multiplier = f64::min(1.0 + g(A::area_of_effect), 1.0 + p.caps.aoe);
    let health = (20.0 + g(A::health)) * (1.0 + g(A::health_percentile)) * van(A::v_max_health) * (1.0 + g(A::health_boost));
    let sf_pct = match st.gfx {
        Some(fx) if fx.steadfast_per != 0.0 => f64::max(0.0, g(A::knockback_resistance) - fx.steadfast_floor) * fx.steadfast_per,
        _ => 0.0,
    };
    let armor = g(A::armor) * (1.0 + g(A::armor_percentile) + sf_pct);
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
    let he = 1.0 + g(A::healing_effectiveness);
    let he_lm = he + st.methodical;
    let mut d = Derived {
        attack_damage, attack_speed, ability_power: ap, damage_increase: g(A::damage_increase), lucky_hit_chance,
        aoe_multiplier, health, armor, resistance, resistance_cap: res_cap, block, dodge, mana_max, mana_regen, mana_regen_vt,
        cooldown_reduction: cdr.min(cdr_cap), cooldown_skip: g(A::ability_cooldown_skip), echo_chance: g(A::echoing_chance).min(1.0), echo_damage: g(A::echoing_damage),
        execution: g(A::execution_damage), reaving: g(A::reaving_damage), hexing: g(A::hexing_chance), burning: g(A::burning_hit_chance),
        cloud_chance: g(A::effect_cloud_chance_additive), chain: g(A::on_hit_chain), on_hit_aoe: g(A::on_hit_aoe),
        double_hit: g(A::double_hit_chance).min(1.0), relentless: g(A::relentless_strike), third_attack: g(A::third_attack),
        effect_duration: g(A::effect_duration),
        healing_effectiveness: if bloodthirst { 0.0 } else { he.max(0.0) }, he_low_mana: if bloodthirst { 0.0 } else { he_lm.max(0.0) },
        leech: g(A::leech),
        thorns_flat: g(A::thorns_damage_flat) * (1.0 + g(A::thorns_damage_flat_percentile)), thorns_pct: g(A::thorns_damage), thorns_scaling: g(A::thorns_scaling_damage),
        ap_scaling: g(A::ap_scaling_damage), ap_flat: g(A::ability_power) * (1.0 + g(A::ability_power_percent)), ap_base: g(A::ability_power),
        castle: f64::min(0.5, 5.0 * g(A::castle_bastion)), castle_max: 0.0,
        lucky_uncapped: lucky, move_speed: 0.0, god_global: 1.0, god_stack: 1.0, god_true_rage: 0.0, god_archmage: false,
        god_overcrit: 0.0, god_lucky_twice: false, cascade_l: 0.0, cascade_c: 0.0,
        crit_mitigation: g(A::critical_hit_mitigation), u_high: 1.0, u_low: 0.0, empower_speed: 0.0,
        sneaky_dodge: 0.0, sneak_share: 0.0, ranged: false, p_avoid: 0.0, p_seal: 1.0, p_land: 0.0, boss_hits_landed: 0.0, boss_hits_reaching: 0.0,
        reset_spec: usize::MAX, reset_self: 0.0, reset_l: 0.0,
    };
    if let Some(fx) = st.gfx {
        d.health *= fx.health_mult;
        d.armor *= fx.armor_mult;
        d.ability_power *= fx.ap;
        if !bloodthirst {
            for x in [&mut d.healing_effectiveness, &mut d.he_low_mana] {
                let mut he = (*x + fx.he_flow * g(A::mana_regen)) * fx.he_mult;
                if fx.malediction != 0.0 {
                    he = he.min(fx.malediction);
                }
                *x = he.max(0.0);
            }
        }
        let tm = if fx.malediction != 0.0 { (1.0 + g(A::healing_effectiveness)).powf(1.0 / 3.0) } else { 1.0 };
        d.thorns_flat *= fx.thorns_f * tm;
        d.thorns_pct *= fx.thorns_m * tm;
        let mut glob = fx.global;
        if fx.kinetic != 0.0 {
            glob *= 1.0 + fx.kinetic * 100.0 * g(A::movement_speed);
        }
        if fx.greed != 0.0 {
            glob *= f64::max(1.0, 1.0 + fx.greed * (g(A::item_quantity) + g(A::item_rarity)));
        }
        d.god_global = glob;
        d.god_stack = fx.stack;
        d.god_true_rage = fx.true_rage;
        d.god_archmage = fx.archmage;
        d.god_overcrit = f64::max(0.0, lucky - p.caps.lucky.min(1.0)) * fx.overcrit;
        d.god_lucky_twice = fx.luckiest != 0.0;
    }
    d.move_speed = stable_move_speed(st, p, &d);
    d
}

/// True when a change to attribute `a` moves no evaluated value for this build: derive() reads knockback resistance
/// only for Velara's Steadfast and nothing else reads it (evaluate_lin's HP-state pass relies on this).
pub fn attr_inert(st: &Stats, p: &Problem, a: usize) -> bool {
    let steadfast = st.gfx.map_or(false, |fx| fx.steadfast_per != 0.0);
    a == p.at.ids[A::knockback_resistance as usize] && !steadfast
}

/// stats.stable_move_speed: (1 + gear/talent/god movement speed + Empower) x The Stonewall (Swiftness Amplified's
/// multiplier is not exported on this branch: 1).
pub fn stable_move_speed(st: &Stats, p: &Problem, d: &Derived) -> f64 {
    let mut ms = 1.0 + st.g(p, A::movement_speed) + d.empower_speed;
    if let Some(fx) = st.gfx {
        ms *= fx.speed_mult;
    }
    ms * p.prestige.swiftness
}
