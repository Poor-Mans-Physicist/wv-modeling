//! Buff abilities (buffs.buffs_for), cast debuffs, procs.finalize and the mana / Fury / Rampage helpers.

use crate::build::*;
use crate::problem::Sp;
use crate::problem::*;
use crate::eval::{dv, py_round, Ev, CfgM, R, V4};
use crate::hit::Hc;

/// procs.LUCKY_NOVA_HYPOTHERMIA_S
const LUCKY_NOVA_HYPOTHERMIA_S: f64 = 20.0;

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
    /// Spectral Strike: (stacks per cast, max stacks per hit, cooldown s, stack duration s) and stacks per hit.
    pub spectral: Option<(f64, f64, f64, f64)>,
    pub spectral_s: f64,
    pub colossus_resist: f64,
    pub empower: f64,
    pub vulnerable: f64,
    pub porcupine: std::cell::Cell<f64>,
    /// Melee weaving: last swing's raw amount and the share of clippable hits inside its i-frame window.
    /// Other hit streams on the target (hits/s, raw) and the chance a clippable hit lands in one of their windows.
    pub weave_src: Stack<(f64, f64), 24>,
    pub weave_f: f64,
    /// Largest share of a clipped hit's window-landings that were dropped (at or below the opening hit).
    pub weave_drop: std::cell::Cell<f64>,
    /// Rotation probe: the raw (flat + baked) of the first hit an ability evaluates (NaN = record the next one).
    pub raw_probe: std::cell::Cell<f64>,
    /// Dry probe (rotation): hits record their raw and return zero without running the hurt chain.
    pub dry: std::cell::Cell<bool>,
    /// With dry: clipped hits still record their i-frame drop (weave_drop), as the full evaluation would.
    pub clip_only: std::cell::Cell<bool>,
    /// Echo probe (damage._hit): while echo_rec is set the next eligible hit records what its echoes store and one
    /// pulse's value; echo_s stays NaN until then.
    pub echo_rec: std::cell::Cell<bool>,
    pub echo_s: std::cell::Cell<f64>,
    pub echo_r: std::cell::Cell<V4>,
    /// Echoing Smite etching: re-strikes are a second echo stream at this multiple of the bolt (0 = none).
    pub echo_extra: std::cell::Cell<f64>,
    /// Full-strength strikes per original bolt (2 with Lightning Strike Delay; rotation.py echo_orig).
    pub echo_orig: std::cell::Cell<f64>,
    /// Clip of the recorded hit: share inside another source's i-frame window and the raw fraction it keeps there.
    pub echo_cu: std::cell::Cell<f64>,
    pub echo_cf: std::cell::Cell<f64>,
    pub echo_cr: std::cell::Cell<f64>,
    /// God-node multiplier on direct melee swings (set around the melee evaluation).
    pub direct: std::cell::Cell<f64>,
    /// Sneaky Advantage factor (1 + per_effect x expected harmful effects on the boss).
    pub sneaky: f64,
    /// Debuff sources from casts, etchings and the main ability (procs.py; finalize adds the event-driven ones).
    pub db: crate::procs::Srcs,
    /// Lucky Vulnerable etching (level, seconds): applied on lucky hits.
    pub lucky_vuln: Option<(i64, f64)>,
    /// Share of the fight below 20% mana (Depleted): set by the family once the mana split is known.
    pub u_low_mana: f64,
    /// Chance the boss carries at least one harmful effect (Hexbreaker), from finalize.
    pub debuffed: f64,
    /// Empower speed toggle (speedPercentAdded, manaCostPerSecond); its stable speed and the mana it is paid.
    pub emp: Option<(f64, f64)>,
    pub emp_speed: f64,
    pub emp_paid: f64,
    pub sneaky_dodge: f64,
    pub totem_heal: f64,
    pub msp: f64,
    /// Ultimate Shield (drain mana/s, drain per tick, absorbed share, mana per point) and its uptime on the mana flow.
    pub us: Option<(f64, f64, f64, f64)>,
    pub us_uptime: f64,
    /// Rotation: mana a sneak cast spends (0 without sneak-cast Power Dump).
    pub pd_bar: f64,
    /// Instant buff casts per second (buffs_for `_inst_buff_rate`): each one empties the bar while always sneaking.
    pub inst_buff_rate: f64,
    /// Power Dump while sneaking the whole fight (rotation `pd_mode` "always"): the Imploding Javelin reads pd_bar.
    pub pd_always: bool,
    /// Share of the fight spent sneaking for Power Dump (buffs.survival_buffs `sneak_share`).
    pub sneak_share: f64,
    /// Mana a source's hits return per second at its full rate (Poison Mana, Scatter Mana), set by Ev::model.
    pub mana_back: std::cell::Cell<f64>,
    /// Share of casts made below half mana (buffs.mana_below_uptime, set by the rotation; Nova Damage Increase).
    pub u_half_mana: f64,
    /// Mana per second from Blocking Mana and the cloud part of Poison Mana (buffs.mana_etchings).
    pub etch_mana: f64,
    /// Cloud poison: (damage per hit, hits per second) and its scored damage (procs.cloud_poison).
    pub cloud_poison: Option<(f64, f64)>,
    pub cloud_dps: V4,
    /// Lucky Nova etching: Some(Frozen Orb Vulnerable seconds per free Frost Nova, if the Orb modification is worn).
    pub lucky_nova: Option<Option<f64>>,
    /// Rework Lucky Nova etching: the cast Frost Nova's Chilled (duration s, chill level).
    pub lucky_nova_chill: Option<(f64, i64)>,
    /// AP Totem etching: ability power multiplier 1 + uptime x totemPlayerDamagePercent (buffs.apply_buff_stats).
    pub ap_totem: f64,
    /// Colossus uptime (Colossus Titan etching).
    pub colossus_up: f64,
    /// hit_core's per-evaluation terms, filled on first use: lucky_terms (mult, exec_frac, fang), the Executioner
    /// factor, and registry / talent_registry with the u_low_mana bits they were computed at (NaN key = empty).
    pub hk_lucky: std::cell::Cell<Option<(f64, f64, f64)>>,
    pub hk_exec: std::cell::Cell<Option<f64>>,
    pub hk_reg: std::cell::Cell<(u64, f64)>,
    pub hk_treg: std::cell::Cell<(u64, f64)>,
}

