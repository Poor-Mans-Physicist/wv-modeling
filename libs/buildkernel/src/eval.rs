//! Hurt chain, buffs, per-ability models and the cycle score (model/damage.py, families.py, abilities.py, evaluate.py).
//! Every `req` mirrors a Python `cfg["key"]` (KeyError -> evaluation error); `or` mirrors `cfg.get(key, default)`.

use crate::build::*;
use crate::problem::*;

type R<T> = Result<T, String>;

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
impl std::ops::MulAssign<f64> for V4 {
    #[inline]
    fn mul_assign(&mut self, m: f64) {
        for x in self.0.iter_mut() {
            *x *= m;
        }
    }
}

const BOSS_EXEC_MULT: f64 = 0.25;
const PLAIN_MAX_HITS_PER_S: f64 = 2.0;
pub const C_LO: f64 = -12.0;
pub const C_HI: f64 = 80.0;

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
}

#[derive(Clone)]
pub struct Buffs {
    pub totem_pd: Option<f64>,
    pub mana_regen_add: f64,
    pub rampage: Option<f64>,
    pub rampage_cost: f64,
    pub rampage_fixed: Option<f64>,
    pub rampage_lucky: f64,
    pub bc_ad: f64,
    pub bc_lucky_per_hit: f64,
    pub bc_lucky_hits_per_s: f64,
    pub colossus_resist: f64,
    pub empower: f64,
    pub vulnerable: f64,
    pub porcupine: f64,
    /// Melee weaving: last swing's raw amount and the share of clippable hits inside its i-frame window.
    pub weave_raw: f64,
    pub weave_f: f64,
    pub weave_drop: std::cell::Cell<bool>,
}

/// A tier config with the build's per-ability cooldown and mana-cost multipliers applied (families.spec_cfg).
#[derive(Clone, Copy)]
pub struct CfgM<'a> {
    pub c: &'a Cfg,
    pub cd: f64,
    pub mana: f64,
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

/// One family's output in the four lanes plus the lane-independent rates healing needs.
pub struct FamOut {
    pub dps: V4,
    pub pack: V4,
    pub resist_bonus: f64,
    pub leech_rate: f64,
    pub lucky_rate: f64,
    pub supply: f64,
    pub demand: f64,
}

pub struct Ev<'a> {
    pub p: &'a Problem,
    pub b: &'a Build,
    pub st: &'a Stats,
    pub d: &'a Derived,
}

