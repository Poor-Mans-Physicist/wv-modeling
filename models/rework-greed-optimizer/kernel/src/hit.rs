//! The hurt chain for one hit (damage._hit / evaluate_hit): hit classes, the damage registries, lucky terms,
//! the i-frame weaving clip and the rework execute threshold.

use crate::problem::*;
use crate::buffs::{Buffs, HK_EMPTY};
use crate::eval::{Ev, FamOut, BOSS_EXEC_MULT, LANE_EXEC, LANE_HP, R, V4};

#[derive(Clone, Copy, Default)]
pub struct Hc {
    pub ap: bool,
    pub aoe: bool,
    pub normal: bool,
    pub lucky: bool,
    pub di_baked: bool,
    pub echo_ok: bool,
    pub rampage: bool,
    /// Double hit applies (normal hits and thorns reflects).
    pub double_ok: bool,
    /// Vanilla i-frames can clip it (plain hurts and Javelin; damage.CLIPPED_IFRAMES).
    pub clip: bool,
    /// Poison Nova / Toxic Grenade tick (the dot class): skipped by the global damage registry, Sneaky Advantage and
    /// Hexbreaker.
    pub dot: bool,
    /// A direct melee swing (Weaponmaster / Pincushion multiplier).
    pub direct: bool,
}

impl<'a> Ev<'a> {
    /// Flat thorns after Shell Porcupine (damage.thorns_flat).
    pub(crate) fn thorns_flat(&self, bf: &Buffs) -> f64 {
        self.d.thorns_flat * (1.0 + bf.porcupine.get())
    }

    /// Damage reflected per boss hit (damage.thorns_reflect).
    pub(crate) fn thorns_reflect(&self, bf: &Buffs) -> f64 {
        self.d.attack_damage * 2.0 * self.d.thorns_pct + self.thorns_flat(bf)
    }

    /// registry() cached on the Buffs at the current u_low_mana (its only input that changes within one compute).
    pub(crate) fn registry_c(&self, bf: &Buffs) -> R<f64> {
        let key = bf.u_low_mana.to_bits();
        let (k, v) = bf.hk_reg.get();
        if k == key && key != HK_EMPTY {
            return Ok(v);
        }
        let v = self.registry(bf)?;
        bf.hk_reg.set((key, v));
        Ok(v)
    }

    /// talent_registry() cached like registry_c.
    fn talent_registry_c(&self, bf: &Buffs) -> R<f64> {
        let key = bf.u_low_mana.to_bits();
        let (k, v) = bf.hk_treg.get();
        if k == key && key != HK_EMPTY {
            return Ok(v);
        }
        let v = self.talent_registry(bf)?;
        bf.hk_treg.set((key, v));
        Ok(v)
    }

    /// The Executioner factor of hit_core's conditional multiplier (None without the talent), cached on the Buffs.
    fn executioner_c(&self, bf: &Buffs) -> R<Option<f64>> {
        if let Some(x) = bf.hk_exec.get() {
            return Ok(Some(x));
        }
        match self.talent_tier(self.p.nt.executioner) {
            Some(t) => {
                let th = t.th;
                let inc = t.di.ok_or("KeyError damageIncrease")?;
                let x = 1.0 / ((1.0 - th) + th / (1.0 + inc)) - 1.0;
                bf.hk_exec.set(Some(1.0 + x));
                Ok(Some(1.0 + x))
            }
            None => Ok(None),
        }
    }

    /// lucky_terms() without the lucky chance, cached on the Buffs.
    fn lucky_terms_c(&self, bf: &Buffs, lucky: f64) -> R<(f64, f64, f64, f64)> {
        if let Some((mult, ex, fang)) = bf.hk_lucky.get() {
            return Ok((lucky, mult, ex, fang));
        }
        let (pp, mult, ex, fang) = self.lucky_terms(lucky)?;
        bf.hk_lucky.set(Some((mult, ex, fang)));
        Ok((pp, mult, ex, fang))
    }

