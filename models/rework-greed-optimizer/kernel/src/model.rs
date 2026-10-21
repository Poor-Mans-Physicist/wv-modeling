//! Per-ability formulas (model/abilities.py): one spec's (dps, pack dps, mana/s, hit rate) before mana limiting.

use crate::build::*;
use crate::problem::Sp;
use crate::problem::*;
use crate::buffs::Buffs;
use crate::hit::Hc;
use crate::eval::{dv, Ev, CfgM, PLAIN_MAX_HITS_PER_S, R, V4};

impl<'a> Ev<'a> {
    pub(crate) fn pack_targets(&self, radius: f64) -> f64 {
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

    /// abilities.ap_hit: raw = AP x pct x Spectral + add; rad replaces the config radius for pack targets; free hits
    /// neither set nor respect i-frames (no clip, no repeat-hit cap).
    fn ap_instant(&self, c: &CfgM, bf: &Buffs, pct: f64, radius: bool, aoe: bool, echo: bool, spectral: bool, add: f64, rad: Option<f64>, free: bool) -> R<(V4, V4, f64, f64)> {
        let spm = if spectral { 1.0 + self.p.knobs.spectral_ap_per_stack * bf.spectral_s } else { 1.0 };
        let raw = self.d.ability_power * pct * 1.0 * spm + add;
        let hc = Hc { ap: true, aoe, echo_ok: echo, clip: !free, ..Hc::default() };
        let per = self.hit(raw, 0.0, hc, self.d.lucky_hit_chance, bf)?;
        let cd = self.cd_s(c);
        let rate = if free { 1.0 / cd } else { plain_rate(1.0 / cd) };
        let targets = match rad {
            Some(r) => self.pack_targets(r),
            None => if radius { self.pack_targets(c.or(K::radius, 0.0)) } else { 1.0 },
        };
        Ok(finish(per, rate, targets, c.or(K::manaCost, 0.0) / cd))
    }

    /// abilities.greedball: (flat add, explosion radius) of the Greedball etching on Fireball.
    fn greedball(&self) -> R<(f64, Option<f64>)> {
        let p = self.p;
        Ok(match self.st.etch_num(p, Etq::Greedball)? {
            Some(v) if v != 0.0 => ((self.st.g(p, A::item_quantity) + self.st.g(p, A::item_rarity)) * 100.0 * v, Some(GREEDBALL_RADIUS)),
            _ => (0.0, None),
        })
    }

    /// abilities.steel_inferno: (boss x(1 + e B), pack x(1 + m B), radius multiplier) with Steel Inferno worn.
    fn steel(&self) -> Option<(f64, f64, f64)> {
        let b = self.st.steel_b;
        if b <= 0.0 {
            return None;
        }
        let k = &self.p.knobs;
        Some((1.0 + k.steel_inferno_boss_efficiency * b, 1.0 + k.steel_inferno_pack_mean * b, self.st.steel_r))
    }

    fn pair(c: &CfgM) -> R<f64> {
        Ok(0.5 * (c.req(K::percentAbilityPowerDealtMin)? + c.req(K::percentAbilityPowerDealtMax)?))
    }

    /// One ability spec's (dps, pack_dps, mana_per_s) before mana limiting.
    pub(crate) fn model(&self, fb: &mut Fallbacks, main: usize, c: &CfgM, bf: &Buffs) -> R<(V4, V4, f64, f64)> {
        let p = self.p;
        let d = self.d;
        let b = self.b;
        let lk = d.lucky_hit_chance;
        let ap = d.ability_power;
        let ad = d.attack_damage;
        let name = p.specs[main].id.as_str();
        let sp = p.spec_sp[main];
        let hit = |flat: f64, baked: f64, hc: Hc| self.hit(flat, baked, hc, lk, bf);
        let ap_aoe = Hc { ap: true, aoe: true, ..Hc::default() };
        let ap_only = Hc { ap: true, ..Hc::default() };
        let ap_aoe_c = Hc { clip: true, ..ap_aoe };
        let ap_only_c = Hc { clip: true, ..ap_only };
        let aoe_c = Hc { aoe: true, clip: true, ..Hc::default() };
        Ok(match sp {
            Sp::Fireball_Base => {
                let (add, rad) = self.greedball()?;
                let st = self.steel();
                let rad = match st {
                    Some((_, _, rm)) => Some(rad.unwrap_or(c.or(K::radius, 0.0)) * rm),
                    None => rad,
                };
                let mut o = self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, true, true, false, false, add, rad, p.spec_free[main])?;
                if let Some((bm, pm, _)) = st {
                    o.1 = o.1 * pm;
                    o.0 = o.0 * bm;
                }
                let pr = self.st.special[1];
                if pr > 0.0 {
                    o.0 *= 1.0 + pr;
                    o.1 *= 1.0 + pr;
                }
                o
            }
            Sp::Fireball_Fireshot => self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, false, false, true, true, 0.0, None, false)?,
            Sp::Arcane_Rail => self.ap_instant(c, bf, Self::pair(c)?, false, false, true, true, 0.0, None, false)?,
            Sp::Nova_Base => {
                let mut o = self.ap_instant(c, bf, c.req(K::percentAbilityPowerDealt)?, true, true, false, false, 0.0, None, false)?;
                if let Some(v) = self.st.etch_num(p, Etq::NovaRecast)? {
                    let m = 1.0 + f64::min(1.0, v / 100.0);
                    o.0 *= m;
                    o.1 *= m;
                }
                if let Some(low) = b.etch_max(p, Etq::NovaLowMana) {
                    if low != 0.0 && bf.u_half_mana > 0.0 {
                        let m = 1.0 + low * bf.u_half_mana;
                        o.0 *= m;
                        o.1 *= m;
                    }
                }
                o
            }
            Sp::Nova_Dot | Sp::Toxic_Grenade => {
                let total = ap * c.req(K::percentAbilityPowerDealt)?;
                let (_, dticks, period) = self.dot_dp(sp, c)?;
                let hits = (dticks / 20.0 + 1e-9).floor();
                let per = hit(total * 20.0 / dticks.max(1.0), 0.0, Hc { dot: true, ..ap_aoe })?;
                let o = finish(per, hits / period, self.pack_targets(c.or(K::radius, 0.0)), c.or(K::manaCost, 0.0) / period);
                if let Some(pm) = self.st.etch_num(p, Etq::PoisonMana)? {
                    bf.mana_back.set(pm * o.3);
                }
                o
            }
            Sp::Smite_Base | Sp::Smite_Archon => {
                let base = sp == Sp::Smite_Base;
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, Hc { ap: true, echo_ok: base, ..Hc::default() })?;
                let bolts = 20.0 / c.req(K::intervalTicks)?.max(1.0);
                let n = p.knobs.smite_targets_in_range.max(1.0);
                let mut rate = bolts / n;
                let ev = self.st.etch_num(p, Etq::SmiteEcho)?;
                if let Some(ev) = ev {
                    rate *= 1.0 + ev;
                    bf.echo_extra.set(ev);
                }
                if self.st.etch_num(p, Etq::StrikeDelay)?.map_or(false, |v| v != 0.0) {
                    rate = bolts / n * (2.0 + ev.unwrap_or(0.0));
                    bf.echo_orig.set(2.0);
                }
                let mana = c.or(K::manaCostPerSecond, 0.0) + c.or(K::additionalManaPerBolt, 0.0) * bolts;
                finish(per, rate, 1.0, mana)
            }
            Sp::Smite_Blast_Wave => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, ap_aoe_c)?;
                let rate = plain_rate(20.0 / c.req(K::intervalTicks)?.max(1.0));
                finish(per, rate, self.pack_targets(c.req(K::radius)?), c.or(K::manaCostPerSecond, 0.0))
            }
            Sp::Arcane_Base => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_only_c)?;
                let rate = plain_rate(20.0);
                let targets = match self.st.etch_num(p, Etq::ArcanePierce)? {
                    Some(pierce) => 1.0 + (p.knobs.pack_size.min(pierce) - 1.0) * 0.5,
                    None => 1.0,
                };
                finish(per, rate, targets, c.or(K::manaCostPerSecond, 0.0))
            }
            Sp::Arcane_Prism => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_aoe_c)?;
                let dt = c.req(K::durationTicks)?;
                let pulses = (dt / c.req(K::intervalTicks)?.max(1.0)).floor().max(1.0);
                let period = self.cd_s(c).max(dt / 20.0);
                let rate = plain_rate(pulses / period);
                let targets = c.or(K::maxTargets, 8.0).min(self.pack_targets(c.req(K::radius)?));
                finish(per, rate, targets, c.or(K::manaCost, 0.0) / period)
            }
            Sp::Chain_Lightning_Base => {
                let mut o = self.ap_instant(c, bf, Self::pair(c)?, false, true, false, false, 0.0, None, false)?;
                let m = 1.0 + self.lightning_bonus()?;
                o.0 *= m;
                let r = c.or(K::chainRange, 0.0) * d.aoe_multiplier;
                let x = r / p.knobs.pack_radius;
                let targets = 1.0 + (p.knobs.pack_size - 1.0) * f64::min(1.0, x * x);
                if self.st.etch_num(p, Etq::StrikeDelay)?.map_or(false, |v| v != 0.0) {
                    let rate2 = plain_rate(2.0 / self.cd_s(c));
                    let mm = rate2 / o.3;
                    let single = o.0;
                    let dps = o.0 * mm;
                    let mut pack = single * targets;
                    for l in 0..4 {
                        pack.0[l] = pack.0[l] + dps.0[l] - single.0[l];
                    }
                    (dps, pack, o.2, rate2)
                } else {
                    (o.0, o.0 * targets, o.2, o.3)
                }
            }
            Sp::Chain_Lightning_Orbs => {
                let o = self.ap_instant(c, bf, Self::pair(c)?, false, true, false, false, 0.0, None, false)?;
                let mut m = 1.0 + self.lightning_bonus()?;
                if let Some(v) = self.st.etch_num(p, Etq::OrbTriple)? {
                    m *= 3.0 * (1.0 - v);
                }
                let dps = o.0 * m;
                (dps, dps * 3.0, o.2, o.3)
            }
            Sp::Chain_Lightning_Charged_Bolts => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + c.req(K::percentAbilityPowerDealtMax)?) * (1.0 + self.lightning_bonus()?);
                let n = 0.5 * c.req(K::boltCount)?;
                let (f, r) = (c.req(K::fullDamageHitsPerTarget)?, c.req(K::repeatHitDamageMultiplier)?);
                let eff = n.min(f) + r * f64::max(0.0, n - f);
                let per = hit(ap * pct, 0.0, ap_aoe)?;
                let cd = self.cd_s(c);
                finish(per, eff / cd, 3.0, c.req(K::manaCost)? / cd)
            }
            Sp::Storm_Arrow_Base => {
                let pct = 0.5 * (c.req(K::percentAbilityPowerDealtMin)? + self.lightning_bonus()? + c.req(K::percentAbilityPowerDealtMax)?);
                let per = hit(ap * pct, 0.0, ap_only)?;
                let dd = c.req(K::cloudDuration)? * (1.0 + d.effect_duration);
                let shots = (dv(dd + 1.0, c.req(K::intervalTicks)? + 1.0)? - 1e-9).ceil();
                let period = dd / 20.0 + self.cd_s(c);
                let occ = if self.st.storm_tracking { 1.0 } else { p.knobs.storm_boss_occupancy };
                finish(per, shots / period * occ, 1.0, c.req(K::manaCost)? / period)
            }
            Sp::Ice_Bolt_Base => {
                let lucky_bolt = self.st.etch(p, Etq::IceBoltLucky).is_some();
                let m_reg = self.registry_c(bf)?;
                let adq = ad + if lucky_bolt { self.spectral_ad(bf) } else { 0.0 };
                let baked = adq * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * if lucky_bolt { 1.0 } else { m_reg };
                let hc = Hc { aoe: !lucky_bolt, di_baked: true, ..Hc::default() };
                let per = hit(c.req(K::damagePerBolt)?, baked, hc)?;
                let cd = self.cd_s(c);
                let mut casts = 1.0;
                if let Some(v) = self.st.etch_num(p, Etq::IceBoltMulticast)? {
                    casts += v;
                }
                finish(per, casts / cd, 1.0, c.req(K::manaCost)? * casts / cd)
            }
            Sp::Shard_Blizzard => {
                let m_reg = self.registry_c(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let mut per = hit(c.req(K::damagePerShard)?, baked, Hc { aoe: true, di_baked: true, clip: true, ..Hc::default() })?;
                if let Some(v) = self.st.etch_num(p, Etq::BlizzardDamage)? {
                    if v != 0.0 {
                        let extra = f64::max(0.0, ap * v - (c.req(K::damagePerShard)? + baked));
                        if extra > 0.0 {
                            per = per + hit(extra, 0.0, ap_aoe)?;
                        }
                    }
                }
                let dd = c.req(K::cloudDuration)? * (1.0 + d.effect_duration);
                let shots = (dv(dd + 1.0, c.req(K::intervalTicks)? + 1.0)? - 1e-9).ceil();
                let active = dd / 20.0;
                let period = active + self.cd_s(c);
                let mut rate = plain_rate(dv(shots, active)?) * active / period;
                if p.knobs.storm_boss_occupancy != 1.0 {
                    rate *= p.knobs.storm_boss_occupancy;
                }
                finish(per, rate, self.pack_targets(c.req(K::radius)?) * 0.5, c.req(K::manaCost)? / period)
            }
            Sp::Javelin_Base | Sp::Javelin_Piercing | Sp::Javelin_Scatter => {
                let mut per = hit(ad * c.req(K::percentAttackDamageDealt)?, 0.0, Hc { clip: true, ..Hc::default() })?;
                if self.st.javelin_implode && !c.has(K::piercing) && !c.has(K::numberOfJavelins) {
                    if let Some(isp) = p.implode_spec {
                        let learned = b.abil(isp);
                        let pd = self.st.gfx.map_or(false, |fx| fx.power_dump != 0.0) && self.b.pd >> main & 1 == 1 && bf.pd_always;
                        let mana = if pd { f64::min(d.mana_max, bf.pd_bar) } else { f64::max(0.0, d.mana_max - c.req(K::manaCost)?) };
                        if learned > 0 && mana > 0.0 {
                            let (t, _) = self.effective_tier(isp, learned);
                            let ic = self.cfg(fb, isp, t);
                            let boost = 1.0 + self.st.special[3];
                            per = per + hit(mana * ic.req(K::percentManaDealt)? * boost, 0.0, Hc { aoe: true, ..Hc::default() })?;
                        }
                    }
                }
                let cd = self.cd_s(c);
                let mut targets = 1.0 + f64::min(p.knobs.pack_size - 1.0, c.or(K::piercing, 0.0)) * 0.5;
                if let Some(nj) = c.get(K::numberOfJavelins) {
                    if nj != 0.0 {
                        targets += nj * 0.3;
                    }
                }
                if let Some(v) = self.st.etch_num(p, Etq::ExtraPiercingJavelin)? {
                    if c.has(K::piercing) && !c.has(K::numberOfJavelins) {
                        targets += 2.0 * v;
                    }
                }
                let mut hits = 1.0;
                if self.st.javelin_multi_hit {
                    if let Some(nj) = c.get(K::numberOfJavelins) {
                        if nj != 0.0 {
                            let split = self.st.g(p, A::splitting_javelins);
                            hits = if split >= 2.0 - 1e-9 { split } else { nj };
                        }
                    }
                }
                let o = finish(per, hits / cd, targets, c.req(K::manaCost)? / cd);
                if let Some(sm) = self.st.etch_num(p, Etq::ScatterMana)? {
                    if sm != 0.0 && c.get(K::numberOfJavelins).map_or(false, |nj| nj != 0.0) {
                        bf.mana_back.set(sm * o.3);
                    }
                }
                o
            }
            Sp::Earthquake_Base | Sp::Earthquake_Singularity | Sp::Earthquake_Tremor => {
                let m_reg = self.registry_c(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let mut per = hit(0.0, baked, ap_aoe_c)?;
                let mut shocks = c.req(K::shockCount)?;
                let period = shocks * c.req(K::shockIntervalTicks)? / 20.0 + self.cd_s(c);
                if self.st.etch_num(p, Etq::QuakeRecast)?.map_or(false, |v| v != 0.0) {
                    let p2 = hit(0.0, baked, ap_aoe)?;
                    let w = 1.0 / (shocks + 1.0);
                    per = (per * shocks + p2) * w;
                    shocks += 1.0;
                }
                finish(per, shocks / period, self.pack_targets(c.req(K::radius)?), c.req(K::manaCost)? / period)
            }
            Sp::Grenade_Base | Sp::Grenade_Sticky => {
                let m_reg = self.registry_c(bf)?;
                let baked = ad * (1.0 + d.damage_increase) * c.req(K::percentAttackDamageDealt)? * m_reg;
                let per = hit(0.0, baked, ap_aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), c.req(K::manaCost)? / cd)
            }
            Sp::Necromancy_Base | Sp::Necromancy_Archer => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)?, 0.0, ap_only_c)?;
                let rate = c.req(K::summonCap)?.min(PLAIN_MAX_HITS_PER_S);
                finish(per, rate, 1.5, 0.0)
            }
            Sp::Necromancy_Golem => {
                let per = hit(ap * c.req(K::percentAbilityPowerDealt)? * c.req(K::summonCap)?, 0.0, ap_only_c)?;
                let rate = plain_rate(20.0 / c.req(K::attackIntervalTicks)?.max(1.0));
                let targets = 1.0 + c.req(K::aoeDamageMultiplier)? * (self.pack_targets(c.req(K::aoeRadius)?) - 1.0);
                finish(per, rate, targets, 0.0)
            }
            Sp::Dash_Damage => {
                let m_reg = self.registry_c(bf)?;
                let per = hit(ad * c.req(K::attackDamagePercentPerDash)? * m_reg, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, plain_rate(1.0 / cd), 2.0, c.req(K::manaCost)? / cd)
            }
            Sp::Fangs_Base | Sp::Fangs_Maw => {
                let maw = sp == Sp::Fangs_Maw;
                let mut a = (ad + self.spectral_ad(bf)) * bf.empower;
                if !maw && self.st.etch(p, Etq::RavenousFangs).is_some() {
                    a *= 0.25;
                }
                let raw = a * c.req(K::damageMultiplier)? + c.req(K::baseDamage)?;
                let per = hit(raw, 0.0, Hc { normal: true, lucky: true, rampage: true, ..Hc::default() })?;
                let hits = self.fang_hits(fb, main, c)?;
                let cd = self.cd_s(c);
                let targets = if maw { 1.0 } else { self.pack_targets(c.req(K::radius)?) / 2.0 };
                finish(per, hits / cd, targets.max(1.0), c.req(K::manaCost)? / cd)
            }
            Sp::Mana_Shield_Implode => {
                let cd = self.cd_s(c);
                let mana = d.mana_max.min(d.mana_regen * cd);
                let per = hit(mana * c.req(K::percentManaDealt)?, 0.0, aoe_c)? * self.implode_pd(mana);
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), 0.0)
            }
            Sp::Implode_Life_Tap => {
                let extra = self.st.etch_num(p, Etq::LifeTapExtra)?.unwrap_or(0.0);
                let raw = d.health * c.req(K::percentHealthDrained)? * (c.req(K::damagePerHealth)? + extra);
                let per = hit(raw, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, 1.0 / cd, self.pack_targets(c.req(K::radius)?), 0.0)
            }
            Sp::Shield_Bash | Sp::Shield_Bash_Earthshatter | Sp::Shield_Bash_Battering_Ram => {
                let m_reg = self.registry_c(bf)?;
                let ev = self.st.etch_num(p, Etq::ShieldBashDamage)?.unwrap_or(0.0);
                let mut raw = ad * (1.0 + d.block * c.req(K::blockChanceDamageScalar)?) + ad * ev;
                if sp == Sp::Shield_Bash_Battering_Ram {
                    raw += self.thorns_flat(bf) * c.or(K::thornsDamageScalar, 0.0);
                }
                let per = hit(raw * m_reg, 0.0, aoe_c)?;
                let cd = self.cd_s(c);
                finish(per, plain_rate(1.0 / cd), 2.5, c.req(K::manaCost)? / cd)
            }
            Sp::Fireball_Volley => {
                let k = &p.knobs;
                let mit = self.st.etch_num(p, Etq::Mitosis)?;
                let pen = if mit.is_some() { 0.5 } else { 1.0 };
                let free = p.spec_free[main];
                let raw = ap * c.req(K::percentAbilityPowerDealt)? * pen;
                let per = hit(raw, 0.0, if free { ap_aoe } else { ap_aoe_c })?;
                let cd = self.cd_s(c);
                let bb = k.volley_bounces;
                let bounce = mit.is_some() || b.bug(Bug::VolleyBounceExplode);
                let mut explosions = if bounce { bb + 1.0 } else { 1.0 };
                if let Some(m) = mit {
                    explosions += m * bb;
                }
                let rate = if free { explosions / cd } else { plain_rate(explosions.min(k.volley_iframe_hits_per_cast) / cd) };
                let st = self.steel();
                let mut radius = if self.st.etch_num(p, Etq::Greedball)?.map_or(false, |v| v != 0.0) { GREEDBALL_RADIUS } else { c.req(K::radius)? };
                if let Some((_, _, rm)) = st {
                    radius *= rm;
                }
                let targets = self.pack_targets(radius);
                let mut pack_mult = 1.0;
                if mit.is_some() && !free {
                    pack_mult = k.mitosis_multiplier / (pen * f64::max(rate * cd, 1e-9));
                }
                let o = finish(per, rate, targets, c.req(K::manaCost)? / cd);
                let mut o = (o.0, o.1 * pack_mult, o.2, o.3);
                if let Some((bm, pm, _)) = st {
                    o.1 = o.1 * pm;
                    o.0 = o.0 * bm;
                }
                o
            }
            Sp::Totem_Mob_Damage => {
                let ad_etch = self.st.etch(p, Etq::TotemMobAd).is_some();
                let m_reg = self.registry_c(bf)?;
                let base = ap + if ad_etch { ad * m_reg } else { 0.0 };
                let raw = base * c.req(K::totemPercentDamagePerInterval)?;
                let hc = if ad_etch { Hc { echo_ok: true, clip: true, ..Hc::default() } } else { Hc { ap: true, echo_ok: true, clip: true, ..Hc::default() } };
                let per = hit(raw, 0.0, hc)?;
                let dur = c.req(K::totemDurationTicks)? * (1.0 + d.effect_duration) / 20.0;
                let period = dur + self.cd_s(c);
                let hits = dv(dur, c.req(K::totemDamageIntervalTicks)? / 20.0)?;
                finish(per, hits / period, self.pack_targets(c.req(K::totemEffectRadius)?), c.req(K::manaCost)? / period)
            }
            Sp::Shell_Porcupine => {
                let old = bf.porcupine.replace(c.req(K::additionalThornsDamagePercent)?);
                let hc = Hc { lucky: self.st.lucky_thorns, echo_ok: true, double_ok: true, clip: true, ..Hc::default() };
                let per = self.hit(self.thorns_reflect(bf), 0.0, hc, lk, bf);
                bf.porcupine.set(old);
                let per = per?;
                finish(per, d.boss_hits_reaching, 1.0, c.req(K::manaCostPerSecond)?)
            }
            _ => return Err(format!("no kernel model for {}", name)),
        })
    }
}

