//! Simulated annealing over legal builds (model/search.py), with proposals drawn only from legal, non-identical moves.
//! Candidates are scored by delta: their additive stat totals are patched from the current build's totals.

use crate::build::*;
use crate::eval::{evaluate, score_lin};
use crate::problem::*;
use serde::{Deserialize, Serialize};

pub struct Rng {
    s: [u64; 4],
}

impl Rng {
    pub fn new(seed: u64) -> Rng {
        let mut z = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut s = [0u64; 4];
        for x in s.iter_mut() {
            z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut v = z;
            v = (v ^ (v >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            v = (v ^ (v >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            *x = v ^ (v >> 31);
        }
        Rng { s }
    }
    pub fn next_u64(&mut self) -> u64 {
        let r = self.s[0].wrapping_add(self.s[3]).rotate_left(23).wrapping_add(self.s[0]);
        let t = self.s[1] << 17;
        self.s[2] ^= self.s[0];
        self.s[3] ^= self.s[1];
        self.s[1] ^= self.s[2];
        self.s[0] ^= self.s[3];
        self.s[2] ^= t;
        self.s[3] = self.s[3].rotate_left(45);
        r
    }
    #[inline]
    pub fn f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }
    #[inline]
    pub fn below(&mut self, n: usize) -> usize {
        ((self.next_u64() >> 11) % n as u64) as usize
    }
    #[inline]
    pub fn choice<T: Copy>(&mut self, v: &[T]) -> T {
        v[self.below(v.len())]
    }
    pub fn shuffle<T>(&mut self, v: &mut [T]) {
        for i in (1..v.len()).rev() {
            let j = self.below(i + 1);
            v.swap(i, j);
        }
    }
}

#[derive(Deserialize, Clone)]
#[serde(tag = "kind")]
pub enum Schedule {
    #[serde(rename = "geometric")]
    Geometric { t0: f64, t1: f64 },
    /// Temperature set so the q-quantile of recent worsening moves is accepted with probability p(t).
    #[serde(rename = "adaptive")]
    Adaptive { p0: f64, p1: f64, q: f64, window: usize, calib: usize },
    /// Stochastic-approximation controller: after every worsening proposal T is nudged so the share of worsening
    /// proposals accepted tracks a target falling geometrically from a0 to a1.
    #[serde(rename = "target")]
    Target { a0: f64, a1: f64, t_init: f64, eta: f64 },
}

#[derive(Serialize, Default, Clone)]
pub struct Trace {
    pub checkpoints: Vec<(f64, f64)>,
    pub temps: Vec<(f64, f64)>,
    /// Per 5% window: (progress, temperature, worsening proposals accepted / proposed, median worsening |delta|).
    pub windows: Vec<(f64, f64, f64, f64)>,
    pub start: f64,
    pub after_polish1: f64,
    pub sa_best: f64,
    pub after_polish3: f64,
    pub final_score: f64,
    pub accepted: u64,
    pub worse_accepted: u64,
    pub worse_proposed: u64,
    pub illegal_draws: u64,
    pub evals: u64,
    pub secs: f64,
}

pub struct Search<'a> {
    pub p: &'a Problem,
    pub fb: Fallbacks,
    pub evals: u64,
    pub illegal: u64,
    pub no_legal: u64,
    /// Re-score every delta-scored candidate from scratch and count disagreements (testing only).
    pub verify_delta: bool,
    pub delta_checked: u64,
    pub delta_mismatch: u64,
    pub delta_max_err: f64,
}

const MOVES: [u8; 18] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 10, 10, 11, 12, 12, 13, 14];
/// Slots a unique can take (armor, mainhand, offhand).
const UNIQUE_SLOTS: [usize; 6] = [0, 1, 2, 3, MAINHAND, OFFHAND];
const RESYNC_ACCEPTS: u64 = 1000;

impl<'a> Search<'a> {
    pub fn new(p: &'a Problem) -> Search<'a> {
        let n_holes = p.specs.iter().flat_map(|s| s.cfg.iter().flat_map(|v| v.iter().filter_map(|c| c.hole))).max().map(|m| m + 1).unwrap_or(0);
        Search {
            p, fb: Fallbacks { holes_used: vec![false; n_holes], ..Fallbacks::default() },
            evals: 0, illegal: 0, no_legal: 0, verify_delta: false, delta_checked: 0, delta_mismatch: 0, delta_max_err: 0.0,
        }
    }

    /// Full score (no delta).
    pub fn score(&mut self, b: &Build) -> (f64, Lin) {
        self.evals += 1;
        let lin = lin_full(b, self.p);
        (score_lin(b, self.p, &lin, &mut self.fb), lin)
    }

    /// Delta score of `b` from a scored neighbour `prev`.
    pub fn score_from(&mut self, prev: &Build, prev_lin: &Lin, b: &Build) -> (f64, Lin) {
        self.evals += 1;
        let lin = lin_delta(prev, prev_lin, b, self.p);
        let s = score_lin(b, self.p, &lin, &mut self.fb);
        if self.verify_delta {
            let full = score_lin(b, self.p, &lin_full(b, self.p), &mut self.fb);
            let err = (full - s).abs();
            self.delta_checked += 1;
            if err > 1e-9 * full.abs().max(1.0) {
                self.delta_mismatch += 1;
                if std::env::var("WVK_DEBUG").is_ok() {
                    let a = crate::eval::evaluate_lin(b, self.p, &lin, &mut self.fb);
                    let f = crate::eval::evaluate_lin(b, self.p, &lin_full(b, self.p), &mut self.fb);
                    if let (Ok(a), Ok(f)) = (a, f) {
                        eprintln!("[wvk] delta {:?} full {:?}", (a.cycle_damage, a.cycle_survival), (f.cycle_damage, f.cycle_survival));
                    }
                    let (l1, l2) = (lin, lin_full(b, self.p));
                    for i in 0..MAX_ATTRS {
                        if l1.v[i] != l2.v[i] {
                            eprintln!("[wvk]   attr {} delta {:e} full {:e}", i, l1.v[i], l2.v[i]);
                        }
                    }
                }
            }
            if err.is_finite() && err > self.delta_max_err {
                self.delta_max_err = err;
            }
        }
        (s, lin)
    }

    fn affix_ok(&self, b: &Build, a: usize) -> bool {
        let r = &self.p.affixes[a];
        match r.k {
            1 => {
                let lk = &self.p.lvl_keys[r.key];
                lk.all || (0..self.p.specs.len()).any(|s| b.abilities[s] > 0 && self.p.lvl_hit[r.key][s])
            }
            2 => {
                let tk = &self.p.tal_keys[r.key];
                tk.all || (tk.talent >= 0 && b.talents[tk.talent as usize] > 0)
            }
            _ => true,
        }
    }

    fn pick_affix(&self, b: &Build, opts: &[usize], rng: &mut Rng) -> usize {
        let mut good = [0usize; 64];
        let mut n = 0;
        for &a in opts {
            if n < good.len() && self.affix_ok(b, a) {
                good[n] = a;
                n += 1;
            }
        }
        if n > 0 && rng.f64() < 0.9 {
            good[rng.below(n)]
        } else {
            rng.choice(opts)
        }
    }

    fn capacity(&self, it: &Item) -> (usize, usize, i64) {
        let t = &self.p.types[it.ty()];
        let mut cap = (t.pre + t.suf) as i64;
        if it.seal.is_some() {
            cap -= 1;
        }
        if it.unusual.is_some() {
            cap -= 1;
        }
        (t.pre, t.suf, cap)
    }

    /// Groups of the item's prefixes and suffixes, optionally skipping one (side, index).
    fn used_groups(&self, it: &Item, exclude: Option<(usize, usize)>) -> Small<16> {
        let mut g = Small::<16>::new();
        for side in 0..2 {
            for (i, a) in it.side(side).iter().enumerate() {
                if exclude != Some((side, i)) {
                    g.push(self.p.affixes[a].g);
                }
            }
        }
        g
    }

    fn open_affixes(&self, pool: &[usize], used: &Small<16>, out: &mut Vec<usize>) {
        out.clear();
        out.extend(pool.iter().copied().filter(|&a| !used.contains(self.p.affixes[a].g)));
    }

    /// fill_item(): implicits for missing groups, trim to capacity, then fill prefixes and suffixes.
    fn fill_item(&self, b: &mut Build, slot: usize, rng: &mut Rng) {
        let p = self.p;
        let ty = b.items[slot].ty();
        let t = &p.types[ty];
        for g in b.items[slot].implicits.len()..t.implicit_groups.len() {
            let a = rng.choice(&t.implicit_groups[g]);
            b.items[slot].implicits.push(a);
        }
        let (pre_n, suf_n, cap) = self.capacity(&b.items[slot]);
        {
            let it = &mut b.items[slot];
            while (it.prefixes.len() + it.suffixes.len()) as i64 > cap {
                if !it.suffixes.is_empty() && it.suffixes.len() >= it.prefixes.len() {
                    it.suffixes.pop();
                } else if !it.prefixes.is_empty() {
                    it.prefixes.pop();
                } else {
                    break;
                }
            }
        }
        let mut opts = Vec::with_capacity(64);
        for side in 0..2 {
            let n = if side == 0 { pre_n } else { suf_n };
            loop {
                let it = &b.items[slot];
                if !(it.side(side).len() < n && ((it.prefixes.len() + it.suffixes.len()) as i64) < cap) {
                    break;
                }
                let used = self.used_groups(it, None);
                self.open_affixes(if side == 0 { &t.prefix } else { &t.suffix }, &used, &mut opts);
                if opts.is_empty() {
                    break;
                }
                let a = self.pick_affix(b, &opts, rng);
                b.items[slot].side_mut(side).push(a);
            }
        }
    }

    fn new_item(&self, b: &mut Build, slot: usize, ty: usize, rng: &mut Rng) {
        b.items[slot] = Item::new(ty);
        self.fill_item(b, slot, rng);
    }

    fn etching_choices(&self, b: &Build, slot: usize, out: &mut Vec<usize>) {
        out.clear();
        let mut taken = Small::<8>::new();
        for (s, it) in b.items.iter().enumerate() {
            if s != slot {
                if let Some(e) = it.etching {
                    taken.push(e as usize);
                }
            }
        }
        out.extend(self.p.types[b.items[slot].ty()].etchings.iter().copied()
            .filter(|&e| !taken.contains(e) || self.p.etchings[e].stacking));
    }

    fn trinket_feasible(&self, lst: &Small<MAX_TRINKETS>) -> bool {
        let slots = &self.p.trinket_slots;
        let total: usize = slots.iter().sum();
        for i in 0..lst.len() {
            for j in i + 1..lst.len() {
                if lst.get(i) == lst.get(j) {
                    return false;
                }
            }
        }
        let mut counts = [0usize; 8];
        for t in lst.iter() {
            counts[self.p.trinkets[t].color] += 1;
        }
        if self.p.trinket_fusion {
            let anchors: usize = counts.iter().zip(slots).map(|(&n, &s)| n.min(s)).sum();
            return lst.len() <= 2 * total && lst.len().saturating_sub(total) <= anchors;
        }
        counts.iter().zip(slots).all(|(&n, &s)| n <= s)
    }

    fn talent_cost(&self, t: usize, n: usize) -> f64 {
        if n == 0 { 0.0 } else { self.p.talents[t].cost[n.min(self.p.talents[t].cost.len() - 1)] }
    }

    /// Skill gates (search.talent_legal): points spent on other talents, required talents, either-of groups, lock-outs.
    fn talent_legal(&self, b: &Build) -> bool {
        let mut spent: Option<f64> = None;
        for g in &self.p.gates {
            if b.talents[g.t] == 0 {
                continue;
            }
            if g.spent > 0.0 {
                let sp = *spent.get_or_insert_with(|| (0..self.p.talents.len()).map(|t| self.talent_cost(t, b.tal(t))).sum());
                if sp - self.talent_cost(g.t, b.tal(g.t)) < g.spent {
                    return false;
                }
            }
            if g.deps.iter().any(|&x| x < 0 || b.talents[x as usize] == 0) {
                return false;
            }
            if g.either.iter().any(|grp| !grp.iter().any(|&x| b.talents[x] > 0)) {
                return false;
            }
            if g.locked.iter().any(|&x| b.talents[x] > 0) {
                return false;
            }
        }
        true
    }

    fn count_seals(&self, b: &Build) -> usize {
        b.items.iter().filter(|it| it.seal.is_some()).count()
    }

    fn count_uniques(&self, b: &Build) -> usize {
        b.items.iter().filter(|it| it.unique.is_some()).count()
    }

    fn unique_seal_legal(&self, b: &Build) -> bool {
        b.items.iter().filter(|it| it.unique.is_some() && it.seal.is_some()).count() <= self.p.unique_seals_max
            && self.count_seals(b) <= self.p.seals
    }

    /// Uniques that may go in this slot for this family (Context.unique_choices).
    fn unique_choices(&self, slot: usize, out: &mut Vec<usize>) {
        out.clear();
        let p = self.p;
        out.extend((0..p.uniques.len()).filter(|&u| {
            let un = &p.uniques[u];
            un.slot == slot && (slot != MAINHAND || p.fam.mainhands.contains(&un.ty))
        }));
    }

    /// equip_unique(): swap the whole piece for unique `u`, parking the normal piece's seal, etching and unusual.
    fn equip_unique(&self, b: &mut Build, slot: usize, u: usize, rng: &mut Rng) {
        let ty = self.p.uniques[u].ty;
        if b.items[slot].ty() != ty {
            self.new_item(b, slot, ty, rng);
        }
        let it = &mut b.items[slot];
        if it.unique.is_none() {
            it.shadow = [it.seal, it.etching, it.unusual];
        }
        it.unique = Some(u as u16);
        it.udrop = None;
        it.seal = None;
        it.etching = None;
        it.unusual = None;
    }

    /// unequip_unique(): back to the parked normal piece, dropping a parked seal, etching or unusual that is no longer legal.
    fn unequip_unique(&self, b: &mut Build, slot: usize, rng: &mut Rng) {
        let [seal, etching, unusual] = b.items[slot].shadow;
        {
            let it = &mut b.items[slot];
            it.unique = None;
            it.udrop = None;
            it.shadow = [None; 3];
        }
        if seal.is_some() && self.count_seals(b) < self.p.seals {
            b.items[slot].seal = seal;
        }
        if unusual.is_some() && b.items.iter().filter(|it| it.unusual.is_some()).count() < self.p.unusual {
            b.items[slot].unusual = unusual;
        }
        if let Some(e) = etching {
            let mut es = Vec::new();
            self.etching_choices(b, slot, &mut es);
            if es.contains(&(e as usize)) {
                b.items[slot].etching = Some(e);
            }
        }
        self.fill_item(b, slot, rng);
    }

    fn greed_frontier(&self, owned: &Bits, out: &mut Vec<usize>) {
        let p = self.p;
        out.clear();
        for n in 0..p.greed.len() {
            if !owned.has(n) && !p.greed[n].free && p.greed[n].parents.iter().any(|&q| owned.has(q) || p.greed[q].free) {
                out.push(n);
            }
        }
    }

    fn greed_leaves(&self, owned: &Bits, out: &mut Vec<usize>) {
        out.clear();
        for n in owned.iter() {
            if !self.p.greed_children[n].iter().any(|&c| c != n && owned.has(c)) {
                out.push(n);
            }
        }
    }

    fn base_of(&self, s: usize) -> usize {
        self.p.fam.baseline.iter().find(|(x, _)| *x == s).map(|x| x.1).unwrap_or(0)
    }

    fn main_spec(&self) -> Option<usize> {
        match self.p.fam.kind {
            FamKind::Ability { main } => Some(main),
            _ => None,
        }
    }

    fn pick_charm_mods(&self, god: usize, rng: &mut Rng) -> Small<MAX_MODS> {
        let mut idx: Vec<usize> = (0..self.p.gods[god].mods.len()).collect();
        rng.shuffle(&mut idx);
        let mut out = Small::new();
        let mut groups = Small::<16>::new();
        for m in idx {
            let g = self.p.gods[god].mods[m].g;
            if groups.contains(g) {
                continue;
            }
            out.push(m);
            groups.push(g);
            if out.len() >= self.p.charm_prefixes {
                break;
            }
        }
        out
    }

    pub fn initial_build(&self, rng: &mut Rng) -> Build {
        let p = self.p;
        let mut b = Build::empty();
        if let Some(m) = self.main_spec() {
            b.abilities[m] = p.specs[m].max as u8;
        }
        for &(s, n) in &p.fam.baseline {
            b.abilities[s] = b.abilities[s].max(n as u8);
        }
        for (k, &t) in p.armor_types.iter().enumerate() {
            self.new_item(&mut b, k, t, rng);
        }
        let ty = rng.choice(&p.fam.mainhands);
        self.new_item(&mut b, MAINHAND, ty, rng);
        let ty = rng.choice(&p.offhand_types);
        self.new_item(&mut b, OFFHAND, ty, rng);
        self.new_item(&mut b, NECKLACE, p.necklace_type, rng);
        for (color, &n) in p.trinket_slots.iter().enumerate() {
            let mut opts: Vec<usize> = (0..p.trinkets.len()).filter(|&t| p.trinkets[t].color == color).collect();
            rng.shuffle(&mut opts);
            for t in opts.into_iter().take(n) {
                b.trinkets.push(t);
            }
        }
        b.god = rng.below(p.gods.len()) as u16;
        b.mods = self.pick_charm_mods(b.god as usize, rng);
        for o in &p.deck {
            b.deck.push(rng.below(o.len()));
        }
        let mut fr = Vec::new();
        while b.greed.count() < p.greed_budget {
            self.greed_frontier(&b.greed, &mut fr);
            if fr.is_empty() {
                break;
            }
            b.greed.set(rng.choice(&fr));
        }
        b.bugs = if p.mode_bugged { (1u32 << BUG_NAMES.len()) - 1 } else { 0 };
        b
    }

    /// One move of the given kind on a copy of `b`; None when that draw is illegal.
    fn try_move(&self, kind: u8, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<Build> {
        let p = self.p;
        let mut n = *b;
        match kind {
            0 => {
                let slot = rng.below(N_SLOTS);
                if n.items[slot].unique.is_some() {
                    return None;
                }
                let side = rng.below(2);
                let len = n.items[slot].side(side).len();
                if len == 0 {
                    self.fill_item(&mut n, slot, rng);
                    return Some(n);
                }
                let i = rng.below(len);
                let used = self.used_groups(&n.items[slot], Some((side, i)));
                let t = &p.types[n.items[slot].ty()];
                self.open_affixes(if side == 0 { &t.prefix } else { &t.suffix }, &used, buf);
                if buf.is_empty() {
                    return None;
                }
                let a = self.pick_affix(&n, buf, rng);
                n.items[slot].side_mut(side).set(i, a);
            }
            1 => {
                let slot = rng.below(N_SLOTS);
                if n.items[slot].unique.is_some() {
                    return None;
                }
                let groups = &p.types[n.items[slot].ty()].implicit_groups;
                if groups.is_empty() {
                    return None;
                }
                let g = rng.below(groups.len());
                n.items[slot].implicits.set(g, rng.choice(&groups[g]));
            }
            2 => {
                let ty = rng.choice(&p.fam.mainhands);
                self.new_item(&mut n, MAINHAND, ty, rng);
            }
            3 => {
                let ty = rng.choice(&p.offhand_types);
                self.new_item(&mut n, OFFHAND, ty, rng);
            }
            4 | 5 => {
                let seal = kind == 4;
                let slot = rng.below(N_SLOTS);
                if let Some(u) = n.items[slot].unique {
                    if !seal {
                        return None;
                    }
                    let un = &p.uniques[u as usize];
                    if n.items[slot].seal.is_some() && rng.f64() < 0.4 {
                        n.items[slot].seal = None;
                        n.items[slot].udrop = None;
                    } else {
                        if p.unique_seals.is_empty() {
                            return None;
                        }
                        n.items[slot].seal = Some(rng.choice(&p.unique_seals) as u16);
                        n.items[slot].udrop = if un.explicit.is_empty() { None } else { Some(rng.choice(&un.explicit) as u8) };
                    }
                    if !self.unique_seal_legal(&n) {
                        return None;
                    }
                    return Some(n);
                }
                let count = n.items.iter().filter(|it| if seal { it.seal.is_some() } else { it.unusual.is_some() }).count();
                let t = &p.types[n.items[slot].ty()];
                let (cur, pool, cap) = if seal { (n.items[slot].seal, &t.seal, p.seals) } else { (n.items[slot].unusual, &t.unusual, p.unusual) };
                if cur.is_some() && rng.f64() < 0.4 {
                    if seal { n.items[slot].seal = None } else { n.items[slot].unusual = None }
                } else {
                    if pool.is_empty() || (cur.is_none() && count >= cap) {
                        return None;
                    }
                    let a = Some(rng.choice(pool) as u16);
                    if seal { n.items[slot].seal = a } else { n.items[slot].unusual = a }
                }
                self.fill_item(&mut n, slot, rng);
            }
            6 => {
                let slot = rng.below(N_SLOTS - 1);
                self.etching_choices(&n, slot, buf);
                n.items[slot].etching = if buf.is_empty() || rng.f64() < 0.2 { None } else { Some(rng.choice(buf) as u16) };
            }
            7 => {
                buf.clear();
                buf.extend((0..p.trinkets.len()).filter(|&t| !n.trinkets.contains(t)));
                let r = rng.f64();
                if r < 0.3 && !buf.is_empty() {
                    n.trinkets.push(rng.choice(buf));
                } else if r < 0.45 && !n.trinkets.is_empty() {
                    let i = rng.below(n.trinkets.len());
                    n.trinkets.remove(i);
                } else if !n.trinkets.is_empty() && !buf.is_empty() {
                    let i = rng.below(n.trinkets.len());
                    n.trinkets.set(i, rng.choice(buf));
                } else {
                    return None;
                }
                if !self.trinket_feasible(&n.trinkets) {
                    return None;
                }
            }
            8 => {
                let god = if rng.f64() < 0.3 { rng.below(p.gods.len()) } else { n.god as usize };
                n.god = god as u16;
                n.mods = self.pick_charm_mods(god, rng);
            }
            9 => {
                if n.deck.is_empty() {
                    return None;
                }
                let i = rng.below(n.deck.len());
                if rng.f64() < 0.3 && n.deck.len() > 1 {
                    let j = rng.below(n.deck.len());
                    let (a, c) = (p.deck[i][n.deck.get(i)].card, p.deck[j][n.deck.get(j)].card);
                    if a == c {
                        return None;
                    }
                    let (Some(ci), Some(aj)) = (p.deck[i].iter().position(|o| o.card == c), p.deck[j].iter().position(|o| o.card == a)) else {
                        return None;
                    };
                    n.deck.set(i, ci);
                    n.deck.set(j, aj);
                } else {
                    n.deck.set(i, rng.below(p.deck[i].len()));
                }
            }
            10 => {
                let tid = rng.below(p.talents.len());
                let cur = n.tal(tid) as i64;
                let mx = p.talents[tid].max as i64;
                let delta = [-1, 1, 1, mx][rng.below(4)];
                let new = (cur + delta).clamp(0, mx);
                if new == cur {
                    return None;
                }
                n.talents[tid] = new as u8;
                if !self.talent_legal(&n) {
                    return None;
                }
                if !n.points_ok(p) {
                    let main = self.main_spec();
                    let mut cands: Vec<(bool, usize)> = Vec::with_capacity(32);
                    for _ in 0..6 {
                        cands.clear();
                        cands.extend((0..p.talents.len()).filter(|&t| n.talents[t] > 0 && t != tid).map(|t| (true, t)));
                        cands.extend((0..p.specs.len()).filter(|&s| n.abil(s) > self.base_of(s) && Some(s) != main).map(|s| (false, s)));
                        if cands.is_empty() {
                            break;
                        }
                        let (is_t, v) = cands[rng.below(cands.len())];
                        if is_t { n.talents[v] -= 1 } else { n.abilities[v] -= 1 }
                        if n.points_ok(p) {
                            break;
                        }
                    }
                    if !n.points_ok(p) || !self.talent_legal(&n) {
                        return None;
                    }
                }
            }
            11 => {
                let main = self.main_spec();
                let nb = p.buff_specs.len() + main.is_some() as usize;
                let k = rng.below(nb);
                let sid = if k < p.buff_specs.len() { p.buff_specs[k] } else { main.unwrap() };
                if p.specs[sid].baseline_skill {
                    return None;
                }
                let skill = p.specs[sid].skill;
                for other in 0..p.specs.len() {
                    if other != sid && n.abilities[other] > 0 && p.specs[other].skill == skill {
                        if Some(other) == main {
                            return None;
                        }
                        n.abilities[other] = 0;
                    }
                }
                let cur = n.abil(sid) as i64;
                let mx = p.specs[sid].max as i64;
                let mut new = (cur + [-1, 1, mx, -mx][rng.below(4)]).clamp(0, mx);
                if Some(sid) == main {
                    new = new.max(1);
                }
                n.abilities[sid] = new as u8;
                if !n.points_ok(p) {
                    return None;
                }
            }
            13 => {
                let slot = UNIQUE_SLOTS[rng.below(UNIQUE_SLOTS.len())];
                self.unique_choices(slot, buf);
                let cur = n.items[slot].unique.map(|u| u as usize);
                buf.retain(|&u| Some(u) != cur);
                if cur.is_some() && (buf.is_empty() || rng.f64() < 0.5) {
                    self.unequip_unique(&mut n, slot, rng);
                } else {
                    if buf.is_empty() || (cur.is_none() && self.count_uniques(&n) >= p.unique_max) {
                        return None;
                    }
                    let u = rng.choice(buf);
                    self.equip_unique(&mut n, slot, u, rng);
                }
                if !self.unique_seal_legal(&n) {
                    return None;
                }
            }
            14 => {
                self.main_spec()?;
                n.weave = !n.weave;
            }
            _ => {
                self.greed_leaves(&n.greed, buf);
                if !buf.is_empty() && (n.greed.count() >= p.greed_budget || rng.f64() < 0.5) {
                    n.greed.clear(rng.choice(buf));
                }
                loop {
                    if n.greed.count() >= p.greed_budget {
                        break;
                    }
                    self.greed_frontier(&n.greed, buf);
                    if buf.is_empty() {
                        break;
                    }
                    n.greed.set(rng.choice(buf));
                }
                if !n.points_ok(p) {
                    return None;
                }
            }
        }
        Some(n)
    }

    /// A legal candidate that differs from `b`: illegal or identical draws are redrawn instead of costing an evaluation.
    pub fn propose(&mut self, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Build {
        for _ in 0..500 {
            let kind = MOVES[rng.below(MOVES.len())];
            match self.try_move(kind, b, rng, buf) {
                Some(n) if n != *b => return n,
                _ => self.illegal += 1,
            }
        }
        self.no_legal += 1;
        *b
    }

    /// Old proposal rule (model/search.py): one draw per iteration; an illegal draw uses up the iteration and an
    /// unchanged candidate is still scored. Kept for benchmarking against the legal-only proposals.
    fn propose_legacy(&mut self, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<Build> {
        let kind = MOVES[rng.below(MOVES.len())];
        let r = self.try_move(kind, b, rng, buf);
        if r.is_none() {
            self.illegal += 1;
        }
        r
    }

    pub fn polish(&mut self, b: Build, rounds: usize) -> (Build, f64) {
        let p = self.p;
        let mut b = b;
        let (mut best_s, mut b_lin) = self.score(&b);
        let mut es = Vec::new();
        for _ in 0..rounds {
            let mut improved = false;
            macro_rules! consider {
                ($c:expr) => {{
                    let c = $c;
                    let (s2, l2) = self.score_from(&b, &b_lin, &c);
                    if s2 > best_s + 1e-9 {
                        b = c;
                        b_lin = l2;
                        best_s = s2;
                        improved = true;
                    }
                }};
            }
            if self.main_spec().is_some() {
                let mut c = b;
                c.weave = !c.weave;
                consider!(c);
            }
            let mut us = Vec::new();
            for &slot in &UNIQUE_SLOTS {
                self.unique_choices(slot, &mut us);
                let cur = b.items[slot].unique.map(|u| u as usize);
                let opts: Vec<Option<usize>> = std::iter::once(None).chain(us.iter().map(|&u| Some(u))).collect();
                for u in opts {
                    if u == cur {
                        continue;
                    }
                    let mut c = b;
                    let mut r0 = Rng::new(0);
                    match u {
                        None => self.unequip_unique(&mut c, slot, &mut r0),
                        Some(u) => {
                            if cur.is_none() && self.count_uniques(&c) >= p.unique_max {
                                continue;
                            }
                            self.equip_unique(&mut c, slot, u, &mut r0);
                        }
                    }
                    if self.unique_seal_legal(&c) {
                        consider!(c);
                    }
                }
            }
            for slot in 0..N_SLOTS {
                if let Some(u) = b.items[slot].unique {
                    let un = &p.uniques[u as usize];
                    let seals: Vec<Option<u16>> = std::iter::once(None).chain(p.unique_seals.iter().map(|&a| Some(a as u16))).collect();
                    for sl in seals {
                        let drops: Vec<Option<u8>> = if sl.is_none() || un.explicit.is_empty() {
                            vec![None]
                        } else {
                            un.explicit.iter().map(|&e| Some(e as u8)).collect()
                        };
                        for dr in drops {
                            if sl == b.items[slot].seal && dr == b.items[slot].udrop {
                                continue;
                            }
                            let mut c = b;
                            c.items[slot].seal = sl;
                            c.items[slot].udrop = dr;
                            if self.unique_seal_legal(&c) {
                                consider!(c);
                            }
                        }
                    }
                    self.etching_choices(&b, slot, &mut es);
                    let opts: Vec<Option<u16>> = std::iter::once(None).chain(es.iter().map(|&e| Some(e as u16))).collect();
                    for e in opts {
                        if b.items[slot].etching == e {
                            continue;
                        }
                        let mut c = b;
                        c.items[slot].etching = e;
                        consider!(c);
                    }
                    continue;
                }
                let t = &p.types[b.items[slot].ty()];
                for side in 0..2 {
                    let pool = if side == 0 { &t.prefix } else { &t.suffix };
                    let len = b.items[slot].side(side).len();
                    for i in 0..len {
                        for &a in pool {
                            let it = &b.items[slot];
                            if a == it.side(side).get(i) || self.used_groups(it, Some((side, i))).contains(p.affixes[a].g) {
                                continue;
                            }
                            let mut c = b;
                            c.items[slot].side_mut(side).set(i, a);
                            consider!(c);
                        }
                    }
                }
                for (g, opts) in t.implicit_groups.iter().enumerate() {
                    for &a in opts {
                        if b.items[slot].implicits.get(g) == a {
                            continue;
                        }
                        let mut c = b;
                        c.items[slot].implicits.set(g, a);
                        consider!(c);
                    }
                }
                if slot != NECKLACE {
                    self.etching_choices(&b, slot, &mut es);
                    let opts: Vec<Option<u16>> = std::iter::once(None).chain(es.iter().map(|&e| Some(e as u16))).collect();
                    for e in opts {
                        if b.items[slot].etching == e {
                            continue;
                        }
                        let mut c = b;
                        c.items[slot].etching = e;
                        consider!(c);
                    }
                }
            }
            for i in 0..p.deck.len() {
                for o in 0..p.deck[i].len() {
                    if b.deck.get(i) == o {
                        continue;
                    }
                    let mut c = b;
                    c.deck.set(i, o);
                    consider!(c);
                }
            }
            for i in 0..b.trinkets.len() {
                for t in 0..p.trinkets.len() {
                    if b.trinkets.contains(t) {
                        continue;
                    }
                    let mut c = b;
                    c.trinkets.set(i, t);
                    if self.trinket_feasible(&c.trinkets) {
                        consider!(c);
                    }
                }
            }
            let god = b.god as usize;
            for i in 0..b.mods.len() {
                for m in 0..p.gods[god].mods.len() {
                    let clash = b.mods.iter().enumerate().any(|(j, x)| j != i && p.gods[god].mods[x].g == p.gods[god].mods[m].g);
                    if b.mods.contains(m) || clash {
                        continue;
                    }
                    let mut c = b;
                    c.mods.set(i, m);
                    consider!(c);
                }
            }
            if !improved {
                break;
            }
        }
        (b, best_s)
    }

    fn same_output(&mut self, a: &Build, c: &Build) -> bool {
        self.evals += 2;
        let (ra, rc) = match (evaluate(a, self.p, &mut self.fb), evaluate(c, self.p, &mut self.fb)) {
            (Ok(x), Ok(y)) => (x, y),
            _ => return false,
        };
        (ra.dps - rc.dps).abs() <= 1e-6 * ra.dps.max(1.0)
            && (ra.pack_dps - rc.pack_dps).abs() <= 1e-6 * ra.pack_dps.max(1.0)
            && (ra.ehp - rc.ehp).abs() <= 1e-6 * ra.ehp.max(1.0)
    }

    pub fn strip_idle_etchings(&mut self, mut b: Build, mut s: f64) -> (Build, f64) {
        for slot in 0..N_SLOTS {
            if b.items[slot].etching.is_none() {
                continue;
            }
            let mut c = b;
            c.items[slot].etching = None;
            let s2 = self.score(&c).0;
            if s2 >= s - 1e-9 && self.same_output(&b, &c) {
                b = c;
                s = s.max(s2);
            }
        }
        (b, s)
    }

    pub fn strip_idle_points(&mut self, mut b: Build, mut s: f64) -> (Build, f64) {
        let p = self.p;
        let main = self.main_spec();
        for _ in 0..2 {
            let mut changed = false;
            let mut ts: Vec<usize> = (0..p.talents.len()).filter(|&t| b.talents[t] > 0).collect();
            ts.sort_by(|x, y| p.talent_names[*x].cmp(&p.talent_names[*y]));
            for t in ts {
                let mut c = b;
                c.talents[t] = 0;
                if !self.talent_legal(&c) {
                    continue;
                }
                let s2 = self.score(&c).0;
                if s2 >= s - 1e-9 && self.same_output(&b, &c) {
                    b = c;
                    s = s.max(s2);
                    changed = true;
                }
            }
            let mut ss: Vec<usize> = (0..p.specs.len())
                .filter(|&a| b.abilities[a] > 0 && Some(a) != main && !p.fam.baseline.iter().any(|(x, _)| *x == a)).collect();
            ss.sort_by(|x, y| p.spec_names[*x].cmp(&p.spec_names[*y]));
            for a in ss {
                let mut c = b;
                c.abilities[a] = 0;
                let s2 = self.score(&c).0;
                if s2 >= s - 1e-9 && self.same_output(&b, &c) {
                    b = c;
                    s = s.max(s2);
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
        (b, s)
    }

    pub fn anneal(&mut self, iters: usize, seed: u64, sched: &Schedule, want_trace: bool, legacy: bool) -> (Build, f64, Trace) {
        let t_start = std::time::Instant::now();
        let ev0 = self.evals;
        let il0 = self.illegal;
        let mut rng = Rng::new(seed);
        let mut buf = Vec::with_capacity(128);
        let mut tr = Trace::default();
        let init = self.initial_build(&mut rng);
        tr.start = self.score(&init).0;
        let (mut cur, mut cur_s) = self.polish(init, 1);
        tr.after_polish1 = cur_s;
        let mut cur_lin = lin_full(&cur, self.p);
        let mut best = cur;
        let mut best_s = cur_s;
        let checkpoints: Vec<usize> = [0.1, 0.25, 0.5, 0.75, 1.0].iter().map(|f| ((iters as f64 * f) as usize).max(1) - 1).collect();
        let mut ring: Vec<f64> = Vec::new();
        let mut ring_pos = 0usize;
        let mut temp = match sched { Schedule::Geometric { t0, .. } => *t0, Schedule::Target { t_init, .. } => *t_init, _ => 0.1 };
        let mut start_i = 0usize;
        let mut win_d: Vec<f64> = Vec::new();
        let mut win_acc = 0u64;
        let mut accepts_since_sync = 0u64;
        if let Schedule::Adaptive { window, calib, .. } = sched {
            for _ in 0..(*calib).min(iters) {
                let cand = self.propose(&cur, &mut rng, &mut buf);
                let (s, _) = self.score_from(&cur, &cur_lin, &cand);
                let dlt = s - cur_s;
                if dlt < -1e-12 && dlt > -1e6 {
                    push_ring(&mut ring, &mut ring_pos, *window, -dlt);
                }
            }
            start_i = (*calib).min(iters);
        }
        for i in start_i..iters {
            let frac = i as f64 / (iters.max(2) - 1) as f64;
            match sched {
                Schedule::Geometric { t0, t1 } => temp = t0 * (t1 / t0).powf(frac),
                Schedule::Adaptive { p0, p1, q, .. } => {
                    if (i - start_i) % 50 == 0 && !ring.is_empty() {
                        let mut v = ring.clone();
                        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                        let m = v[((v.len() - 1) as f64 * q).round() as usize];
                        let pt = p0 * (p1 / p0).powf(frac);
                        temp = (m / (1.0 / pt).ln()).max(1e-6);
                    }
                }
                Schedule::Target { .. } => {}
            }
            if want_trace && i % (iters / 20).max(1) == 0 {
                tr.temps.push((frac, temp));
                if !win_d.is_empty() {
                    win_d.sort_by(|a, b| a.partial_cmp(b).unwrap());
                    tr.windows.push((frac, temp, win_acc as f64 / win_d.len() as f64, win_d[win_d.len() / 2]));
                }
                win_d.clear();
                win_acc = 0;
            }
            let cand = if legacy {
                match self.propose_legacy(&cur, &mut rng, &mut buf) {
                    Some(c) => c,
                    None => {
                        if want_trace && checkpoints.contains(&i) {
                            tr.checkpoints.push(((i + 1) as f64 / iters as f64, best_s));
                        }
                        continue;
                    }
                }
            } else {
                self.propose(&cur, &mut rng, &mut buf)
            };
            let (s, lin) = self.score_from(&cur, &cur_lin, &cand);
            let dlt = s - cur_s;
            let worse = dlt < -1e-12;
            if worse {
                tr.worse_proposed += 1;
                if dlt > -1e6 {
                    if let Schedule::Adaptive { window, .. } = sched {
                        push_ring(&mut ring, &mut ring_pos, *window, -dlt);
                    }
                    if want_trace {
                        win_d.push(-dlt);
                    }
                }
            }
            let took = s >= cur_s || rng.f64() < (dlt / temp).exp();
            if let Schedule::Target { a0, a1, eta, .. } = sched {
                if worse && dlt > -1e6 {
                    let target = a0 * (a1 / a0).powf(frac);
                    let hit = if took { 1.0 } else { 0.0 };
                    temp = (temp * (eta * (target - hit)).exp()).clamp(1e-6, 10.0);
                }
            }
            if took {
                tr.accepted += 1;
                if s < cur_s {
                    tr.worse_accepted += 1;
                }
                if want_trace && worse && dlt > -1e6 {
                    win_acc += 1;
                }
                cur = cand;
                cur_s = s;
                cur_lin = lin;
                accepts_since_sync += 1;
                if accepts_since_sync >= RESYNC_ACCEPTS {
                    cur_lin = lin_full(&cur, self.p);
                    accepts_since_sync = 0;
                }
                if s > best_s {
                    best = cur;
                    best_s = s;
                }
            }
            if want_trace && checkpoints.contains(&i) {
                tr.checkpoints.push(((i + 1) as f64 / iters as f64, best_s));
            }
        }
        tr.sa_best = best_s;
        let (b, s) = self.polish(best, 3);
        tr.after_polish3 = s;
        let (b, s) = self.strip_idle_etchings(b, s);
        let (b, _) = self.strip_idle_points(b, s);
        let s = self.score(&b).0;
        tr.final_score = s;
        tr.evals = self.evals - ev0;
        tr.illegal_draws = self.illegal - il0;
        tr.secs = t_start.elapsed().as_secs_f64();
        (b, s, tr)
    }
}

fn push_ring(ring: &mut Vec<f64>, pos: &mut usize, window: usize, x: f64) {
    if ring.len() < window {
        ring.push(x);
    } else {
        ring[*pos] = x;
        *pos = (*pos + 1) % window;
    }
}
