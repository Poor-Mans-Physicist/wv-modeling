//! Greed-rework god trees (model/gods.py): points, transfer slots, effect ranks with the 25% foreign carryover,
//! charm piety units, the god-tree stat contribution, legality, graph helpers and the search moves.

use crate::build::*;
use crate::problem::*;
use crate::search::Rng;

pub const N_GODS: usize = 4;
pub const MAX_MTS: usize = 3;
pub const MAX_GEFF: usize = 192;
/// Per-tree array size of removable() and path_arr() (one slot is removable()'s super root).
pub const MAX_TREE_NODES: usize = 192;
pub const FOREIGN_SCALE: f64 = 0.25;

/// Effect rank and scale for every god effect that applies (rank 0 = does not apply); `t[..nt]` lists them.
#[derive(Clone, Copy)]
pub struct Ranks {
    pub n: [u16; MAX_GEFF],
    pub s: [f64; MAX_GEFF],
    pub t: [u16; MAX_GEFF],
    pub nt: usize,
}

/// Load-time lookups: effect per global node index, per-tree and Pious Devotion bit masks.
#[derive(Clone, Default)]
pub struct GodFast {
    pub node_eff: Vec<u16>,
    pub tree_mask: Vec<Bits>,
    pub pious_mask: Bits,
    /// Neighbours of each node (global indices).
    pub adj: Vec<Bits>,
    /// Per tree: roots (t.roots), buyable roots (type 0 and usable), usable, unusable, usable minor/major nodes.
    pub roots: Vec<Bits>,
    pub roots_buy: Vec<Bits>,
    pub usable: Vec<Bits>,
    pub unusable: Vec<Bits>,
    pub big: Vec<Bits>,
    /// Per tree: its major nodes (a build may own at most one per tree).
    pub major: Vec<Bits>,
    /// Effects gfx() reads (on/off), as (effect, bit of the gfx key).
    pub gfx_eff: Vec<(usize, u32)>,
    /// Per tree: hop distance between usable nodes through usable nodes (n x n, local indices; 255 = unreachable).
    pub dist: Vec<Vec<u8>>,
}

impl GodFast {
    pub fn new(g: &GodData) -> GodFast {
        let total: usize = g.trees.iter().map(|t| t.n).sum();
        assert!(total <= MAX_GREED, "god trees have {} nodes, kernel capacity is {}", total, MAX_GREED);
        for t in &g.trees {
            assert!(t.n < MAX_TREE_NODES, "a god tree has {} nodes, kernel capacity is {} (MAX_TREE_NODES in gods.rs)", t.n, MAX_TREE_NODES - 1);
        }
        assert!(g.effects.len() <= MAX_GEFF, "{} god effects, kernel capacity is {} (MAX_GEFF)", g.effects.len(), MAX_GEFF);
        let mut node_eff = vec![0u16; total];
        let mut tree_mask = Vec::new();
        let mut pious_mask = Bits::new();
        for t in &g.trees {
            let mut m = Bits::new();
            for i in 0..t.n {
                node_eff[t.off + i] = t.eff[i] as u16;
                m.set(t.off + i);
                if g.effects[t.eff[i]].pious {
                    pious_mask.set(t.off + i);
                }
            }
            tree_mask.push(m);
        }
        let mut adj = vec![Bits::new(); total];
        let (mut roots, mut roots_buy, mut usable, mut unusable, mut big) = (Vec::new(), Vec::new(), Vec::new(), Vec::new(), Vec::new());
        for t in &g.trees {
            let (mut r, mut rb, mut u, mut nu, mut bg) = (Bits::new(), Bits::new(), Bits::new(), Bits::new(), Bits::new());
            for i in 0..t.n {
                for &j in &t.adj[i] {
                    adj[t.off + i].set(t.off + j);
                }
                if t.usable[i] {
                    u.set(t.off + i);
                    if t.ty[i] == 0 {
                        rb.set(t.off + i);
                    }
                    if t.ty[i] >= 2 {
                        bg.set(t.off + i);
                    }
                } else {
                    nu.set(t.off + i);
                }
            }
            for &x in &t.roots {
                r.set(t.off + x);
            }
            roots.push(r);
            roots_buy.push(rb);
            usable.push(u);
            unusable.push(nu);
            big.push(bg);
        }
        let x = &g.ix;
        let reads = [x.banked, x.surrounded, x.under, x.paced, x.sneaky, x.eot, x.demon, x.immortal, x.kinetic, x.greed,
                     x.crushing, x.weaponmaster, x.pincushion, x.true_rage, x.overcrit, x.luckiest, x.archmage, x.power_dump,
                     x.fury, x.stonewall, x.cactus, x.flow, x.malediction, x.counter, x.indomitable, x.adaptive, x.shield,
                     x.fleeting, x.starved, x.super_stacker, x.utilized, x.plushie_lover, x.steadfast, x.stack_hoarder];
        let gfx_eff = reads.iter().enumerate().filter(|(_, &e)| e >= 0).map(|(i, &e)| (e as usize, i as u32)).collect();
        let mut dist = Vec::new();
        for t in &g.trees {
            let n = t.n;
            let mut d = vec![255u8; n * n];
            let mut q = vec![0usize; n];
            for src in 0..n {
                if !t.usable[src] {
                    continue;
                }
                let row = &mut d[src * n..(src + 1) * n];
                row[src] = 0;
                let (mut h, mut tl) = (0, 1);
                q[0] = src;
                while h < tl {
                    let x = q[h];
                    h += 1;
                    for &y in &t.adj[x] {
                        if t.usable[y] && row[y] == 255 {
                            row[y] = row[x] + 1;
                            q[tl] = y;
                            tl += 1;
                        }
                    }
                }
            }
            dist.push(d);
        }
        let major = g.trees.iter().map(|t| {
            let mut m = Bits::new();
            for i in 0..t.n {
                if t.ty[i] == 3 {
                    m.set(t.off + i);
                }
            }
            m
        }).collect();
        GodFast { node_eff, tree_mask, pious_mask, adj, roots, roots_buy, usable, unusable, big, major, gfx_eff, dist }
    }
}

impl GodData {
    pub fn points(&self, level: usize) -> usize {
        let mut p = 0;
        if level > 0 {
            p = self.p1 + self.p_to * (level.min(self.max_defined) - 1) + self.p_past * level.saturating_sub(self.max_defined);
        }
        p + self.rep / self.rep_per_point
    }