impl<'a> Ev<'a> {
    /// Poison Nova / Toxic Grenade: (duration s, DoT ticks floor(20 x duration), cast period max(cooldown, duration)).
    pub(crate) fn dot_dp(&self, sp: Sp, c: &CfgM) -> R<(f64, f64, f64)> {
        let d = self.d;
        let dur = if sp == Sp::Toxic_Grenade {
            f64::max(1.0, c.req(K::poisonTicks)? * (1.0 + d.effect_duration) / 20.0)
        } else {
            c.req(K::durationSeconds)? * (1.0 + d.effect_duration)
        };
        Ok((dur, (20.0 * dur + 1e-9).floor(), self.cd_s(c).max(dur)))
    }

    /// abilities.dot_slot: with Poison Nova and Toxic Grenade both cast, each keeps min(1, 1 / (a x D)) of its ticks,
    /// a = sum(share / period) over the two; 1.0 for every other source.
    pub(crate) fn dot_slot<I: Iterator<Item = (usize, CfgM<'a>, f64)>>(&self, it: I) -> R<[f64; MAX_ROT]> {
        let mut f = [1.0f64; MAX_ROT];
        let mut dd = [0.0f64; MAX_ROT];
        let (mut a, mut n) = (0.0, 0);
        for (j, (spec, c, share)) in it.enumerate() {
            let sp = self.p.spec_sp[spec];
            if share > 0.0 && (sp == Sp::Nova_Dot || sp == Sp::Toxic_Grenade) {
                let (_, dticks, period) = self.dot_dp(sp, &c)?;
                a += share / period;
                dd[j] = dticks / 20.0;
                n += 1;
            }
        }
        if n >= 2 {
            for j in 0..MAX_ROT {
                if dd[j] > 0.0 {
                    f[j] = f64::min(1.0, 1.0 / (a * dd[j]));
                }
            }
        }
        Ok(f)
    }
}

/// abilities.GREEDBALL_RADIUS
const GREEDBALL_RADIUS: f64 = 3.0;

/// Main ability plus at most 16 secondaries (families.rs secs).
pub(crate) const MAX_ROT: usize = 17;

fn plain_rate(rate: f64) -> f64 {
    if rate > PLAIN_MAX_HITS_PER_S { PLAIN_MAX_HITS_PER_S } else { rate }
}

fn finish(per: V4, rate: f64, targets: f64, mana: f64) -> (V4, V4, f64, f64) {
    let dps = per * rate;
    (dps, dps * targets, mana, rate)
}