    pub(crate) fn registry(&self, bf: &Buffs) -> R<f64> {
        let d = self.d;
        let mut add = 0.0;
        if let Some(v) = bf.totem_pd {
            if v != 0.0 {
                add += v;
            }
        }
        if self.st.berserk_power {
            add += self.berserk_add();
        }
        if d.relentless > 0.0 {
            add += d.relentless * 10.0;
        }
        if d.third_attack > 0.0 {
            add += d.third_attack / 3.0;
        }
        if let Some(t) = self.talent_tier(self.p.nt.berserking) {
            add += t.di.ok_or("KeyError damageIncrease")? * self.d.u_low;
        }
        if let Some(t) = self.talent_tier(self.p.nt.depleted) {
            add += t.di.ok_or("KeyError damageIncrease")? * bf.u_low_mana;
        }
        Ok(1.0 + add)
    }

    /// damage.berserk_add: 20 stacks while kills_per_s x 20 s >= 1, else 1 stack at that uptime.
    fn berserk_add(&self) -> f64 {
        let n = self.p.knobs.kills_per_s * 400.0 / 20.0;
        if n >= 1.0 - 1e-9 { 1.0 } else { 0.05 * n }
    }

    /// damage.talent_registry_add: Berserking and Depleted, which AP/AoE hits take too in the rework.
    fn talent_registry(&self, bf: &Buffs) -> R<f64> {
        let mut add = 0.0;
        if let Some(t) = self.talent_tier(self.p.nt.berserking) {
            add += t.di.ok_or("KeyError damageIncrease")? * self.d.u_low;
        }
        if let Some(t) = self.talent_tier(self.p.nt.depleted) {
            add += t.di.ok_or("KeyError damageIncrease")? * bf.u_low_mana;
        }
        Ok(add)
    }

    /// Greed rework execute threshold (model/evaluate.py execute_threshold): the gear stat on normal melee hits, gear
    /// plus the Executing Strike tier on lucky hits, halved on the hyperboss.
    pub(crate) fn execute_threshold(&self, fo: &FamOut) -> f64 {
        if self.p.godtree.is_none() {
            return 0.0;
        }
        let gear = self.d.execution;
        let melee = self.p.is_melee() || self.b.weave;
        let mut best = if melee { gear } else { 0.0 };
        if fo.lucky_rate > 0.0 {
            if let Some(tier) = self.talent_tier(self.p.nt.execution_strike) {
                if tier.ttype == 6 {
                    best = best.max(gear + tier.di.unwrap_or(0.0));
                }
            }
        }
        let mut theta = 0.5 * best;
        if melee && self.d.reaving > 0.0 {
            theta += self.d.reaving * 0.25;
        }
        theta.min(0.95)
    }

    fn lucky_terms(&self, lucky: f64) -> R<(f64, f64, f64, f64)> {
        let nt = &self.p.nt;
        let mut mult = 1.0;
        let mut has_any = false;
        for t in [nt.fatal_strike, nt.execution_strike] {
            if let Some(tier) = self.talent_tier(t) {
                has_any = true;
                if tier.ttype == 5 {
                    mult *= 1.0 + tier.di.ok_or("KeyError damageIncrease")?;
                }
            }
        }
        let mut exec_frac = 0.0;
        if let Some(tier) = self.talent_tier(nt.execution_strike).filter(|_| self.p.godtree.is_none()) {
            if tier.ttype == 6 {
                exec_frac = tier.di.ok_or("KeyError damageIncrease")?;
            }
        }
        for t in [nt.fanged_strike, nt.arcane_strike, nt.cleave, nt.mana_steal, nt.life_steal] {
            if self.st.tier(t) > 0 {
                has_any = true;
            }
        }
        if !has_any {
            mult = 1.5;
        }
        let fang = match self.talent_tier(nt.fanged_strike) {
            Some(t) => t.di.ok_or("KeyError damageIncrease")?,
            None => 0.0,
        };
        Ok((lucky, mult, exec_frac, fang))
    }

