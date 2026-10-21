//! Simulated annealing over legal builds (model/search.py), with proposals drawn only from legal, non-identical moves.
//! Candidates are scored by delta: their additive stat totals are patched from the current build's totals.

use crate::build::*;
use crate::eval::{evaluate, score_lin_relaxed};
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

/// Experimental search options (per job; the defaults reproduce the plain anneal).
#[derive(Deserialize, Clone)]
pub struct SearchOpts {
    /// God moves are accepted at temperature x god_t.
    #[serde(default = "one_f")]
    pub god_t: f64,
    /// After a god move, this many greedy gear steps before the god move is judged on the repaired score.
    #[serde(default)]
    pub repair: usize,
    /// Weight god-move node picks by a probe of each minor/major node's score gain on the starting build.
    #[serde(default)]
    pub weights: bool,
    /// Parallel tempering with this many replicas (0 = plain anneal); fixed temperatures pt_cold..pt_hot.
    #[serde(default)]
    pub pt: usize,
    #[serde(default = "pt_hot")]
    pub pt_hot: f64,
    #[serde(default = "pt_cold")]
    pub pt_cold: f64,
    #[serde(default = "pt_swap")]
    pub pt_swap: usize,
    /// Rework deck menus: also move between the design's top core sets.
    #[serde(default)]
    pub cset: bool,
    /// Deck-only search: only deck moves (cards, design, core set), a deck-only polish, and the rest of the build
    /// (gear, gods, talents, secondaries) kept exactly as given.
    #[serde(default)]
    pub deck_only: bool,
}

fn one_f() -> f64 {
    1.0
}
fn pt_hot() -> f64 {
    0.3
}
fn pt_cold() -> f64 {
    0.002
}
fn pt_swap() -> usize {
    20
}

impl Default for SearchOpts {
    fn default() -> Self {
        SearchOpts { god_t: 1.0, repair: 0, weights: false, pt: 0, pt_hot: pt_hot(), pt_cold: pt_cold(), pt_swap: pt_swap(), cset: false, deck_only: false }
    }
}

pub struct Search<'a> {
    pub p: &'a Problem,
    pub fb: Fallbacks,
    pub evals: u64,
    pub illegal: u64,
    pub no_legal: u64,
    /// Re-score every delta-scored candidate from scratch and count disagreements (testing only).
    /// Start the anneal from this build (rework Edge of Time pass) instead of a random one.
    /// Rework: the god a restart starts as primary and charm god (from the restart strata in anneal()).
    /// Rework: a major the restart starts with (path forced), -1 for none.
    /// Rework: run this restart in one stratum (index into the strata) with its charm god and major locked.
    pub stratum: i64,
    pub major_hint: i64,
    pub god_hint: Option<usize>,
    pub init: Option<Build>,
    pub force_eot: bool,
    /// Rework polish: for every unowned minor or major node, buy its path and greedily sell the cheapest nodes.
    pub path_polish: bool,
    /// MOVES without the kinds that can never be legal for this problem (draws of them were always redrawn).
    pub moves: Vec<u8>,
    pub moves_gear: Vec<u8>,
    pub opts: SearchOpts,
    pub last_kind: u8,
    /// Probe weights per global god node (empty = uniform picks).
    pub gw: Vec<f64>,
    /// The stratum's major was out of reach of the initial build, so the restart ran unlocked.
    pub lock_dropped: bool,
    pub verify_delta: bool,
    pub delta_checked: u64,
    pub delta_mismatch: u64,
    pub delta_max_err: f64,
    /// Share of the movement-minimum penalty given back while scoring (1 at the start of an anneal, 0 from
    /// MOVE_RELAX_END of its steps on): the search may cross slow builds early but must end at the minimum.
    pub move_relax: f64,
    /// Unique bench: (slot, unique index) the build must keep for the whole search.
    pub ulock: Option<(usize, u16)>,
    /// The unique lock could not be made legal on the starting build; the search ran unlocked.
    pub ulock_failed: bool,
    /// Why the unique lock failed: "mainhand" (not a weapon this family uses), "time-lock" (Temporal Breaking could
    /// not be bought), "illegal" (god tree or ancient limit), "limits" (unique count or seals).
    pub ulock_reason: &'static str,
    /// Rework deck menus: start design (-1 = by seed), allowed designs (empty = all), design fixed for the whole
    /// search, and whether design switches are open (the first half of an anneal).
    pub design_hint: i64,
    pub design_allowed: Vec<u8>,
    pub design_lock: bool,
    pub design_open: bool,
    pub rcache: crate::build::RankCache,
}

/// Share of an anneal's steps over which the movement-minimum penalty ramps from relaxed to full.
const MOVE_RELAX_END: f64 = 0.5;

const MOVES: [u8; 20] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 10, 10, 11, 12, 12, 13, 14, 15, 15];
/// Slots a unique can take (armor, mainhand, offhand).
const UNIQUE_SLOTS: [usize; 6] = [0, 1, 2, 3, MAINHAND, OFFHAND];
const RESYNC_ACCEPTS: u64 = 1000;
/// Greedy card moves after a deck design switch, before the switch is judged.
const DESIGN_REPAIR: usize = 8;
/// Capacity of the per-call affix and charm-mod scratch arrays (checked at load).
pub const MAX_POOL: usize = 256;
pub const MAX_CHARM_POOL: usize = 64;