    pub fn slots(&self, level: usize) -> usize {
        self.mts_levels.iter().filter(|&&u| level >= u).count()
    }

    pub fn xp_for_level(&self, level: usize) -> f64 {
        if level == 0 {
            0.0
        } else if level <= self.max_defined {
            self.cum[level - 1]
        } else {
            self.cum[self.max_defined - 1] + (level - self.max_defined) as f64 * self.past_max
        }
    }

    pub fn xp_used(&self, glv: &[u8; N_GODS]) -> f64 {
        glv.iter().map(|&l| self.xp_for_level(l as usize)).sum()
    }

    pub fn count(&self, b: &Build, g: usize) -> usize {
        b.gnodes.and_count(&self.fast.tree_mask[g])
    }

    pub fn active(&self, b: &Build) -> usize {
        self.charm_to_god[b.god as usize]
    }

    pub fn mts_has(&self, b: &Build, g: usize, e: usize) -> bool {
        b.gmts[g].iter().any(|&x| x == e as i16)
    }

    pub fn mts_len(&self, b: &Build, g: usize) -> usize {
        b.gmts[g].iter().filter(|&&x| x >= 0).count()
    }

    /// gods.Gods.ranks: placements per effect; active god in full, foreign stat/root at 25%, foreign minors only
    /// through that god's transfer slots, foreign majors never.
    pub fn ranks(&self, b: &Build) -> Ranks {
        let mut r = Ranks { n: [0; MAX_GEFF], s: [0.0; MAX_GEFF], t: [0; MAX_GEFF], nt: 0 };
        let act = self.active(b);
        let mut seen = [0u16; MAX_GEFF];
        let mut ns = 0usize;
        for gi in b.gnodes.iter() {
            let e = self.fast.node_eff[gi] as usize;
            if r.n[e] == 0 {
                seen[ns] = e as u16;
                ns += 1;
            }
            r.n[e] += 1;
        }
        for &e16 in &seen[..ns] {
            let e = e16 as usize;
            let ef = &self.effects[e];
            let g = ef.god;
            let sc = if g == act {
                1.0
            } else if ef.ty == 0 || ef.ty == 1 {
                FOREIGN_SCALE
            } else if ef.ty == 2 && self.mts_has(b, g, e) {
                1.0
            } else {
                0.0
            };
            if sc > 0.0 {
                r.s[e] = sc;
                r.t[r.nt] = e16;
                r.nt += 1;
            } else {
                r.n[e] = 0;
            }
        }
        r
    }

    /// floor(piety / 10) of the charm god: reputation + 2 x level + Pious Devotion placements.
    pub fn piety_units(&self, b: &Build) -> f64 {
        let g = self.active(b);
        let pious = b.gnodes.and3_count(&self.fast.pious_mask, &self.fast.tree_mask[g]);
        (self.rep + 2 * b.glv[g] as usize + pious) as f64
    }

    /// The god-state results that only change with the god state: the custom effect numbers and the Stack Stack
    /// Stack extra stacks. Cached in Lin so scoring skips ranks() and gfx() when a move leaves the gods alone.
    pub fn cache(&self, r: &Ranks, k: &Knobs) -> (Option<GodFx>, f64) {
        let e = self.ix.sss;
        let mut sss = 0.0;
        if e >= 0 && r.n[e as usize] > 0 {
            let v0 = self.effects[e as usize].values.first().copied().unwrap_or(1.0);
            sss = (r.n[e as usize] as f64 * v0 * r.s[e as usize] + 1e-9).floor();
        }
        (Some(self.gfx(r, k)), sss)
    }

    /// Bit set of the gfx() effects that are on: gfx() is a function of this key alone.
    pub fn gfx_key(&self, r: &Ranks) -> u64 {
        let mut k = 0u64;
        for &(e, bit) in &self.fast.gfx_eff {
            if r.n[e] > 0 {
                k |= 1 << bit;
            }
        }
        k
    }

    /// One effect's additive contribution at rank n and scale s (lin_r for a single effect); calls mark(attr).
    fn eff_lin(&self, e: usize, n: usize, s: f64, v: &mut [f64; MAX_ATTRS], sign: f64, mark: &mut dyn FnMut(usize)) {
        let ef = &self.effects[e];
        if !ef.ga {
            for &(a, x) in &ef.adds {
                v[a] += sign * x;
                mark(a);
            }
            return;
        }
        let mut x = 0.0;
        for k in 0..n {
            x += if ef.values.is_empty() { 0.0 } else { ef.values[k.min(ef.values.len() - 1)] };
        }
        x *= s;
        if ef.a >= 0 {
            v[ef.a as usize] += sign * x;
            mark(ef.a as usize);
        }
        if ef.a2 >= 0 {
            v[ef.a2 as usize] += sign * x;
            mark(ef.a2 as usize);
        }
    }

    /// Replace the contribution of ranks r0 by r1, touching only effects whose rank or scale changed.
    pub fn lin_diff(&self, r0: &Ranks, r1: &Ranks, v: &mut [f64; MAX_ATTRS], mark: &mut dyn FnMut(usize)) {
        let mut seen = [false; MAX_GEFF];
        for &e16 in r0.t[..r0.nt].iter().chain(r1.t[..r1.nt].iter()) {
            let e = e16 as usize;
            if seen[e] {
                continue;
            }
            seen[e] = true;
            let (n0, n1) = (r0.n[e] as usize, r1.n[e] as usize);
            if n0 == n1 && (n0 == 0 || r0.s[e] == r1.s[e]) {
                continue;
            }
            if n0 > 0 {
                self.eff_lin(e, n0, r0.s[e], v, -1.0, mark);
            }
            if n1 > 0 {
                self.eff_lin(e, n1, r1.s[e], v, 1.0, mark);
            }
        }
    }

    pub fn sss_of(&self, r: &Ranks) -> f64 {
        let e = self.ix.sss;
        if e >= 0 && r.n[e as usize] > 0 {
            let v0 = self.effects[e as usize].values.first().copied().unwrap_or(1.0);
            (r.n[e as usize] as f64 * v0 * r.s[e as usize] + 1e-9).floor()
        } else {
            0.0
        }
    }

