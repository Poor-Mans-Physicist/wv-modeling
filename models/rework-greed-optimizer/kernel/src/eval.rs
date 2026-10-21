//! Evaluation core: the per-build evaluator `Ev` with its tier/cooldown helpers, the four-lane `V4`, and
//! evaluate() with the Arcane Cascade fixed point. The hurt chain is in hit.rs, buffs in buffs.rs, families in
//! families.rs, the rotation in rotation.rs, per-ability formulas in model.rs and the cycle solve in survival.rs.
//! Every `req` mirrors a Python `cfg["key"]` (KeyError -> evaluation error); `or` mirrors `cfg.get(key, default)`.

use crate::build::*;
use crate::problem::*;
use crate::survival::{attack_stream, damage_cycle, grid_at, hp_state, speed_curve, survival, Score};

pub(crate) type R<T> = Result<T, String>;

/// Four evaluation lanes in one pass: target HP 0 and 1e9 (for the missing-HP slope), each with and without
/// execution (the +5-cycle execution cap needs both). Lane arithmetic matches separate Python calls exactly.
#[derive(Clone, Copy, Debug)]
pub struct V4(pub [f64; 4]);
pub const LANE_HP: [f64; 4] = [0.0, 1e9, 0.0, 1e9];
pub const LANE_EXEC: [bool; 4] = [true, true, false, false];

impl std::ops::Mul<f64> for V4 {
    type Output = V4;
    #[inline]
    fn mul(self, m: f64) -> V4 {
        V4([self.0[0] * m, self.0[1] * m, self.0[2] * m, self.0[3] * m])
    }
}
impl std::ops::Add for V4 {
    type Output = V4;
    #[inline]
    fn add(self, o: V4) -> V4 {
        V4([self.0[0] + o.0[0], self.0[1] + o.0[1], self.0[2] + o.0[2], self.0[3] + o.0[3]])
    }
}
impl std::ops::MulAssign<f64> for V4 {
    #[inline]
    fn mul_assign(&mut self, m: f64) {
        for x in self.0.iter_mut() {
            *x *= m;
        }
    }
}

pub(crate) const BOSS_EXEC_MULT: f64 = 0.25;
/// abilities.LUCKY_RESET_SHARE
const LUCKY_RESET_SHARE: f64 = 0.25;
/// abilities.LUCKY_RESET_REMAINING (rework: each proc removes 10% of the remaining cooldown, once per cast).
const LUCKY_RESET_REMAINING: f64 = 0.10;
pub(crate) const PLAIN_MAX_HITS_PER_S: f64 = 2.0;
pub const C_LO: f64 = -40.0;
pub const C_HI: f64 = 80.0;

/// A tier config with the build's per-ability cooldown and mana-cost multipliers applied (families.spec_cfg).
#[derive(Clone, Copy)]
pub struct CfgM<'a> {
    pub c: &'a Cfg,
    pub cd: f64,
    pub mana: f64,
    /// The Lucky Reset etching's ability (abilities.cd_s `_reset`).
    pub reset: bool,
}

impl<'a> CfgM<'a> {
    #[inline]
    fn m(&self, k: K) -> f64 {
        match k {
            K::cooldownTicks => self.cd,
            K::manaCost | K::manaCostPerSecond => self.mana,
            _ => 1.0,
        }
    }
    #[inline]
    pub fn get(&self, k: K) -> Option<f64> {
        self.c.get(k).map(|x| x * self.m(k))
    }
    #[inline]
    pub fn req(&self, k: K) -> Result<f64, String> {
        self.c.req(k).map(|x| x * self.m(k))
    }
    #[inline]
    pub fn or(&self, k: K, d: f64) -> f64 {
        match self.c.get(k) {
            Some(x) => x * self.m(k),
            None => d,
        }
    }
    #[inline]
    pub fn has(&self, k: K) -> bool {
        self.c.has(k)
    }
}

pub use crate::buffs::SurvOut;