#[inline]
fn dv(a: f64, b: f64) -> R<f64> {
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
    fn cfg(&self, fb: &mut Fallbacks, spec: usize, tier: usize) -> CfgM<'a> {
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
        CfgM { c, cd, mana }
    }

    /// Flat thorns after Shell Porcupine (damage.thorns_flat).
    fn thorns_flat(&self, bf: &Buffs) -> f64 {
        self.d.thorns_flat * (1.0 + bf.porcupine)
    }

    /// Damage reflected per boss hit (damage.thorns_reflect).
    fn thorns_reflect(&self, bf: &Buffs) -> f64 {
        self.d.attack_damage * 2.0 * self.d.thorns_pct + self.thorns_flat(bf)
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

    fn talent_tier(&self, t: Option<usize>) -> Option<&'a TalentTier> {
        let tier = self.st.tier(t);
        if tier == 0 {
            None
        } else {
            Some(&self.p.talents[t.unwrap()].tiers[tier - 1])
        }
    }

    fn cooldown_s(&self, ticks: f64) -> f64 {
        f64::max(0.05, ticks * (1.0 - self.d.cooldown_reduction) / 20.0)
    }

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
        Ok(d.healing_effectiveness * (leech + ls + heal))
    }

    fn cd_s(&self, c: &CfgM) -> f64 {
        self.cooldown_s(c.or(K::cooldownTicks, 0.0))
    }

    fn pack_targets(&self, radius: f64) -> f64 {
        let r = radius * self.d.aoe_multiplier;
        let n = self.p.knobs.pack_size;
        let x = r / self.p.knobs.pack_radius;
        1.0 + (n - 1.0) * f64::min(1.0, x * x)
    }

    fn lightning_bonus(&self) -> R<f64> {
        match self.talent_tier(self.p.nt.lightning_damage) {
            Some(t) => t.pdd.ok_or_else(|| "KeyError percentDamageDealt".into()),
            None => Ok(0.0),
        }
    }

    pub fn buffs(&self, fb: &mut Fallbacks) -> R<(Buffs, f64)> {
        let p = self.p;
        let d = self.d;
        let k = &p.knobs;
        let t_kill = k.kill_time_s;
        let mut bf = Buffs {
            totem_pd: None, mana_regen_add: 0.0, rampage: None, rampage_cost: 0.0, rampage_fixed: None, rampage_lucky: 0.0,
            bc_ad: 0.0, bc_lucky_per_hit: 0.0, bc_lucky_hits_per_s: 0.0, colossus_resist: 0.0, empower: 1.0, vulnerable: 1.0,
            porcupine: 0.0, weave_raw: 0.0, weave_f: 0.0, weave_drop: std::cell::Cell::new(false),
        };
        let main = match p.fam.kind {
            FamKind::Ability { main } => Some(main),
            _ => None,
        };
        let mut demand = 0.0;
        let mut vuln: Vec<(i64, f64, bool)> = Vec::new();
        for &sid in &p.buff_specs {
            let learned = self.b.abil(sid);
            if learned == 0 || Some(sid) == main {
                continue;
            }
            let (tier, _) = self.effective_tier(sid, learned);
            let c = self.cfg(fb, sid, tier);
            let name = p.specs[sid].id.as_str();
            if name == "Totem_Player_Damage" {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.totem_pd = Some(c.req(K::totemPlayerDamagePercent)? * up);
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if name == "Totem_Mana_Regen" {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.mana_regen_add = c.req(K::totemManaRegenPercent)? * up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if name.starts_with("Rampage") {
                let cost = c.req(K::manaCostPerSecond)? + 0.5 * c.or(K::manaRampPerSecond, 0.0) * t_kill;
                bf.rampage = Some(c.req(K::damageIncrease)?);
                bf.rampage_cost = cost;
                if name == "Rampage_Berserker" && d.mana_regen >= k.rampage_refresh_regen - 1e-9 && d.cooldown_reduction >= k.rampage_refresh_cdr - 1e-9 {
                    bf.rampage_fixed = Some(k.rampage_refresh_uptime);
                    bf.rampage_cost = c.req(K::manaCostPerSecond)?;
                }
                if name == "Rampage_Instinct" {
                    bf.rampage_lucky = c.or(K::luckyHitChance, 0.0);
                }
            } else if name.starts_with("Battle_Cry") {
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let stacks = c.req(K::maxStacksTotal)?.min(30.0);
                if name == "Battle_Cry_Base" {
                    bf.bc_ad = c.req(K::attackDamagePerStack)? * stacks / cd;
                } else if name == "Battle_Cry_Spectral_Strike" {
                    c.req(K::abilityPowerPerStack)?;
                } else {
                    let used = c.req(K::maxStacksUsedPerHit)?;
                    bf.bc_lucky_per_hit = c.req(K::luckyHitChancePerStack)? * used;
                    bf.bc_lucky_hits_per_s = dv(stacks, used)? / cd;
                }
                demand += c.req(K::manaCost)? / cd;
            } else if name == "Taunt_Base" {
                let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = f64::min(1.0, dur / cd);
                vuln.push((c.req(K::amplifier)? as i64, up, true));
                demand += c.req(K::manaCost)? / cd.max(dur);
            } else if name == "Colossus_Base" {
                let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.colossus_resist = c.req(K::additionalResistance)? * up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if name == "Nova_Slow" {
                let lvl = self.st.special[0];
                if lvl > 0.0 {
                    let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                    let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                    let up = f64::min(1.0, 4.0 * dur / cd);
                    vuln.push((lvl as i64 - 1, up, true));
                    demand += c.req(K::manaCost)? / cd;
                }
            } else if name == "Shell_Porcupine" {
                bf.porcupine = c.req(K::additionalThornsDamagePercent)?;
                demand += c.req(K::manaCostPerSecond)?;
            } else if name == "Concentrate_Base" {
                bf.empower = 1.0 + 0.1 * (k.concentrate_empower_amp + 1.0);
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                demand += c.req(K::manaCost)? / cd;
            }
        }
        if let Some(lvl) = self.b.etch_num(p, Etq::LuckyVulnerable)? {
            if p.family_lucky() {
                let lvl = py_round(lvl) as i64;
                let up = f64::min(1.0, d.lucky_hit_chance * 3.0);
                vuln.push((lvl - 1, up, false));
            }
        }
        if let Some(lvl) = self.b.etch_num(p, Etq::TotemPlayerDamageEffect)? {
            if bf.totem_pd.is_some() {
                let lvl = py_round(lvl) as i64;
                let sid = p.spec_by_name["Totem_Player_Damage"];
                let (t, _) = self.effective_tier(sid, self.b.abil(sid));
                let tc = self.cfg(fb, sid, t);
                let dur = tc.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let up = dur / (dur + self.cooldown_s(tc.req(K::cooldownTicks)?));
                vuln.push((lvl - 1, up, false));
            }
        }
        if let Some(rl) = self.b.etch_num(p, Etq::RampageLucky)? {
            if bf.rampage.is_some() {
                bf.rampage_lucky += rl;
            }
        }
        if !vuln.is_empty() {
            let mut best = 1.0;
            for &(amp, up, is_amp) in &vuln {
                let capped = (if is_amp { amp } else { amp + 1 }).min(8) - if is_amp { 0 } else { 1 };
                let bonus = if self.b.bug(Bug::VulnOff1) { 0.1 * capped as f64 } else { 0.1 * (capped + 1) as f64 };
                let m = 1.0 + bonus * up;
                if m > best {
                    best = m;
                }
            }
            bf.vulnerable = best;
        }
        Ok((bf, demand))
    }

    fn mana_supply(&self, bf: &Buffs) -> f64 {
        self.d.mana_regen + bf.mana_regen_add * self.d.mana_regen_vt
    }

    /// families.mana_steal_pct: max mana fraction restored per lucky hit.
    fn mana_steal_pct(&self) -> R<f64> {
        match self.talent_tier(self.p.nt.mana_steal) {
            Some(t) => t.mmp.ok_or_else(|| "KeyError maxManaPercentage".into()),
            None => Ok(0.0),
        }
    }

    fn registry(&self, bf: &Buffs) -> R<f64> {
        let k = &self.p.knobs;
        let d = self.d;
        let mut add = 0.0;
        if let Some(v) = bf.totem_pd {
            if v != 0.0 {
                add += v;
            }
        }
        if self.st.berserk_power {
            add += 1.0 * k.uptime_kill_stacks;
        }
        if d.relentless > 0.0 {
            add += d.relentless * 10.0;
        }
        if d.third_attack > 0.0 {
            add += d.third_attack / 3.0;
        }
        if let Some(t) = self.talent_tier(self.p.nt.berserking) {
            add += t.di.ok_or("KeyError damageIncrease")? * k.uptime_low_hp;
        }
        if let Some(t) = self.talent_tier(self.p.nt.depleted) {
            add += t.di.ok_or("KeyError damageIncrease")? * k.uptime_low_mana;
        }
        Ok(1.0 + add)
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
        if let Some(tier) = self.talent_tier(nt.execution_strike) {
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
        if !hc.clip || bf.weave_f <= 0.0 {
            return self.hit_core(flat, baked, hc, lucky, bf);
        }
        let f = bf.weave_f;
        let full = self.hit_core(flat, baked, hc, lucky, bf)?;
        let base = flat + baked;
        let r = if base > 0.0 { f64::max(0.0, 1.0 - bf.weave_raw / base) } else { 0.0 };
        let cl = if r > 0.0 {
            self.hit_core(flat * r, baked * r, hc, lucky, bf)?
        } else {
            bf.weave_drop.set(true);
            V4([0.0; 4])
        };
        let mut out = [0.0; 4];
        for l in 0..4 {
            out[l] = (1.0 - f) * full.0[l] + f * cl.0[l];
        }
        Ok(V4(out))
    }

    /// One hit before the weaving clip (damage._hit). `lucky` is the hit's lucky chance.
    fn hit_core(&self, flat: f64, baked: f64, hc: Hc, lucky: f64, bf: &Buffs) -> R<V4> {
        let b = self.b;
        let d = self.d;
        let di = d.damage_increase;
        let e_mult = if hc.ap { 1.0 } else { 1.0 + di };
        let m_mult = self.registry(bf)?;
        let m_applies = !(hc.ap || hc.aoe);
        let di2 = b.bug(Bug::IceboltDi2);
        let e_and_m = |xf: f64, xb: f64| -> f64 {
            let mut v = if hc.di_baked && !di2 { xf * e_mult + xb } else { (xf + xb) * e_mult };
            if m_applies {
                v *= m_mult;
            }
            v
        };
        let base = flat + baked;
        let mut cond = 1.0;
        if let Some(t) = self.talent_tier(self.p.nt.executioner) {
            let th = t.th;
            let inc = t.di.ok_or("KeyError damageIncrease")?;
            let x = 1.0 / ((1.0 - th) + th / (1.0 + inc)) - 1.0;
            cond *= 1.0 + x;
        }
        if hc.ap {
            if let Some(t) = self.talent_tier(self.p.nt.hexbreaker) {
                let x = t.di.ok_or("KeyError damageIncrease")? * self.p.knobs.uptime_target_debuffed;
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
        let lt = if hc.lucky { Some(self.lucky_terms(lucky)?) } else { None };
        let gear_bug = b.bug(Bug::ExecGear);
        let order_bug = b.bug(Bug::OrderFlatadd);
        let fang_bug = b.bug(Bug::FangResidual);
        let plain = e_and_m(flat, baked);
        let x_add = if hc.normal { d.ap_flat * d.ap_scaling + self.thorns_flat(bf) * d.thorns_scaling } else { 0.0 };
        let mut out = [0.0; 4];
        for l in 0..4 {
            let missing = 0.5 * LANE_HP[l];
            let exec_attr = if LANE_EXEC[l] { d.execution } else { 0.0 };
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
        if hc.echo_ok && d.echo_chance > 0.0 {
            let c = d.echo_chance;
            let stored = base * (1.0 + d.echo_damage) * 0.667;
            let (mut series, mut term, mut decay, mut k) = (0.0, 1.0, 1.0, 0);
            let sq = c.powf(0.5);
            while decay > 0.0 && k < 40 {
                series += term;
                term *= sq * decay;
                decay = decay * 0.95 - 0.05;
                k += 1;
            }
            let keep = !b.bug(Bug::EchoFlagloss);
            let hce = Hc { ap: hc.ap && keep, aoe: false, normal: if keep { hc.normal } else { true }, ..Hc::default() };
            let replay = self.hit(stored, 0.0, hce, lucky, bf)?;
            for l in 0..4 {
                out[l] += c * series * replay.0[l];
            }
        }
        Ok(V4(out))
    }

    /// Family DPS in the four lanes plus the rates and mana figures healing needs.
    pub fn compute(&self, fb: &mut Fallbacks) -> R<FamOut> {
        match &self.p.fam.kind {
            FamKind::Melee { combo_avg, weapon } => self.melee(fb, *combo_avg, weapon),
            FamKind::Ability { main } => self.ability(fb, *main),
        }
    }

    /// families.melee_lucky
    fn melee_lucky(&self, bf: &Buffs, aps: f64) -> f64 {
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

    /// families.rampage_uptime: returns the uptime and scales bf.rampage.
    fn rampage_uptime(bf: &mut Buffs, free: f64) -> f64 {
        let mut up = 1.0;
        if let Some(r) = bf.rampage {
            let ramp_cost = bf.rampage_cost;
            up = if ramp_cost > 0.0 { f64::min(1.0, free / ramp_cost) } else { 1.0 };
            if let Some(fx) = bf.rampage_fixed {
                up = up.min(fx);
            }
            bf.rampage = Some(r * up);
        }
        up
    }

    /// families.melee_swings: (per hit, swings/s, lucky chance, raw pre-event amount, pack targets).
    fn melee_swings(&self, bf: &Buffs, combo_avg: f64, weapon: &str) -> R<(V4, f64, f64, f64, f64)> {
        let p = self.p;
        let d = self.d;
        let ad = d.attack_damage * bf.empower;
        let aps = bc_swings_per_s(d.attack_speed).min(p.knobs.max_swings_per_s);
        let raw = ad * combo_avg;
        let bc = bf.bc_ad * ad / aps.max(1e-6);
        let lucky = self.melee_lucky(bf, aps);
        let hc = Hc { normal: true, lucky: true, echo_ok: true, rampage: true, ..Hc::default() };
        let per_hit = self.hit(raw + bc, 0.0, hc, lucky, bf)?;
        let mut targets = 1.0;
        if d.on_hit_aoe > 0.0 {
            targets += 0.6 * f64::min(p.knobs.pack_size - 1.0, d.on_hit_aoe * 1.5);
        }
        if d.chain > 0.0 {
            let mut s = 0.0;
            for k in 1..=(d.chain as i64) {
                s += 0.5f64.powf(k as f64);
            }
            targets += s;
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
        let aps0 = bc_swings_per_s(d.attack_speed).min(p.knobs.max_swings_per_s);
        let lp = self.melee_lucky(&bf, aps0);
        let steal = aps0 * lp * d.mana_max * self.mana_steal_pct()?;
        let supply = self.mana_supply(&bf) + steal;
        let ramp_cost = bf.rampage_cost;
        Self::rampage_uptime(&mut bf, f64::max(0.0, supply - demand));
        let (per_hit, aps, lucky, _, targets) = self.melee_swings(&bf, combo_avg, weapon)?;
        let dps = per_hit * aps;
        let total_demand = demand + if bf.rampage.is_some() { ramp_cost } else { 0.0 };
        Ok(FamOut {
            dps, pack: dps * targets, resist_bonus: bf.colossus_resist, leech_rate: aps, lucky_rate: aps * lucky, supply,
            demand: total_demand,
        })
    }

    fn ability(&self, fb: &mut Fallbacks, main: usize) -> R<FamOut> {
        let (mut bf, demand) = self.buffs(fb)?;
        let p = self.p;
        let d = self.d;
        let learned = self.b.abil(main);
        let (tier, _) = self.effective_tier(main, learned.max(1));
        let c = self.cfg(fb, main, tier);
        let weave = self.b.weave;
        let mut steal_m = 0.0;
        if weave {
            let aps0 = bc_swings_per_s(d.attack_speed).min(p.knobs.max_swings_per_s);
            steal_m = aps0 * self.melee_lucky(&bf, aps0) * d.mana_max * self.mana_steal_pct()?;
        }
        let base = self.mana_supply(&bf) + steal_m;
        let ramp_cost = bf.rampage_cost;
        let mut free = 0.0;
        if bf.rampage.is_some() {
            let (_, _, need0, rate0) = self.model(fb, main, &c, &bf)?;
            let (s0, k0) = self.mana_scale(main, need0, rate0, base, demand)?;
            free = f64::max(0.0, base + k0 * s0 - demand - need0 * s0);
        }
        Self::rampage_uptime(&mut bf, free);
        let mut mel = None;
        if weave {
            let weapon = p.types[self.b.items[MAINHAND].ty()].name.as_str();
            let m = self.melee_swings(&bf, combo_avg(weapon), weapon)?;
            bf.weave_raw = m.3;
            bf.weave_f = f64::min(1.0, p.knobs.weave_window_s * m.1);
            mel = Some(m);
        }
        let (dps, pack, need, rate) = self.model(fb, main, &c, &bf)?;
        let (leech_ok, lucky_ok) = self.leech_lucky_ok(main);
        let (scale, k) = self.mana_scale(main, need, rate, base, demand)?;
        let supply = base + k * scale;
        let rate = rate * scale;
        let (mut leech_rate, mut lucky_rate) = (0.0, 0.0);
        if leech_ok {
            leech_rate = rate;
            if lucky_ok {
                lucky_rate = rate * self.d.lucky_hit_chance;
            }
        }
        let dps = dps * scale;
        let pack = pack * scale;
        if let Some((_, aps, lucky, _, _)) = mel {
            if bf.weave_drop.get() {
                leech_rate *= 1.0 - bf.weave_f;
                lucky_rate *= 1.0 - bf.weave_f;
            }
            leech_rate += aps;
            lucky_rate += aps * lucky;
        }
        Ok(FamOut {
            dps, pack, resist_bonus: bf.colossus_resist, leech_rate, lucky_rate, supply,
            demand: demand + need + if bf.rampage.is_some() { ramp_cost } else { 0.0 },
        })
    }

    fn leech_lucky_ok(&self, main: usize) -> (bool, bool) {
        let p = self.p;
        if p.specs[main].id == "Ice_Bolt_Base" {
            return (self.b.etch(p, Etq::IceBoltLucky).is_some(), false);
        }
        (p.leech_spec[main], p.lucky_spec[main])
    }

    /// AbilityFamily.mana_scale: (share of the cooldown rate mana allows, Mana Steal per unit rate share).
    fn mana_scale(&self, main: usize, need: f64, rate: f64, base: f64, demand: f64) -> R<(f64, f64)> {
        let (leech_ok, lucky_ok) = self.leech_lucky_ok(main);
        let k = if leech_ok && lucky_ok { rate * self.d.lucky_hit_chance * self.d.mana_max * self.mana_steal_pct()? } else { 0.0 };
        let avail = f64::max(0.0, base - demand);
        let mut scale = 1.0;
        if need > 0.0 && avail + k < need {
            scale = avail / (need - k);
        }
        Ok((scale, k))
    }

    fn ap_instant(&self, c: &CfgM, bf: &Buffs, pct: f64, radius: bool, aoe: bool, echo: bool) -> R<(V4, V4, f64, f64)> {
        let raw = self.d.ability_power * pct * 1.0;
        let hc = Hc { ap: true, aoe, echo_ok: echo, clip: true, ..Hc::default() };
        let per = self.hit(raw, 0.0, hc, self.d.lucky_hit_chance, bf)?;
        let cd = self.cd_s(c);
        let rate = plain_rate(1.0 / cd);
        let targets = if radius { self.pack_targets(c.or(K::radius, 0.0)) } else { 1.0 };
        Ok(finish(per, rate, targets, c.or(K::manaCost, 0.0) / cd))
    }

    fn pair(c: &CfgM) -> R<f64> {
        Ok(0.5 * (c.req(K::percentAbilityPowerDealtMin)? + c.req(K::percentAbilityPowerDealtMax)?))
    }

    /// One ability spec's (dps, pack_dps, mana_per_s) before mana limiting.
    fn model(&self, _fb: &mut Fallbacks, main: usize, c: &CfgM, bf: &Buffs) -> R<(V4, V4, f64, f64)> {
        let p = self.p;
        let d = self.d;
        let b = self.b;
        let lk = d.lucky_hit_chance;
        let ap = d.ability_power;
        let ad = d.attack_damage;
        let name = p.specs[main].id.as_str();
        let hit = |flat: f64, baked: f64, hc: Hc| self.hit(flat, baked, hc, lk, bf);
        let ap_aoe = Hc { ap: true, aoe: true, ..Hc::default() };
        let ap_only = Hc { ap: true, ..Hc::default() };
        let ap_aoe_c = Hc { clip: true, ..ap_aoe };
        let ap_only_c = Hc { clip: true, ..ap_only };
        let aoe_c = Hc { aoe: true, clip: true, ..Hc::default() };
        Ok(match name {
            "Fireball_Base" => {
                let mut o = self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, true, true, false)?;
                let pr = self.st.special[1];
                if pr > 0.0 {
                    o.0 *= 1.0 + pr;
                    o.1 *= 1.0 + pr;
                }
                o
            }
            "Fireball_Fireshot" => self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, false, false, true)?,
            "Arcane_Rail" => self.ap_instant(c, bf, Self::pair(c)?, false, false, true)?,
            "Nova_Base" => {
                let mut o = self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, true, true, false)?;
                if let Some(v) = b.etch_num(p, Etq::NovaRecast)? {
                    let m = 1.0 + f64::min(1.0, v / 100.0);
                    o.0 *= m;
                    o.1 *= m;
                }
                o
            }
            "Nova_Dot" | "Toxic_Grenade" => {
                let total = ap * c.req(K::percentAbilityPowerDealt)?;
                let dur = if name == "Toxic_Grenade" {
                    f64::max(1.0, c.req(K::poisonTicks)? * (1.0 + d.effect_duration) / 20.0)
                } else {
                    c.req(K::durationSeconds)? * (1.0 + d.effect_duration)
                };
                let ticks = (dur as i64).max(1) as f64;
                let per = hit(total / ticks, 0.0, ap_aoe)?;
                let period = self.cd_s(c).max(dur);
                finish(per, ticks / period, self.pack_targets(c.or(K::radius, 0.0)), c.or(K::manaCost, 0.0) / period)
            }
            "Smite_Base" | "Smite_Archon" => {
                let base = name == "Smite_Base";
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, Hc { ap: true, echo_ok: base, ..Hc::default() })?;
                let bolts = 20.0 / c.req(K::intervalTicks)?.max(1.0);
                let n = p.knobs.smite_targets_in_range.max(1.0);
                let mut rate = bolts / n;
                if let Some(ev) = b.etch_num(p, Etq::SmiteEcho)? {
                    rate *= 1.0 + ev;
                }
                let mana = c.or(K::manaCostPerSecond, 0.0) + c.or(K::additionalManaPerBolt, 0.0) * bolts;
                finish(per, rate, 1.0, mana)
            }
            "Smite_Blast_Wave" => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, ap_aoe_c)?;
                let rate = plain_rate(20.0 / c.req(K::intervalTicks)?.max(1.0));
                finish(per, rate, self.pack_targets(c.req(K::radius)?), c.or(K::manaCostPerSecond, 0.0))
            }
            "Arcane_Base" => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_only_c)?;
                let rate = plain_rate(20.0);
                let targets = match b.etch_num(p, Etq::ArcanePierce)? {
                    Some(pierce) => 1.0 + (p.knobs.pack_size.min(pierce) - 1.0) * 0.5,
                    None => 1.0,
                };
                finish(per, rate, targets, c.or(K::manaCostPerSecond, 0.0))
            }
            "Arcane_Prism" => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_aoe_c)?;
                let dt = c.req(K::durationTicks)?;
                let pulses = (dt / c.req(K::intervalTicks)?.max(1.0)).floor().max(1.0);
                let period = self.cd_s(c).max(dt / 20.0);
                let rate = plain_rate(pulses / period);
                let targets = c.or(K::maxTargets, 8.0).min(self.pack_targets(c.req(K::radius)?));
                finish(per, rate, targets, c.or(K::manaCost, 0.0) / period)
            }
            "Chain_Lightning_Base" => {
                let mut o = self.ap_instant(c, bf, Self::pair(c)?, false, true, false)?;
                let m = 1.0 + self.lightning_bonus()?;
                o.0 *= m;
                let r = c.or(K::chainRange, 0.0) * d.aoe_multiplier;
                let x = r / p.knobs.pack_radius;
                let targets = 1.0 + (p.knobs.pack_size - 1.0) * f64::min(1.0, x * x);
                (o.0, o.0 * targets, o.2, o.3)
            }
            "Chain_Lightning_Orbs" => {
                let o = self.ap_instant(c, bf, Self::pair(c)?, false, true, false)?;
                let mut m = 1.0 + self.lightning_bonus()?;
                if let Some(v) = b.etch_num(p, Etq::OrbTriple)? {
                    m *= 3.0 * (1.0 - v);
                }
                let dps = o.0 * m;
                (dps, dps * 3.0, o.2, o.3)
            }
            "Chain_Lightning_Charged_Bolts" => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + c.req(K::percentAbilityPowerDealtMax)?) * (1.0 + self.lightning_bonus()?);
                let n = 0.5 * c.req(K::boltCount)?;
                let (f, r) = (c.req(K::fullDamageHitsPerTarget)?, c.req(K::repeatHitDamageMultiplier)?);
                let eff = n.min(f) + r * f64::max(0.0, n - f);
                let per = hit(ap * pct, 0.0, ap_aoe)?;
                let cd = self.cd_s(c);
                finish(per, eff / cd, 3.0, c.req(K::manaCost)? / cd)
            }
            "Storm_Arrow_Base" => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, ap_only)?;
                let dd = c.req(K::cloudDuration)? * (1.0 + d.effect_duration);
                let shots = dv(dd + 1.0, c.req(K::intervalTicks)? + 1.0)?.ceil();
                let period = dd / 20.0 + self.cd_s(c);
                finish(per, shots / period, 1.0, c.req(K::manaCost)? / period)
            }
            "Ice_Bolt_Base" => {
                let lucky_bolt = b.etch(p, Etq::IceBoltLucky).is_some();
                let m_reg = self.registry(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * if lucky_bolt { 1.0 } else { m_reg };
                let hc = Hc { aoe: !lucky_bolt, di_baked: true, ..Hc::default() };
                let per = hit(c.req(K::damagePerBolt)?, baked, hc)?;
                let cd = self.cd_s(c);
                let mut casts = 1.0;
                if let Some(v) = b.etch_num(p, Etq::IceBoltMulticast)? {
                    casts += v;
                }
                finish(per, casts / cd, 1.0, c.req(K::manaCost)? * casts / cd)
            }
            "Shard_Blizzard" => {
                let m_reg = self.registry(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let per = hit(c.req(K::damagePerShard)?, baked, Hc { aoe: true, di_baked: true, clip: true, ..Hc::default() })?;
                let dd = c.req(K::cloudDuration)? * (1.0 + d.effect_duration);
                let shots = dv(dd + 1.0, c.req(K::intervalTicks)? + 1.0)?.ceil();
                let active = dd / 20.0;
                let period = active + self.cd_s(c);
                let rate = plain_rate(dv(shots, active)?) * active / period;
                finish(per, rate, self.pack_targets(c.req(K::radius)?) * 0.5, c.req(K::manaCost)? / period)
            }
            "Javelin_Base" | "Javelin_Piercing" | "Javelin_Scatter" => {
                let per = hit(ad * c.req(K::percentAttackDamageDealt)?, 0.0, Hc { clip: true, ..Hc::default() })?;
                let cd = self.cd_s(c);
                let mut targets = 1.0 + f64::min(p.knobs.pack_size - 1.0, c.or(K::piercing, 0.0)) * 0.5;
                if let Some(nj) = c.get(K::numberOfJavelins) {
                    if nj != 0.0 {
                        targets += nj * 0.3;
                    }
                }
                if let Some(v) = b.etch_num(p, Etq::ExtraPiercingJavelin)? {
                    if c.has(K::piercing) && !c.has(K::numberOfJavelins) {
                        targets += 2.0 * v;
                    }
                }
                finish(per, 1.0 / cd, targets, c.req(K::manaCost)? / cd)
            }
            "Earthquake_Base" | "Earthquake_Singularity" | "Earthquake_Tremor" => {
                let m_reg = self.registry(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let per = hit(0.0, baked, ap_aoe_c)?;
                let shocks = c.req(K::shockCount)?;
                let period = shocks * c.req(K::shockIntervalTicks)? / 20.0 + self.cd_s(c);
                finish(per, shocks / period, self.pack_targets(c.req(K::radius)?), c.req(K::manaCost)? / period)
            }
            "Grenade_Base" | "Grenade_Sticky" => {
                let m_reg = self.registry(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let per = hit(0.0, baked, ap_aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), c.req(K::manaCost)? / cd)
            }
            "Necromancy_Base" | "Necromancy_Archer" => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_only_c)?;
                let rate = c.req(K::summonCap)?.min(PLAIN_MAX_HITS_PER_S);
                finish(per, rate, 1.5, 0.0)
            }
            "Dash_Damage" => {
                let m_reg = self.registry(bf)?;
                let per = hit(ad * c.req(K::attackDamagePercentPerDash)? * m_reg, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, plain_rate(1.0 / cd), 2.0, c.req(K::manaCost)? / cd)
            }
            "Fangs_Base" | "Fangs_Maw" => {
                let maw = name == "Fangs_Maw";
                let mut a = ad * bf.empower;
                if !maw && b.etch(p, Etq::RavenousFangs).is_some() {
                    a *= 0.25;
                }
                let raw = a * c.req(K::damageMultiplier)? + c.req(K::baseDamage)?;
                let per = hit(raw, 0.0, Hc { normal: true, lucky: true, rampage: true, ..Hc::default() })?;
                let hits = if maw { 8.7 } else { 3.0 };
                let cd = self.cd_s(c);
                let targets = if maw { 1.0 } else { self.pack_targets(c.req(K::radius)?) / 2.0 };
                finish(per, hits / cd, targets.max(1.0), c.req(K::manaCost)? / cd)
            }
            "Mana_Shield_Implode" => {
                let cd = self.cd_s(c);
                let mana = d.mana_max.min(d.mana_regen * cd);
                let per = hit(mana * c.req(K::percentManaDealt)?, 0.0, aoe_c)?;
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), 0.0)
            }
            "Implode_Life_Tap" => {
                let extra = b.etch_num(p, Etq::LifeTapExtra)?.unwrap_or(0.0);
                let raw = d.health * c.req(K::percentHealthDrained)? * (c.req(K::damagePerHealth)? + extra);
                let per = hit(raw, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), 0.0)
            }
            "Shield_Bash" | "Shield_Bash_Earthshatter" | "Shield_Bash_Battering_Ram" => {
                let m_reg = self.registry(bf)?;
                let ev = b.etch_num(p, Etq::ShieldBashDamage)?.unwrap_or(0.0);
                let mut raw = ad * (1.0 + d.block * c.req(K::blockChanceDamageScalar)?) + ad * ev;
                if name == "Shield_Bash_Battering_Ram" {
                    raw += self.thorns_flat(bf) * c.or(K::thornsDamageScalar, 0.0);
                }
                let per = hit(raw * m_reg, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, plain_rate(1.0 / cd), 2.5, c.req(K::manaCost)? / cd)
            }
            "Fireball_Volley" => {
                let k = &p.knobs;
                let mit = b.etch_num(p, Etq::Mitosis)?;
                let pen = if mit.is_some() { 0.5 } else { 1.0 };
                let raw = ap * c.req(K::percentAbilityPowerDealt)? * pen;
                let per = hit(raw, 0.0, ap_aoe_c)?;
                let cd = self.cd_s(c);
                let bb = k.volley_bounces;
                let bounce = mit.is_some() || b.bug(Bug::VolleyBounceExplode);
                let mut explosions = if bounce { bb + 1.0 } else { 1.0 };
                if let Some(m) = mit {
                    explosions += m * bb;
                }
                let single = explosions.min(k.volley_iframe_hits_per_cast);
                let rate = plain_rate(single / cd);
                let targets = self.pack_targets(c.req(K::radius)?);
                let mut pack_mult = 1.0;
                if mit.is_some() {
                    pack_mult = k.mitosis_multiplier / (pen * f64::max(rate * cd, 1e-9));
                }
                let o = finish(per, rate, targets, c.req(K::manaCost)? / cd);
                (o.0, o.1 * pack_mult, o.2, o.3)
            }
            "Totem_Mob_Damage" => {
                let ad_etch = b.etch(p, Etq::TotemMobAd).is_some();
                let m_reg = self.registry(bf)?;
                let base = ap + if ad_etch { ad * m_reg } else { 0.0 };
                let raw = base * c.req(K::totemPercentDamagePerInterval)?;
                let hc = if ad_etch { Hc { echo_ok: true, clip: true, ..Hc::default() } } else { Hc { ap: true, echo_ok: true, clip: true, ..Hc::default() } };
                let per = hit(raw, 0.0, hc)?;
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let period = dur + self.cd_s(c);
                let hits = dv(dur, c.req(K::totemDamageIntervalTicks)? / 20.0)?;
                finish(per, hits / period, self.pack_targets(c.req(K::totemEffectRadius)?), c.req(K::manaCost)? / period)
            }
            "Shell_Porcupine" => {
                let mut b2 = bf.clone();
                b2.porcupine = c.req(K::additionalThornsDamagePercent)?;
                let hc = Hc { lucky: self.st.lucky_thorns, echo_ok: true, double_ok: true, clip: true, ..Hc::default() };
                let per = self.hit(self.thorns_reflect(&b2), 0.0, hc, lk, &b2)?;
                finish(per, 1.0 / p.knobs.hit_interval_s, 1.0, c.req(K::manaCostPerSecond)?)
            }
            _ => return Err(format!("no kernel model for {}", name)),
        })
    }
}

