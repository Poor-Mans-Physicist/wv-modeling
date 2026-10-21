//! Family dispatch and melee (model/families.py): Better Combat swings, counter attacks, the Starforge nova.

use crate::build::*;
use crate::problem::*;
use crate::buffs::Buffs;
use crate::hit::Hc;
use crate::eval::{Ev, FamOut, R, V4};

impl<'a> Ev<'a> {
    /// families.swing_rate: 0 with Grand Archmage.
    pub(crate) fn swing_rate(&self) -> f64 {
        if self.d.god_archmage { 0.0 } else { bc_swings_per_s(self.d.attack_speed).min(self.p.knobs.max_swings_per_s) }
    }

    /// families.arcane_nova_dps: Starforge's every-Nth-swing AP nova, minus the swing's raw (it lands inside its i-frames).
    pub(crate) fn arcane_nova(&self, bf: &Buffs, aps: f64, trigger_raw: f64) -> R<V4> {
        let st = self.st;
        if st.nova_hits <= 0.0 || st.nova_pct <= 0.0 || aps <= 0.0 {
            return Ok(V4([0.0; 4]));
        }
        let raw = f64::max(0.0, self.d.ability_power * st.nova_pct - trigger_raw);
        if raw <= 0.0 {
            return Ok(V4([0.0; 4]));
        }
        Ok(self.hit(raw, 0.0, Hc { ap: true, aoe: true, ..Hc::default() }, 0.0, bf)? * (aps / st.nova_hits))
    }

    /// families.counter_dps (four lanes).
    fn counter_dps(&self, bf: &Buffs) -> R<(V4, f64)> {
        let d = self.d;
        let fx = match self.st.gfx {
            Some(fx) if fx.counter != 0.0 && !d.god_archmage => fx,
            _ => return Ok((V4([0.0; 4]), 0.0)),
        };
        let pr = 1.0 - (1.0 - d.block) * (1.0 - d.dodge);
        let rate = fx.counter * pr * d.boss_hits_reaching;
        if rate <= 0.0 {
            return Ok((V4([0.0; 4]), 0.0));
        }
        let hc = Hc { normal: true, lucky: true, echo_ok: true, ..Hc::default() };
        Ok((self.hit(d.attack_damage, 0.0, hc, d.lucky_hit_chance, bf)? * rate, rate))
    }

    /// Family DPS in the four lanes plus the rates and mana figures healing needs.
    pub fn compute(&self, fb: &mut Fallbacks) -> R<FamOut> {
        match &self.p.fam.kind {
            FamKind::Melee { combo_avg, weapon } => self.melee(fb, *combo_avg, weapon),
            FamKind::Ability { main } => self.ability(fb, *main, false),
            FamKind::Rang => self.rang(fb),
        }
    }

    /// FamOut.lucky_hits of a melee or vaultarang family without its damage: the swing or throw rate times the
    /// swing's lucky chance (Arcane Cascade's trial function), and whether it is the same at every cascade rate (only
    /// Battle Cry Lucky Strike's stack rate reads a cooldown).
    pub(crate) fn lucky_hits_only(&self, fb: &mut Fallbacks) -> R<(f64, bool)> {
        let (bf, _) = self.buffs(fb)?;
        let d = self.d;
        let rate = match &self.p.fam.kind {
            FamKind::Melee { .. } => bc_swings_per_s(d.attack_speed).min(self.p.knobs.max_swings_per_s),
            FamKind::Rang => 1.0 / self.rang_period(),
            FamKind::Ability { .. } => unreachable!("lucky_hits_only is for melee and vaultarang families"),
        };
        let lucky = self.melee_lucky(&bf, rate);
        Ok((if d.god_archmage { 0.0 } else { rate } * lucky, bf.bc_lucky_per_hit == 0.0))
    }