/// One family's output in the four lanes plus the lane-independent rates healing needs.
pub struct FamOut {
    pub fury_inc: f64,
    pub dps: V4,
    pub pack: V4,
    pub resist_bonus: f64,
    pub leech_rate: f64,
    pub lucky_rate: f64,
    /// Lucky hits per second on the boss, per hit (Arcane Cascade's rate), and without the Lucky Reset ability's own.
    pub lucky_hits: f64,
    pub lucky_ext: f64,
    pub supply: f64,
    pub demand: f64,
    /// Rotation: the main ability's share of the build's damage (lane 0; 1 without secondaries or scored swings).
    pub main_share: f64,
    /// Share of the fight below 20% mana (Depleted, Methodical).
    pub u_low_mana: f64,
    /// Sum over scored sources of dps^2 / rate (lane 0): The Dice's effective hit count is dps^2 / hit_sq.
    pub hit_sq: f64,
    /// Buff values the survival model reads (buffs.survival_buffs).
    pub surv: crate::buffs::SurvOut,
}

pub struct Ev<'a> {
    pub p: &'a Problem,
    pub b: &'a Build,
    pub st: &'a Stats,
    pub d: &'a Derived,
}

#[inline]
pub(crate) fn dv(a: f64, b: f64) -> R<f64> {
    if b == 0.0 {
        Err("ZeroDivisionError".into())
    } else {
        Ok(a / b)
    }
}

pub fn py_round(x: f64) -> f64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 {
        2.0 * (x / 2.0).round()
    } else {
        r
    }
}

impl<'a> Ev<'a> {
    pub(crate) fn cfg(&self, fb: &mut Fallbacks, spec: usize, tier: usize) -> CfgM<'a> {
        let v = self.b.bug(Bug::IceboltT12Hole) as usize;
        let c = &self.p.specs[spec].cfg[v][tier - 1];
        if let Some(h) = c.hole {
            fb.holes_used[h] = true;
        }
        let (mut cd, mut mana) = (1.0, 1.0);
        for &(k, kind, a) in self.st.amods() {
            if self.p.amod_hit[k as usize][spec] {
                let m = f64::max(0.0, 1.0 + a);
                if kind == 0 {
                    cd *= m;
                } else {
                    mana *= m;
                }
            }
        }
        CfgM { c, cd, mana, reset: spec == self.d.reset_spec }
    }

    pub fn effective_tier(&self, spec: usize, learned: usize) -> (usize, f64) {
        let n = self.p.specs[spec].n_tiers() as f64;
        let mut bonus = 0.0;
        for &(k, ch) in self.st.ability_levels() {
            if self.p.lvl_hit[k as usize][spec] {
                bonus += ch;
            }
        }
        if self.st.masterful && bonus > 0.0 {
            bonus *= 2.0;
        }
        let t = (n.min(learned as f64 + bonus) as i64).max(1) as usize;
        (t, bonus)
    }

    pub(crate) fn talent_tier(&self, t: Option<usize>) -> Option<&'a TalentTier> {
        let tier = self.st.tier(t);
        if tier == 0 {
            None
        } else {
            Some(&self.p.talents[t.unwrap()].tiers[tier - 1])
        }
    }

    /// abilities.cooldown_s: cooldown reduction, skip chance and Arcane Cascade at the fixed-point rate d.cascade_l.
    pub(crate) fn cooldown_s(&self, ticks: f64) -> f64 {
        self.cooldown_r(ticks, false)
    }

    /// cooldown_s with the Lucky Reset term when `reset` (abilities.cooldown_s).
    pub(crate) fn cooldown_r(&self, ticks: f64, reset: bool) -> f64 {
        let d = self.d;
        let mut t = ticks * (1.0 - d.cooldown_reduction) * (1.0 - d.cooldown_skip.min(1.0)) / 20.0;
        if reset && self.p.godtree.is_some() {
            let full = ticks * (1.0 - d.cooldown_reduction);
            let mut a = 1.0;
            if d.cascade_l > 0.0 {
                a += d.cascade_l * (full * d.cascade_c + 1e-9).floor() / 20.0;
            }
            let r0 = full / 20.0 * (1.0 - LUCKY_RESET_REMAINING * d.reset_self);
            let kk = LUCKY_RESET_REMAINING * d.reset_l;
            let tt = if kk > 0.0 { (kk * r0 / a).ln_1p() / kk } else { r0 / a };
            return f64::max(0.05, (1.0 - d.cooldown_skip.min(1.0)) * tt);
        }
        if reset {
            let full = ticks * (1.0 - d.cooldown_reduction);
            let r = (full * LUCKY_RESET_SHARE + 1e-9).floor() / 20.0;
            let t = (1.0 - d.cooldown_skip.min(1.0)) * f64::max(0.0, full / 20.0 - d.reset_self * r);
            let mut den = 1.0 + d.reset_l * r;
            if d.cascade_l > 0.0 {
                den += d.cascade_l * (full * d.cascade_c + 1e-9).floor() / 20.0;
            }
            return f64::max(0.05, t / den);
        }
        if d.cascade_l > 0.0 {
            let full = ticks * (1.0 - d.cooldown_reduction);
            let den = 1.0 + d.cascade_l * (full * d.cascade_c + 1e-9).floor() / 20.0;
                t = t / den;
        }
        f64::max(0.05, t)
    }

    pub(crate) fn cd_s(&self, c: &CfgM) -> f64 {
        self.cooldown_r(c.or(K::cooldownTicks, 0.0), c.reset)
    }

    /// abilities.lucky_reset_spec: the main ability if the Lucky Reset pool names it, else the learned secondary in
    /// the pool with the highest full-rate lane-0 damage (never a non-main Implode).
    fn lucky_reset_spec(&self, fb: &mut Fallbacks) -> R<Option<usize>> {
        let p = self.p;
        let main = match p.fam.kind {
            FamKind::Ability { main } => main,
            _ => return Ok(None),
        };
        let pool = match self.st.etch_pool(p, Etq::LuckyReset) {
            Some(pool) => pool,
            None => return Ok(None),
        };
        if pool.contains(&main) {
            return Ok(Some(main));
        }
        let cand = |s: usize| s != main && self.b.abil(s) > 0 && pool.contains(&s) && Some(s) != p.implode_spec;
        let n = p.sec_specs.iter().filter(|&&s| cand(s)).count();
        if n <= 1 {
            return Ok(p.sec_specs.iter().copied().find(|&s| cand(s)));
        }
        let mut bf0 = None;
        let mut best: Option<(f64, usize)> = None;
        for &s in &p.sec_specs {
            if !cand(s) {
                continue;
            }
            if bf0.is_none() {
                bf0 = Some(self.buffs(fb)?.0);
            }
            let (tier, _) = self.effective_tier(s, self.b.abil(s).max(1));
            let c = self.cfg(fb, s, tier);
            let dps = self.model(fb, s, &c, bf0.as_ref().unwrap())?.0 .0[0];
            if best.map_or(true, |(x, _)| crate::procs::tie_key(dps) > crate::procs::tie_key(x)) {
                best = Some((dps, s));
            }
        }
        Ok(best.map(|x| x.1))
    }
}