    pub fn lin_r(&self, r: &Ranks, v: &mut [f64; MAX_ATTRS], sign: f64) {
        for &e16 in &r.t[..r.nt] {
            let e = e16 as usize;
            let ef = &self.effects[e];
            let n = r.n[e] as usize;
            if !ef.ga {
                for &(a, x) in &ef.adds {
                    v[a] += sign * x;
                }
                continue;
            }
            let mut x = 0.0;
            for k in 0..n {
                x += if ef.values.is_empty() { 0.0 } else { ef.values[k.min(ef.values.len() - 1)] };
            }
            x *= r.s[e];
            if ef.a >= 0 {
                v[ef.a as usize] += sign * x;
            }
            if ef.a2 >= 0 {
                v[ef.a2 as usize] += sign * x;
            }
        }
    }

    pub fn connected(&self, b: &Build, g: usize) -> bool {
        self.connected_bits(&b.gnodes, g)
    }

    /// Every owned node of tree g reaches an owned root (bitmask flood fill).
    pub fn connected_bits(&self, bits: &Bits, g: usize) -> bool {
        let f = &self.fast;
        let own = bits.and(&f.tree_mask[g]);
        if own.is_zero() {
            return true;
        }
        let mut reach = own.and(&f.roots[g]);
        let mut wave = reach;
        while !wave.is_zero() {
            let mut nb = Bits::new();
            for x in wave.iter() {
                nb.or_with(&f.adj[x]);
            }
            wave = nb.and(&own).andnot(&reach);
            reach.or_with(&wave);
        }
        reach == own
    }

    /// Unowned usable nodes of tree g that can be bought now (global indices).
    pub fn frontier_bits(&self, b: &Build, g: usize) -> Bits {
        let f = &self.fast;
        let mut acc = f.roots_buy[g];
        for x in b.gnodes.and(&f.tree_mask[g]).iter() {
            acc.or_with(&f.adj[x]);
        }
        acc.and(&f.usable[g]).andnot(&b.gnodes)
    }

