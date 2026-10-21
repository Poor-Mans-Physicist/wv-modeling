//! model/procs.py: events, triggers and debuffs on the hyperboss. Same kinds, order and arithmetic as the Python
//! reference (kernel_parity.py checks them).

use crate::build::Stats;
use crate::problem::Problem;

pub const VULNERABLE: u8 = 0;
pub const HYPOTHERMIA: u8 = 1;
pub const SLOWNESS: u8 = 2;
pub const POISON: u8 = 3;
pub const WITHER: u8 = 4;
pub const WEAKNESS: u8 = 5;
pub const UNLUCK: u8 = 6;
pub const FEAR: u8 = 7;
pub const BURN: u8 = 8;
pub const CURSE: u8 = 9;
pub const MARK: u8 = 10;
pub const CHILLED: u8 = 11;
pub const N_KINDS: usize = 12;
pub const MAX_SRC: usize = 40;

/// HexEffects.HEX_EFFECTS (nine equal weights); 255 = never lands on a hyperboss (Bleed; Chilled in 0.34.1).
const HEX: [(u8, f64); 9] = [(SLOWNESS, 80.0), (POISON, 100.0), (WITHER, 80.0), (WEAKNESS, 80.0), (UNLUCK, 100.0), (255, 100.0),
                             (255, 80.0), (VULNERABLE, 80.0), (FEAR, 60.0)];
/// procs.HEX_EFFECTS_REWORK: Chilled (level 1) lands on the rework hyperboss.
const HEX_REWORK: [(u8, f64); 9] = [(SLOWNESS, 80.0), (POISON, 100.0), (WITHER, 80.0), (WEAKNESS, 80.0), (UNLUCK, 100.0), (255, 100.0),
                                    (CHILLED, 80.0), (VULNERABLE, 80.0), (FEAR, 60.0)];

#[derive(Clone, Copy, Default)]
pub struct Src {
    pub kind: u8,
    pub amp: i64,
    pub up: f64,
    /// Frozen Orb under the proposed rule: added once to the Vulnerable already up.
    pub add: bool,
}

#[derive(Clone, Copy)]
pub struct Srcs {
    s: crate::build::Stack<Src, MAX_SRC>,
}

impl Srcs {
    #[inline]
    pub fn new() -> Srcs {
        Srcs { s: crate::build::Stack::new() }
    }
    pub fn push(&mut self, kind: u8, amp: i64, up: f64, add: bool) {
        assert!(self.s.len() < MAX_SRC, "too many debuff sources; raise MAX_SRC in kernel/src/procs.rs");
        self.s.push(Src { kind, amp, up, add });
    }
    #[inline]
    pub fn as_slice(&self) -> &[Src] {
        &self.s
    }
    #[inline]
    pub fn as_mut_slice(&mut self) -> &mut [Src] {
        &mut self.s
    }
}

pub fn periodic_up(duration: f64, period: f64) -> f64 {
    if period > 0.0 { f64::min(1.0, duration / period) } else { 0.0 }
}

pub fn proc_up(rate: f64, duration: f64, base: f64) -> f64 {
    if rate <= 0.0 || duration <= 0.0 {
        return base;
    }
    1.0 - (1.0 - base) * (-rate * duration).exp()
}

pub fn luck_mult(st: &Stats) -> f64 {
    if st.luck > 0 { luck_pow(st.luck) } else { 1.0 }
}

/// 1.15^luck for a Luck level, from a table built with the same powf calls.
pub fn luck_pow(luck: i32) -> f64 {
    static TABLE: std::sync::OnceLock<[f64; 65]> = std::sync::OnceLock::new();
    if (0..65).contains(&luck) {
        let t = TABLE.get_or_init(|| {
            let mut t = [0.0; 65];
            for (n, x) in t.iter_mut().enumerate() {
                *x = 1.15f64.powf(n as f64);
            }
            t
        });
        return t[luck as usize];
    }
    1.15f64.powf(luck as f64)
}