    /// Expected final damage of one hit (evaluate_hit) in all four lanes, with the melee-weaving i-frame clip.
    pub fn hit(&self, flat: f64, baked: f64, hc: Hc, lucky: f64, bf: &Buffs) -> R<V4> {
        if bf.raw_probe.get().is_nan() {
            bf.raw_probe.set(flat + baked);
        }
        if bf.dry.get() {
            if bf.clip_only.get() && hc.clip && bf.weave_f > 0.0 {
                let (_, pdrop) = Self::weave_clip(bf, flat + baked);
                bf.weave_drop.set(f64::max(bf.weave_drop.get(), pdrop));
            }
            return Ok(V4([0.0; 4]));
        }
        if !hc.clip || bf.weave_f <= 0.0 {
            return self.hit_core(flat, baked, hc, lucky, bf);
        }
        let f = bf.weave_f;
        let armed = bf.echo_rec.get();
        let full = self.hit_core(flat, baked, hc, lucky, bf)?;
        let (r, pdrop) = Self::weave_clip(bf, flat + baked);
        if armed && !bf.echo_rec.get() {
            let surv = 1.0 - pdrop;
            bf.echo_cu.set(1.0 - f);
            bf.echo_cf.set(f * surv);
            bf.echo_cr.set(if surv > 1e-12 { r / surv } else { 0.0 });
        }
        bf.weave_drop.set(f64::max(bf.weave_drop.get(), pdrop));
        let cl = if r > 0.0 { self.hit_core(flat * r, baked * r, hc, lucky, bf)? } else { V4([0.0; 4]) };
        let mut out = [0.0; 4];
        for l in 0..4 {
            out[l] = (1.0 - f) * full.0[l] + f * cl.0[l];
        }
        Ok(V4(out))
    }

    /// The i-frame clip of a hit with raw `base` against the other streams: (kept raw fraction weighted over the
    /// streams, share of window landings dropped).
    #[inline]
    fn weave_clip(bf: &Buffs, base: f64) -> (f64, f64) {
        let src: &[(f64, f64)] = &bf.weave_src;
        let mut lam = 0.0;
        for &(rate, _) in src {
            lam += rate;
        }
        let (mut r, mut pdrop) = (0.0, 0.0);
        for &(rate, raw) in src {
            let mut rj = if base > 0.0 { f64::max(0.0, 1.0 - raw / base) } else { 0.0 };
            if rj <= 1e-9 {
                rj = 0.0;
                pdrop += rate / lam;
            }
            r += rate / lam * rj;
        }
        (r, pdrop)
    }