pub fn evaluate(b: &Build, p: &Problem, fb: &mut Fallbacks) -> R<Score> {
    let lin = lin_full(b, p);
    evaluate_lin(b, p, &lin, fb)
}

/// evaluate.CASCADE_COMPUTES / CASCADE_TOL / CASCADE_ACCEPT / CASCADE_WIDTH
const CASCADE_COMPUTES: usize = 12;
const CASCADE_TOL: f64 = 1e-12;
const CASCADE_ACCEPT: f64 = 1e-5;
const CASCADE_WIDTH: f64 = 4e-6;

/// evaluate.cascade_grid: x > 0 rounded to 20 mantissa bits (a relative step of 2^-21 to 2^-20; round numbers sit
/// on grid points, not on the cell edges).
#[inline]
pub(crate) fn cascade_grid(x: f64) -> f64 {
    f64::from_bits((x.to_bits() + 0x8000_0000) & 0xFFFF_FFFF_0000_0000)
}

/// evaluate.cascade_solve: root of y(f(x)) - x from a known point (x0, g0) and a first trial x1, n computes spent.
/// Returns (x, f at x, converged) for the last x evaluated.
fn cascade_solve<T, F: FnMut(f64) -> R<T>, Y: Fn(&T) -> f64>(f: &mut F, y_of: Y, x0: f64, g0: f64, x1: f64, mut n: usize) -> R<(f64, T, bool)> {
    let mut lo = if g0 > 0.0 { Some((x0, g0)) } else { None };
    let mut hi = if g0 < 0.0 { Some((x0, g0)) } else { None };
    let (mut xp, mut gp) = (x0, g0);
    let mut x = cascade_grid(x1);
    let mut side = 0i8;
    loop {
        let fo = f(x)?;
        let y = y_of(&fo);
        n += 1;
        let g = y - x;
        if g.abs() <= CASCADE_TOL * x {
            return Ok((x, fo, true));
        }
        let ok = g.abs() <= CASCADE_ACCEPT * x;
        if n >= CASCADE_COMPUTES {
            return Ok((x, fo, ok));
        }
        if g > 0.0 {
            if side == 1 {
                if let Some(h) = hi.as_mut() {
                    h.1 /= 2.0;
                }
            }
            lo = Some((x, g));
            side = 1;
        } else {
            if side == -1 {
                if let Some(l) = lo.as_mut() {
                    l.1 /= 2.0;
                }
            }
            hi = Some((x, g));
            side = -1;
        }
        let nx = match (lo, hi) {
            (Some(l), Some(h)) => {
                if (h.0 - l.0).abs() <= CASCADE_WIDTH * x {
                    return Ok((x, fo, ok));
                }
                l.0 - l.1 * (h.0 - l.0) / (h.1 - l.1)
            }
            _ => {
                let mut nx = y;
                if g != gp {
                    let s = x - g * (x - xp) / (g - gp);
                    if s > 0.0 && (s > x) == (g > 0.0) {
                        nx = s;
                    }
                }
                nx
            }
        };
        let nx = cascade_grid(nx);
        if nx == x {
            return Ok((x, fo, ok));
        }
        xp = x;
        gp = g;
        x = nx;
    }
}