    /// families.melee_lucky
    pub(crate) fn melee_lucky(&self, bf: &Buffs, aps: f64) -> f64 {
        let d = self.d;
        let mut lp = d.lucky_hit_chance;
        if bf.rampage_lucky != 0.0 || bf.bc_lucky_per_hit != 0.0 {
            let mut extra = bf.rampage_lucky;
            if bf.bc_lucky_per_hit != 0.0 {
                extra += bf.bc_lucky_per_hit * f64::min(1.0, bf.bc_lucky_hits_per_s / aps.max(1e-6));
            }
            lp = f64::min(1.0, d.lucky_hit_chance + extra);
        }
        lp
    }

    /// families.melee_swings: (per hit, swings/s, lucky chance, raw pre-event amount, pack targets).
    pub(crate) fn melee_swings(&self, bf: &Buffs, combo_avg: f64, weapon: &str) -> R<(V4, f64, f64, f64, f64)> {
        let p = self.p;
        let d = self.d;
        let ad = (d.attack_damage + self.spectral_ad(bf)) * bf.empower;
        let aps = bc_swings_per_s(d.attack_speed).min(p.knobs.max_swings_per_s);
        let raw = ad * combo_avg;
        let bc = bf.bc_ad * ad / aps.max(1e-6);
        let lucky = self.melee_lucky(bf, aps);
        let hc = Hc { normal: true, lucky: true, echo_ok: true, rampage: true, direct: true, ..Hc::default() };
        let mut per_hit = match self.st.gfx {
            Some(fx) => {
                let mut dm = 1.0;
                if weapon == "battlestaff" {
                    dm *= fx.weaponmaster;
                }
                if fx.pincushion != 0.0 {
                    dm *= 1.0 + fx.pincushion * aps * p.knobs.kill_time_s / 2.0;
                }
                let old = bf.direct.replace(dm);
                let v = self.hit(raw + bc, 0.0, hc, lucky, bf);
                bf.direct.set(old);
                v?
            }
            None => self.hit(raw + bc, 0.0, hc, lucky, bf)?,
        };
        let mut aps = aps;
        if d.god_archmage {
            per_hit = V4([0.0; 4]);
            aps = 0.0;
        }
        let mut targets = 1.0;
        if d.on_hit_aoe > 0.0 {
            targets += 0.6 * f64::min(p.knobs.pack_size - 1.0, d.on_hit_aoe * 1.5);
        }
        if d.chain > 0.0 {
            targets += chain_sum((d.chain + 1e-9) as i64);
        }
        if let Some(t) = self.talent_tier(p.nt.cleave) {
            targets += lucky * t.dp.ok_or("KeyError damagePercentage")? * 2.0;
        }
        if weapon == "battlestaff" {
            targets += 1.5;
        } else if weapon == "sword" || weapon == "axe" {
            targets += 0.8;
        }
        Ok((per_hit, aps, lucky, raw + bc, targets))
    }

