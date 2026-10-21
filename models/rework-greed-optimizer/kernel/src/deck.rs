//! Rework deck designs (model/deck_rework.py, DeckFAST tagsim.rs simulate_rework): a design fixes each slot's type,
//! colour, modifiers and top core sets; the build picks the card in every slot. Per scorable card:
//! base x (1 + sum additive) x product multiplicative x (1 + sum greed). Kept equal to the Python reference by
//! tools/parity_cases_deck.py.

use serde::Deserialize;
use std::collections::HashMap;

pub const T_ROW: u8 = 0;
pub const T_COL: u8 = 1;
pub const T_DIAG: u8 = 3;
pub const T_DELUXE: u8 = 4;
pub const T_TYPELESS: u8 = 5;
pub const T_G_UP: u8 = 6;
pub const T_G_RIGHT: u8 = 9;
pub const T_ARCANE: u8 = 10;
pub const T_DEAD: u8 = 12;
const COLOR_NONE: u8 = 255;

/// Card kinds (kernel.py DECK_KINDS).
pub const K_DEAD: u8 = 0;
pub const K_GREED: u8 = 1;
pub const K_EVO: u8 = 2;
pub const K_TYPELESS: u8 = 3;
pub const K_DELUXE: u8 = 4;
pub const K_ARCANE: u8 = 8;

// Group bits (model/deck_rework.py GROUP_BITS).
const B_OFF: u32 = 1 << 0;
const B_DEF: u32 = 1 << 1;
const B_PHYS: u32 = 1 << 2;
const B_MAG: u32 = 1 << 3;
const B_UTIL: u32 = 1 << 4;
const B_RES: u32 = 1 << 5;
const B_KNACK: u32 = 1 << 6;
const B_TEMP: u32 = 1 << 7;
const B_FOIL: u32 = 1 << 9;
const B_STAT: u32 = 1 << 10;
const B_EVO: u32 = 1 << 11;
const B_DELUXE: u32 = 1 << 12;
const B_SHINY: u32 = 1 << 15;
const NONSTAT: u32 = B_RES | B_KNACK | B_TEMP;
const N_GROUPS: usize = 16;
/// Group bits in alphabetical order of their game names (dominance tie-break).
const ALPHA: [u32; N_GROUPS] = [
    1 << 13, 1 << 1, 1 << 12, 1 << 8, 1 << 11, 1 << 9, 1 << 14, 1 << 6, 1 << 3, 1 << 0, 1 << 2, 1 << 5, 1 << 15,
    1 << 10, 1 << 7, 1 << 4,
];

pub const C_PURE: u8 = 0;
pub const C_EQUILIBRIUM: u8 = 1;
pub const C_STEADFAST: u8 = 2;
pub const C_SHINY: u8 = 3;
pub const C_COLOR: u8 = 4;
pub const C_PREMIUM: u8 = 5;
pub const C_VOID: u8 = 6;
pub const C_ARCHIVE: u8 = 7;
pub const C_EMPYREAL: u8 = 8;
pub const C_ARSENAL: u8 = 9;
pub const C_AEGIS: u8 = 10;
pub const C_TOOL: u8 = 11;
pub const C_NATURAL: u8 = 12;
pub const C_FAE: u8 = 13;
pub const C_JUPITER: u8 = 14;
pub const C_PLUTO: u8 = 15;
pub const C_BAZAAR: u8 = 16;
pub const C_TALENT: u8 = 17;
pub const C_TEMPORAL: u8 = 18;
pub const C_GREED: u8 = 19;