/// Arcane Cascade fixed point (evaluate.cascade_fixed_point) from f(0); returns the family output at the solution,
/// so evaluate_lin needs no further compute. An ability family runs a trial whose output is not kept (every trial
/// when `want` is false, f(0) when a lucky hit is possible) in the rotation's lucky-only mode. Melee and vaultarang families solve on their lucky-hit rate alone (it does
/// not read the damage; without Battle Cry Lucky Strike it does not read the rate either, so f(0) answers every trial)
/// and compute the family once at the solution, only when `want` asks for it.
fn cascade(p: &Problem, b: &Build, st: &Stats, d: &mut Derived, fb: &mut Fallbacks, c: f64, reset: bool, want: bool) -> R<Option<FamOut>> {
    d.cascade_c = c;
    if !reset && !matches!(p.fam.kind, FamKind::Ability { .. }) {
        d.cascade_l = 0.0;
        let (y0, constant) = Ev { p, b, st, d: &*d }.lucky_hits_only(fb)?;
        let (l, ok) = if y0 <= 0.0 {
            (0.0, true)
        } else if constant {
            let (x, _, ok) = cascade_solve(&mut |_: f64| -> R<f64> { Ok(y0) }, |y: &f64| *y, 0.0, y0, y0, 1)?;
            (x, ok)
        } else {
            let mut f = |x: f64| -> R<f64> {
                d.cascade_l = x;
                Ok(Ev { p, b, st, d: &*d }.lucky_hits_only(fb)?.0)
            };
            let (x, _, ok) = cascade_solve(&mut f, |y: &f64| *y, 0.0, y0, y0, 1)?;
            (x, ok)
        };
        d.cascade_l = l;
        if !ok {
            fb.cascade_unconverged += 1;
        }
        return if want { Ok(Some(Ev { p, b, st, d: &*d }.compute(fb)?)) } else { Ok(None) };
    }
    let lucky_only = !want;
    let lead = want && Ev { p, b, st, d: &*d }.may_land_lucky();
    let (l, fo, ok) = {
        let mut f = |x: f64, lo: bool| -> R<FamOut> {
            d.cascade_l = x;
            let ev = Ev { p, b, st, d: &*d };
            let fo = if lo { ev.compute_lucky(fb)? } else { ev.compute(fb)? };
            if reset {
                d.reset_l = fo.lucky_ext;
            }
            Ok(fo)
        };
        let fo = f(0.0, lucky_only || lead)?;
        let y0 = fo.lucky_hits;
        if y0 <= 0.0 {
            if lead { (0.0, f(0.0, false)?, true) } else { (0.0, fo, true) }
        } else {
            let mut g = |x: f64| f(x, lucky_only);
            cascade_solve(&mut g, |fo: &FamOut| fo.lucky_hits, 0.0, y0, y0, 1)?
        }
    };
    d.cascade_l = l;
    if !ok {
        fb.cascade_unconverged += 1;
    }
    Ok(if want { Some(fo) } else { None })
}