impl<'a> Search<'a> {
    pub fn new(p: &'a Problem) -> Search<'a> {
        let n_holes = p.specs.iter().flat_map(|s| s.cfg.iter().flat_map(|v| v.iter().filter_map(|c| c.hole))).max().map(|m| m + 1).unwrap_or(0);
        Search {
            p, fb: Fallbacks { holes_used: vec![false; n_holes], ..Fallbacks::default() },
            evals: 0, illegal: 0, no_legal: 0, init: None, god_hint: None, major_hint: -1, stratum: -1, force_eot: false, path_polish: true, moves: MOVES.iter().copied().filter(|&k| !(k == 5 && p.unusual == 0) ).collect(),
            moves_gear: Vec::new(), opts: SearchOpts::default(), last_kind: 0, gw: Vec::new(), lock_dropped: false, verify_delta: false, delta_checked: 0, delta_mismatch: 0, delta_max_err: 0.0,
            move_relax: 0.0, ulock: None, ulock_failed: false, ulock_reason: "", rcache: Default::default(),
            design_hint: -1, design_allowed: Vec::new(), design_lock: false, design_open: false,
        }
    }

    /// Designs this search may use (all when the job names none).
    fn allowed_designs(&self) -> Vec<usize> {
        if self.design_allowed.is_empty() {
            (0..self.p.designs.len()).collect()
        } else {
            self.design_allowed.iter().map(|&d| d as usize).collect()
        }
    }

    fn gear_moves(&self) -> Vec<u8> {
        self.moves.iter().copied().filter(|&k| k != 12 && k != 15).collect()
    }

    /// Full score (no delta).
    pub fn score(&mut self, b: &Build) -> (f64, Lin) {
        self.evals += 1;
        let lin = lin_full(b, self.p);
        (score_lin_relaxed(b, self.p, &lin, &mut self.fb, self.move_relax), lin)
    }

    /// Delta score of `b` from a scored neighbour `prev`.
    pub fn score_from(&mut self, prev: &Build, prev_lin: &Lin, b: &Build) -> (f64, Lin) {
        self.evals += 1;
        let lin = crate::build::lin_delta_c(prev, prev_lin, b, self.p, Some(&mut self.rcache));
        let s = score_lin_relaxed(b, self.p, &lin, &mut self.fb, self.move_relax);
        if self.verify_delta {
            let full = score_lin_relaxed(b, self.p, &lin_full(b, self.p), &mut self.fb, self.move_relax);
            let err = (full - s).abs();
            self.delta_checked += 1;
            if err > 1e-9 * full.abs().max(1.0) {
                self.delta_mismatch += 1;
                if std::env::var("WVK_DEBUG").is_ok() {
                    crate::survival::SURV_DEBUG.with(|f| f.set(true));
                    eprintln!("[wvk] -- delta path");
                    let a = crate::eval::evaluate_lin(b, self.p, &lin, &mut self.fb);
                    eprintln!("[wvk] -- full path");
                    let f = crate::eval::evaluate_lin(b, self.p, &lin_full(b, self.p), &mut self.fb);
                    crate::survival::SURV_DEBUG.with(|f| f.set(false));
                    if let (Ok(a), Ok(f)) = (a, f) {
                        eprintln!("[wvk] delta {:?} full {:?}", (a.cycle_damage, a.cycle_survival), (f.cycle_damage, f.cycle_survival));
                        eprintln!("[wvk] doc {}", serde_json::to_string(&b.to_doc(self.p)).unwrap_or_default());
                    }
                    let (l1, l2) = (lin, lin_full(b, self.p));
                    if l1.gfx != l2.gfx || l1.sss != l2.sss || l1.gkey != l2.gkey {
                        eprintln!("[wvk]   gfx delta {:?} sss {} gkey {}", l1.gfx, l1.sss, l1.gkey);
                        eprintln!("[wvk]   gfx full  {:?} sss {} gkey {}", l2.gfx, l2.sss, l2.gkey);
                    }
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
        let mut good = [0usize; MAX_POOL];
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

    /// open_affixes into a fixed array; returns the count.
    fn open_affixes_arr(&self, pool: &[usize], used: &Small<16>, out: &mut [usize; MAX_POOL]) -> usize {
        let mut n = 0;
        for &a in pool {
            if !used.contains(self.p.affixes[a].g) {
                out[n] = a;
                n += 1;
            }
        }
        n
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
        let mut opts = [0usize; MAX_POOL];
        for side in 0..2 {
            let n = if side == 0 { pre_n } else { suf_n };
            loop {
                let it = &b.items[slot];
                if !(it.side(side).len() < n && ((it.prefixes.len() + it.suffixes.len()) as i64) < cap) {
                    break;
                }
                let used = self.used_groups(it, None);
                let k = self.open_affixes_arr(if side == 0 { &t.prefix } else { &t.suffix }, &used, &mut opts);
                if k == 0 {
                    break;
                }
                let a = self.pick_affix(b, &opts[..k], rng);
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

    /// `e` is among etching_choices(b, slot).
    fn etching_ok(&self, b: &Build, slot: usize, e: usize) -> bool {
        self.p.types[b.items[slot].ty()].etchings.contains(&e)
            && (self.p.etchings[e].stacking || !b.items.iter().enumerate().any(|(s, it)| s != slot && it.etching == Some(e as u16)))
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
            if self.etching_ok(b, slot, e as usize) {
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

    /// Learned damage secondaries (the rotation's sources besides the main ability).
    fn n_secondaries(&self, b: &Build) -> usize {
        let main = self.main_spec();
        self.p.sec_specs.iter().filter(|&&s| Some(s) != main && b.abil(s) > 0).count()
    }

    /// Secondary rule: while the build has more than max_secondaries secondaries, or its least valuable one is worth
    /// less than `min_value` score, drop that one (ties go to the earlier spec name); with `share`, then enforce the
    /// main-damage share (enforce_main_share).
    pub fn prune_secondaries(&mut self, mut b: Build, min_value: f64, share: bool) -> (Build, f64) {
        let p = self.p;
        let main = self.main_spec();
        let mut s = self.score(&b).0;
        loop {
            let mut ss: Vec<usize> = p.sec_specs.iter().copied().filter(|&x| Some(x) != main && b.abil(x) > 0).collect();
            if ss.is_empty() {
                break;
            }
            ss.sort_by(|x, y| p.spec_names[*x].cmp(&p.spec_names[*y]));
            let mut worst: Option<(f64, Build, f64)> = None;
            for &a in &ss {
                let mut c = b;
                c.abilities[a] = 0;
                let s2 = self.score(&c).0;
                if worst.as_ref().map_or(true, |w| s - s2 < w.0) {
                    worst = Some((s - s2, c, s2));
                }
            }
            let (loss, c, s2) = worst.unwrap();
            if ss.len() > p.knobs.max_secondaries || loss < min_value {
                b = c;
                s = s2;
            } else {
                break;
            }
        }
        if share { self.enforce_main_share(b, s) } else { (b, s) }
    }

    /// search.enforce_main_share: while the main ability deals less than main_damage_share_min of the damage, drop the
    /// secondary (or weaving) that costs the least score.
    fn enforce_main_share(&mut self, mut b: Build, mut s: f64) -> (Build, f64) {
        let p = self.p;
        let ms = p.knobs.main_damage_share_min;
        let main = self.main_spec();
        if main.is_none() || ms <= 0.0 {
            return (b, s);
        }
        loop {
            self.evals += 1;
            let share = match crate::eval::evaluate_lin(&b, p, &lin_full(&b, p), &mut self.fb) {
                Ok(sc) => sc.main_share,
                Err(e) => {
                    self.fb.eval_error += 1;
                    if self.fb.first_error.is_none() {
                        self.fb.first_error = Some(e);
                    }
                    break;
                }
            };
            if f64::max(0.0, ms - share) / ms <= 1e-9 {
                break;
            }
            let mut ss: Vec<usize> = p.sec_specs.iter().copied().filter(|&x| Some(x) != main && b.abil(x) > 0).collect();
            ss.sort_by(|x, y| p.spec_names[*x].cmp(&p.spec_names[*y]));
            let mut cands: Vec<Option<usize>> = ss.into_iter().map(Some).collect();
            if b.weave {
                cands.push(None);
            }
            if cands.is_empty() {
                break;
            }
            let mut worst: Option<(f64, Build, f64)> = None;
            for a in cands {
                let mut c = b;
                match a {
                    Some(x) => c.abilities[x] = 0,
                    None => c.weave = false,
                }
                let s2 = self.score(&c).0;
                if worst.as_ref().map_or(true, |w| s - s2 < w.0) {
                    worst = Some((s - s2, c, s2));
                }
            }
            let (_, c, s2) = worst.unwrap();
            b = c;
            s = s2;
        }
        (b, s)
    }

    /// Move 14 choices: 0 = the Starforge sneak bit, 1 = weaving (ability builds), then the learned Power Dump
    /// eligible damage abilities (left in `buf`). Returns the number of choices.
    fn pd_choices(&self, b: &Build, buf: &mut Vec<usize>) -> usize {
        buf.clear();
        match self.main_spec() {
            Some(m) => {
                if self.p.specs[m].pd_ok {
                    buf.push(m);
                }
                for &s in &self.p.sec_specs {
                    if s != m && b.abil(s) > 0 && self.p.specs[s].pd_ok {
                        buf.push(s);
                    }
                }
                2 + buf.len()
            }
            None => 1,
        }
    }

    fn main_spec(&self) -> Option<usize> {
        match self.p.fam.kind {
            FamKind::Ability { main } => Some(main),
            _ => None,
        }
    }

    fn pick_charm_mods(&self, god: usize, rng: &mut Rng) -> Small<MAX_MODS> {
        let n = self.p.gods[god].mods.len();
        let mut idx = [0usize; MAX_CHARM_POOL];
        for (i, x) in idx[..n].iter_mut().enumerate() {
            *x = i;
        }
        rng.shuffle(&mut idx[..n]);
        let mut out = Small::new();
        let mut groups = Small::<16>::new();
        for &m in &idx[..n] {
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

    /// Rework EoT pass: switch the charm to Edge of Time's god, buy the path to it, raise that god's level with XP
    /// taken from the others (selling their nodes), and keep the result if it is legal.
    fn force_eot_path(&self, b: &mut Build, rng: &mut Rng, buf: &mut Vec<usize>) {
        if let Some(g) = &self.p.godtree {
            self.force_path(b, g.ix.eot, rng, buf);
        }
    }

    /// Lower other gods' levels (highest first, never below the greed-rank gate) until the XP pool fits, selling their
    /// nodes. False if it cannot fit.
    fn free_xp(&self, c: &mut Build, w: usize, rng: &mut Rng, buf: &mut Vec<usize>) -> bool {
        let g = match &self.p.godtree {
            Some(g) => g,
            None => return true,
        };
        while g.xp_used(&c.glv) > g.pool {
            let gate_ok = |k: usize| {
                let mut l = c.glv;
                l[k] -= 1;
                *l.iter().max().unwrap() as usize >= g.rank_gate
            };
            let other = (0..4).filter(|&k| k != w && c.glv[k] > 0 && gate_ok(k)).max_by_key(|&k| c.glv[k]);
            let other = match other {
                Some(k) => k,
                None => return false,
            };
            c.glv[other] -= 1;
            if !g.trim(c, other, rng, buf) {
                return false;
            }
        }
        true
    }

    /// Unique bench: make minor effect `e` apply without changing the charm god, through a transfer slot of its god
    /// (path bought, level raised for the points and the slot, XP taken from other gods). True if it applies.
    fn force_minor(&self, b: &mut Build, e: i64, rng: &mut Rng, buf: &mut Vec<usize>) -> bool {
        let p = self.p;
        let g = match &p.godtree {
            Some(g) => g,
            None => return false,
        };
        if e < 0 {
            return false;
        }
        let ef = &g.effects[e as usize];
        let (w, node) = (ef.god, ef.node);
        if g.active(b) == w {
            self.force_path(b, e, rng, buf);
            return g.minor_on(b, e);
        }
        let mut c = *b;
        let path = match g.path_to(&c, w, node) {
            Some(x) => x,
            None => return false,
        };
        for &x in &path {
            c.gnodes.set(g.trees[w].off + x);
        }
        let need = g.mts_len(&c, w) + if g.mts_has(&c, w, e as usize) { 0 } else { 1 };
        while g.slots(c.glv[w] as usize) < need && c.glv[w] < 200 {
            c.glv[w] += 1;
        }
        while g.count(&c, w) > g.points(c.glv[w] as usize) {
            g.removable(&c, w, buf);
            buf.retain(|x| !path.contains(x));
            if let Some(&x) = buf.first() {
                let x = if buf.len() > 1 { rng.choice(buf) } else { x };
                c.gnodes.clear(g.trees[w].off + x);
            } else if c.glv[w] < 200 {
                c.glv[w] += 1;
            } else {
                return false;
            }
        }
        g.clean_mts(&mut c, w);
        if !g.mts_has(&c, w, e as usize) {
            match c.gmts[w].iter().position(|&x| x < 0) {
                Some(k) if k < g.slots(c.glv[w] as usize) => c.gmts[w][k] = e as i16,
                _ => return false,
            }
        }
        if !self.free_xp(&mut c, w, rng, buf) {
            return false;
        }
        if g.legal(&c, p) && g.minor_on(&c, e) {
            *b = c;
            return true;
        }
        false
    }

    /// Make effect `e` apply: charm on its god, buy the path to its node, raise that god's level with XP taken from
    /// the others (selling their nodes); keep the result only if it is legal.
    fn force_path(&self, b: &mut Build, e: i64, rng: &mut Rng, buf: &mut Vec<usize>) {
        let p = self.p;
        let g = match &p.godtree {
            Some(g) => g,
            None => return,
        };
        if e < 0 {
            return;
        }
        let w = g.effects[e as usize].god;
        let node = g.effects[e as usize].node;
        let mut c = *b;
        if let Some(ci) = (0..p.gods.len()).find(|&ci| g.charm_to_god[ci] == w) {
            if c.god as usize != ci {
                c.god = ci as u16;
                c.mods = self.pick_charm_mods(ci, rng);
            }
        }
        if g.trees[w].ty[node as usize] == 3 {
            g.drop_other_majors(&mut c, w, node as usize);
        }
        let path = match g.path_to(&c, w, node) {
            Some(x) => x,
            None => return,
        };
        for &x in &path {
            c.gnodes.set(g.trees[w].off + x);
        }
        while g.count(&c, w) > g.points(c.glv[w] as usize) {
            g.removable(&c, w, buf);
            buf.retain(|x| !path.contains(x));
            if let Some(&x) = buf.first() {
                let x = if buf.len() > 1 { rng.choice(buf) } else { x };
                c.gnodes.clear(g.trees[w].off + x);
            } else if c.glv[w] < 200 {
                c.glv[w] += 1;
            } else {
                return;
            }
        }
        if !self.free_xp(&mut c, w, rng, buf) {
            return;
        }
        if !g.time_lock_ok(&c, p) && g.plushie_type >= 0 {
            self.new_item(&mut c, OFFHAND, g.plushie_type as usize, rng);
        }
        if g.legal(&c, p) {
            *b = c;
        } else if std::env::var("WVK_DEBUG").is_ok() {
            eprintln!("[wvk] force_path {} illegal: xp {} / {}, time_lock {}, cheap {}, levels {:?}", e, g.xp_used(&c.glv), g.pool,
                      g.time_lock_ok(&c, p), g.cheap_legal(&c, p), c.glv);
            for gi in 0..4 {
                eprintln!("[wvk]   god {} count {} points {} connected {} mts {} slots {} unusable {}", gi, g.count(&c, gi),
                          g.points(c.glv[gi] as usize), g.connected(&c, gi), g.mts_len(&c, gi), g.slots(c.glv[gi] as usize),
                          !c.gnodes.and(&g.fast.unusable[gi]).is_zero());
            }
        }
    }

    pub fn initial_build(&mut self, rng: &mut Rng) -> Build {
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
        self.set_mj(&mut b);
        let ty = rng.choice(&p.fam.mainhands);
        self.new_item(&mut b, MAINHAND, ty, rng);
        let ty = rng.choice(&p.offhand_types);
        self.new_item(&mut b, OFFHAND, ty, rng);
        self.new_item(&mut b, NECKLACE, p.necklace_type, rng);
        for (color, &n) in p.trinket_slots.iter().enumerate() {
            let mut opts: Vec<usize> = (0..p.trinkets.len()).filter(|&t| p.trinkets[t].color == color).collect();
            rng.shuffle(&mut opts);
            if let Some(i) = opts.iter().position(|&t| p.trinkets[t].negation) {
                if n > 0 && p.knobs.effect_negation_required {
                    let t = opts.remove(i);
                    opts.insert(0, t);
                }
            }
            for t in opts.into_iter().take(n) {
                b.trinkets.push(t);
            }
        }
        b.god = rng.below(p.gods.len()) as u16;
        b.mods = self.pick_charm_mods(b.god as usize, rng);
        for o in &p.deck {
            b.deck.push(rng.below(o.len()));
        }
        if !p.designs.is_empty() {
            let allowed = self.allowed_designs();
            let d = if self.design_hint >= 0 { self.design_hint as usize } else { allowed[rng.below(allowed.len())] };
            b.design = d as u8;
            b.cset = 0;
            b.dk = Small::from_slice(&p.designs[d].doc.seed);
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
        if let Some(g) = &p.godtree {
            let mut buf = Vec::new();
            for _ in 0..50 {
                let mut c = b;
                g.init(&mut c, rng, &mut buf, self.god_hint);
                if self.god_hint.is_some() || rng.f64() < 0.7 {
                    let primary = self.god_hint.unwrap_or_else(|| (0..4).max_by_key(|&k| c.glv[k]).unwrap());
                    if let Some(ci) = (0..p.gods.len()).find(|&ci| g.charm_to_god[ci] == primary) {
                        c.god = ci as u16;
                        c.mods = self.pick_charm_mods(ci, rng);
                    }
                }
                if self.major_hint >= 0 {
                    self.force_path(&mut c, self.major_hint, rng, &mut buf);
                }
                if !g.time_lock_ok(&c, p) && g.plushie_type >= 0 {
                    self.new_item(&mut c, OFFHAND, g.plushie_type as usize, rng);
                    if c.items[MAINHAND].ty() as i64 == g.staff_type {
                        let others: Vec<usize> = p.fam.mainhands.iter().copied().filter(|&t| t as i64 != g.staff_type).collect();
                        if !others.is_empty() {
                            let ty = rng.choice(&others);
                            self.new_item(&mut c, MAINHAND, ty, rng);
                        }
                    }
                }
                if g.legal(&c, p) {
                    return c;
                }
            }
            let mut c = b;
            g.init(&mut c, rng, &mut buf, self.god_hint);
            self.force_path(&mut c, g.tb_effect, rng, &mut buf);
            if g.legal(&c, p) {
                return c;
            }
            self.fb.init_illegal += 1;
        }
        b
    }

    /// One move of the given kind on a copy of `b`; None when that draw is illegal.
    pub fn try_move(&self, kind: u8, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<Build> {
        let p = self.p;
        if let Some(g) = &p.godtree {
            if kind == 12 || kind == 15 {
                let mut n = *b;
                if !g.mv(&mut n, rng, buf, if self.gw.is_empty() { None } else { Some(&self.gw) }) {
                    return None;
                }
                return if g.legal_vs(&n, Some(b), p) && self.lock_ok(&n) && self.ulock_ok(&n) && self.mj_ok(&n) { Some(n) } else { None };
            }
            let n = self.try_move_raw(kind, b, rng, buf)?;
            return if g.cheap_legal(&n, p) && self.lock_ok(&n) && self.ulock_ok(&n) && self.mj_ok(&n) && self.neg_ok(b, &n) { Some(n) } else { None };
        }
        let n = self.try_move_raw(kind, b, rng, buf)?;
        if self.ulock_ok(&n) && self.mj_ok(&n) && self.neg_ok(b, &n) { Some(n) } else { None }
    }

    /// search.negation_kept: a move may not take away the build's last Carapace / Blessed source.
    pub fn neg_ok(&self, b: &Build, n: &Build) -> bool {
        let p = self.p;
        !p.knobs.effect_negation_required || crate::eval::negation_met(n, p) || !crate::eval::negation_met(b, p)
    }

    fn try_move_raw(&self, kind: u8, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<Build> {
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
                let locked = p.godtree.as_ref().map_or(false, |g| g.time_lock_dynamic && g.plushie_type >= 0 && !g.has_temporal_breaking(&n));
                let ty = if locked { p.godtree.as_ref().unwrap().plushie_type as usize } else { rng.choice(&p.offhand_types) };
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
            9 if !p.designs.is_empty() => {
                let allowed = self.allowed_designs();
                if self.design_open && !self.design_lock && allowed.len() > 1 && rng.f64() < 0.2 {
                    let d = allowed[rng.below(allowed.len())];
                    if d == n.design as usize {
                        return None;
                    }
                    n.design = d as u8;
                    n.cset = 0;
                    n.dk = Small::from_slice(&p.designs[d].doc.seed);
                    return Some(n);
                }
                let d = &p.designs[n.design as usize];
                if self.opts.cset && d.doc.csets.len() > 1 && rng.f64() < 0.1 {
                    let c = rng.below(d.doc.csets.len());
                    if c == n.cset as usize {
                        return None;
                    }
                    n.cset = c as u8;
                    return Some(n);
                }
                let i = rng.below(d.n);
                if rng.f64() < 0.3 && d.n > 1 {
                    let j = rng.below(d.n);
                    let (a, c) = (d.card(i, n.dk.get(i)).card, d.card(j, n.dk.get(j)).card);
                    if a == c {
                        return None;
                    }
                    let (Some(ci), Some(aj)) = (d.doc.opts[i].iter().position(|o| o.card == c),
                                                d.doc.opts[j].iter().position(|o| o.card == a)) else {
                        return None;
                    };
                    n.dk.set(i, ci);
                    n.dk.set(j, aj);
                } else {
                    if d.doc.opts[i].len() < 2 {
                        return None;
                    }
                    n.dk.set(i, rng.below(d.doc.opts[i].len()));
                }
                if !d.limits_ok(n.dk.as_slice()) {
                    return None;
                }
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
                    let mut cands = [(false, 0usize); MAX_TALENTS + MAX_SPECS];
                    for _ in 0..6 {
                        let mut nc = 0;
                        for t in (0..p.talents.len()).filter(|&t| n.talents[t] > 0 && t != tid) {
                            cands[nc] = (true, t);
                            nc += 1;
                        }
                        for s in (0..p.specs.len()).filter(|&s| n.abil(s) > self.base_of(s) && Some(s) != main) {
                            cands[nc] = (false, s);
                            nc += 1;
                        }
                        if nc == 0 {
                            break;
                        }
                        let (is_t, v) = cands[rng.below(nc)];
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
                let had = self.n_secondaries(&n);
                let ns = if main.is_some() { p.sec_specs.len() } else { 0 };
                let nb = p.buff_specs.len() + main.is_some() as usize + ns;
                let k = rng.below(nb);
                let sid = if k < p.buff_specs.len() {
                    p.buff_specs[k]
                } else if k == p.buff_specs.len() {
                    main.unwrap()
                } else {
                    p.sec_specs[k - p.buff_specs.len() - 1]
                };
                if Some(sid) != main && p.sec_specs.contains(&sid) && main.is_none() {
                    return None;
                }
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
                let has = self.n_secondaries(&n);
                if has > p.knobs.max_secondaries && has > had {
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
                let c = self.pd_choices(&n, buf);
                let k = rng.below(c);
                if k == 0 {
                    n.pd ^= crate::build::PD_NOVA;
                } else if k == 1 && self.main_spec().is_some() {
                    n.weave = !n.weave;
                } else {
                    n.pd ^= 1u128 << buf[k - 2];
                }
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

    /// Multi-jump rule (search.multijump_ok): the Prismatic Feather, or normal boots with the Multi Jump implicit.
    pub fn mj_ok(&self, n: &Build) -> bool {
        let p = self.p;
        if p.boots_mj < 0 {
            return true;
        }
        if p.feather >= 0 && (0..n.trinkets.len()).any(|i| n.trinkets.get(i) as i64 == p.feather) {
            return true;
        }
        let it = &n.items[p.boots_slot];
        it.unique.is_none() && it.implicits.iter().any(|a| a as i64 == p.boots_mj)
    }

    /// set_multijump(): the normal boots take the Multi Jump implicit (initial builds).
    fn set_mj(&self, b: &mut Build) {
        let p = self.p;
        let s = p.boots_slot;
        if p.boots_mj < 0 || b.items[s].unique.is_some() {
            return;
        }
        let t = &p.types[b.items[s].ty()];
        for g in 0..b.items[s].implicits.len().min(t.implicit_groups.len()) {
            if t.implicit_groups[g].iter().any(|&a| a as i64 == p.boots_mj) {
                b.items[s].implicits.set(g, p.boots_mj as usize);
            }
        }
    }

    /// Put the Prismatic Feather in a slot of its colour (replacing that colour's first trinket), for unique boots.
    fn force_feather(&self, b: &mut Build, f: usize) {
        let p = self.p;
        let col = p.trinkets[f].color;
        for i in 0..b.trinkets.len() {
            if p.trinkets[b.trinkets.get(i)].color == col {
                b.trinkets.set(i, f);
                return;
            }
        }
    }

    /// Unique bench lock: the locked slot keeps the locked unique.
    pub fn ulock_ok(&self, n: &Build) -> bool {
        match self.ulock {
            Some((slot, u)) => n.items[slot].unique == Some(u),
            None => true,
        }
    }

    /// Unique bench: put the locked unique on `b`, take off other uniques over the unique and ancient limits, and
    /// force Temporal Breaking when the time lock forbids the piece. False if the result is still illegal.
    fn apply_ulock(&mut self, b: &mut Build, rng: &mut Rng, buf: &mut Vec<usize>) -> bool {
        let p = self.p;
        let (slot, u) = match self.ulock {
            Some(x) => x,
            None => return true,
        };
        let un = &p.uniques[u as usize];
        if un.slot != slot || (slot == MAINHAND && !p.fam.mainhands.contains(&un.ty)) {
            self.ulock_reason = "mainhand";
            return false;
        }
        let mut c = *b;
        if c.items[slot].unique != Some(u) {
            self.equip_unique(&mut c, slot, u as usize, rng);
        }
        let anc = |c: &Build| c.items.iter().filter(|it| it.unique.map_or(false, |x| p.uniques[x as usize].ancient)).count();
        for s in 0..N_SLOTS {
            if s == slot || c.items[s].unique.is_none() {
                continue;
            }
            let is_anc = p.uniques[c.items[s].unique.unwrap() as usize].ancient;
            if self.count_uniques(&c) > p.unique_max || (is_anc && anc(&c) > p.ancient_max) {
                self.unequip_unique(&mut c, s, rng);
            }
        }
        if let Some(g) = &p.godtree {
            if !g.time_lock_ok(&c, p) {
                let keep = c.items[slot];
                if !self.force_minor(&mut c, g.tb_effect, rng, buf) {
                    self.force_path(&mut c, g.tb_effect, rng, buf);
                }
                c.items[slot] = keep;
                if !g.time_lock_ok(&c, p) {
                    self.ulock_reason = "time-lock";
                    return false;
                }
            }
            if !(g.legal(&c, p) && g.cheap_legal(&c, p)) {
                self.ulock_reason = "illegal";
                return false;
            }
        }
        if !self.unique_seal_legal(&c) {
            c.items[slot].seal = None;
            c.items[slot].udrop = None;
        }
        if !self.ulock_ok(&c) || self.count_uniques(&c) > p.unique_max {
            self.ulock_reason = "limits";
            return false;
        }
        if !self.mj_ok(&c) {
            if let Some(&f) = (p.feather >= 0).then_some(&p.feather) {
                self.force_feather(&mut c, f as usize);
            }
            if !self.mj_ok(&c) {
                self.ulock_reason = "multijump";
                return false;
            }
        }
        *b = c;
        true
    }

    /// Stratum lock: the charm stays on the stratum's god and its major stays applied.
    pub fn lock_ok(&self, n: &Build) -> bool {
        if self.stratum < 0 {
            return true;
        }
        let g = match &self.p.godtree {
            Some(g) => g,
            None => return true,
        };
        if let Some(gh) = self.god_hint {
            if g.active(n) != gh {
                return false;
            }
        }
        if self.major_hint >= 0 {
            let e = self.major_hint as usize;
            let ef = &g.effects[e];
            if !n.gnodes.has(g.trees[ef.god].off + ef.node) {
                return false;
            }
        }
        true
    }

    /// A legal candidate that differs from `b`: illegal or identical draws are redrawn instead of costing an evaluation.
    pub fn propose(&mut self, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Build {
        for _ in 0..500 {
            let kind = self.moves[rng.below(self.moves.len())];
            match self.try_move(kind, b, rng, buf) {
                Some(n) if n != *b => {
                    self.last_kind = kind;
                    return n;
                }
                _ => self.illegal += 1,
            }
        }
        self.no_legal += 1;
        *b
    }

    /// propose() restricted to non-god moves.
    pub fn propose_gear(&mut self, b: &Build, rng: &mut Rng, buf: &mut Vec<usize>) -> Build {
        if self.moves_gear.is_empty() {
            self.moves_gear = self.gear_moves();
        }
        for _ in 0..500 {
            let kind = self.moves_gear[rng.below(self.moves_gear.len())];
            match self.try_move(kind, b, rng, buf) {
                Some(n) if n != *b => return n,
                _ => self.illegal += 1,
            }
        }
        self.no_legal += 1;
        *b
    }

    /// Probe weights for god-move picks: each unowned usable minor/major node's score gain when its path is bought
    /// on `b` (levels raised to fit, XP pool ignored), mapped to 0.25 + 4 x gain / best gain; other nodes 1.
    fn god_weights(&mut self, b: &Build, base: f64) {
        let p = self.p;
        let g = match &p.godtree {
            Some(g) => g,
            None => return,
        };
        let total: usize = g.trees.iter().map(|t| t.n).sum();
        let mut gain = vec![0.0f64; total];
        let mut big = vec![false; total];
        for gi in 0..4 {
            let t = &g.trees[gi];
            for i in 0..t.n {
                if !t.usable[i] || t.ty[i] < 2 || b.gnodes.has(t.off + i) {
                    continue;
                }
                big[t.off + i] = true;
                let path = match g.path_to(b, gi, i) {
                    Some(x) => x,
                    None => continue,
                };
                let mut c = *b;
                for &x in &path {
                    c.gnodes.set(t.off + x);
                }
                while g.count(&c, gi) > g.points(c.glv[gi] as usize) && c.glv[gi] < 200 {
                    c.glv[gi] += 1;
                }
                gain[t.off + i] = (self.score(&c).0 - base).max(0.0);
            }
        }
        let mx = gain.iter().cloned().fold(0.0, f64::max);
        self.gw = (0..total).map(|k| if !big[k] { 1.0 } else if mx > 0.0 { 0.25 + 4.0 * gain[k] / mx } else { 1.0 }).collect();
    }

    /// Parallel tempering from `start`: replicas at fixed temperatures pt_cold..pt_hot (geometric), one proposal
    /// per replica per sweep, neighbour swaps every pt_swap sweeps. Returns the best build seen.
    fn tempering(&mut self, start: Build, start_s: f64, iters: usize, rng: &mut Rng, buf: &mut Vec<usize>) -> (Build, f64) {
        let r = self.opts.pt.max(2);
        let (hot, cold) = (self.opts.pt_hot, self.opts.pt_cold);
        let temps: Vec<f64> = (0..r).map(|k| cold * (hot / cold).powf(k as f64 / (r - 1) as f64)).collect();
        let lin0 = lin_full(&start, self.p);
        let mut st: Vec<(Build, f64, Lin)> = (0..r).map(|_| (start, start_s, lin0)).collect();
        let (mut best, mut best_s) = (start, start_s);
        let sweeps = iters / r;
        for sw in 0..sweeps {
            for k in 0..r {
                let cand = self.propose(&st[k].0, rng, buf);
                let (sc, lin) = self.score_from(&st[k].0, &st[k].2, &cand);
                let d = sc - st[k].1;
                if d >= 0.0 || rng.f64() < (d / temps[k]).exp() {
                    st[k] = (cand, sc, lin);
                    if sc > best_s {
                        best = cand;
                        best_s = sc;
                    }
                }
            }
            if sw % self.opts.pt_swap == 0 {
                for k in 0..r - 1 {
                    let a = (st[k + 1].1 - st[k].1) * (1.0 / temps[k] - 1.0 / temps[k + 1]);
                    if a >= 0.0 || rng.f64() < a.exp() {
                        st.swap(k, k + 1);
                    }
                }
            }
            if sw % 20000 == 19999 {
                for k in 0..r {
                    st[k].2 = lin_full(&st[k].0, self.p);
                }
            }
        }
        (best, best_s)
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
                    let ok = self.ulock_ok(&c) && self.mj_ok(&c) && match &p.godtree {
                        Some(g) => g.cheap_legal(&c, p) && self.lock_ok(&c),
                        None => true,
                    };
                    if ok {
                        let (s2, l2) = self.score_from(&b, &b_lin, &c);
                        if s2 > best_s + 1e-9 {
                            b = c;
                            b_lin = l2;
                            best_s = s2;
                            improved = true;
                        }
                    }
                }};
            }
            let deck_only = self.opts.deck_only;
            if self.main_spec().is_some() && !deck_only {
                let mut c = b;
                c.weave = !c.weave;
                consider!(c);
            }
            let mut pdb: Vec<usize> = Vec::new();
            let nc = if deck_only { 0 } else { self.pd_choices(&b, &mut pdb) };
            for k in 0..nc {
                if k == 1 && self.main_spec().is_some() {
                    continue;
                }
                let mut c = b;
                c.pd ^= if k == 0 { crate::build::PD_NOVA } else { 1u128 << pdb[k - 2] };
                consider!(c);
            }
            if let Some(g) = p.godtree.as_ref().filter(|_| !deck_only) {
                let (mut rem, mut fr) = (Vec::new(), Vec::new());
                for gi in 0..4 {
                    let off = g.trees[gi].off;
                    if g.count(&b, gi) < g.points(b.glv[gi] as usize) {
                        g.frontier(&b, gi, &mut fr);
                        for &f in fr.clone().iter() {
                            let mut c = b;
                            c.gnodes.set(off + f);
                            if g.legal_vs(&c, Some(&b), p) {
                                consider!(c);
                            }
                        }
                    }
                    g.removable(&b, gi, &mut rem);
                    for &r in rem.clone().iter() {
                        let mut c0 = b;
                        c0.gnodes.clear(off + r);
                        g.clean_mts(&mut c0, gi);
                        g.frontier(&c0, gi, &mut fr);
                        for &f in fr.iter() {
                            if f == r {
                                continue;
                            }
                            let mut c = c0;
                            c.gnodes.set(off + f);
                            if g.legal_vs(&c, Some(&b), p) {
                                consider!(c);
                            }
                        }
                    }
                }
                if self.path_polish {
                    for gi in 0..4 {
                        let t = &g.trees[gi];
                        let off = t.off;
                        let cap = g.points(b.glv[gi] as usize);
                        if cap == 0 {
                            continue;
                        }
                        for target in 0..t.n {
                            if !t.usable[target] || t.ty[target] < 2 || b.gnodes.has(off + target) {
                                continue;
                            }
                            let path = match g.path_to(&b, gi, target) {
                                Some(x) if x.len() >= 2 && x.len() <= cap => x,
                                _ => continue,
                            };
                            let mut c = b;
                            for &x in &path {
                                c.gnodes.set(off + x);
                            }
                            let mut ok = true;
                            while g.count(&c, gi) > cap {
                                g.removable(&c, gi, &mut rem);
                                let mut pick: Option<(f64, Build)> = None;
                                for &r in rem.iter() {
                                    if path.contains(&r) {
                                        continue;
                                    }
                                    let mut c2 = c;
                                    c2.gnodes.clear(off + r);
                                    g.clean_mts(&mut c2, gi);
                                    let (s2, _) = self.score_from(&b, &b_lin, &c2);
                                    if pick.as_ref().map_or(true, |(ps, _)| s2 > *ps) {
                                        pick = Some((s2, c2));
                                    }
                                }
                                match pick {
                                    Some((_, c2)) => c = c2,
                                    None => {
                                        ok = false;
                                        break;
                                    }
                                }
                            }
                            if ok && g.legal_vs(&c, Some(&b), p) {
                                consider!(c);
                            }
                        }
                    }
                }
            }
            let mut us = Vec::new();
            for &slot in UNIQUE_SLOTS.iter().filter(|_| !deck_only) {
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
            for slot in (0..N_SLOTS).filter(|_| !deck_only) {
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
            if !p.designs.is_empty() && !b.dk.is_empty() {
                let d = &p.designs[b.design as usize];
                for i in 0..d.n {
                    for o in 0..d.doc.opts[i].len() {
                        if b.dk.get(i) == o {
                            continue;
                        }
                        let mut c = b;
                        c.dk.set(i, o);
                        if d.limits_ok(c.dk.as_slice()) {
                            consider!(c);
                        }
                    }
                }
                if self.opts.cset {
                    for cs in 0..d.doc.csets.len() {
                        if cs != b.cset as usize {
                            let mut c = b;
                            c.cset = cs as u8;
                            consider!(c);
                        }
                    }
                }
            }
            for i in (0..b.trinkets.len()).filter(|_| !deck_only) {
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
            for i in (0..b.mods.len()).filter(|_| !deck_only) {
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
            for bit in 0..128u32 {
                let m = 1u128 << bit;
                if b.pd & m == 0 {
                    continue;
                }
                let mut c = b;
                c.pd &= !m;
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
        if let Some(g) = &self.p.godtree {
            // Restart strata (god, forced major): consecutive restarts cover the combat majors and every god.
            let x = &g.ix;
            let strata: [(usize, i64); 8] = [(1, x.immortal), (0, x.fury), (0, x.archmage), (1, -1), (0, -1), (3, -1), (2, -1), (1, x.immortal)];
            let si = if self.stratum >= 0 { self.stratum as usize % 8 } else { ((seed / 1000 + seed) % 8) as usize };
            let (gh, maj) = strata[si];
            self.god_hint = Some(gh);
            self.major_hint = maj;
        }
        let mut buf = Vec::with_capacity(128);
        let mut tr = Trace::default();
        let init = match self.init.take() {
            Some(b0) => {
                let mut b0 = b0;
                if self.force_eot {
                    self.force_eot_path(&mut b0, &mut rng, &mut buf);
                }
                b0
            }
            None => self.initial_build(&mut rng),
        };
        let mut init = init;
        if self.n_secondaries(&init) > self.p.knobs.max_secondaries {
            self.fb.sec_over_cap += 1;
            init = self.prune_secondaries(init, f64::NEG_INFINITY, false).0;
        }
        if self.ulock.is_some() && !self.apply_ulock(&mut init, &mut rng, &mut buf) {
            self.ulock = None;
            self.ulock_failed = true;
            let s = self.score(&init).0;
            tr.final_score = s;
            return (init, s, tr);
        }
        if self.stratum >= 0 && !self.lock_ok(&init) {
            self.stratum = -1;
            self.lock_dropped = true;
        }
        if self.opts.deck_only {
            self.moves = vec![9];
        }
        let relax_end = if self.p.godtree.is_some() && self.p.knobs.min_move_speed > 0.0 && self.opts.pt == 0 && !self.opts.deck_only {
            (iters as f64 * MOVE_RELAX_END) as usize
        } else {
            0
        };
        let relax_step = (relax_end / 20).max(1);
        self.move_relax = if relax_end > 0 { 1.0 } else { 0.0 };
        tr.start = self.score(&init).0;
        let (mut cur, mut cur_s) = self.polish(init, 1);
        tr.after_polish1 = cur_s;
        let relaxed = self.move_relax;
        self.move_relax = 0.0;
        let start_strict = (cur, self.score(&cur).0);
        self.move_relax = relaxed;
        if self.opts.weights {
            self.god_weights(&cur, cur_s);
        }
        let mut cur_lin = lin_full(&cur, self.p);
        let mut best = cur;
        let mut best_s = cur_s;
        let split = self.opts.god_t != 1.0 || self.opts.repair > 0;
        let iters = if self.opts.pt > 0 {
            let (b, s) = self.tempering(cur, cur_s, iters, &mut rng, &mut buf);
            if s > best_s {
                best = b;
                best_s = s;
            }
            0
        } else {
            iters
        };
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
            if relax_end > 0 && i <= relax_end && (i % relax_step == 0 || i == relax_end) {
                let r = 1.0 - i as f64 / relax_end as f64;
                if r != self.move_relax {
                    self.move_relax = r;
                    cur_s = self.score(&cur).0;
                    if r <= 0.0 {
                        (best, best_s) = if cur_s >= start_strict.1 { (cur, cur_s) } else { start_strict };
                    }
                }
            }
            let frac = i as f64 / (iters.max(2) - 1) as f64;
            self.design_open = !self.design_lock && 2 * i < iters;
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
            let is_god = (self.last_kind == 12 || self.last_kind == 15) && self.p.godtree.is_some();
            let is_design = self.last_kind == 9 && !self.p.designs.is_empty() && cand.design != cur.design;
            let (cand, s, lin) = if is_design {
                // A design switch starts from the design's DeckFAST cards; a few greedy card moves adapt them to
                // this build before the switch is judged.
                let (mut cs, mut cl) = self.score_from(&cur, &cur_lin, &cand);
                let mut c = cand;
                let open = self.design_open;
                self.design_open = false;
                for _ in 0..DESIGN_REPAIR {
                    if let Some(c2) = self.try_move(9, &c, &mut rng, &mut buf) {
                        let (s2, l2) = self.score_from(&c, &cl, &c2);
                        if s2 > cs {
                            c = c2;
                            cs = s2;
                            cl = l2;
                        }
                    }
                }
                self.design_open = open;
                (c, cs, cl)
            } else if is_god && self.opts.repair > 0 {
                let (mut cs, mut cl) = self.score_from(&cur, &cur_lin, &cand);
                let mut c = cand;
                for _ in 0..self.opts.repair {
                    let c2 = self.propose_gear(&c, &mut rng, &mut buf);
                    let (s2, l2) = self.score_from(&c, &cl, &c2);
                    if s2 > cs {
                        c = c2;
                        cs = s2;
                        cl = l2;
                    }
                }
                (c, cs, cl)
            } else {
                let (s, lin) = self.score_from(&cur, &cur_lin, &cand);
                (cand, s, lin)
            };
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
            let t_eff = if is_god { temp * self.opts.god_t } else { temp };
            let took = s >= cur_s || rng.f64() < (dlt / t_eff).exp();
            if let Schedule::Target { a0, a1, eta, .. } = sched {
                if worse && dlt > -1e6 && !(split && is_god) {
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
                if s > best_s && self.move_relax <= 0.0 {
                    best = cur;
                    best_s = s;
                }
            }
            if want_trace && checkpoints.contains(&i) {
                tr.checkpoints.push(((i + 1) as f64 / iters as f64, best_s));
            }
        }
        if self.move_relax > 0.0 {
            self.move_relax = 0.0;
            let s = self.score(&cur).0;
            best = if s >= start_strict.1 { cur } else { start_strict.0 };
        }
        tr.sa_best = best_s;
        let (b, s) = self.polish(best, 3);
        tr.after_polish3 = s;
        let (b, s) = if self.opts.deck_only {
            (b, s)
        } else {
            let (b, s) = self.strip_idle_etchings(b, s);
            let (b, _) = self.strip_idle_points(b, s);
            self.prune_secondaries(b, self.p.knobs.secondary_min_value, true)
        };
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