    /// A uniformly random removable owned node of tree g (local index): rejection sampling over owned nodes, with
    /// the exact removable() list as the fallback.
    pub fn sample_removable(&self, b: &Build, g: usize, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<usize> {
        self.sample_removable_except(b, g, &Bits::new(), rng, buf)
    }

    /// sample_removable over owned nodes outside `keep`.
    pub fn sample_removable_except(&self, b: &Build, g: usize, keep: &Bits, rng: &mut Rng, buf: &mut Vec<usize>) -> Option<usize> {
        let own = b.gnodes.and(&self.fast.tree_mask[g]).andnot(keep);
        let c = own.count();
        if c == 0 {
            return None;
        }
        let off = self.trees[g].off;
        let all = b.gnodes.and(&self.fast.tree_mask[g]);
        for _ in 0..8 {
            let x = own.select(rng.below(c));
            if !self.fast.roots[g].has(x) && self.fast.adj[x].and_count(&all) <= 1 {
                return Some(x - off);
            }
            let mut rest = b.gnodes;
            rest.clear(x);
            if self.connected_bits(&rest, g) {
                return Some(x - off);
            }
        }
        self.removable(b, g, buf);
        buf.retain(|&x| !keep.has(off + x));
        if buf.is_empty() {
            None
        } else {
            Some(rng.choice(buf))
        }
    }

    pub fn frontier(&self, b: &Build, g: usize, out: &mut Vec<usize>) {
        out.clear();
        let off = self.trees[g].off;
        for x in self.frontier_bits(b, g).iter() {
            out.push(x - off);
        }
    }

    /// Owned nodes whose removal keeps every other owned node connected to an owned root (index order): an iterative
    /// Tarjan pass from the owned roots flags candidate cut nodes, and only those get a full connectivity check.
    pub fn removable(&self, b: &Build, g: usize, out: &mut Vec<usize>) {
        out.clear();
        let t = &self.trees[g];
        let n = t.n;
        const S: usize = MAX_TREE_NODES - 1;
        let mut disc = [u16::MAX; MAX_TREE_NODES];
        let mut low = [0u16; MAX_TREE_NODES];
        let mut art = [false; MAX_TREE_NODES];
        let owned = |i: usize| b.gnodes.has(t.off + i);
        let mut time: u16 = 0;
        disc[S] = 0;
        low[S] = 0;
        time += 1;
        let mut stack = [(0usize, 0usize, 0usize); MAX_TREE_NODES];
        for &r0 in &t.roots {
            if !owned(r0) || disc[r0] != u16::MAX {
                continue;
            }
            disc[r0] = time;
            low[r0] = time;
            time += 1;
            stack[0] = (r0, S, 0);
            let mut sp = 1usize;
            while sp > 0 {
                let (v, parent, k) = stack[sp - 1];
                let adj = &t.adj[v];
                if k < adj.len() {
                    let w = adj[k];
                    stack[sp - 1].2 = k + 1;
                    if !owned(w) {
                        continue;
                    }
                    if disc[w] == u16::MAX {
                        disc[w] = time;
                        low[w] = time;
                        time += 1;
                        stack[sp] = (w, v, 0);
                        sp += 1;
                    } else if w != parent {
                        low[v] = low[v].min(disc[w]);
                    }
                } else {
                    sp -= 1;
                    if parent != S {
                        low[parent] = low[parent].min(low[v]);
                        if low[v] >= disc[parent] {
                            art[parent] = true;
                        }
                    }
                }
            }
        }
        // The DFS leaves out the super root's edges to the other owned roots, which can only over-report cut
        // nodes; every flagged node is confirmed with a full connectivity check, so the result is exact.
        let mut rest = b.gnodes;
        for i in 0..n {
            if !owned(i) {
                continue;
            }
            let removable = if art[i] {
                rest.clear(t.off + i);
                let ok = self.connected_bits(&rest, g);
                rest.set(t.off + i);
                ok
            } else {
                true
            };
            if removable {
                out.push(i);
            }
        }
    }

    /// Shortest list of unowned usable nodes whose purchase makes `target` owned legally (BFS from owned + roots).
    pub fn path_to(&self, b: &Build, g: usize, target: usize) -> Option<Vec<usize>> {
        let mut out = [0u16; MAX_TREE_NODES];
        let n = self.path_arr(b, g, target, &mut out)?;
        Some(out[..n].iter().map(|&x| x as usize).collect())
    }

    /// path_to into a fixed array (no allocation); returns the path length. Uses the precomputed distances: the
    /// nearest owned node (cost = hops) or buyable root (cost = hops + 1) is the source, and the path walks back from
    /// the target along strictly decreasing distance. A shortest path from the nearest source has no owned interior.
    pub fn path_arr(&self, b: &Build, g: usize, target: usize, out: &mut [u16; MAX_TREE_NODES]) -> Option<usize> {
        let t = &self.trees[g];
        let f = &self.fast;
        let n = t.n;
        if b.gnodes.has(t.off + target) || !t.usable[target] {
            return None;
        }
        let d = &f.dist[g];
        let (mut best, mut src, mut from_root) = (usize::MAX, 0usize, false);
        for x in b.gnodes.and(&f.tree_mask[g]).iter() {
            let s0 = x - t.off;
            let c = d[s0 * n + target] as usize;
            if c != 255 && c < best {
                best = c;
                src = s0;
                from_root = false;
            }
        }
        for x in f.roots_buy[g].andnot(&b.gnodes).iter() {
            let r = x - t.off;
            let c = d[r * n + target] as usize;
            if c != 255 && c + 1 < best {
                best = c + 1;
                src = r;
                from_root = true;
            }
        }
        if best == usize::MAX {
            return None;
        }
        let row = &d[src * n..(src + 1) * n];
        let mut len = 0usize;
        let mut x = target;
        loop {
            if x == src {
                if from_root {
                    out[len] = x as u16;
                    len += 1;
                }
                break;
            }
            out[len] = x as u16;
            len += 1;
            let want = row[x] - 1;
            x = *t.adj[x].iter().find(|&&y| t.usable[y] && row[y] == want).expect("distance table walk");
        }
        out[..len].reverse();
        Some(len)
    }

    /// gods.Gods.legal plus the rework time lock and the ancient limit (search.rework_legal).
    pub fn legal(&self, b: &Build, p: &Problem) -> bool {
        self.legal_vs(b, None, p)
    }

    /// legal(), checking only the trees that differ from `prev` (a legal build) when one is given.
    pub fn legal_vs(&self, b: &Build, prev: Option<&Build>, p: &Problem) -> bool {
        if self.xp_used(&b.glv) > self.pool {
            return false;
        }
        if (*b.glv.iter().max().unwrap() as usize) < self.rank_gate {
            return false;
        }
        for g in 0..N_GODS {
            if let Some(q) = prev {
                if q.glv[g] == b.glv[g] && q.gmts[g] == b.gmts[g] && b.gnodes.eq_masked(&q.gnodes, &self.fast.tree_mask[g]) {
                    continue;
                }
            }
            let t = &self.trees[g];
            if self.count(b, g) > self.points(b.glv[g] as usize) {
                return false;
            }
            if !b.gnodes.and(&self.fast.unusable[g]).is_zero() {
                return false;
            }
            if !self.connected(b, g) {
                return false;
            }
            if b.gnodes.and_count(&self.fast.major[g]) > 1 {
                return false;
            }
            let m = self.mts_len(b, g);
            if m > self.slots(b.glv[g] as usize) {
                return false;
            }
            for k in 0..MAX_MTS {
                let e = b.gmts[g][k];
                if e < 0 {
                    continue;
                }
                if b.gmts[g][..k].iter().any(|&x| x == e) {
                    return false;
                }
                let mn = self.effects[e as usize].minor_node;
                if mn < 0 || self.effects[e as usize].god != g || !b.gnodes.has(t.off + mn as usize) {
                    return false;
                }
            }
        }
        self.cheap_legal(b, p)
    }

    /// The parts of legality a non-god move can change: the time lock and the ancient limit.
    pub fn cheap_legal(&self, b: &Build, p: &Problem) -> bool {
        if !self.time_lock_ok(b, p) {
            return false;
        }
        let anc = b.items.iter().filter(|it| it.unique.map_or(false, |u| p.uniques[u as usize].ancient)).count();
        anc <= p.ancient_max
    }

    /// Temporal Breaking applies: owned in the active Wendarr tree, or held in a Wendarr transfer slot otherwise.
    pub fn has_temporal_breaking(&self, b: &Build) -> bool {
        self.minor_on(b, self.tb_effect)
    }

    pub fn time_lock_ok(&self, b: &Build, _p: &Problem) -> bool {
        if !self.time_lock_dynamic || self.has_temporal_breaking(b) {
            return true;
        }
        let off = &b.items[OFFHAND];
        let main = &b.items[MAINHAND];
        off.ty() as i64 == self.plushie_type && off.unique.is_none() && main.ty() as i64 != self.staff_type
    }

    /// Sell random removable nodes until within points; drop transfer entries over the slot count or unowned.
    pub fn trim(&self, b: &mut Build, g: usize, rng: &mut Rng, buf: &mut Vec<usize>) -> bool {
        let cap = self.points(b.glv[g] as usize);
        while self.count(b, g) > cap {
            match self.sample_removable(b, g, rng, buf) {
                Some(x) => b.gnodes.clear(self.trees[g].off + x),
                None => return false,
            }
        }
        self.clean_mts(b, g);
        true
    }

    pub fn clean_mts(&self, b: &mut Build, g: usize) {
        let t = &self.trees[g];
        let slots = self.slots(b.glv[g] as usize);
        let mut out = [-1i16; MAX_MTS];
        let mut k = 0;
        for &e in b.gmts[g].iter() {
            if e < 0 {
                continue;
            }
            let mn = self.effects[e as usize].minor_node;
            if mn >= 0 && b.gnodes.has(t.off + mn as usize) && k < slots && !out[..k].contains(&e) {
                out[k] = e;
                k += 1;
            }
        }
        b.gmts[g] = out;
    }

    /// Add uniformly random purchasable nodes up to the points.
    pub fn fill(&self, b: &mut Build, g: usize, rng: &mut Rng, _buf: &mut Vec<usize>) {
        let cap = self.points(b.glv[g] as usize);
        let mut have = self.count(b, g);
        while have < cap {
            let mut fr = self.frontier_bits(b, g);
            if !b.gnodes.and(&self.fast.major[g]).is_zero() {
                fr = fr.andnot(&self.fast.major[g]);
            }
            let c = fr.count();
            if c == 0 {
                break;
            }
            b.gnodes.set(fr.select(rng.below(c)));
            have += 1;
        }
    }

    /// Before buying major `keep` (local index) of tree g: sell the tree's other majors and every node that then no
    /// longer reaches an owned root, so the build keeps one major per tree.
    pub fn drop_other_majors(&self, b: &mut Build, g: usize, keep: usize) {
        let f = &self.fast;
        let mut other = b.gnodes.and(&f.major[g]);
        other.clear(self.trees[g].off + keep);
        if other.is_zero() {
            return;
        }
        for x in other.iter() {
            b.gnodes.clear(x);
        }
        let own = b.gnodes.and(&f.tree_mask[g]);
        let mut reach = own.and(&f.roots[g]);
        let mut wave = reach;
        while !wave.is_zero() {
            let mut nb = Bits::new();
            for x in wave.iter() {
                nb.or_with(&f.adj[x]);
            }
            wave = nb.and(&own).andnot(&reach);
            reach.or_with(&wave);
        }
        for x in own.andnot(&reach).iter() {
            b.gnodes.clear(x);
        }
        self.clean_mts(b, g);
    }

    /// search.init_gods: one god levelled at random (at least the rank gate), the rest random, nodes filled.
    pub fn init(&self, b: &mut Build, rng: &mut Rng, buf: &mut Vec<usize>, primary: Option<usize>) {
        let mut order = [0usize, 1, 2, 3];
        rng.shuffle(&mut order);
        if let Some(pg) = primary {
            let k = order.iter().position(|&x| x == pg).unwrap();
            order.swap(0, k);
        }
        let primary = order[0];
        b.glv = [0; N_GODS];
        b.gnodes = Bits::new();
        b.gmts = [[-1; MAX_MTS]; N_GODS];
        let lo = self.rank_gate.min(self.max_level);
        b.glv[primary] = (lo + rng.below(self.max_level - lo + 1)) as u8;
        for &g in &order[1..] {
            while rng.f64() < 0.6 {
                b.glv[g] += 1;
                if self.xp_used(&b.glv) > self.pool {
                    b.glv[g] -= 1;
                    break;
                }
            }
        }
        if self.xp_used(&b.glv) > self.pool {
            b.glv = [0; N_GODS];
            b.glv[primary] = self.max_level as u8;
        }
        for g in 0..N_GODS {
            self.fill(b, g, rng, buf);
        }
    }

    /// search.god_move: node / path / level / transfer-slot moves. Level moves only touch the two gods whose levels
    /// change; nothing allocates.
    pub fn mv(&self, n: &mut Build, rng: &mut Rng, buf: &mut Vec<usize>, w: Option<&[f64]>) -> bool {
        let kind = rng.below(5);
        self.mv_kind(n, rng, buf, kind, w)
    }

    /// A set bit of `bits`, uniform, or proportional to `w` (per global node) when given.
    fn pick(bits: &Bits, rng: &mut Rng, w: Option<&[f64]>) -> usize {
        match w {
            None => bits.select(rng.below(bits.count())),
            Some(w) => {
                let tot: f64 = bits.iter().map(|x| w[x]).sum();
                let mut r = rng.f64() * tot;
                let mut last = 0;
                for x in bits.iter() {
                    last = x;
                    r -= w[x];
                    if r <= 0.0 {
                        return x;
                    }
                }
                last
            }
        }
    }

    pub fn mv_kind(&self, n: &mut Build, rng: &mut Rng, buf: &mut Vec<usize>, kind: usize, w: Option<&[f64]>) -> bool {
        if kind == 3 {
            let up = rng.below(N_GODS);
            let mut down = rng.below(N_GODS - 1);
            if down >= up {
                down += 1;
            }
            if rng.f64() < 0.5 {
                n.glv[up] += 1;
            }
            if n.glv[down] > 0 && rng.f64() < 0.7 {
                n.glv[down] -= 1;
            }
            if self.xp_used(&n.glv) > self.pool {
                return false;
            }
            if !self.trim(n, down, rng, buf) || !self.trim(n, up, rng, buf) {
                return false;
            }
            if rng.f64() < 0.5 {
                self.fill(n, up, rng, buf);
            }
            return true;
        }
        let mut with = [0usize; N_GODS];
        let mut nw = 0;
        for g in 0..N_GODS {
            if self.points(n.glv[g] as usize) > 0 {
                with[nw] = g;
                nw += 1;
            }
        }
        if nw == 0 {
            return false;
        }
        let g = with[rng.below(nw)];
        let t = &self.trees[g];
        let cap = self.points(n.glv[g] as usize);
        if kind == 4 {
            if g == self.active(n) || self.slots(n.glv[g] as usize) == 0 {
                return false;
            }
            buf.clear();
            for (e, ef) in self.effects.iter().enumerate() {
                if ef.god == g && ef.minor_node >= 0 && n.gnodes.has(t.off + ef.minor_node as usize) && !self.mts_has(n, g, e) {
                    buf.push(e);
                }
            }
            let m = self.mts_len(n, g);
            if m > 0 && (buf.is_empty() || rng.f64() < 0.3) {
                let k = rng.below(m);
                let mut out = [-1i16; MAX_MTS];
                let mut j = 0;
                let mut seen = 0;
                for &e in n.gmts[g].iter() {
                    if e < 0 {
                        continue;
                    }
                    if seen != k {
                        out[j] = e;
                        j += 1;
                    }
                    seen += 1;
                }
                n.gmts[g] = out;
            } else if !buf.is_empty() {
                let e = rng.choice(buf) as i16;
                if m < self.slots(n.glv[g] as usize) {
                    n.gmts[g][m] = e;
                } else {
                    n.gmts[g][rng.below(m)] = e;
                }
            } else {
                return false;
            }
            return true;
        }
        if kind == 2 {
            let pool = self.fast.usable[g].andnot(&n.gnodes);
            if pool.is_zero() {
                return false;
            }
            let big = pool.and(&self.fast.big[g]);
            let src = if !big.is_zero() && rng.f64() < 0.7 { big } else { pool };
            let target = Self::pick(&src, rng, w) - t.off;
            if t.ty[target] == 3 {
                self.drop_other_majors(n, g, target);
            }
            let mut path = [0u16; MAX_TREE_NODES];
            let len = match self.path_arr(n, g, target, &mut path) {
                Some(l) if l > 0 && l <= cap => l,
                _ => return false,
            };
            let mut keep = Bits::new();
            for &x in &path[..len] {
                n.gnodes.set(t.off + x as usize);
                keep.set(t.off + x as usize);
            }
            while self.count(n, g) > cap {
                match self.sample_removable_except(n, g, &keep, rng, buf) {
                    Some(x) => n.gnodes.clear(t.off + x),
                    None => return false,
                }
            }
        } else {
            let have = self.count(n, g);
            if have >= cap || (have > 0 && rng.f64() < 0.3) {
                match self.sample_removable(n, g, rng, buf) {
                    Some(x) => n.gnodes.clear(t.off + x),
                    None => return false,
                }
            }
            let have = self.count(n, g);
            if have < cap && (rng.f64() < 0.85 || have == 0) {
                let fr = self.frontier_bits(n, g);
                if fr.is_zero() {
                    return false;
                }
                n.gnodes.set(Self::pick(&fr, rng, w));
            }
        }
        self.clean_mts(n, g);
        true
    }
}

/// Effect indices the combat effects read (-1 when the effect is absent).
#[derive(Clone, Default)]
pub struct GfxIx {
    pub banked: i64, pub surrounded: i64, pub under: i64, pub paced: i64, pub sneaky: i64, pub eot: i64, pub demon: i64,
    pub immortal: i64, pub kinetic: i64, pub greed: i64, pub crushing: i64, pub weaponmaster: i64, pub pincushion: i64,
    pub true_rage: i64, pub overcrit: i64, pub luckiest: i64, pub archmage: i64, pub power_dump: i64, pub fury: i64,
    pub stonewall: i64, pub cactus: i64, pub flow: i64, pub malediction: i64, pub counter: i64, pub indomitable: i64,
    pub adaptive: i64, pub shield: i64, pub fleeting: i64, pub starved: i64, pub super_stacker: i64, pub utilized: i64,
    pub plushie_lover: i64, pub sss: i64, pub steadfast: i64, pub stack_hoarder: i64,
}

impl GfxIx {
    pub fn new(names: &[String]) -> GfxIx {
        let f = |n: &str| names.iter().position(|x| x == n).map_or(-1, |i| i as i64);
        GfxIx {
            banked: f("idona_banked_anger"), surrounded: f("idona_surrounded"), under: f("idona_under_pressure"),
            paced: f("wendarr_paced_strikes"), sneaky: f("idona_sneaky_advantage"), eot: f("wendarr_edge_of_time"),
            demon: f("wendarr_speed_demon"), immortal: f("velara_immortal"), kinetic: f("idona_kinetic_impact"),
            greed: f("tenos_unstoppable_greed"), crushing: f("idona_crushing_blows"), weaponmaster: f("idona_weaponmaster"),
            pincushion: f("idona_pincushion"), true_rage: f("idona_true_rage"), overcrit: f("idona_overcrit"),
            luckiest: f("idona_luckiest_hit"), archmage: f("idona_grand_archmage"), power_dump: f("idona_power_dump"),
            fury: f("idona_ultra_rampaging"), stonewall: f("velara_the_stonewall"), cactus: f("velara_cactus"),
            flow: f("velara_healing_flow"), malediction: f("velara_malediction"), counter: f("velara_counterstrike"),
            indomitable: f("velara_indomitable"), adaptive: f("velara_adaptive_armor"),
            shield: f("wendarr_temporal_shielding"), fleeting: f("velara_fleeting_physicality"),
            starved: f("tenos_mana_starved"), super_stacker: f("idona_super_stacker"), utilized: f("velara_utilized"),
            plushie_lover: f("wendarr_plushie_lover"), sss: f("idona_stack_stack_stack"), steadfast: f("velara_steadfast"),
            stack_hoarder: f("idona_stack_hoarder"),
        }
    }
}

/// The numeric parameters gfx() reads, resolved once at load (0 for an effect the trees do not have); a missing
/// parameter of a present effect fails at load instead of mid-search.
#[derive(Clone, Default)]
pub struct GfxPar {
    pub banked_base: f64, pub surrounded_per_mob: f64, pub under_window_ticks: f64, pub under_max: f64,
    pub paced_reference_minutes: f64, pub eot_multiplier: f64, pub demon_stat_multiplier: f64,
    pub immortal_damage_multiplier: f64, pub immortal_armor_multiplier: f64, pub stonewall_armor_multiplier: f64,
    pub cactus_armor_multiplier: f64, pub indomitable_regeneration_levels: f64, pub immortal_regeneration_levels: f64,
    pub kinetic_per_percent: f64, pub greed_ratio: f64, pub crushing_multiplier: f64,
    pub weaponmaster_two_handed: f64, pub pincushion_per_hit: f64, pub true_rage_efficiency: f64,
    pub luckiest_chance_scale: f64, pub archmage_ability_damage: f64, pub power_dump_per_mana: f64,
    pub immortal_health_multiplier: f64, pub immortal_healing_multiplier: f64, pub flow_per_mana_regen: f64,
    pub malediction_forced_healing: f64, pub cactus_thorns_multiplier: f64, pub counter_chance: f64,
    pub adaptive_per_stack: f64, pub adaptive_max_stacks: f64, pub shield_reduction: f64,
    pub fleeting_damage_multiplier: f64, pub starved_max_bonus: f64, pub super_stacker_multiplier: f64,
    pub utilized_ability_levels: f64, pub sneaky_per_effect: f64, pub power_dump_surplus_ttl_ticks: f64,
    pub stonewall_speed_multiplier: f64, pub steadfast_knockback_floor: f64, pub steadfast_armor_per_excess: f64,
    pub stack_hoarder_multiplier: f64,
}

impl GfxPar {
    pub fn new(g: &GodData) -> GfxPar {
        let ix = &g.ix;
        GfxPar {
            banked_base: g.par_or_zero(ix.banked, "base"),
            surrounded_per_mob: g.par_or_zero(ix.surrounded, "per_mob"),
            under_window_ticks: g.par_or_zero(ix.under, "window_ticks"),
            under_max: g.par_or_zero(ix.under, "max"),
            paced_reference_minutes: g.par_or_zero(ix.paced, "reference_minutes"),
            eot_multiplier: g.par_or_zero(ix.eot, "multiplier"),
            demon_stat_multiplier: g.par_or_zero(ix.demon, "stat_multiplier"),
            immortal_damage_multiplier: g.par_or_zero(ix.immortal, "damage_multiplier"),
            immortal_armor_multiplier: g.par_or_zero(ix.immortal, "armor_multiplier"),
            stonewall_armor_multiplier: g.par_or_zero(ix.stonewall, "armor_multiplier"),
            cactus_armor_multiplier: g.par_or_zero(ix.cactus, "armor_multiplier"),
            indomitable_regeneration_levels: g.par_or_zero(ix.indomitable, "regeneration_levels"),
            immortal_regeneration_levels: g.par_or_zero(ix.immortal, "regeneration_levels"),
            kinetic_per_percent: g.par_or_zero(ix.kinetic, "per_percent"),
            greed_ratio: g.par_or_zero(ix.greed, "ratio"),
            crushing_multiplier: g.par_or_zero(ix.crushing, "multiplier"),
            weaponmaster_two_handed: g.par_or_zero(ix.weaponmaster, "two_handed"),
            pincushion_per_hit: g.par_or_zero(ix.pincushion, "per_hit"),
            true_rage_efficiency: g.par_or_zero(ix.true_rage, "efficiency"),
            luckiest_chance_scale: g.par_or_zero(ix.luckiest, "chance_scale"),
            archmage_ability_damage: g.par_or_zero(ix.archmage, "ability_damage"),
            power_dump_per_mana: g.par_or_zero(ix.power_dump, "per_mana"),
            immortal_health_multiplier: g.par_or_zero(ix.immortal, "health_multiplier"),
            immortal_healing_multiplier: g.par_or_zero(ix.immortal, "healing_multiplier"),
            flow_per_mana_regen: g.par_or_zero(ix.flow, "per_mana_regen"),
            malediction_forced_healing: g.par_or_zero(ix.malediction, "forced_healing"),
            cactus_thorns_multiplier: g.par_or_zero(ix.cactus, "thorns_multiplier"),
            counter_chance: g.par_or_zero(ix.counter, "chance"),
            adaptive_per_stack: g.par_or_zero(ix.adaptive, "per_stack"),
            adaptive_max_stacks: g.par_or_zero(ix.adaptive, "max_stacks"),
            shield_reduction: g.par_or_zero(ix.shield, "reduction"),
            fleeting_damage_multiplier: g.par_or_zero(ix.fleeting, "damage_multiplier"),
            starved_max_bonus: g.par_or_zero(ix.starved, "max_bonus"),
            super_stacker_multiplier: g.par_or_zero(ix.super_stacker, "multiplier"),
            utilized_ability_levels: g.par_or_zero(ix.utilized, "ability_levels"),
            sneaky_per_effect: g.par_or_zero(ix.sneaky, "per_effect"),
            power_dump_surplus_ttl_ticks: g.par_or_zero(ix.power_dump, "surplus_ttl_ticks"),
            stonewall_speed_multiplier: g.par_or_zero(ix.stonewall, "speed_multiplier"),
            steadfast_knockback_floor: g.par_or_zero(ix.steadfast, "knockback_floor"),
            steadfast_armor_per_excess: g.par_or_zero(ix.steadfast, "armor_per_excess"),
            stack_hoarder_multiplier: g.par_or_zero(ix.stack_hoarder, "multiplier"),
        }
    }
}

/// model/godfx.compute: the constant numbers of the custom god effects that apply.
#[derive(Clone, Copy, Default, Debug, PartialEq)]
pub struct GodFx {
    pub global: f64, pub kinetic: f64, pub greed: f64, pub stack: f64, pub weaponmaster: f64, pub pincushion: f64,
    pub true_rage: f64, pub overcrit: f64, pub luckiest: f64, pub archmage: bool, pub ap: f64, pub power_dump: f64,
    pub fury: bool, pub health_mult: f64, pub armor_mult: f64, pub he_mult: f64, pub he_flow: f64, pub malediction: f64,
    pub thorns_m: f64, pub thorns_f: f64, pub counter: f64, pub revive: bool, pub regen_levels: i64, pub adapt_per: f64,
    pub adapt_max: f64, pub shield: f64, pub fleeting: f64, pub mana_starved: f64, pub super_stacker: f64,
    pub utilized: f64, pub plushie_lover: bool, pub speed_mult: f64, pub power_dump_ttl: f64, pub sneaky: f64,
    pub steadfast_floor: f64, pub steadfast_per: f64, pub stack_hoarder: f64,
}

pub fn paced_avg(r: f64, m: f64) -> f64 {
    if m <= 0.0 { 1.0 } else { (2.0 / (3.0 * m)) * r * (((r + m) / r).powf(1.5) - 1.0) }
}

pub fn under_pressure_avg(window: f64, m: f64, mx: f64) -> f64 {
    if m <= 0.0 {
        return mx;
    }
    let w = window.min(m);
    1.0 + (mx - 1.0) * (w - w * w / (2.0 * window)) / m
}

pub fn regen_hps(levels: i64) -> f64 {
    if levels <= 0 {
        return 0.0;
    }
    let period = if levels - 1 < 31 { 50i64 >> (levels - 1) } else { 0 };
    20.0 / (period.max(1) as f64)
}

impl GodData {
    /// Parameter `k` of effect `e` (0 when the effect is absent); panics at load when a present effect lacks it.
    fn par_or_zero(&self, e: i64, k: &str) -> f64 {
        if e < 0 {
            return 0.0;
        }
        *self.effects[e as usize].params.get(k).unwrap_or_else(|| panic!("god effect {} has no param {}", self.names[e as usize], k))
    }