const I_GLOBAL: u8 = 0;
const I_FREQ: u8 = 1;
const I_ADJ: u8 = 2;
const I_MISMATCH: u8 = 3;
const I_ROWPOS: u8 = 4;
const I_CHAIN: u8 = 5;
const I_EMPTY: u8 = 6;
const I_UNIQUE: u8 = 7;
const I_MIRROR: u8 = 8;

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct DCard {
    pub card: usize,
    pub kind: u8,
    /// Attribute index, or -1 for cards with no stat.
    pub a: i32,
    pub v: f64,
    pub g: u32,
    /// Level grant: 0 none, 1 ability level, 2 talent level; key indexes lvl_keys / tal_keys.
    pub gk: u8,
    pub key: usize,
    pub lv: f64,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct CoreSet {
    /// (core code, value, k, colour) per core.
    pub cores: Vec<(u8, f64, usize, u8)>,
    pub nitwit: f64,
    pub shiny: bool,
    pub nslots: Vec<bool>,
}

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct DesignDoc {
    pub slots: Vec<(i32, i32)>,
    pub t: Vec<u8>,
    pub color: Vec<u8>,
    pub opts: Vec<Vec<DCard>>,
    pub csets: Vec<CoreSet>,
    /// (kind, value, group mask, colour mask, ptype) per modifier.
    pub imps: Vec<(u8, f64, u32, u8, u8)>,
    pub greed: f64,
    pub deluxe_base: f64,
    /// Card limits (Knack, Temporal, Deluxe); -1 = none.
    pub limits: (i32, i32, i32),
    pub seed: Vec<usize>,
}

#[derive(Clone)]
pub struct Design {
    pub doc: DesignDoc,
    pub n: usize,
    row: Vec<i32>,
    colc: Vec<i32>,
    surr: Vec<Vec<usize>>,
    diag: Vec<Vec<usize>>,
    colp: Vec<Vec<usize>>,
    orth: Vec<Vec<usize>>,
    dir: Vec<[Option<usize>; 4]>,
    rows_from_bottom: Vec<f64>,
    mirror_self: Vec<bool>,
    mirror_of: Vec<Option<usize>>,
    starcross: Vec<Vec<usize>>,
    row_min: i32,
    col_min: i32,
    rspan: usize,
    cspan: usize,
}

impl Design {
    pub fn new(doc: DesignDoc) -> Design {
        let s = &doc.slots;
        let n = s.len();
        let idx: HashMap<(i32, i32), usize> = s.iter().enumerate().map(|(i, &p)| (p, i)).collect();
        let peers = |f: &dyn Fn((i32, i32), (i32, i32)) -> bool| -> Vec<Vec<usize>> {
            (0..n).map(|i| (0..n).filter(|&j| j != i && f(s[i], s[j])).collect()).collect()
        };
        let surr = peers(&|p, q| (q.0 - p.0).abs().max((q.1 - p.1).abs()) <= 1);
        let diag = peers(&|p, q| q.0 - q.1 == p.0 - p.1 || q.0 + q.1 == p.0 + p.1);
        let colp = peers(&|p, q| q.1 == p.1);
        let orth: Vec<Vec<usize>> = (0..n).map(|i| {
            let mut v: Vec<usize> = [(-1, 0), (1, 0), (0, -1), (0, 1)].iter()
                .filter_map(|&(dr, dc)| idx.get(&(s[i].0 + dr, s[i].1 + dc)).copied()).collect();
            v.sort_unstable();
            v
        }).collect();
        let dir: Vec<[Option<usize>; 4]> = (0..n).map(|i| {
            let g = |dr: i32, dc: i32| idx.get(&(s[i].0 + dr, s[i].1 + dc)).copied();
            [g(-1, 0), g(1, 0), g(0, -1), g(0, 1)]
        }).collect();
        let rmin = s.iter().map(|p| p.0).min().unwrap_or(0);
        let rmax = s.iter().map(|p| p.0).max().unwrap_or(0);
        let cmin = s.iter().map(|p| p.1).min().unwrap_or(0);
        let cmax = s.iter().map(|p| p.1).max().unwrap_or(0);
        let mirror_self: Vec<bool> = s.iter().map(|p| cmax - (p.1 - cmin) == p.1).collect();
        let mirror_of: Vec<Option<usize>> = (0..n).map(|i| {
            if mirror_self[i] { None } else { idx.get(&(s[i].0, cmax - (s[i].1 - cmin))).copied() }
        }).collect();
        const OFFS: [(i32, i32); 12] = [(-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2),
                                        (-1, -1), (-1, 1), (1, -1), (1, 1)];
        let starcross = (0..n).map(|i| OFFS.iter()
            .filter_map(|&(dr, dc)| idx.get(&(s[i].0 + dr, s[i].1 + dc)).copied()).collect()).collect();
        Design {
            n, row: s.iter().map(|p| p.0).collect(), colc: s.iter().map(|p| p.1).collect(), surr, diag, colp, orth,
            dir, rows_from_bottom: s.iter().map(|p| (rmax - p.0 + 1) as f64).collect(), mirror_self, mirror_of,
            starcross, row_min: rmin, col_min: cmin, rspan: (rmax - rmin + 1) as usize,
            cspan: (cmax - cmin + 1) as usize, doc,
        }
    }

    #[inline]
    pub fn card(&self, i: usize, o: usize) -> &DCard {
        &self.doc.opts[i][o]
    }

    /// Early-game card limits, each its own cap.
    pub fn limits_ok(&self, choice: &[u16]) -> bool {
        let (lk, lt, ld) = self.doc.limits;
        if lk < 0 && lt < 0 && ld < 0 {
            return true;
        }
        let (mut k, mut t, mut d) = (0, 0, 0);
        for (i, &o) in choice.iter().enumerate() {
            let g = self.doc.opts[i][o as usize].g;
            k += (g & B_KNACK != 0) as i32;
            t += (g & B_TEMP != 0) as i32;
            d += (g & B_DELUXE != 0) as i32;
        }
        (lk < 0 || k <= lk) && (lt < 0 || t <= lt) && (ld < 0 || d <= ld)
    }

    /// Per-slot terms of the deck (0 for non-scoring slots), written into `terms`.
    pub fn terms(&self, cset: usize, choice: &[u16], terms: &mut Vec<f64>) {
        let n = self.n;
        let d = &self.doc;
        let cs = &d.csets[cset];
        terms.clear();
        terms.resize(n, 0.0);
        let mut t = [T_DEAD; 128];
        let mut col = [COLOR_NONE; 128];
        let mut xg = [0u32; 128];
        assert!(n <= 128, "deck design with more than 128 slots");
        for i in 0..n {
            let c = &d.opts[i][choice[i] as usize];
            let st = d.t[i];
            t[i] = match c.kind {
                K_DEAD => T_DEAD,
                K_GREED | K_EVO => st,
                K_ARCANE => T_ARCANE,
                K_DELUXE => T_DELUXE,
                _ => T_TYPELESS,
            };
            if t[i] == T_DEAD {
                continue;
            }
            col[i] = d.color[i];
            let mut m = c.g;
            if cs.shiny {
                if st <= T_DIAG && c.kind == K_EVO {
                    m = (m & !B_EVO) | B_SHINY | B_STAT;
                }
                if c.kind == K_EVO || c.kind == K_TYPELESS || c.kind == K_DELUXE {
                    m |= B_FOIL;
                }
            }
            xg[i] = m;
        }
        let mut gcount = [0u32; N_GROUPS];
        let (mut n_empty, mut n_nonfoil, mut n_deluxe, mut n_arcane, mut n_util) = (0u32, 0u32, 0u32, 0u32, 0u32);
        let mut seen = [false; 4];
        let mut row_col = vec![0u32; self.rspan * 4];
        let mut col_col = vec![0u32; self.cspan * 4];
        for i in 0..n {
            if t[i] == T_DEAD {
                n_empty += 1;
                continue;
            }
            let m = xg[i];
            for b in 0..N_GROUPS {
                if m >> b & 1 == 1 {
                    gcount[b] += 1;
                }
            }
            n_nonfoil += (m & B_FOIL == 0) as u32;
            n_deluxe += (t[i] == T_DELUXE) as u32;
            n_arcane += (t[i] == T_ARCANE) as u32;
            n_util += (m & B_UTIL != 0) as u32;
            if col[i] != COLOR_NONE {
                let k = col[i] as usize;
                seen[k] = true;
                row_col[(self.row[i] - self.row_min) as usize * 4 + k] += 1;
                col_col[(self.colc[i] - self.col_min) as usize * 4 + k] += 1;
            }
        }
        let n_colors = seen.iter().filter(|&&b| b).count() as f64;

        let (mut add_all, mut void_rate, mut greed_core) = (0.0f64, 0.0f64, 0.0f64);
        let (mut jupiter, mut pluto) = ((0u32, 0.0f64), (0u32, 0.0f64));
        let mut empyreal: Option<(f64, usize)> = None;
        for &(code, v, k, _) in &cs.cores {
            match code {
                C_PREMIUM => add_all += v * n_deluxe as f64,
                C_ARCHIVE => add_all += (1.0 + v).powf(n_arcane as f64) - 1.0,
                C_VOID => void_rate += v,
                C_JUPITER => jupiter = (dominant(&gcount, true, B_STAT), v),
                C_PLUTO => pluto = (dominant(&gcount, false, 0), v),
                C_GREED => greed_core += v,
                C_EMPYREAL => empyreal = Some((v, k)),
                _ => {}
            }
        }
        let mut freq = [1.0f64; 4];
        let (mut mirror_tag, mut have_mirror, mut chain_value, mut have_chain, mut unique_value) =
            (0.0f64, false, 0.0f64, false, 0.0f64);
        for &(kind, v, _, _, pt) in &d.imps {
            match kind {
                I_FREQ => freq[pt as usize] *= v.max(1.0),
                I_MIRROR => { mirror_tag = v; have_mirror = true; }
                I_EMPTY => void_rate += v,
                I_CHAIN => { chain_value = v; have_chain = true; }
                I_UNIQUE => unique_value = v,
                _ => {}
            }
        }
        let mut chain_id = [0u32; 128];
        let mut chain_size = [0u32; 129];
        if have_chain {
            let mut nid = 0u32;
            let mut stack: Vec<usize> = Vec::with_capacity(n);
            for s in 0..n {
                if t[s] == T_DEAD || chain_id[s] != 0 {
                    continue;
                }
                nid += 1;
                let mut size = 0u32;
                stack.clear();
                stack.push(s);
                chain_id[s] = nid;
                while let Some(i) = stack.pop() {
                    size += 1;
                    for &j in &self.orth[i] {
                        if t[j] == T_DEAD || chain_id[j] != 0 {
                            continue;
                        }
                        if col[i] != COLOR_NONE && col[i] == col[j] {
                            chain_id[j] = nid;
                            stack.push(j);
                        }
                    }
                }
                chain_size[nid as usize] = size;
            }
        }
        let unique_groups = if unique_value > 0.0 { gcount.iter().filter(|&&c| c > 0).count() as f64 } else { 0.0 };
        let void_factor = 1.0 + void_rate * n_empty as f64;

        let mut nsrc = [0u32; 128];
        let mut target = [usize::MAX; 128];
        for i in 0..n {
            if (T_G_UP..=T_G_RIGHT).contains(&t[i]) {
                if let Some(j) = self.dir[i][(t[i] - T_G_UP) as usize] {
                    if t[j] <= T_TYPELESS {
                        target[i] = j;
                        nsrc[j] += 1;
                    }
                }
            }
        }
        let mut boost = [1.0f64; 128];
        for i in 0..n {
            let j = target[i];
            if j != usize::MAX {
                boost[j] += d.greed + if nsrc[j] >= 2 { greed_core } else { 0.0 };
            }
        }

        for i in 0..n {
            let ti = t[i];
            if ti > T_TYPELESS {
                continue;
            }
            let m = xg[i];
            if m & NONSTAT != 0 {
                continue;
            }
            let base = if ti <= T_DIAG {
                let raw = match ti {
                    T_ROW => row_col[(self.row[i] - self.row_min) as usize * 4 + col[i] as usize] as f64,
                    T_COL => col_col[(self.colc[i] - self.col_min) as usize * 4 + col[i] as usize] as f64,
                    _ => {
                        let peers = if ti == T_DIAG { &self.diag[i] } else { &self.surr[i] };
                        peers.iter().filter(|&&q| t[q] != T_DEAD && col[q] == col[i]).count() as f64
                    }
                };
                let scaled = raw * freq[ti as usize];
                if ti == T_DIAG { scaled.max(1.0) } else { scaled }
            } else if ti == T_DELUXE {
                d.deluxe_base
            } else {
                1.0
            };
            let count = |peers: &[usize], bit: u32| peers.iter().filter(|&&q| t[q] != T_DEAD && xg[q] & bit != 0).count();
            let mut add = add_all;
            let mut mult = void_factor;
            for &(code, v, _, ccol) in &cs.cores {
                match code {
                    C_PURE => if m & (B_STAT | B_EVO) != 0 { add += v * n_nonfoil as f64; },
                    C_EQUILIBRIUM => if m & B_STAT != 0 { add += v * n_colors; },
                    C_STEADFAST => if m & B_STAT != 0 { add += v; },
                    C_SHINY => if m & B_FOIL != 0 { add += v; },
                    C_COLOR => if ccol == COLOR_NONE || col[i] == ccol { add += v; },
                    C_NATURAL => if m & B_PHYS != 0 { add += v; },
                    C_FAE => if m & B_MAG != 0 { add += v; },
                    C_BAZAAR => if count(&self.surr[i], B_RES) > 0 { add += v; },
                    C_TALENT => add += v * count(&self.surr[i], B_KNACK) as f64,
                    C_TEMPORAL => if count(&self.starcross[i], B_TEMP) > 0 { add += v; },
                    C_ARSENAL => if m & B_OFF != 0 { mult *= 1.0 + v; },
                    C_AEGIS => if m & B_DEF != 0 { mult *= 1.0 + v * count(&self.orth[i], B_DEF) as f64; },
                    C_TOOL => if m & B_UTIL != 0 { mult *= 1.0 + v * n_util.saturating_sub(1) as f64; },
                    _ => {}
                }
            }
            if jupiter.0 != 0 && m & jupiter.0 != 0 {
                add += jupiter.1;
            }
            if pluto.0 != 0 && m & pluto.0 != 0 {
                mult *= 1.0 + pluto.1;
            }
            if cs.nslots[i] {
                add += cs.nitwit;
            }
            for &(kind, v, gm, cm, pt) in &d.imps {
                match kind {
                    I_GLOBAL => {
                        if m & gm == gm && (cm == 0 || (col[i] != COLOR_NONE && cm >> col[i] & 1 == 1)) {
                            add += v;
                        }
                    }
                    I_ADJ => {
                        let peers = if pt == 1 { &self.surr[i] } else { &self.colp[i] };
                        let k = peers.iter().filter(|&&q| t[q] != T_DEAD && !(T_G_UP..=T_G_RIGHT).contains(&t[q])
                                                          && xg[q] & gm != 0).count();
                        add += v * k as f64;
                    }
                    I_MISMATCH => {
                        let k = self.orth[i].iter().filter(|&&q| t[q] != T_DEAD && col[q] != COLOR_NONE && col[q] != col[i])
                            .count();
                        add += v * k as f64;
                    }
                    I_ROWPOS => add += v * self.rows_from_bottom[i],
                    I_CHAIN => {
                        if chain_id[i] != 0 {
                            add += chain_value * (chain_size[chain_id[i] as usize] - 1) as f64;
                        }
                    }
                    I_UNIQUE => if m & B_STAT != 0 { add += unique_value * unique_groups; },
                    _ => {}
                }
            }
            if have_mirror {
                let k = if self.mirror_self[i] {
                    Some(m.count_ones() + 1)
                } else if let Some(mi) = self.mirror_of[i] {
                    if t[mi] != T_DEAD && !(T_G_UP..=T_G_RIGHT).contains(&t[mi]) && col[mi] != COLOR_NONE && col[mi] == col[i] {
                        Some((m & xg[mi]).count_ones() + 1)
                    } else { None }
                } else { None };
                if let Some(k) = k {
                    mult *= 1.0 + mirror_tag * k as f64;
                }
            }
            terms[i] = base * (1.0 + add) * mult * boost[i];
        }
        if let Some((v, k)) = empyreal {
            let mut idx: Vec<usize> = (0..n).filter(|&i| terms[i] > 0.0 && xg[i] & (B_STAT | B_EVO) != 0).collect();
            idx.sort_by(|&a, &b| terms[b].total_cmp(&terms[a]).then(a.cmp(&b)));
            for &i in idx.iter().take(k) {
                terms[i] *= 1.0 + v;
            }
        }
    }
}

fn dominant(counts: &[u32; N_GROUPS], most: bool, exclude: u32) -> u32 {
    let mut best: Option<(u32, u32)> = None;
    for &bit in ALPHA.iter() {
        if bit & exclude != 0 {
            continue;
        }
        let n = counts[bit.trailing_zeros() as usize];
        if n == 0 {
            continue;
        }
        let better = match best {
            None => true,
            Some((_, bn)) => if most { n > bn } else { n < bn },
        };
        if better {
            best = Some((bit, n));
        }
    }
    best.map_or(0, |b| b.0)
}

#[cfg(test)]
mod fixture_tests {
    use super::*;
    use serde_json::Value;

    /// tools/parity_cases_deck.py fixtures: every slot's term must match the Python reference to 1e-9.
    #[test]
    fn deck_fixture() {
        let Ok(path) = std::env::var("WV_DECK_FIXTURE") else {
            eprintln!("deck_fixture: WV_DECK_FIXTURE not set; skipped");
            return;
        };
        let v: Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("fixture")).expect("json");
        let kind = |k: &str| match k { "dead" => K_DEAD, "greed" => K_GREED, "evo" => K_EVO, "typeless" => K_TYPELESS,
                                       "deluxe" => K_DELUXE, "arcane" => K_ARCANE, _ => 5 };
        let core = |n: &str| ["pure", "equilibrium", "steadfast", "shiny", "color", "premium", "void", "archive",
                              "empyreal", "arsenal", "aegis", "tool", "natural", "fae", "jupiter", "pluto", "bazaar",
                              "talent", "temporal", "greed"].iter().position(|&x| x == n).unwrap() as u8;
        let (mut worst, mut n) = (0.0f64, 0usize);
        for d in v["decks"].as_array().unwrap() {
            let slots: Vec<(i32, i32)> = d["slots"].as_array().unwrap().iter()
                .map(|p| (p[0].as_i64().unwrap() as i32, p[1].as_i64().unwrap() as i32)).collect();
            let opts: Vec<Vec<DCard>> = d["cards"].as_array().unwrap().iter().map(|c| vec![DCard {
                card: 0, kind: kind(c["kind"].as_str().unwrap()), a: 0, v: c["value"].as_f64().unwrap(),
                g: c["g"].as_u64().unwrap() as u32, gk: 0, key: 0, lv: 0.0,
            }]).collect();
            let cs = &d["cset"];
            let cores = cs["cores"].as_array().unwrap().iter().map(|c| (core(c[0].as_str().unwrap()), c[1].as_f64().unwrap(),
                c[2].as_u64().unwrap() as usize, c[3].as_u64().unwrap() as u8)).collect();
            let imps = d["imps"].as_array().unwrap().iter().map(|x| (x[0].as_u64().unwrap() as u8, x[1].as_f64().unwrap(),
                x[2].as_u64().unwrap() as u32, x[3].as_u64().unwrap() as u8, x[4].as_u64().unwrap() as u8)).collect();
            let nsl = slots.len();
            let doc = DesignDoc {
                slots,
                t: d["types"].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u8).collect(),
                color: d["colors"].as_array().unwrap().iter().map(|x| x.as_u64().unwrap() as u8).collect(),
                opts,
                csets: vec![CoreSet { cores, nitwit: cs["nitwit"].as_f64().unwrap(), shiny: cs["shiny"].as_bool().unwrap(),
                                      nslots: cs["nitwit_slots"].as_array().unwrap().iter().map(|x| x.as_bool().unwrap()).collect() }],
                imps, greed: d["greed"].as_f64().unwrap(), deluxe_base: d["deluxe_base"].as_f64().unwrap(),
                limits: (-1, -1, -1), seed: vec![0; nsl],
            };
            let des = Design::new(doc);
            let mut terms = Vec::new();
            des.terms(0, &vec![0u16; nsl], &mut terms);
            for (a, b) in terms.iter().zip(d["terms"].as_array().unwrap()) {
                let b = b.as_f64().unwrap();
                let err = (a - b).abs() / b.abs().max(1.0);
                worst = worst.max(err);
                n += 1;
                assert!(err < 1e-9, "deck term mismatch: kernel {} python {}", a, b);
            }
        }
        eprintln!("deck_fixture: {} slot terms, worst relative error {:.2e}", n, worst);
    }
}
