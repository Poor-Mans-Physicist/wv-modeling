//! Cycle solve on the boss grid (hyper.solve_damage / solve_survival), the score record, healing per second
//! (evaluate.healing), the attack stream, the binomial survival window and the HP-state heuristic.

use crate::build::*;
use crate::problem::*;
use crate::eval::{py_round, CfgM, Ev, FamOut, SurvOut, C_HI, C_LO, R};

#[derive(Clone, Copy, Default, serde::Serialize)]
pub struct Score {
    pub score: f64,
    pub cycle: f64,
    pub cycle_damage: f64,
    pub cycle_survival: f64,
    pub dps: f64,
    pub pack_dps: f64,
    pub ehp: f64,
    /// The part of the requirement penalty that the movement minimum adds (the anneal may relax it early).
    pub move_pen: f64,
    /// The family's main-damage share (search.prune_secondaries enforces main_damage_share_min on it).
    pub main_share: f64,
    /// Parity fields: requirements met, stable movement speed, healing per second, rework execute threshold.
    pub met: bool,
    pub move_speed: f64,
    pub heal: f64,
    pub theta: f64,
}

impl<'a> Ev<'a> {
    /// Healing per second after healing effectiveness (evaluate.healing).
    pub fn healing(&self, fb: &mut Fallbacks, fo: &FamOut) -> R<f64> {
        let p = self.p;
        let d = self.d;
        let hh = d.health;
        let leech = fo.leech_rate * hh * d.leech;
        let mut ls = 0.0;
        if let Some(t) = self.talent_tier(p.nt.life_steal) {
            ls = fo.lucky_rate * hh * t.mhp.ok_or("KeyError maxHealthPercentage")?;
        }
        let mut heal = 0.0;
        if let Some(&spec) = p.heal_specs.iter().rev().find(|&&s| self.b.abil(s) > 0) {
            let (tier, _) = self.effective_tier(spec, self.b.abil(spec));
            let c = self.cfg(fb, spec, tier);
            let amount = c.or(K::flatLifeHealed, 0.0);
            let cost = c.or(K::manaCost, 0.0);
            if amount > 0.0 {
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let spare = f64::max(0.0, fo.supply - fo.demand);
                let casts = if cost <= 0.0 { 1.0 / cd } else { f64::min(1.0 / cd, spare / cost) };
                heal = casts * amount;
            }
        }
        let mut regen = 0.0;
        if let Some(fx) = self.st.gfx {
            let p = self.p;
            let b = self.b;
            let mut lv = fx.regen_levels + p.prestige.regen;
            for t in b.trinkets.iter() {
                lv += p.trinkets[t].regen;
            }
            for it in b.items.iter() {
                if let Some(u) = it.unique {
                    lv += p.uniques[u as usize].regen;
                }
            }
            if lv > 0 {
                regen = crate::gods::regen_hps(lv);
            }
            if fx.luckiest != 0.0 {
                ls *= 2.0;
            }
        }
        let (he_lo, he_lm, u) = (d.healing_effectiveness, d.he_low_mana, fo.u_low_mana);
        let he = if he_lm != he_lo { (1.0 - u) * he_lo + u * he_lm } else { he_lo };
        let hl = if self.p.godtree.is_none() {
            he
        } else if he_lm != he_lo {
            (1.0 - u) * he_lo.min(1.0) + u * he_lm.min(1.0)
        } else {
            he.min(1.0)
        };
        let total = hl * (leech + ls) + he * (heal + regen);
        Ok(if fo.surv.totem_heal != 0.0 { total + he * fo.surv.totem_heal } else { total })
    }
}