/// evaluate.dice_percentile: The Dice at its percentile over the fight's effective hit count, as a ratio to its mean.
fn dice_ratio(p: &Problem, st: &Stats, dps: f64, hit_sq: f64) -> Option<f64> {
    let (lo, hi) = st.dice?;
    let k = &p.knobs;
    let mean = 0.5 * (lo + hi);
    let cv = (hi - lo) / 12f64.sqrt() / mean;
    let n = (if hit_sq > 0.0 { dps * dps / hit_sq } else { 0.0 }) * k.kill_time_s * (1.0 - k.boss_shield_downtime);
    Some(f64::max(1.0 / mean, 1.0 - k.dice_z * cv / n.max(1.0).sqrt()))
}

/// evaluate.hp_state_talents: a learned talent whose effective tier reads the HP state.
fn hp_talent<'p>(p: &'p Problem, st: &Stats, tid: usize) -> Option<&'p TalentTier> {
    let t = st.talent_tier[tid] as usize;
    if !p.hp_talents[tid] || t == 0 {
        return None;
    }
    let tier = &p.talents[tid].tiers[t - 1];
    if matches!(tier.ttype, TT_HIGH_HEALTH | TT_LOW_HEALTH_RES | TT_LOW_HEALTH_DMG) { Some(tier) } else { None }
}