/// procs.gear_sources: hexing, burning hits and effect clouds on normal hits.
pub fn gear_sources(p: &Problem, st: &Stats, hexing: f64, burning: f64, cloud_chance: f64, normal: f64, out: &mut Srcs) {
    let lm = luck_mult(st);
    if hexing > 0.0 && normal > 0.0 {
        let hex = if p.godtree.is_some() { &HEX_REWORK } else { &HEX };
        let rate = f64::min(1.0, hexing * lm) * normal / hex.len() as f64;
        for &(kind, ticks) in hex {
            if kind != 255 {
                out.push(kind, 0, proc_up(rate, ticks / 20.0, 0.0), false);
            }
        }
    }
    if burning > 0.0 && normal > 0.0 {
        out.push(BURN, 0, proc_up(f64::min(1.0, burning * lm) * normal, 10.0, 0.0), false);
    }
    for i in 0..st.n_clouds {
        let c = &p.clouds[st.clouds[i] as usize];
        let rate = cloud_trigger(c.chance, cloud_chance) * normal;
        for &(kind, ticks) in &c.effects {
            out.push(kind as u8, if kind as u8 == CHILLED { c.chill } else { 0 }, proc_up(rate, (c.ticks + ticks) / 20.0, 0.0), false);
        }
    }
}

/// buffs.cloud_trigger: min(1, min(1, chance) + effect_cloud_chance_additive), no Luck.
pub fn cloud_trigger(chance: f64, additive: f64) -> f64 {
    f64::min(1.0, f64::min(1.0, chance) + additive)
}

/// procs.combine: (Vulnerable multiplier, expected count of harmful effect kinds).
pub fn combine(src: &[Src], fixed: bool, additive: bool) -> (f64, f64) {
    let bonus = |a: i64| if fixed { 0.1 * (a + 1) as f64 } else { 0.1 * a as f64 };
    let mut vul = [(0i64, 0.0f64); MAX_SRC];
    let mut nv = 0;
    for s in src.iter().filter(|s| s.kind == VULNERABLE && !s.add) {
        let mut i = nv;
        while i > 0 && vul[i - 1].0 < s.amp {
            vul[i] = vul[i - 1];
            i -= 1;
        }
        vul[i] = (s.amp, s.up);
        nv += 1;
    }
    let mut e_max = 0.0;
    let mut miss = 1.0;
    let mut states = [(0i64, 0.0f64); MAX_SRC];
    for (i, &(amp, up)) in vul[..nv].iter().enumerate() {
        states[i] = (amp, up * miss);
        e_max += bonus(amp) * up * miss;
        miss *= 1.0 - up;
    }
    let mut e = e_max;
    if additive {
        if let Some(a0) = src.iter().find(|s| s.kind == VULNERABLE && s.add) {
            let (lv, uo) = (a0.amp, a0.up);
            let mut with_add = 0.0;
            for &(a, w) in &states[..nv] {
                with_add += bonus(a + lv) * w;
            }
            with_add += miss * bonus(lv - 1);
            e = uo * with_add + (1.0 - uo) * e_max;
        }
    }
    let mut up = [0.0f64; N_KINDS];
    for s in src {
        let k = s.kind as usize;
        up[k] = 1.0 - (1.0 - up[k]) * (1.0 - s.up);
    }
    let mut count = 0.0;
    for k in 0..N_KINDS {
        count += up[k];
    }
    (1.0 + e, count)
}

/// procs.tie_key: a non-negative value rounded to 30 mantissa bits for tie-stable sorting.
pub fn tie_key(x: f64) -> i64 {
    if x == f64::INFINITY {
        return 1i64 << 62;
    }
    (x.to_bits() as i64 + (1i64 << 21)) >> 22
}

/// One eligible hit stream for the target's Echoing state (procs.echo): landing hits per second, the damage its echoes
/// store, one pulse's value in the four lanes (zero for streams that are not scored) and its pack targets.
#[derive(Clone, Copy)]
pub struct EchoSrc {
    pub rate: f64,
    pub s: f64,
    pub r: crate::eval::V4,
    pub targets: f64,
    /// From the family's main ability (counts toward main_damage_share_min).
    pub main: bool,
}