    /// A minor applies: owned and (its god is the charm god, or it sits in that god's transfer slots).
    pub fn minor_on(&self, b: &Build, e: i64) -> bool {
        if e < 0 {
            return false;
        }
        let ef = &self.effects[e as usize];
        let g = ef.god;
        if ef.minor_node < 0 || !b.gnodes.has(self.trees[g].off + ef.minor_node as usize) {
            return false;
        }
        self.active(b) == g || self.mts_has(b, g, e as usize)
    }

    pub fn gfx(&self, r: &Ranks, k: &Knobs) -> GodFx {
        let x = &self.ix;
        let on = |e: i64| e >= 0 && r.n[e as usize] > 0;
        let mut glob = 1.0;
        if on(x.banked) {
            let b = self.gp.banked_base;
            glob *= (b + k.god_banked_damage_taken).ln() / b.ln();
        }
        if on(x.surrounded) {
            glob *= 1.0 + self.gp.surrounded_per_mob * k.god_surrounded_mobs;
        }
        if on(x.under) {
            glob *= under_pressure_avg(self.gp.under_window_ticks / 1200.0, k.god_vault_minutes, self.gp.under_max);
        }
        if on(x.paced) {
            glob *= paced_avg(self.gp.paced_reference_minutes, k.god_vault_minutes);
        }
        if on(x.eot) {
            glob *= self.gp.eot_multiplier;
        }
        if on(x.demon) {
            glob *= self.gp.demon_stat_multiplier;
        }
        if on(x.immortal) {
            glob *= self.gp.immortal_damage_multiplier;
        }
        let mut am = 1.0;
        if on(x.immortal) {
            am *= self.gp.immortal_armor_multiplier;
        }
        if on(x.stonewall) {
            am *= self.gp.stonewall_armor_multiplier;
        }
        if on(x.cactus) {
            am *= self.gp.cactus_armor_multiplier;
        }
        let mut regen = 0i64;
        if on(x.indomitable) {
            regen += self.gp.indomitable_regeneration_levels as i64;
        }
        if on(x.immortal) {
            regen += self.gp.immortal_regeneration_levels as i64;
        }
        GodFx {
            global: glob,
            kinetic: if on(x.kinetic) { self.gp.kinetic_per_percent } else { 0.0 },
            greed: if on(x.greed) { self.gp.greed_ratio } else { 0.0 },
            stack: if on(x.crushing) { self.gp.crushing_multiplier } else { 1.0 },
            weaponmaster: if on(x.weaponmaster) { self.gp.weaponmaster_two_handed } else { 1.0 },
            pincushion: if on(x.pincushion) { self.gp.pincushion_per_hit } else { 0.0 },
            true_rage: if on(x.true_rage) { self.gp.true_rage_efficiency } else { 0.0 },
            overcrit: if on(x.overcrit) { 1.0 } else { 0.0 },
            luckiest: if on(x.luckiest) { self.gp.luckiest_chance_scale } else { 0.0 },
            archmage: on(x.archmage),
            ap: if on(x.archmage) { self.gp.archmage_ability_damage } else { 1.0 },
            power_dump: if on(x.power_dump) { self.gp.power_dump_per_mana } else { 0.0 },
            fury: on(x.fury),
            health_mult: if on(x.immortal) { self.gp.immortal_health_multiplier } else { 1.0 },
            armor_mult: am,
            he_mult: if on(x.immortal) { self.gp.immortal_healing_multiplier } else { 1.0 },
            he_flow: if on(x.flow) { self.gp.flow_per_mana_regen } else { 0.0 },
            malediction: if on(x.malediction) { self.gp.malediction_forced_healing } else { 0.0 },
            thorns_m: if on(x.cactus) { self.gp.cactus_thorns_multiplier * 0.75 } else { 1.0 },
            thorns_f: if on(x.cactus) { self.gp.cactus_thorns_multiplier } else { 1.0 },
            counter: if on(x.counter) { self.gp.counter_chance } else { 0.0 },
            revive: on(x.immortal),
            regen_levels: regen,
            adapt_per: if on(x.adaptive) { self.gp.adaptive_per_stack } else { 0.0 },
            adapt_max: if on(x.adaptive) { self.gp.adaptive_max_stacks } else { 0.0 },
            shield: if on(x.shield) { self.gp.shield_reduction } else { 1.0 },
            fleeting: if on(x.fleeting) { self.gp.fleeting_damage_multiplier } else { 0.0 },
            mana_starved: if on(x.starved) { self.gp.starved_max_bonus } else { 0.0 },
            super_stacker: if on(x.super_stacker) { self.gp.super_stacker_multiplier } else { 1.0 },
            utilized: if on(x.utilized) { self.gp.utilized_ability_levels } else { 0.0 },
            plushie_lover: on(x.plushie_lover),
            sneaky: if on(x.sneaky) { self.gp.sneaky_per_effect } else { 0.0 },
            power_dump_ttl: if on(x.power_dump) { self.gp.power_dump_surplus_ttl_ticks } else { 0.0 },
            speed_mult: if on(x.stonewall) { self.gp.stonewall_speed_multiplier } else { 1.0 },
            steadfast_floor: if on(x.steadfast) { self.gp.steadfast_knockback_floor } else { 0.0 },
            steadfast_per: if on(x.steadfast) { self.gp.steadfast_armor_per_excess } else { 0.0 },
            stack_hoarder: if on(x.stack_hoarder) { self.gp.stack_hoarder_multiplier } else { 1.0 },
        }
    }