pub fn evaluate_lin(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks) -> R<Score> {
    let mut st = stats(b, p, lin, fb);
    let k = &p.knobs;
    let (mut has_high, mut has_low_dmg, mut has_low_res) = (false, false, false);
    if k.hp_state_heuristic {
        for tid in 0..p.talents.len() {
            if let Some(tier) = hp_talent(p, &st, tid) {
                match tier.ttype {
                    TT_HIGH_HEALTH => has_high = true,
                    TT_LOW_HEALTH_DMG => has_low_dmg = true,
                    _ => has_low_res = true,
                }
            }
        }
    }
    let second = has_high || has_low_dmg || has_low_res;
    let mut hp: Option<(f64, f64)> = None;
    for npass in 0..2 {
        let mut d = derive(&st, p, b.bug(Bug::ManaCap));
        if let Some((uh, ul)) = hp {
            d.u_high = uh;
            d.u_low = ul;
        } else if !k.hp_state_heuristic {
            d.u_low = k.uptime_low_hp;
        }
        d.castle_max = d.castle;
        d.castle = d.castle_max * speed_curve(d.move_speed, k, k.castle_uptime_lo, k.castle_uptime_hi);
        if st.safer_space {
            let n = (10.0 * (1.0 - d.block) / k.hit_interval_s - 1e-9).ceil();
            d.block = 1.0 / (1.0 + n.max(0.0));
        }
        let ranged = (Ev { p, b, st: &st, d: &d }).is_ranged(fb);
        attack_stream(b, p, &st, &mut d, ranged);
        let cdd = (Ev { p, b, st: &st, d: &d }).talent_tier(p.nt.arcane_strike).and_then(|t| t.cdd);
        let reset = (Ev { p, b, st: &st, d: &d }).lucky_reset_spec(fb)?;
        let cdd = match (cdd, reset) {
            (Some(c), _) if c > 0.0 => Some(c),
            (_, Some(_)) => Some(0.0),
            _ => None,
        };
        let mut fo_c: Option<FamOut> = None;
        let pre_etch = match st.etch(p, Etq::ColossusTitan) {
            Some((n, true)) => n.map_or(true, |v| v != 0.0),
            _ => false,
        } || st.etch(p, Etq::ApTotem).map_or(false, |e| e.1);
        if let Some(c) = cdd {
            d.reset_spec = reset.unwrap_or(usize::MAX);
            if let Some(r) = reset {
                let rs = if p.lucky_spec[r] && p.spec_fang[r] != 0 {
                    let ev0 = Ev { p, b, st: &st, d: &d };
                    let (tier, _) = ev0.effective_tier(r, b.abil(r).max(1));
                    let c = ev0.cfg(fb, r, tier);
                    let n = ev0.fang_hits(fb, r, &c)?;
                    if p.godtree.is_some() { 1.0 - (1.0 - d.lucky_hit_chance).powf(n) } else { d.lucky_hit_chance * n }
                } else {
                    0.0
                };
                d.reset_self = rs;
                d.reset_l = 0.0;
            }
            fo_c = cascade(p, b, &st, &mut d, fb, c, reset.is_some(), !pre_etch && reset.is_none())?;
        }
        let titan = st.etch_num(p, Etq::ColossusTitan)?.filter(|&v| v != 0.0);
        let pre = if titan.is_some() || st.etch(p, Etq::ApTotem).map_or(false, |e| e.1) {
            Some(Ev { p, b, st: &st, d: &d }.buffs(fb)?.0)
        } else {
            None
        };
        if let Some(bf0) = &pre {
            d.ability_power *= bf0.ap_totem;
        }
        let ev = Ev { p, b, st: &st, d: &d };
        let fo = match fo_c {
            Some(fo) if pre.is_none() && reset.is_none() => fo,
            _ => ev.compute(fb)?,
        };
        let (mut dps, mut pack, resist_bonus) = (fo.dps, fo.pack, fo.resist_bonus);
        if let Some(ratio) = dice_ratio(p, &st, fo.dps.0[0], fo.hit_sq) {
            dps = dps * ratio;
            pack = pack * ratio;
        }
        let up = 1.0 - k.boss_shield_downtime;
        let dps0 = dps.0[0] * up;
        let theta = ev.execute_threshold(&fo);
        let c_full = damage_cycle(p, dps0 / (1.0 - theta), (dps.0[1] - dps.0[0]) * up / 1e9);
        let mut c_dmg = c_full;
        if p.godtree.is_none() && (d.execution > 0.0 || st.tier(p.nt.execution_strike) > 0) {
            let c_ne = damage_cycle(p, dps.0[2] * up, (dps.0[3] - dps.0[2]) * up / 1e9);
            c_dmg = c_full.min(c_ne + k.execution_cycle_cap);
        }
        if fo.surv.empower_speed != 0.0 || fo.surv.sneaky_dodge != 0.0 || fo.surv.sneak_share != 0.0 {
            d.empower_speed = fo.surv.empower_speed;
            d.sneaky_dodge = fo.surv.sneaky_dodge;
            d.sneak_share = fo.surv.sneak_share;
            d.move_speed = stable_move_speed(&st, p, &d);
            d.castle = d.castle_max * speed_curve(d.move_speed, k, k.castle_uptime_lo, k.castle_uptime_hi);
            attack_stream(b, p, &st, &mut d, ranged);
        }
        let ev = Ev { p, b, st: &st, d: &d };
        let u_col = pre.as_ref().map_or(0.0, |x| x.colossus_up);
        let resistance = match titan {
            Some(v) if u_col > 0.0 => {
                let res = st.g(p, A::resistance) + resist_bonus;
                u_col * res.min(d.resistance_cap + v).min(0.95) + (1.0 - u_col) * res.min(d.resistance_cap)
            }
            _ => if resist_bonus != 0.0 { (d.resistance + resist_bonus).min(d.resistance_cap) } else { d.resistance },
        };
        let armor_mult = if d.armor <= 0.0 { 1.0 } else { 1600.0 / (1600.0 + d.armor * d.armor) };
        let m_det = armor_mult * (1.0 - resistance) * (1.0 - d.castle);
        let mut mult = (1.0 - d.block) * (1.0 - d.dodge) * m_det;
        if mult <= 1e-9 {
            fb.ehp_zero += 1;
            mult = 1e-9;
        }
        let e = d.health / mult;
        let heal = ev.healing(fb, &fo)?;
        let sv = survival(&ev, fb, &fo, heal, resistance)?;
        let mut c_surv = sv.cycle;
        let mut c_eff = c_dmg.min(c_surv);
        if npass == 0 && second {
            let g = &p.grid;
            let arr = if b.bug(Bug::BossDmgCap) { &g.ds } else { &g.df };
            let (uh, ul) = hp_state(&d, k, grid_at(g, arr, c_eff) * sv.crit_scale * sv.m_hit, heal);
            hp = Some((uh, ul));
            if (has_high && uh != 1.0) || (has_low_dmg && ul != 0.0) {
                let mut live = ul != 0.0 && (has_low_dmg || st.tier(p.nt.berserking) > 0);
                if uh != 1.0 {
                    for tid in 0..p.talents.len() {
                        if let Some(tier) = hp_talent(p, &st, tid) {
                            if tier.ttype == TT_HIGH_HEALTH && !attr_inert(&st, p, tier.a as usize) {
                                live = true;
                            }
                        }
                    }
                }
                if live {
                    if uh != 1.0 {
                        for tid in 0..p.talents.len() {
                            if let Some(tier) = hp_talent(p, &st, tid) {
                                if tier.ttype == TT_HIGH_HEALTH {
                                    st.attr[tier.a as usize] += tier.v * (uh - 1.0);
                                }
                            }
                        }
                    }
                    continue;
                }
                d.u_high = uh;
                d.u_low = ul;
                if ul != 0.0 && st.tier(p.nt.last_stand) > 0 {
                    c_surv = survival(&Ev { p, b, st: &st, d: &d }, fb, &fo, heal, resistance)?.cycle;
                    c_eff = c_dmg.min(c_surv);
                }
            } else if has_low_res && ul != 0.0 {
                d.u_low = ul;
                c_surv = survival(&Ev { p, b, st: &st, d: &d }, fb, &fo, heal, resistance)?.cycle;
                c_eff = c_dmg.min(c_surv);
            }
        }
        let mut gap_move = 0.0;
        if p.godtree.is_some() {
            gap_move = f64::max(0.0, k.min_move_speed - d.move_speed) / k.min_move_speed;
        }
        let gap_regen = f64::max(0.0, k.min_mana_regen - d.mana_regen) / k.min_mana_regen;
        let gap_cdr = f64::max(0.0, k.min_cooldown_reduction - d.cooldown_reduction) / k.min_cooldown_reduction;
        let ms = k.main_damage_share_min;
        let gap_main = if ms > 0.0 { f64::max(0.0, ms - fo.main_share) / ms } else { 0.0 };
        let gap_neg = if k.effect_negation_required && !negation_met(b, p) { 1.0 } else { 0.0 };
        let pen = |gm: f64| (if gap_regen <= 1e-9 && gap_cdr <= 1e-9 && gm <= 1e-9 && gap_neg <= 1e-9 { 0.0 } else { 8.0 + 40.0 * (gap_regen + gap_cdr + gm + gap_neg) }) + 40.0 * gap_main;
        let penalty = pen(gap_move);
        let hard = gap_regen <= 1e-9 && gap_cdr <= 1e-9 && gap_move <= 1e-9 && gap_neg <= 1e-9;
        return Ok(Score {
            score: c_eff + 0.002 * c_dmg.max(c_surv) - penalty, cycle: c_eff, cycle_damage: c_dmg, cycle_survival: c_surv,
            dps: dps0, pack_dps: pack.0[0], ehp: e, move_pen: penalty - pen(0.0), main_share: fo.main_share,
            met: hard && gap_main <= 1e-9, move_speed: d.move_speed, heal, theta,
        });
    }
    unreachable!("evaluate_lin ends in its second pass")
}


/// build.negation_met: the Carapace trinket or the Blessed etching on any piece.
pub fn negation_met(b: &Build, p: &Problem) -> bool {
    b.trinkets.iter().any(|t| p.trinkets[t].negation) || b.etch(p, Etq::Divinity).is_some()
}

/// score_lin() with `relax` of the movement-minimum penalty given back (the anneal's early phase).
pub fn score_lin_relaxed(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks, relax: f64) -> f64 {
    if relax <= 0.0 {
        return score_lin(b, p, lin, fb);
    }
    match evaluate_lin(b, p, lin, fb) {
        Ok(s) => s.score + relax * s.move_pen,
        Err(e) => {
            fb.eval_error += 1;
            if fb.first_error.is_none() {
                fb.first_error = Some(e);
            }
            -1e9
        }
    }
}

/// _score(): evaluation errors score -1e9 and are counted.
pub fn score_lin(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks) -> f64 {
    match evaluate_lin(b, p, lin, fb) {
        Ok(s) => s.score,
        Err(e) => {
            fb.eval_error += 1;
            if fb.first_error.is_none() {
                fb.first_error = Some(e);
            }
            -1e9
        }
    }
}