/// procs.echo_value: bilinear in (log10 a, q) on the simulated table, linear in a below it, flat above it.
pub fn echo_value(t: &crate::problem::EchoTable, a: f64, q: f64, n: usize, fb: &mut crate::build::Fallbacks, reverb: bool) -> f64 {
    if a <= 0.0 || n == 0 {
        return 0.0;
    }
    let n = if n > t.n_max {
        fb.echo_n_clamp += 1;
        t.n_max
    } else {
        n
    };
    let (na, nq) = (t.na, t.nq);
    let base = (n - 1) * na * nq;
    let mut x = (a.log10() + 2.0) * 8.0;
    let mut scale = 1.0;
    if x < 0.0 {
        scale = a / 0.01;
        if reverb {
            scale = scale * scale;
        }
        x = 0.0;
    }
    let x = x.min(na as f64 - 1.0);
    let i = (x as usize).min(na - 2);
    let fx = x - i as f64;
    let y = q.max(0.0).min(1.0) * (nq - 1) as f64;
    let j = (y as usize).min(nq - 2);
    let fy = y - j as f64;
    let v = |i: usize, j: usize| t.v[base + i * nq + j];
    let lo = v(i, j) * (1.0 - fy) + v(i, j + 1) * fy;
    let hi = v(i + 1, j) * (1.0 - fy) + v(i + 1, j + 1) * fy;
    scale * (lo * (1.0 - fx) + hi * fx)
}

/// procs.echo: echo DPS, pack DPS, the main ability's lane-0 part and the sum of pulses/s x pulse damage^2 (lane 0)
/// from every eligible stream; reverb reads the Reverberating Echo table.
pub fn echo(p: &Problem, st: &Stats, chance: f64, effect_duration: f64, src: &[EchoSrc], fb: &mut crate::build::Fallbacks, reverb: bool) -> (crate::eval::V4, crate::eval::V4, f64, f64) {
    let zero = crate::eval::V4([0.0; 4]);
    if chance <= 0.0 || src.is_empty() {
        return (zero, zero, 0.0, 0.0);
    }
    let lm = luck_mult(st);
    let pr = (chance * lm).min(1.0);
    let q = (chance.sqrt() * lm).min(1.0);
    let n = (1.0 + effect_duration + 1e-9).floor();
    let n = if n > 0.0 { n as usize } else { 0 };
    let mut order = [0usize; 72];
    let k = src.len();
    assert!(k <= 72, "too many echo streams");
    for j in 0..k {
        order[j] = j;
    }
    order[..k].sort_by(|&x, &y| tie_key(src[y].s).cmp(&tie_key(src[x].s)).then(x.cmp(&y)));
    let (mut a, mut prev) = (0.0, 0.0);
    let mut dps = [0.0; 4];
    let mut pack = [0.0; 4];
    let mut main = 0.0;
    let mut sq = 0.0;
    for &j in &order[..k] {
        let aj = src[j].rate * pr;
        if aj <= 0.0 {
            continue;
        }
        a += aj;
        let v = if reverb && p.echo_reverb.v.is_empty() {
            fb.echo_reverb_missing += 1;
            echo_value(&p.echo, a, q, n, fb, false)
        } else if reverb {
            echo_value(&p.echo_reverb, a, q, n, fb, true)
        } else {
            echo_value(&p.echo, a, q, n, fb, false)
        };
        let w = v - prev;
        prev = v;
        sq += w * src[j].r.0[0] * src[j].r.0[0];
        for l in 0..4 {
            dps[l] += w * src[j].r.0[l];
            pack[l] += w * src[j].r.0[l] * src[j].targets;
        }
        if src[j].main {
            main += w * src[j].r.0[0];
        }
    }
    (crate::eval::V4(dps), crate::eval::V4(pack), main, sq)
}