/// Empty key of the registry caches (a NaN bit pattern u_low_mana never takes: it is computed, not NaN-boxed).
pub(crate) const HK_EMPTY: u64 = 0x7FF8_DEAD_BEEF_0001;

/// buffs.survival_buffs: what the survival model reads from the family (Mana Shield pool, Ninja dodge, Totem heal,
/// Empower's stable speed, Ultimate Shield uptime / share / window budget).
#[derive(Clone, Copy, Default)]
pub struct SurvOut {
    pub msp: f64,
    pub sneaky_dodge: f64,
    pub totem_heal: f64,
    pub empower_speed: f64,
    pub us_u: f64,
    pub us_pct: f64,
    pub us_budget: f64,
    pub sneak_share: f64,
}

impl<'a> Ev<'a> {
    pub fn buffs(&self, fb: &mut Fallbacks) -> R<(Buffs, f64)> {
        let p = self.p;
        let d = self.d;
        let k = &p.knobs;
        let t_kill = k.kill_time_s;
        let mut bf = Buffs {
            totem_pd: None, mana_regen_add: 0.0, rampage: None, rampage_cost: 0.0, rampage_fixed: None, rampage_lucky: 0.0,
            bc_ad: 0.0, bc_lucky_per_hit: 0.0, bc_lucky_hits_per_s: 0.0, spectral: None, spectral_s: 0.0, colossus_resist: 0.0, empower: 1.0, vulnerable: 1.0,
            porcupine: std::cell::Cell::new(0.0), weave_src: Stack::new(), weave_f: 0.0, weave_drop: std::cell::Cell::new(0.0), raw_probe: std::cell::Cell::new(0.0), dry: std::cell::Cell::new(false), clip_only: std::cell::Cell::new(false),
            echo_rec: std::cell::Cell::new(false), echo_s: std::cell::Cell::new(f64::NAN), echo_r: std::cell::Cell::new(V4([0.0; 4])), echo_extra: std::cell::Cell::new(0.0), echo_orig: std::cell::Cell::new(1.0),
            echo_cu: std::cell::Cell::new(1.0), echo_cf: std::cell::Cell::new(0.0), echo_cr: std::cell::Cell::new(1.0),
            direct: std::cell::Cell::new(1.0), sneaky: 1.0,
            db: crate::procs::Srcs::new(), lucky_vuln: None,
            u_low_mana: if k.mana_state_heuristic { 0.0 } else { k.uptime_low_mana }, debuffed: 0.0,
            emp: None, emp_speed: 0.0, emp_paid: 0.0, sneaky_dodge: 0.0, totem_heal: 0.0, msp: 0.0, us: None, us_uptime: 0.0,
            pd_bar: 0.0, inst_buff_rate: 0.0, pd_always: false, sneak_share: 0.0,
            mana_back: std::cell::Cell::new(0.0), ap_totem: 1.0, colossus_up: 0.0, lucky_nova: None, lucky_nova_chill: None, u_half_mana: 0.0, etch_mana: 0.0, cloud_poison: None, cloud_dps: V4([0.0; 4]),
            hk_lucky: std::cell::Cell::new(None), hk_exec: std::cell::Cell::new(None),
            hk_reg: std::cell::Cell::new((HK_EMPTY, 0.0)), hk_treg: std::cell::Cell::new((HK_EMPTY, 0.0)),
        };
        let main = match p.fam.kind {
            FamKind::Ability { main } => Some(main),
            _ => None,
        };
        let mut demand = 0.0;
        for &sid in &p.buff_specs {
            let learned = self.b.abil(sid);
            if learned == 0 || Some(sid) == main {
                continue;
            }
            let (tier, _) = self.effective_tier(sid, learned);
            let c = self.cfg(fb, sid, tier);
            if self.unaffordable(&c) {
                fb.note_unaffordable(sid);
                continue;
            }
            let d0 = demand;
            let sp = p.spec_sp[sid];
            if sp == Sp::Totem_Player_Damage {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.totem_pd = Some(c.req(K::totemPlayerDamagePercent)? * up);
                demand += c.req(K::manaCost)? / (dur + cd);
                if self.st.etch(p, Etq::ApTotem).map_or(false, |e| e.1) {
                    bf.ap_totem = 1.0 + up * c.req(K::totemPlayerDamagePercent)?;
                }
            } else if sp == Sp::Totem_Mana_Regen {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.mana_regen_add = c.req(K::totemManaRegenPercent)? * up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if matches!(sp, Sp::Rampage_Base | Sp::Rampage_Berserker | Sp::Rampage_Bloodlust | Sp::Rampage_Instinct) {
                let cost = c.req(K::manaCostPerSecond)? + 0.5 * c.or(K::manaRampPerSecond, 0.0) * t_kill;
                bf.rampage = Some(c.req(K::damageIncrease)?);
                bf.rampage_cost = cost;
                if sp == Sp::Rampage_Berserker && d.mana_regen >= k.rampage_refresh_regen - 1e-9 && d.cooldown_reduction >= k.rampage_refresh_cdr - 1e-9 {
                    bf.rampage_fixed = Some(k.rampage_refresh_uptime);
                    bf.rampage_cost = c.req(K::manaCostPerSecond)?;
                }
                if sp == Sp::Rampage_Instinct {
                    bf.rampage_lucky = c.or(K::luckyHitChance, 0.0);
                }
            } else if matches!(sp, Sp::Battle_Cry_Base | Sp::Battle_Cry_Spectral_Strike | Sp::Battle_Cry_Lucky_Strike) {
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let stacks = c.req(K::maxStacksTotal)?.min(30.0);
                if sp == Sp::Battle_Cry_Base {
                    bf.bc_ad = c.req(K::attackDamagePerStack)? * stacks / cd;
                } else if sp == Sp::Battle_Cry_Spectral_Strike {
                    let dur = (c.req(K::stackDuration)? * (1.0 + d.effect_duration) + 1e-9).floor() / 20.0;
                    bf.spectral = Some((stacks, c.req(K::maxStacksUsedPerHit)?, cd, dur));
                } else {
                    let used = c.req(K::maxStacksUsedPerHit)?;
                    bf.bc_lucky_per_hit = c.req(K::luckyHitChancePerStack)? * used;
                    bf.bc_lucky_hits_per_s = dv(stacks, used)? / cd;
                }
                demand += c.req(K::manaCost)? / cd;
            } else if sp == Sp::Taunt_Base {
                let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = f64::min(1.0, dur / cd);
                let mut amp = c.req(K::amplifier)? as i64;
                let h = k.taunt_amp_half_above as i64;
                if h > 0 && amp > h {
                    amp = h + (amp - h) / 2;
                }
                bf.db.push(crate::procs::VULNERABLE, amp, up, false);
                demand += c.req(K::manaCost)? / cd.max(dur);
            } else if sp == Sp::Colossus_Base {
                let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.colossus_resist = c.req(K::additionalResistance)? * up;
                bf.colossus_up = up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if sp == Sp::Nova_Slow {
                let lvl = self.st.special[0];
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                if lvl > 0.0 {
                    let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                    let ch = self.st.special[2].min(1.0);
                    let procs = if ch > 0.0 { ch * d.boss_hits_landed } else { 0.0 };
                    let up = crate::procs::proc_up(procs, 4.0 * dur, crate::procs::periodic_up(4.0 * dur, cd));
                    bf.db.push(crate::procs::VULNERABLE, lvl as i64, up, true);
                }
                bf.db.push(crate::procs::HYPOTHERMIA, 0, 1.0, false);
                if p.godtree.is_some() {
                    let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                    let ch = self.st.special[2].min(1.0);
                    let throne = if ch > 0.0 { ch * d.boss_hits_landed } else { 0.0 };
                    let up = crate::procs::proc_up(throne, dur, crate::procs::periodic_up(dur, cd));
                    bf.db.push(crate::procs::CHILLED, c.req(K::amplifier)? as i64 + 1, up, false);
                }
                demand += c.req(K::manaCost)? / cd;
            } else if sp == Sp::Shell_Porcupine {
                bf.porcupine.set(c.req(K::additionalThornsDamagePercent)?);
                demand += c.req(K::manaCostPerSecond)?;
            } else if sp == Sp::Concentrate_Base {
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let dur = c.req(K::baseEffectDuration)? * (1.0 + d.effect_duration);
                let steps = if self.p.godtree.is_some() { 10 + tier / 3 } else { 20 };
                let (amp, up) = concentrate_ladder(c.req(K::baseAmplitude)?, c.req(K::amplitudeScaleChance)?, dur, cd * 20.0,
                                                   0.2 * k.concentrate_effects_stripped, steps);
                bf.empower = 1.0 + 0.1 * (amp + 1.0) * up;
                demand += c.req(K::manaCost)? / cd;
            } else if sp == Sp::Empower_Base {
                bf.emp = Some((c.req(K::speedPercentAdded)?, c.req(K::manaCostPerSecond)?));
            } else if sp == Sp::Empower_Slowness_Aura {
                bf.db.push(crate::procs::SLOWNESS, 0, if d.ranged { 0.0 } else { 1.0 }, false);
                demand += c.req(K::manaCostPerSecond)?;
            } else if sp == Sp::Sneaky_Getaway {
                let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.sneaky_dodge = match self.st.etch_num(p, Etq::SneakyNinja)? {
                    Some(v) if v != 0.0 => v * up,
                    _ => 0.0,
                };
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if sp == Sp::Totem_Base {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.totem_heal = c.req(K::totemHealthPerSecond)? * up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if sp == Sp::Mana_Shield_Base {
                let dur = c.req(K::durationTicks)? / 20.0;
                let cd = self.cooldown_s(c.req(K::cooldownTicks)?);
                let up = dur / (dur + cd);
                bf.msp = c.req(K::healthPoints)? * up;
                demand += c.req(K::manaCost)? / (dur + cd);
            } else if sp == Sp::UltimateShield_Base {
                let tick = f64::min(c.req(K::baseManaDrainPerTick)? + c.req(K::manaDrainRampPerSecond)? * 0.5 * t_kill,
                                    c.req(K::maxManaDrainPerTick)?);
                let drain = 20.0 * tick;
                let scalar = f64::max(c.req(K::manaPerDamageScalar)? * k.ultimate_shield_scalar_mult, 1e-5);
                bf.us = Some((drain, tick, c.req(K::percentageDamageAbsorbed)?.max(0.0).min(1.0), scalar));
                demand += drain;
            }
            if demand > d0 && c.or(K::manaCost, 0.0) > 0.0 && c.or(K::manaCostPerSecond, 0.0) == 0.0 {
                bf.inst_buff_rate += (demand - d0) / c.or(K::manaCost, 0.0);
            }
        }
        if let Some(lvl) = self.st.etch_num(p, Etq::LuckyVulnerable)? {
            let dur = self.st.etch_dur(p, Etq::LuckyVulnerable).ok_or("Lucky Vulnerable etching has no duration")?;
            bf.lucky_vuln = Some((py_round(lvl) as i64, dur));
        }
        if let Some(lvl) = self.st.etch_num(p, Etq::TotemPlayerDamageEffect)? {
            if bf.totem_pd.is_some() {
                let lvl = py_round(lvl) as i64;
                let edur = self.st.etch_dur(p, Etq::TotemPlayerDamageEffect).ok_or("Totem damage etching has no duration")?;
                let sid = p.spec_by_name["Totem_Player_Damage"];
                let (t, _) = self.effective_tier(sid, self.b.abil(sid));
                let tc = self.cfg(fb, sid, t);
                let dur = tc.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let period = dur + self.cooldown_s(tc.req(K::cooldownTicks)?);
                bf.db.push(crate::procs::VULNERABLE, lvl - 1, crate::procs::periodic_up(dur + edur, period), false);
            }
        }
        self.mana_etchings(&mut bf)?;
        if let Some(lv) = self.st.etch_num(p, Etq::LuckyNova)? {
            if lv != 0.0 {
                if p.godtree.is_some() {
                    if let Some(&ns) = p.spec_by_name.get("Nova_Slow") {
                        let tier = (lv as i64).min(p.specs[ns].n_tiers() as i64).max(1) as usize;
                        let c = self.cfg(fb, ns, tier);
                        bf.lucky_nova_chill = Some((c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0, c.req(K::amplifier)? as i64 + 1));
                    }
                }
                let mut orb = None;
                if self.st.special[0] > 0.0 {
                    match p.spec_by_name.get("Nova_Slow") {
                        Some(&ns) => {
                            let tier = (lv as i64).min(p.specs[ns].n_tiers() as i64).max(1) as usize;
                            let c = self.cfg(fb, ns, tier);
                            orb = Some(4.0 * c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0);
                        }
                        None => fb.lucky_nova_no_spec += 1,
                    }
                }
                bf.lucky_nova = Some(orb);
            }
        }
        if let Some(rl) = self.st.etch_num(p, Etq::RampageLucky)? {
            if bf.rampage.is_some() {
                bf.rampage_lucky += rl;
            }
        }
        if let Some((drain, _, _, _)) = bf.us {
            let deficit = drain - (self.mana_supply(&bf) - (demand - drain));
            bf.us_uptime = if deficit <= 0.0 { 1.0 } else { f64::min(1.0, d.mana_max / (deficit * t_kill)) };
        }
        Ok((bf, demand))
    }

    /// buffs.empower_toggle: Empower on the mana the damage abilities leave (before Rampage); its speed counts only at
    /// full uptime. Returns the mana left for Rampage.
    pub(crate) fn empower_toggle(bf: &mut Buffs, free: f64, eth: f64) -> f64 {
        let (speed, cost) = match bf.emp {
            Some(e) => e,
            None => return free,
        };
        let cost = cost * (1.0 - eth);
        let up = if cost > 0.0 { f64::min(1.0, free / cost) } else { 1.0 };
        bf.emp_paid = up * cost;
        bf.emp_speed = if up >= 1.0 - 1e-9 { speed } else { 0.0 };
        f64::max(0.0, free - up * cost)
    }

    /// buffs.survival_buffs
    pub(crate) fn surv_out(&self, bf: &Buffs) -> SurvOut {
        let mut o = SurvOut { msp: bf.msp, sneaky_dodge: bf.sneaky_dodge, totem_heal: bf.totem_heal, empower_speed: bf.emp_speed, sneak_share: bf.sneak_share, ..SurvOut::default() };
        if let Some((_, tick, pct, scalar)) = bf.us {
            let bar = self.snap_bar(bf.pd_bar);
            let mut u = bf.us_uptime;
            let mana = if bar > 0.0 {
                if !(self.p.godtree.is_some() && self.d.mana_regen / 20.0 >= tick * (1.0 - 1e-9)) {
                    u *= f64::max(0.0, 1.0 - tick / bar);
                }
                0.5 * bar.min(self.d.mana_max)
            } else {
                self.d.mana_max
            };
            o.us_u = u;
            o.us_pct = pct;
            o.us_budget = mana / scalar;
        }
        o
    }

    /// buffs.frozen_impact: the Blizzard talent's free Frost Nova on Hypothermic targets adds Frozen Orb Vulnerable
    /// procs, merged into the build's other Frozen Orb source.
    fn frozen_impact(&self, src: &mut crate::procs::Srcs, normal: f64) {
        let lvl = self.st.special[0];
        let t = match self.talent_tier(self.p.nt.blizzard) {
            Some(t) => t,
            None => return,
        };
        if lvl <= 0.0 || normal <= 0.0 {
            return;
        }
        let (pr, ticks) = match (t.pr, t.nd) {
            (Some(pr), Some(nd)) => (pr, nd),
            _ => return,
        };
        let mut miss = 1.0;
        for s in src.as_slice() {
            if s.kind == crate::procs::HYPOTHERMIA {
                miss *= 1.0 - s.up;
            }
        }
        let rate = pr * normal * (1.0 - miss);
        let u = crate::procs::proc_up(rate, 4.0 * ticks * (1.0 + self.d.effect_duration) / 20.0, 0.0);
        if u <= 0.0 {
            return;
        }
        for s in src.as_mut_slice() {
            if s.kind == crate::procs::VULNERABLE && s.add {
                s.up = 1.0 - (1.0 - s.up) * (1.0 - u);
                return;
            }
        }
        src.push(crate::procs::VULNERABLE, lvl as i64, u, true);
    }

    /// buffs.cast_debuffs: harmful effects a rotation ability puts on the boss at its share of the cooldown rate.
    pub(crate) fn cast_debuffs(&self, bf: &mut Buffs, spec: usize, c: &CfgM, share: f64) -> R<()> {
        if share <= 0.0 {
            return Ok(());
        }
        let d = self.d;
        match self.p.spec_cls[spec] {
            1 => {
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let i = c.req(K::totemDamageIntervalTicks)?;
                let up = f64::min(1.0, dur * share / (dur + self.cooldown_r(c.req(K::cooldownTicks)?, c.reset)));
                bf.db.push(crate::procs::MARK, 0, up * i / (i + 1.0), false);
            }
            2 => {
                bf.db.push(crate::procs::SLOWNESS, 0, f64::min(1.0, 9.0 * (1.0 + d.effect_duration) * share / self.cooldown_r(c.req(K::cooldownTicks)?, c.reset)), false);
            }
            3 => bf.db.push(crate::procs::CURSE, 0, 1.0, false),
            4 => bf.db.push(crate::procs::HYPOTHERMIA, 0, share.min(1.0), false),
            _ => {}
        }
        if self.p.godtree.is_some() {
            match self.p.spec_sp[spec] {
                Sp::Shard_Blizzard => {
                    let active = c.req(K::cloudDuration)? * (1.0 + d.effect_duration) / 20.0;
                    let slow = c.req(K::slowDuration)? * (1.0 + d.effect_duration) / 20.0;
                    let up = f64::min(1.0, share * (active + slow) / (active + self.cooldown_r(c.req(K::cooldownTicks)?, c.reset)));
                    bf.db.push(crate::procs::CHILLED, c.req(K::amplifier)? as i64 + 1, up, false);
                }
                Sp::Ice_Bolt_Base => {
                    let dur = c.req(K::durationTicks)? * (1.0 + d.effect_duration) / 20.0;
                    let casts = 1.0 + self.st.etch_num(self.p, Etq::IceBoltMulticast)?.unwrap_or(0.0);
                    let up = f64::min(1.0, share * casts * dur / self.cooldown_r(c.req(K::cooldownTicks)?, c.reset));
                    bf.db.push(crate::procs::CHILLED, c.req(K::amplifier)? as i64 + 1, up, false);
                }
                _ => {}
            }
        }
        if self.p.spec_sp[spec] == Sp::Shard_Blizzard {
            if let Some(lvl) = self.st.etch_num(self.p, Etq::BlizzardEffect)? {
                let ticks = self.st.etch_dur(self.p, Etq::BlizzardEffect).ok_or("Blizzard Effect etching has no duration")?;
                let active = c.req(K::cloudDuration)? * (1.0 + d.effect_duration) / 20.0;
                let period = active + self.cooldown_r(c.req(K::cooldownTicks)?, c.reset);
                bf.db.push(crate::procs::VULNERABLE, py_round(lvl) as i64 - 1, f64::min(1.0, share * (active + ticks / 20.0) / period), false);
            }
        }
        Ok(())
    }

    /// buffs.mana_etchings: Blocking Mana, the cloud poison stream and its Poison Mana.
    fn mana_etchings(&self, bf: &mut Buffs) -> R<()> {
        let p = self.p;
        let b = self.b;
        let d = self.d;
        if let Some(bm) = self.st.etch_num(p, Etq::BlockingMana)? {
            if bm != 0.0 && p.types[b.items[OFFHAND].ty()].name == "shield" && offhand_counts(b, p) {
                bf.etch_mana += bm * d.block * d.boss_hits_reaching;
            }
        }
        let (mut raw, mut hits) = (0.0f64, 0.0);
        let mut normal = None;
        for i in 0..self.st.n_clouds {
            let c = &p.clouds[self.st.clouds[i] as usize];
            if c.poison <= 0.0 {
                continue;
            }
            let nr = *normal.get_or_insert_with(|| if p.is_melee() || b.weave { self.swing_rate() } else { 0.0 });
            let dur = ((c.ticks * (1.0 + d.effect_duration) + 1e-9).floor() + 20.0) / 20.0;
            let u = crate::procs::proc_up(crate::procs::cloud_trigger(c.chance, d.cloud_chance) * nr, dur, 0.0);
            if u > 0.0 {
                raw = raw.max(c.poison);
                hits += u;
            }
        }
        if hits > 0.0 {
            bf.cloud_poison = Some((raw, hits));
            if let Some(pm) = self.st.etch_num(p, Etq::PoisonMana)? {
                if pm != 0.0 {
                    bf.etch_mana += pm * hits;
                }
            }
        }
        Ok(())
    }

    /// procs.finalize: Vulnerable and Sneaky Advantage from the recorded sources plus the event-driven ones, then the
    /// cloud poison stream (procs.cloud_poison).
    pub(crate) fn finalize(&self, bf: &mut Buffs, normal: f64, lucky: f64) -> R<()> {
        let p = self.p;
        let d = self.d;
        let lucky_vuln = bf.lucky_vuln;
        let lucky_nova = bf.lucky_nova;
        let lucky_nova_chill = bf.lucky_nova_chill;
        let src = &mut bf.db;
        if lucky > 0.0 {
            if let Some((lvl, dur)) = lucky_vuln {
                src.push(crate::procs::VULNERABLE, lvl - 1, crate::procs::proc_up(lucky, dur, 0.0), false);
            }
            if let Some(orb) = lucky_nova {
                src.push(crate::procs::HYPOTHERMIA, 0, crate::procs::proc_up(lucky, LUCKY_NOVA_HYPOTHERMIA_S, 0.0), false);
                if let Some((dur, lvl)) = lucky_nova_chill {
                    src.push(crate::procs::CHILLED, lvl, crate::procs::proc_up(lucky, dur, 0.0), false);
                }
                if let Some(dur) = orb {
                    match src.as_slice().iter().position(|s| s.kind == crate::procs::VULNERABLE && s.add) {
                        Some(i) => {
                            let s = &mut src.as_mut_slice()[i];
                            s.up = crate::procs::proc_up(lucky, dur, s.up);
                        }
                        None => src.push(crate::procs::VULNERABLE, self.st.special[0] as i64, crate::procs::proc_up(lucky, dur, 0.0), true),
                    }
                }
            }
        }
        crate::procs::gear_sources(p, self.st, d.hexing, d.burning, d.cloud_chance, normal, src);
        self.frozen_impact(src, normal);
        let additive = p.knobs.frozen_orb_additive;
        if !additive {
            for s in src.as_mut_slice() {
                if s.add {
                    s.amp -= 1;
                    s.add = false;
                }
            }
        }
        let (vm, count) = crate::procs::combine(src.as_slice(), !self.b.bug(Bug::VulnOff1), additive);
        let mut up = [0.0f64; crate::procs::N_KINDS];
        for s in src.as_slice() {
            let k = s.kind as usize;
            up[k] = 1.0 - (1.0 - up[k]) * (1.0 - s.up);
        }
        let mut miss = 1.0;
        for u in up {
            miss *= 1.0 - u;
        }
        let has_vuln = src.as_slice().iter().any(|s| s.kind == crate::procs::VULNERABLE);
        if has_vuln {
            bf.vulnerable = vm;
        }
        if let Some(fx) = self.st.gfx {
            if fx.sneaky != 0.0 {
                bf.sneaky = 1.0 + fx.sneaky * count;
            }
        }
        bf.debuffed = 1.0 - miss;
        if let Some((raw, hits)) = bf.cloud_poison {
            let per = self.hit(raw, 0.0, Hc { ap: true, ..Hc::default() }, 0.0, bf)?;
            bf.cloud_dps = per * hits;
        }
        Ok(())
    }

    /// buffs.unaffordable: an instant cast costing more than the whole bar is never cast.
    pub(crate) fn unaffordable(&self, c: &CfgM) -> bool {
        let mm = self.d.mana_max;
        c.or(K::manaCost, 0.0) > mm + 1e-9 * mm.max(1.0)
    }

    /// buffs.low_mana_state: share of the fight below 20% mana from the mana split.
    pub(crate) fn low_mana_state(&self, limited: bool, pd_rate: f64, inflow: f64) -> f64 {
        let k = &self.p.knobs;
        let mm = self.d.mana_max;
        if !k.mana_state_heuristic {
            return k.uptime_low_mana;
        }
        if limited {
            return 1.0;
        }
        if pd_rate > 0.0 {
            if inflow <= 1e-9 * mm.max(1.0) {
                return 1.0;
            }
            return f64::min(1.0, pd_rate * 0.2 * mm / inflow);
        }
        0.0
    }

    /// buffs.nova_pd_rate: sneaking Starforge novas per second (each one dumps the bar).
    pub(crate) fn nova_pd_rate(&self, aps: f64) -> f64 {
        let st = self.st;
        if !self.st.gfx.map_or(false, |fx| fx.power_dump != 0.0) || self.b.pd & PD_NOVA == 0 {
            return 0.0;
        }
        if st.nova_hits <= 0.0 || st.nova_pct <= 0.0 || aps <= 0.0 {
            return 0.0;
        }
        aps / st.nova_hits
    }

    /// buffs.snap_bar
    pub(crate) fn snap_bar(&self, bar: f64) -> f64 {
        if bar <= 1e-9 * self.d.mana_max.max(1.0) { 0.0 } else { bar }
    }

    pub(crate) fn mana_supply(&self, bf: &Buffs) -> f64 {
        self.d.mana_regen + bf.mana_regen_add * self.d.mana_regen_vt + bf.etch_mana
    }

    /// buffs.mana_steal_pct: max mana fraction restored per lucky hit.
    pub(crate) fn mana_steal_pct(&self) -> R<f64> {
        let pct = match self.talent_tier(self.p.nt.mana_steal) {
            Some(t) => t.mmp.ok_or_else(|| String::from("KeyError maxManaPercentage"))?,
            None => 0.0,
        };
        Ok(if self.st.gfx.map_or(false, |fx| fx.luckiest != 0.0) { pct * 2.0 } else { pct })
    }

    /// buffs.fury_for.
    pub(crate) fn fury_for(&self, h: f64) -> Option<(f64, f64, f64)> {
        match (&self.p.godtree, self.st.gfx) {
            (Some(g), Some(fx)) if fx.fury => Some(g.fury_at(h)),
            _ => None,
        }
    }

    /// buffs.starved_free.
    pub(crate) fn starved_free(&self, free: f64, short: f64) -> f64 {
        match self.st.gfx {
            Some(fx) if fx.mana_starved != 0.0 && short > free => free + f64::min(fx.mana_starved * self.d.mana_regen, short - free),
            _ => free,
        }
    }

    /// buffs.rampage_uptime: returns the uptime and scales bf.rampage.
    pub(crate) fn rampage_uptime(bf: &mut Buffs, free: f64, fury: Option<(f64, f64, f64)>) -> f64 {
        let mut up = 1.0;
        if let Some(r) = bf.rampage {
            let r = match fury { Some(f) => r * f.0 + f.1, None => r };
            let ramp_cost = bf.rampage_cost;
            up = if ramp_cost > 0.0 { f64::min(1.0, free / ramp_cost) } else { 1.0 };
            if let Some(fx) = bf.rampage_fixed {
                up = up.min(fx);
            }
            bf.rampage = Some(r * up);
        }
        up
    }

    /// buffs.ethereal_chance
    pub(crate) fn ethereal(&self) -> f64 {
        self.talent_tier(self.p.nt.ethereal).and_then(|t| t.ch).unwrap_or(0.0)
    }
}

/// godfx.concentrate_ladder: (average Empower amplifier while active, active share) of one chain; the last result is
/// kept per thread and reused for bit-identical inputs (consecutive evaluations rarely move them).
pub(crate) fn concentrate_ladder(base_amp: f64, chance: f64, dur: f64, period: f64, k: f64, steps: usize) -> (f64, f64) {
    thread_local! {
        static LAST: std::cell::Cell<Option<([u64; 6], (f64, f64))>> = const { std::cell::Cell::new(None) };
    }
    let key = [base_amp.to_bits(), chance.to_bits(), dur.to_bits(), period.to_bits(), k.to_bits(), steps as u64];
    if let Some((kk, v)) = LAST.with(|c| c.get()) {
        if kk == key {
            return v;
        }
    }
    let v = concentrate_ladder_calc(base_amp, chance, dur, period, k, steps);
    LAST.with(|c| c.set(Some((key, v))));
    v
}

fn concentrate_ladder_calc(base_amp: f64, chance: f64, dur: f64, period: f64, k: f64, steps: usize) -> (f64, f64) {
    const MAX_TICKS: f64 = 600.0;
    if k <= 0.0 || period <= 0.0 || dur <= 0.0 {
        return (0.0, 0.0);
    }
    let dt = period / k;
    let mut amp = base_amp;
    let mut rem = MAX_TICKS.min(dur);
    let (mut area, mut t_act) = (0.0, 0.0);
    for _ in 1..steps {
        if rem <= dt * (1.0 + 1e-9) {
            break;
        }
        area += amp * dt;
        t_act += dt;
        rem -= dt;
        amp += f64::min(chance / (1.0 + 0.1 * amp), 2.0);
        rem = MAX_TICKS.min(rem + f64::max(1.0, dur * (1.0 - rem / MAX_TICKS)));
    }
    area += amp * rem;
    t_act += rem;
    let span = (t_act / dt - 1e-9).ceil() * dt;
    (area / t_act, t_act / span)
}