    /// (<M>, <M cbrt(F/6500)>, <incoming>) at h player hits/s (godfx.fury_at).
    pub fn fury_at(&self, h: f64) -> (f64, f64, f64) {
        let t = &self.fury;
        if h <= t[0][0] {
            return (t[0][1], t[0][2], t[0][3]);
        }
        for w in t.windows(2) {
            let (a, b) = (&w[0], &w[1]);
            if h <= b[0] {
                let f = (h - a[0]) / (b[0] - a[0]);
                return (a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), a[3] + f * (b[3] - a[3]));
            }
        }
        let l = t[t.len() - 1];
        (l[1], l[2], l[3])
    }

    /// Plushie Lover applies and the offhand is a plushie: its affixes count twice.
    pub fn offhand_weight(&self, b: &Build) -> f64 {
        if self.plushie_type >= 0 && b.items[OFFHAND].ty() as i64 == self.plushie_type && self.minor_on(b, self.ix.plushie_lover) {
            2.0
        } else {
            1.0
        }
    }
}

pub fn adaptive_factors(per: f64, mx: f64, hits: f64) -> (f64, f64) {
    if per <= 0.0 {
        return (1.0, 1.0);
    }
    let n = hits as i64;
    let mut sum = 0.0;
    let mut first = 1.0;
    for k in 1..=n {
        let f = 1.0 - per * (k as f64).min(mx);
        if k == 1 {
            first = f;
        }
        sum += f;
    }
    (first, sum / n as f64)
}