    /// One hit before the weaving clip (damage._hit). `lucky` is the hit's lucky chance.
    fn hit_core(&self, flat: f64, baked: f64, hc: Hc, lucky: f64, bf: &Buffs) -> R<V4> {
        let b = self.b;
        let d = self.d;
        let di = d.damage_increase;
        let e_mult = if hc.ap { 1.0 } else { 1.0 + di };
        let m_mult = self.registry_c(bf)? * d.god_stack;
        let m_applies = !(hc.ap || hc.aoe);
        let rework = self.p.godtree.is_some();
        let t_mult = if m_applies || !rework { 1.0 } else { 1.0 + self.talent_registry_c(bf)? };
        let di2 = b.bug(Bug::IceboltDi2);
        let e_and_m = |xf: f64, xb: f64| -> f64 {
            let mut v = if hc.di_baked && !di2 { xf * e_mult + xb } else { (xf + xb) * e_mult };
            if m_applies {
                v *= m_mult;
            } else if t_mult != 1.0 {
                v *= t_mult;
            }
            v
        };
        let base = flat + baked;
        let mut cond = 1.0;
        if let Some(f) = self.executioner_c(bf)? {
            cond *= f;
        }
        if hc.ap && !hc.dot {
            if let Some(t) = self.talent_tier(self.p.nt.hexbreaker) {
                let x = t.di.ok_or("KeyError damageIncrease")? * bf.debuffed;
                cond *= 1.0 + x;
            }
        }
        if hc.rampage {
            if let Some(r) = bf.rampage {
                if r != 0.0 {
                    cond *= 1.0 + r;
                }
            }
        }
        let lt = if hc.lucky {
            let (pp, mut mult, ex, fang) = self.lucky_terms_c(bf, lucky)?;
            if d.god_lucky_twice {
                mult *= mult;
            }
            if d.god_overcrit != 0.0 {
                mult *= 1.0 + d.god_overcrit;
            }
            Some((pp, mult, ex, fang))
        } else {
            None
        };
        let mut gm = 1.0;
        if !hc.dot {
            gm *= d.god_global * bf.sneaky;
        }
        if d.god_true_rage != 0.0 && hc.aoe && !hc.ap {
            if let Some(r) = bf.rampage {
                if r != 0.0 {
                    gm *= 1.0 + d.god_true_rage * r;
                }
            }
        }
        if hc.direct {
            gm *= bf.direct.get();
        }
        let gear_bug = b.bug(Bug::ExecGear);
        let order_bug = b.bug(Bug::OrderFlatadd);
        let fang_bug = b.bug(Bug::FangResidual);
        let plain = e_and_m(flat, baked);
        let (x_add, ap_post) = if !hc.normal {
            (0.0, 0.0)
        } else if rework {
            (0.0, d.ap_flat * d.ap_scaling + self.thorns_flat(bf) * d.thorns_scaling)
        } else {
            (d.ap_flat * d.ap_scaling + self.thorns_flat(bf) * d.thorns_scaling, 0.0)
        };
        let mut out = [0.0; 4];
        for l in 0..self.p.lanes {
            let missing = 0.5 * LANE_HP[l];
            let exec_attr = if LANE_EXEC[l] && self.p.godtree.is_none() { d.execution } else { 0.0 };
            let mut v = if hc.normal && exec_attr > 0.0 {
                let add = missing * exec_attr;
                if gear_bug {
                    let pre = e_and_m((flat + x_add + add) * BOSS_EXEC_MULT, baked * BOSS_EXEC_MULT);
                    let post = plain * BOSS_EXEC_MULT + add * BOSS_EXEC_MULT + x_add;
                    if order_bug { 0.5 * (pre + post) } else { pre }
                } else {
                    e_and_m(flat + x_add + add * BOSS_EXEC_MULT, baked)
                }
            } else if x_add > 0.0 {
                let pre = e_and_m(flat + x_add, baked);
                if order_bug { 0.5 * (pre + (plain + x_add)) } else { pre }
            } else {
                plain
            };
            if ap_post != 0.0 {
                v += ap_post;
            }
            v *= cond;
            if let Some((pp, mult, exec_on, fang)) = lt {
                let exec_frac = if LANE_EXEC[l] { exec_on } else { 0.0 };
                let lucky_add = pp * exec_frac * missing * BOSS_EXEC_MULT;
                let mut fang_add = 0.0;
                if fang > 0.0 {
                    let mut fh = v * mult * fang;
                    if !fang_bug {
                        fh /= cond;
                    }
                    fang_add = pp * fh;
                }
                let factor = 1.0 + pp * (mult - 1.0);
                v = v * factor + lucky_add + fang_add;
            }
            if gm != 1.0 {
                v *= gm;
            }
            if bf.vulnerable > 1.0 {
                v *= bf.vulnerable;
            }
            if let Some((lo, hi)) = self.st.dice {
                v *= 0.5 * (lo + hi);
            }
            if (hc.normal || hc.double_ok) && d.double_hit > 0.0 {
                v *= 1.0 + d.double_hit * (2.0 - 1.0);
            }
            out[l] = v;
        }
        if self.p.lanes == 1 {
            out = [out[0]; 4];
        }
        if hc.echo_ok && d.echo_chance > 0.0 && bf.echo_rec.get() {
            bf.echo_rec.set(false);
            let mut stored = base * (1.0 + d.echo_damage) * 0.667;
            if hc.rampage && self.p.knobs.echo_inherits_rampage {
                if let Some(r) = bf.rampage {
                    if r != 0.0 {
                        stored *= 1.0 + r;
                    }
                }
            }
            let keep = !b.bug(Bug::EchoFlagloss);
            let hce = Hc { ap: hc.ap && keep, aoe: false, normal: if keep { hc.normal } else { true }, ..Hc::default() };
            let replay = self.hit(stored, 0.0, hce, lucky, bf)?;
            bf.echo_s.set(stored);
            bf.echo_r.set(replay);
        }
        Ok(V4(out))
    }
}