    fn melee(&self, fb: &mut Fallbacks, combo_avg: f64, weapon: &str) -> R<FamOut> {
        let p = self.p;
        let d = self.d;
        let (mut bf, demand) = self.buffs(fb)?;
        let eth = self.ethereal();
        let demand = demand * (1.0 - eth);
        bf.rampage_cost *= 1.0 - eth;
        let aps0 = self.swing_rate();
        if let Some(sp) = bf.spectral {
            bf.spectral_s = Self::spectral_stacks(sp, aps0);
        }
        let lp = self.melee_lucky(&bf, aps0);
        let steal = aps0 * lp * d.mana_max * self.mana_steal_pct()?;
        let supply = self.mana_supply(&bf) + steal;
        let ramp_cost = bf.rampage_cost;
        let fury = self.fury_for(aps0);
        let base_free = Self::empower_toggle(&mut bf, f64::max(0.0, supply - demand), eth);
        let free = self.starved_free(base_free, if bf.rampage.is_some() { ramp_cost } else { 0.0 });
        let up = Self::rampage_uptime(&mut bf, free, fury);
        let drain = if bf.rampage.is_some() { up * ramp_cost } else { 0.0 };
        let left = f64::max(0.0, base_free - drain);
        let nova_rate = self.nova_pd_rate(aps0);
        bf.u_low_mana = self.low_mana_state(false, nova_rate, supply - demand - drain - bf.emp_paid);
        self.finalize(&mut bf, aps0, aps0 * lp)?;
        let zero = V4([0.0; 4]);
        let mut es = [crate::procs::EchoSrc { rate: 0.0, s: 0.0, r: zero, targets: 0.0, main: false }; 2];
        let mut ne = 0;
        Self::echo_arm(&bf);
        let (per_hit, aps, lucky, raw, targets) = self.melee_swings(&bf, combo_avg, weapon)?;
        if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
            es[ne] = crate::procs::EchoSrc { rate: aps, s, r, targets, main: false };
            ne += 1;
        }
        let mdps = per_hit * aps;
        Self::echo_arm(&bf);
        let (cd, crate_) = self.counter_dps(&bf)?;
        if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
            es[ne] = crate::procs::EchoSrc { rate: crate_, s, r, targets: 1.0, main: false };
            ne += 1;
        }
        let (ed, epack, _, esq) = crate::procs::echo(p, self.st, d.echo_chance, d.effect_duration, &es[..ne], fb, self.st.etch(p, Etq::Reverb).map_or(false, |e| e.1));
        let mut nova = self.arcane_nova(&bf, aps, raw)?;
        if nova_rate > 0.0 {
            bf.sneak_share = 1.0;
            let bar = f64::min(d.mana_max, left / (nova_rate + bf.inst_buff_rate));
            bf.pd_bar = bar;
            if nova.0[0] != 0.0 {
                let pm = self.pd_curve(self.snap_bar(bar));
                for l in 0..4 {
                    nova.0[l] *= pm;
                }
            }
        }
        let mut sq = if aps > 0.0 { mdps.0[0] * mdps.0[0] / aps } else { 0.0 };
        sq += if crate_ > 0.0 { cd.0[0] * cd.0[0] / crate_ } else { 0.0 };
        if nova.0[0] > 0.0 {
            sq += nova.0[0] * nova.0[0] / (aps / self.st.nova_hits);
        }
        let mut dps = mdps;
        let mut pack = mdps * targets;
        for l in 0..4 {
            dps.0[l] += cd.0[l] + ed.0[l] + nova.0[l];
            pack.0[l] += cd.0[l] + epack.0[l] + nova.0[l];
        }
        for l in 0..4 {
            dps.0[l] += bf.cloud_dps.0[l];
            pack.0[l] += bf.cloud_dps.0[l];
        }
        let total_demand = demand + if bf.rampage.is_some() { ramp_cost } else { 0.0 } + bf.emp_paid;
        Ok(FamOut {
            fury_inc: fury.map_or(1.0, |f| f.2),
            dps, pack, resist_bonus: bf.colossus_resist, leech_rate: aps, lucky_rate: aps * lucky, lucky_hits: aps * lucky, lucky_ext: aps * lucky, supply,
            demand: total_demand, main_share: 1.0, u_low_mana: bf.u_low_mana, hit_sq: sq + esq, surv: self.surv_out(&bf),
        })
    }

    /// families.rang_period: seconds per vaultarang throw (out and back over rang_travel_blocks, then the catch cooldown).
    fn rang_period(&self) -> f64 {
        let speed = 1.5 + self.st.g(self.p, A::velocity) * 0.325;
        let cd_ticks = (20.0 / self.d.attack_speed + 0.5 + 1e-9).floor();
        2.0 * self.p.knobs.rang_travel_blocks / (speed * 20.0) + cd_ticks / 20.0
    }

    /// families.RangedFamily.compute: vaultarang throws (outbound normal attack plus the clipped return hit).
    fn rang(&self, fb: &mut Fallbacks) -> R<FamOut> {
        let p = self.p;
        let d = self.d;
        let (mut bf, demand) = self.buffs(fb)?;
        let eth = self.ethereal();
        let demand = demand * (1.0 - eth);
        bf.rampage_cost *= 1.0 - eth;
        let rate0 = if d.god_archmage { 0.0 } else { 1.0 / self.rang_period() };
        if let Some(sp) = bf.spectral {
            bf.spectral_s = Self::spectral_stacks(sp, rate0);
        }
        let lp = self.melee_lucky(&bf, rate0);
        let steal = rate0 * lp * d.mana_max * self.mana_steal_pct()?;
        let supply = self.mana_supply(&bf) + steal;
        let ramp_cost = bf.rampage_cost;
        let fury = self.fury_for(rate0);
        let free = Self::empower_toggle(&mut bf, f64::max(0.0, supply - demand), eth);
        let free = self.starved_free(free, if bf.rampage.is_some() { ramp_cost } else { 0.0 });
        let up = Self::rampage_uptime(&mut bf, free, fury);
        let drain = if bf.rampage.is_some() { up * ramp_cost } else { 0.0 };
        bf.u_low_mana = self.low_mana_state(false, 0.0, supply - demand - drain - bf.emp_paid);
        self.finalize(&mut bf, rate0, rate0 * lp)?;
        let zero = V4([0.0; 4]);
        let mut es = [crate::procs::EchoSrc { rate: 0.0, s: 0.0, r: zero, targets: 0.0, main: false }; 2];
        let mut ne = 0;
        Self::echo_arm(&bf);
        let mut rate = 1.0 / self.rang_period();
        let ad = (d.attack_damage + self.spectral_ad(&bf)) * bf.empower;
        let raw = ad + bf.bc_ad * ad / rate.max(1e-6);
        let lucky = self.melee_lucky(&bf, rate);
        let hc = Hc { normal: true, lucky: true, echo_ok: true, rampage: true, clip: true, ..Hc::default() };
        let mut per = self.hit(raw, 0.0, hc, lucky, &bf)?;
        let ret_raw = f64::max(0.0, d.attack_damage * (1.0 + self.st.g(p, A::returning_damage)) - raw);
        let mut ret = if ret_raw > 0.0 { self.hit(ret_raw, 0.0, Hc::default(), d.lucky_hit_chance, &bf)? } else { zero };
        if d.god_archmage {
            per = zero;
            ret = zero;
            rate = 0.0;
        }
        let mut targets = 1.0;
        if d.on_hit_aoe > 0.0 {
            targets += 0.6 * f64::min(p.knobs.pack_size - 1.0, d.on_hit_aoe * 1.5);
        }
        if d.chain > 0.0 {
            targets += chain_sum((d.chain + 1e-9) as i64);
        }
        if let Some(t) = self.talent_tier(p.nt.cleave) {
            targets += lucky * t.dp.ok_or("KeyError damagePercentage")? * 2.0;
        }
        targets += f64::min(p.knobs.pack_size - 1.0, self.st.g(p, A::piercing)) + 0.5 * self.st.g(p, A::ricochet);
        if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
            es[ne] = crate::procs::EchoSrc { rate, s, r, targets, main: false };
            ne += 1;
        }
        Self::echo_arm(&bf);
        let (cd, crate_) = self.counter_dps(&bf)?;
        if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
            es[ne] = crate::procs::EchoSrc { rate: crate_, s, r, targets: 1.0, main: false };
            ne += 1;
        }
        let (ed, epack, _, esq) = crate::procs::echo(p, self.st, d.echo_chance, d.effect_duration, &es[..ne], fb, self.st.etch(p, Etq::Reverb).map_or(false, |e| e.1));
        let mut dps = zero;
        let mut pack = zero;
        for l in 0..4 {
            dps.0[l] = (per.0[l] + ret.0[l]) * rate + cd.0[l] + ed.0[l];
            pack.0[l] = per.0[l] * rate * targets + ret.0[l] * rate + cd.0[l] + epack.0[l];
        }
        let ret_hits = if ret_raw > 0.0 { rate } else { 0.0 };
        let out = per.0[0] * rate;
        let mut sq = if rate > 0.0 { out * out / rate } else { 0.0 };
        if ret_hits > 0.0 {
            let rv = ret.0[0] * rate;
            sq += rv * rv / rate;
        }
        if crate_ > 0.0 {
            sq += cd.0[0] * cd.0[0] / crate_;
        }
        Ok(FamOut {
            fury_inc: fury.map_or(1.0, |f| f.2),
            dps, pack, resist_bonus: bf.colossus_resist, leech_rate: rate + ret_hits, lucky_rate: rate * lucky, lucky_hits: rate * lucky, lucky_ext: rate * lucky,
            supply, demand: demand + if bf.rampage.is_some() { ramp_cost } else { 0.0 } + bf.emp_paid, main_share: 1.0,
            u_low_mana: bf.u_low_mana, hit_sq: sq + esq, surv: self.surv_out(&bf),
        })
    }

    /// compute() for an Arcane Cascade trial whose family output is not kept: an ability family fills only lucky_hits
    /// and lucky_ext (the rotation's lucky-only mode); other families compute in full.
    pub(crate) fn compute_lucky(&self, fb: &mut Fallbacks) -> R<FamOut> {
        match &self.p.fam.kind {
            FamKind::Ability { main } => self.ability(fb, *main, true),
            _ => self.compute(fb),
        }
    }

    /// False when an ability family's lucky_hits is surely 0: no woven swings and no learned cast that lands lucky
    /// hits at a nonzero lucky chance.
    pub(crate) fn may_land_lucky(&self) -> bool {
        let main = match &self.p.fam.kind {
            FamKind::Ability { main } => *main,
            _ => return true,
        };
        if self.b.weave {
            return true;
        }
        if self.d.lucky_hit_chance == 0.0 {
            return false;
        }
        let lands = |s: usize| self.leech_lucky_ok(s) == (true, true);
        lands(main) || self.p.sec_specs.iter().any(|&s| s != main && self.b.abil(s) > 0 && lands(s))
    }

    fn ability(&self, fb: &mut Fallbacks, main: usize, lucky_only: bool) -> R<FamOut> {
        let mut secs = [0usize; 16];
        let mut n_sec = 0;
        for &s in &self.p.sec_specs {
            if s != main && self.b.abil(s) > 0 {
                assert!(n_sec < 16, "too many secondary abilities");
                secs[n_sec] = s;
                n_sec += 1;
            }
        }
        self.rotation(fb, main, &secs[..n_sec], lucky_only)
    }
}

/// Chained hits' extra targets, the sum of 0.5^k for k = 1..=n added in order, from a table built with the same additions.
fn chain_sum(n: i64) -> f64 {
    static TABLE: std::sync::OnceLock<[f64; 65]> = std::sync::OnceLock::new();
    let t = TABLE.get_or_init(|| {
        let mut t = [0.0; 65];
        let mut s = 0.0;
        for k in 1..65 {
            s += 0.5f64.powf(k as f64);
            t[k] = s;
        }
        t
    });
    if (0..65).contains(&n) {
        return t[n as usize];
    }
    let mut s = 0.0;
    for k in 1..=n {
        s += 0.5f64.powf(k as f64);
    }
    s
}

pub fn bc_swings_per_s(attack_speed: f64) -> f64 {
    if attack_speed <= 0.0 {
        return 0.0;
    }
    let dd = 20.0 / attack_speed;
    let k = (0.75 * dd - 1e-9).ceil().max(1.0);
    let u = (0.25 * dd.max(2.0) + 0.5 + 1e-9).floor().max(1.0);
    20.0 / (k + u)
}