/// Largest k with feas(k), assuming feas is true then false (binary search); -1 if none (hyper._last_true).
fn last_true<F: Fn(usize) -> bool>(n: usize, feas: F) -> isize {
    if !feas(0) {
        return -1;
    }
    if feas(n - 1) {
        return (n - 1) as isize;
    }
    let (mut lo, mut hi) = (0usize, n - 1);
    while hi - lo > 1 {
        let mid = (lo + hi) / 2;
        if feas(mid) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    lo as isize
}

/// Damage cycle on the boss grid (hyper.solve_damage): the boss must die within kill_time x M(c).
pub(crate) fn damage_cycle(p: &Problem, dps0: f64, slope: f64) -> f64 {
    if dps0 <= 0.0 {
        return C_LO;
    }
    let g = &p.grid;
    let t = p.knobs.kill_time_s;
    let s = 2.0 * slope;
    let ttk = |hp: f64| -> f64 {
        if s * hp / dps0 < 1e-6 {
            hp / dps0
        } else {
            (s * hp / dps0).ln_1p() / s
        }
    };
    let n = g.h.len();
    let k = last_true(n, |k| g.h[k] <= 0.0 || ttk(g.h[k]) <= t * g.m[k]);
    if k < 0 {
        return C_LO;
    }
    let k = k as usize;
    if k == n - 1 {
        return C_HI;
    }
    if g.h[k] <= 0.0 {
        return g.c0 + k as f64 * g.step;
    }
    let a = g.ln_tm[k] - ttk(g.h[k]).ln();
    let b = g.ln_tm[k + 1] - ttk(g.h[k + 1]).ln();
    if a > b { g.c0 + (k as f64 + a / (a - b)) * g.step } else { g.c0 + k as f64 * g.step }
}

/// Survival cycle on the boss grid (hyper.solve_survival): largest c with D(c) <= cap.
pub(crate) fn survival_cycle(p: &Problem, cap: f64, dmg_cap: bool) -> f64 {
    let g = &p.grid;
    let (dd, ln_dd) = if dmg_cap { (&g.ds, &g.ln_ds) } else { (&g.df, &g.ln_df) };
    let n = dd.len();
    let k = last_true(n, |k| dd[k] <= 0.0 || dd[k] <= cap);
    if k < 0 {
        return C_LO;
    }
    let k = k as usize;
    if k == n - 1 {
        return C_HI;
    }
    if dd[k] <= 0.0 {
        return g.c0 + k as f64 * g.step;
    }
    let a = cap.ln() - ln_dd[k];
    let b = cap.ln() - ln_dd[k + 1];
    if a > b { g.c0 + (k as f64 + a / (a - b)) * g.step } else { g.c0 + k as f64 * g.step }
}

/// binomial.QUANTILE_TOL
const QUANTILE_TOL: f64 = 1e-9;

/// binomial.quantile: smallest k with P(K <= k) >= q, K ~ Binomial(n, p), from the same ratio recurrence.
pub(crate) fn binom_quantile(n: usize, p: f64, q: f64) -> usize {
    if p <= 0.0 {
        return 0;
    }
    let mut c = 0.0;
    if p >= 1.0 {
        for k in 0..=n {
            c += if k == n { 1.0 } else { 0.0 };
            if c >= q - QUANTILE_TOL {
                return k;
            }
        }
        return n;
    }
    let qq = 1.0 - p;
    let mut t = 1.0;
    for _ in 0..n {
        t *= qq;
    }
    let r = p / qq;
    c += t;
    if c >= q - QUANTILE_TOL {
        return 0;
    }
    for k in 0..n {
        t = t * (n - k) as f64 / (k + 1) as f64 * r;
        c += t;
        if c >= q - QUANTILE_TOL {
            return k + 1;
        }
    }
    n
}

/// hyper.at: grid value at cycle c, linear between grid points.
pub(crate) fn grid_at(g: &BossGrid, arr: &[f64], c: f64) -> f64 {
    let x = (c.max(C_LO).min(C_HI) - g.c0) / g.step;
    let k = (x as usize).min(arr.len() - 2);
    let t = x - k as f64;
    (1.0 - t) * arr[k] + t * arr[k + 1]
}

/// evaluate.speed_curve
pub(crate) fn speed_curve(s: f64, k: &Knobs, y_lo: f64, y_hi: f64) -> f64 {
    let t = f64::min(1.0, f64::max(0.0, (s - k.speed_lo) / (k.speed_hi - k.speed_lo)));
    y_lo + (y_hi - y_lo) * t
}

/// evaluate.hp_state: (u_high, u_low) from the landed-hit sawtooth.
pub(crate) fn hp_state(d: &Derived, k: &Knobs, hit: f64, heal: f64) -> (f64, f64) {
    let hh = d.health;
    let lam = d.p_land / k.hit_interval_s;
    if lam <= 0.0 || hh <= 0.0 {
        return (1.0, 0.0);
    }
    let f = hit / hh;
    let r = heal / hh;
    let u_high = if f <= 1.0 - 0.8 {
        1.0
    } else if r <= 0.0 {
        0.0
    } else {
        1.0 - f64::min(1.0, lam * f64::min((f - (1.0 - 0.8)) / r, 1.0 / lam))
    };
    let u_low = f64::min(1.0, lam * f64::max(0.0, f - (1.0 - 0.2)) / r.max(1e-12));
    (u_high, u_low)
}

/// The survival test's result (evaluate.survival).
pub(crate) struct Surv {
    pub cycle: f64,
    /// Per-landed-hit multiplier of the window before Last Stand, and the grid-hit scale m_crit / crit_mult.
    pub m_hit: f64,
    pub crit_scale: f64,
}

impl<'a> Ev<'a> {
    /// abilities.fang_hits: fang hits per cast for a fang spec at its effective radius (0 for other specs).
    pub(crate) fn fang_hits(&self, fb: &mut Fallbacks, spec: usize, c: &CfgM) -> R<f64> {
        let kind = self.p.spec_fang[spec];
        let t = &self.p.fang_table;
        let v = match kind {
            FANG_WALL => &t.wall,
            FANG_MAW => &t.maw,
            _ => return Ok(0.0),
        };
        let x = c.req(K::radius)? * self.d.aoe_multiplier / t.step;
        let n = v.len();
        let h = if x >= (n - 1) as f64 {
            if x > (n - 1) as f64 + 1e-9 {
                fb.fang_radius_clamp += 1;
            }
            v[n - 1]
        } else {
            let i = x as usize;
            v[i] + (v[i + 1] - v[i]) * (x - i as f64)
        };
        Ok(if kind == FANG_MAW { h * self.p.knobs.maw_pull_factor } else { h })
    }

    /// evaluate.source_reach
    fn source_reach(&self, fb: &mut Fallbacks, spec: usize) -> f64 {
        let (rule, key) = self.p.spec_reach[spec];
        match rule {
            REACH_PROJECTILE => f64::INFINITY,
            REACH_ZERO => 0.0,
            REACH_SELF_RADIUS | REACH_FANG_LINE => {
                let (tier, _) = self.effective_tier(spec, self.b.abil(spec).max(1));
                let c = self.cfg(fb, spec, tier);
                let r = c.c.v[key].unwrap_or(0.0) * self.d.aoe_multiplier;
                if rule == REACH_FANG_LINE { r * 1.5 } else { r }
            }
            _ => {
                fb.reach_unknown += 1;
                0.0
            }
        }
    }

    /// evaluate.is_ranged
    pub(crate) fn is_ranged(&self, fb: &mut Fallbacks) -> bool {
        match self.p.fam.kind {
            FamKind::Melee { .. } => false,
            FamKind::Rang => true,
            FamKind::Ability { main } => {
                if self.b.weave {
                    return false;
                }
                let mut reach = self.source_reach(fb, main);
                for &s in &self.p.sec_specs {
                    if s != main && self.b.abil(s) > 0 {
                        reach = reach.min(self.source_reach(fb, s));
                    }
                }
                reach >= self.p.knobs.ranged_reach - 1e-9
            }
        }
    }
}

/// evaluate.attack_stream: avoidance, ranged bonus, Immortal Seal and guards into d.
pub(crate) fn attack_stream(b: &Build, p: &Problem, st: &Stats, d: &mut Derived, ranged: bool) {
    let k = &p.knobs;
    let mut p_avoid = speed_curve(d.move_speed, k, k.avoid_lo, k.avoid_hi);
    if d.sneak_share > 0.0 {
        p_avoid *= 1.0 - (1.0 - k.sneak_avoid_factor) * d.sneak_share;
    }
    let p_ranged = if ranged { k.ranged_bonus } else { 1.0 };
    if d.sneaky_dodge > 0.0 {
        d.dodge = f64::min((st.g(p, A::dodge_percent) + d.sneaky_dodge) * crate::procs::luck_mult(st), 0.95);
    }
    let reach = (1.0 - p_avoid) * p_ranged;
    let mut p_seal = 1.0;
    let (mut miss, mut ticks) = (1.0, 0.0);
    for t in b.trinkets.iter() {
        if let Some((ch, dur)) = p.trinkets[t].seal {
            if ch > 0.0 {
                miss *= 1.0 - ch;
                ticks = f64::max(ticks, dur);
            }
        }
    }
    if miss < 1.0 {
        let ch = 1.0 - miss;
        let n_imm = ticks / 20.0 / k.hit_interval_s;
        let a = reach * (1.0 - d.block);
        p_seal = (1.0 / ch - 1.0) / (1.0 / ch + n_imm * a);
    }
    let p_land = reach * p_seal * (1.0 - d.block) * (1.0 - d.dodge);
    d.ranged = ranged;
    d.p_avoid = p_avoid;
    d.p_seal = p_seal;
    d.p_land = p_land;
    d.boss_hits_landed = p_land / k.hit_interval_s;
    d.boss_hits_reaching = reach / k.hit_interval_s;
}

/// evaluate.window_cap: the largest per-hit damage n landed hits can each be, with the Mana Shield pool and the
/// Ultimate Shield's absorption (uptime u, share pct, budget B): min(x / (n (1 - u pct)), (x + u B) / n).
fn window_cap(x: f64, n: f64, sv: &SurvOut) -> f64 {
    if sv.msp == 0.0 && sv.us_u == 0.0 {
        return x / n;
    }
    let x = x + sv.msp;
    let ua = sv.us_u * sv.us_pct;
    let hi = (x + sv.us_u * sv.us_budget) / n;
    if ua >= 1.0 { hi } else { f64::min(x / (n * (1.0 - ua)), hi) }
}

/// evaluate.survival: the binomial window and the one-shot test (resistance after the Colossus bonus).
thread_local! {
    /// Set by the delta verifier's debug block (WVK_DEBUG) to print the survival internals.
    pub static SURV_DEBUG: std::cell::Cell<bool> = std::cell::Cell::new(false);
}

pub(crate) fn survival(ev: &Ev, fb: &mut Fallbacks, fo: &FamOut, heal: f64, resistance: f64) -> R<Surv> {
    let p = ev.p;
    let d = ev.d;
    let k = &p.knobs;
    let hh = d.health;
    let n = py_round(k.survive_hits) as usize;
    let hd = heal * k.hit_interval_s;
    let cr = k.hyper_boss_crit_mult;
    let m_crit = 1.0 + (cr - 1.0) * f64::max(0.0, 1.0 - d.crit_mitigation);
    let crit = cr / m_crit;
    let k_q = binom_quantile(n, d.p_land, 1.0 - k.survival_risk);
    let armor_mult = if d.armor <= 0.0 { 1.0 } else { 1600.0 / (1600.0 + d.armor * d.armor) };
    let m0 = armor_mult * (1.0 - resistance) * (1.0 - d.castle);
    let (mut m_det, mut m_win, mut pool) = (m0, m0, hh);
    if let Some(fx) = ev.st.gfx {
        let (a1, an) = crate::gods::adaptive_factors(fx.adapt_per, fx.adapt_max, k_q.max(1) as f64);
        m_det *= a1 * fx.shield * fo.fury_inc * if fx.fleeting != 0.0 { fx.fleeting } else { 1.0 };
        m_win *= an * fx.shield * fo.fury_inc * if fx.fleeting != 0.0 { fx.fleeting * 2.0 / 3.0 } else { 1.0 };
        if fx.revive {
            pool = 2.0 * hh;
        }
    }
    let revive = ev.st.gfx.map_or(false, |fx| fx.revive);
    let m_hit = m_win;
    let ls = match ev.talent_tier(p.nt.last_stand) {
        Some(t) => 1.0 - t.dr.ok_or("KeyError damageReduction")? * d.u_low,
        None => 1.0,
    };
    m_win *= ls;
    if m_win <= 1e-9 {
        fb.survival_zero_mult += 1;
        m_win = 1e-9;
    }
    let mut cap1 = hh / m_det.max(1e-9) * crit;
    if revive {
        let n_f = py_round(k.kill_time_s / k.hit_interval_s) as usize;
        if binom_quantile(n_f, d.p_land, 1.0 - k.survival_risk) <= 1 {
            cap1 = f64::INFINITY;
        }
    }
    let mut capw = cap1;
    if revive {
        let m_rev = k_q / 2;
        if m_rev > 0 {
            let mr = m_rev as f64;
            capw = cap1.min(f64::max(hd, window_cap(hh + (mr - 1.0) * hd, mr, &fo.surv)) / m_win * crit);
        }
    } else if k_q > 0 {
        let kq = k_q as f64;
        capw = cap1.min(f64::max(hd, window_cap(pool + (kq - 1.0) * hd, kq, &fo.surv)) / m_win * crit);
    }
    let dmg_cap = ev.b.bug(Bug::BossDmgCap);
    let cw = survival_cycle(p, capw, dmg_cap);
    let c1 = survival_cycle(p, cap1, dmg_cap);
    let protected = k.oneshot_protection && hd >= k.oneshot_heal_fraction * hh - 1e-9;
    let cycle = if protected { cw.max(c1 + k.oneshot_extra_cycles) } else { cw };
    if SURV_DEBUG.with(|f| f.get()) {
        eprintln!("[wvk]   surv: k_q {} p_land {:e} heal {:e} hd {:e} cap1 {:e} capw {:e} m_win {:e} m_det {:e} ls {} u_low {:e} u_low_mana {:e} main_share {} msp {} us_u {} castle {:e} move {:e} block {:e} dodge {:e} health {:e} cycle {}",
                  k_q, d.p_land, heal, hd, cap1, capw, m_win, m_det, ls, d.u_low, fo.u_low_mana, fo.main_share, fo.surv.msp, fo.surv.us_u, d.castle, d.move_speed, d.block, d.dodge, hh, cycle);
    }
    Ok(Surv { cycle, m_hit, crit_scale: 1.0 / crit })
}