fn plain_rate(rate: f64) -> f64 {
    if rate > PLAIN_MAX_HITS_PER_S { PLAIN_MAX_HITS_PER_S } else { rate }
}

fn finish(per: V4, rate: f64, targets: f64, mana: f64) -> (V4, V4, f64, f64) {
    let dps = per * rate;
    (dps, dps * targets, mana, rate)
}

pub fn bc_swings_per_s(attack_speed: f64) -> f64 {
    if attack_speed <= 0.0 {
        return 0.0;
    }
    let dd = 20.0 / attack_speed;
    let k = (0.75 * dd - 1e-9).ceil().max(1.0);
    let u = (0.25 * dd.max(2.0) + 0.5).floor().max(1.0);
    20.0 / (k + u)
}

#[derive(Clone, Copy, Default, serde::Serialize)]
pub struct Score {
    pub score: f64,
    pub cycle: f64,
    pub cycle_damage: f64,
    pub cycle_survival: f64,
    pub dps: f64,
    pub pack_dps: f64,
    pub ehp: f64,
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
fn damage_cycle(p: &Problem, dps0: f64, slope: f64) -> f64 {
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
    let a = (t * g.m[k]).ln() - ttk(g.h[k]).ln();
    let b = (t * g.m[k + 1]).ln() - ttk(g.h[k + 1]).ln();
    if a > b { g.c0 + (k as f64 + a / (a - b)) * g.step } else { g.c0 + k as f64 * g.step }
}

/// Survival cycle on the boss grid (hyper.solve_survival).
/// Survival cycle on the boss grid (hyper.solve_survival): largest c with D(c) <= cap.
fn survival_cycle(p: &Problem, cap: f64) -> f64 {
    let g = &p.grid;
    let n = g.d.len();
    let k = last_true(n, |k| g.d[k] <= 0.0 || g.d[k] <= cap);
    if k < 0 {
        return C_LO;
    }
    let k = k as usize;
    if k == n - 1 {
        return C_HI;
    }
    if g.d[k] <= 0.0 {
        return g.c0 + k as f64 * g.step;
    }
    let a = cap.ln() - g.d[k].ln();
    let b = cap.ln() - g.d[k + 1].ln();
    if a > b { g.c0 + (k as f64 + a / (a - b)) * g.step } else { g.c0 + k as f64 * g.step }
}

pub fn evaluate(b: &Build, p: &Problem, fb: &mut Fallbacks) -> R<Score> {
    let lin = lin_full(b, p);
    evaluate_lin(b, p, &lin, fb)
}

pub fn evaluate_lin(b: &Build, p: &Problem, lin: &Lin, fb: &mut Fallbacks) -> R<Score> {
    let st = stats(b, p, lin, fb);
    let mut d = derive(&st, p, b.bug(Bug::ManaCap));
    d.castle *= p.knobs.castle_bastion_uptime;
    if st.safer_space {
        let n = (10.0 * (1.0 - d.block) / p.knobs.hit_interval_s - 1e-9).ceil();
        d.block = 1.0 / (1.0 + n.max(0.0));
    }
    let ev = Ev { p, b, st: &st, d: &d };
    let fo = ev.compute(fb)?;
    let (dps, pack, resist_bonus) = (fo.dps, fo.pack, fo.resist_bonus);
    let up = 1.0 - p.knobs.boss_shield_downtime;
    let dps0 = dps.0[0] * up;
    let c_full = damage_cycle(p, dps0, (dps.0[1] - dps.0[0]) * up / 1e9);
    let mut c_dmg = c_full;
    if d.execution > 0.0 || st.tier(p.nt.execution_strike) > 0 {
        let c_ne = damage_cycle(p, dps.0[2] * up, (dps.0[3] - dps.0[2]) * up / 1e9);
        c_dmg = c_full.min(c_ne + p.knobs.execution_cycle_cap);
    }
    let resistance = if resist_bonus != 0.0 { (d.resistance + resist_bonus).min(d.resistance_cap) } else { d.resistance };
    let armor_mult = if d.armor <= 0.0 { 1.0 } else { 1600.0 / (1600.0 + d.armor * d.armor) };
    let m_det = armor_mult * (1.0 - resistance) * (1.0 - d.castle);
    let mut mult = (1.0 - d.block) * (1.0 - d.dodge) * m_det;
    if mult <= 1e-9 {
        fb.ehp_zero += 1;
        mult = 1e-9;
    }
    let e = d.health / mult;
    let heal = ev.healing(fb, &fo)?;
    let k = &p.knobs;
    let hh = d.health;
    let n = k.survive_hits;
    let hd = heal * k.hit_interval_s;
    let cap1 = hh / m_det.max(1e-9);
    let cap5 = cap1.min(f64::max(hd, (hh + (n - 1.0) * hd) / n) / mult);
    let c5 = survival_cycle(p, cap5);
    let c1 = survival_cycle(p, cap1);
    let protected = k.oneshot_protection && hd >= k.oneshot_heal_fraction * hh - 1e-9;
    let c_surv = if protected { c5.max(c1 + k.oneshot_extra_cycles) } else { c5 };
    let c_eff = c_dmg.min(c_surv);
    let gap_regen = f64::max(0.0, k.min_mana_regen - d.mana_regen) / k.min_mana_regen;
    let gap_cdr = f64::max(0.0, k.min_cooldown_reduction - d.cooldown_reduction) / k.min_cooldown_reduction;
    let met = gap_regen <= 1e-9 && gap_cdr <= 1e-9;
    let penalty = if met { 0.0 } else { 8.0 + 40.0 * (gap_regen + gap_cdr) };
    Ok(Score {
        score: c_eff + 0.002 * c_dmg.max(c_surv) - penalty, cycle: c_eff, cycle_damage: c_dmg, cycle_survival: c_surv,
        dps: dps0, pack_dps: pack.0[0], ehp: e,
    })
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
