//! The ability rotation (model/rotation.py): mana sharing, i-frame weaving, the echo probe, Power Dump and
//! Spectral Strike.

use crate::build::*;
use crate::problem::*;
use crate::buffs::Buffs;
use crate::hit::Hc;
use crate::eval::{Ev, CfgM, FamOut, R, V4};

/// One damage ability in the rotation (rotation.py `src` entries).
#[derive(Clone, Copy)]
struct RotSrc<'c> {
    spec: usize,
    i: usize,
    c: CfgM<'c>,
    leftover: bool,
    /// Costs more than the whole bar (buffs.unaffordable): never cast.
    blocked: bool,
    /// Implode as the main ability: asks for the whole regeneration, damage per cast from its share of it.
    implode_main: bool,
    rank: f64,
    raw: f64,
    need: f64,
    rate: f64,
    k: f64,
    share: f64,
    /// Power Dump: cast while sneaking (and eligible), held channel, sneaking instant cast in this evaluation.
    pd: bool,
    hold: bool,
    pd_inst: bool,
    /// Fast path: the final evaluation (dps, pack, rate, weave drop, echo probe) already made while probing.
    done: Option<(V4, V4, f64, f64, Option<(f64, V4, f64, f64, f64)>)>,
}

impl<'a> Ev<'a> {
    /// Arms the echo probe (damage._hit) for the next evaluation.
    pub(crate) fn echo_arm(bf: &Buffs) {
        bf.echo_rec.set(true);
        bf.echo_s.set(f64::NAN);
        bf.echo_extra.set(0.0);
        bf.echo_orig.set(1.0);
        bf.echo_cu.set(1.0);
        bf.echo_cf.set(0.0);
        bf.echo_cr.set(1.0);
    }

    /// rotation.window_share: chance a hit lands inside one of the other streams' i-frame windows.
    fn window_share(src: &[(f64, f64)], window: f64) -> f64 {
        let mut miss = 1.0;
        for &(rate, _) in src {
            miss *= 1.0 - f64::min(1.0, window * rate);
        }
        1.0 - miss
    }

    /// Sets the clip context from the other streams.
    fn set_weave(bf: &mut Buffs, src: &[(f64, f64)], window: f64) {
        assert!(src.len() <= 24, "too many hit streams");
        bf.weave_src.clear();
        for &x in src {
            bf.weave_src.push(x);
        }
        bf.weave_f = Self::window_share(src, window);
        bf.weave_drop.set(0.0);
    }

    /// The armed probe's result: (stored damage, pulse value, unclipped share, surviving clipped share, kept raw
    /// fraction of the surviving clipped hits), or None when no eligible hit ran.
    pub(crate) fn echo_take(bf: &Buffs) -> Option<(f64, V4, f64, f64, f64)> {
        bf.echo_rec.set(false);
        let s = bf.echo_s.replace(f64::NAN);
        if s.is_nan() { None } else { Some((s, bf.echo_r.get(), bf.echo_cu.get(), bf.echo_cf.get(), bf.echo_cr.get())) }
    }

    /// rotation.compute: the main ability plus learned secondaries sharing one mana pool (see model/rotation.py).
    /// `lucky_only` (Arcane Cascade trials): the same mana split, casts and i-frame drops, but no debuffs, damage or
    /// echoes; only lucky_hits and lucky_ext are filled.
    pub(crate) fn rotation(&self, fb: &mut Fallbacks, main: usize, secs: &[usize], lucky_only: bool) -> R<FamOut> {
        let (mut bf, demand) = self.buffs(fb)?;
        let eth = self.ethereal();
        let demand = demand * (1.0 - eth);
        bf.rampage_cost *= 1.0 - eth;
        let pdx = self.st.gfx.map_or(0.0, |fx| fx.power_dump);
        let p = self.p;
        let d = self.d;
        let weave = self.b.weave;
        let aps0 = if weave { self.swing_rate() } else { 0.0 };
        let steal_m = if weave { aps0 * self.melee_lucky(&bf, aps0) * d.mana_max * self.mana_steal_pct()? } else { 0.0 };
        let base = self.mana_supply(&bf) + steal_m;
        let ramp_cost = bf.rampage_cost;
        let implode = p.implode_spec;
        let fury_on = self.st.gfx.map_or(false, |fx| fx.fury);
        let fast = secs.is_empty() && bf.rampage.is_none() && !fury_on && !(bf.lucky_vuln.is_some() && p.lucky_spec[main]) && !(bf.lucky_nova.is_some() && p.lucky_spec[main])
            && !(p.spec_sp[main] == Sp::Nova_Base && self.st.etch(p, Etq::NovaLowMana).is_some())
            && p.spec_cls[main] == 0 && bf.spectral.is_none()
            && !(self.st.javelin_implode && pdx != 0.0 && self.b.pd >> main & 1 == 1)
            && !(p.knobs.mana_state_heuristic && self.talent_tier(p.nt.depleted).is_some());
        let sw_on = weave && p.knobs.score_swings;
        let mut mel = None;
        let zero = V4([0.0; 4]);
        let mut es: Stack<crate::procs::EchoSrc, 72> = Stack::new();
        if fast {
            Self::rampage_uptime(&mut bf, 0.0, None);
            let lucky = if weave { aps0 * self.melee_lucky(&bf, aps0) } else { 0.0 };
            self.finalize(&mut bf, aps0, lucky)?;
            if weave {
                let weapon = p.types[self.b.items[MAINHAND].ty()].name.as_str();
                Self::echo_arm(&bf);
                let m = self.melee_swings(&bf, p.type_combo_avg(self.b.items[MAINHAND].ty()), weapon)?;
                if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
                    es.push(crate::procs::EchoSrc { rate: m.1, s, r: if sw_on { r } else { zero }, targets: if sw_on { m.4 } else { 1.0 }, main: false });
                }
                Self::set_weave(&mut bf, &[(m.1, m.3)], p.knobs.weave_window_s);
                mel = Some(m);
            }
        }
        let mut src: Stack<RotSrc, { crate::model::MAX_ROT }> = Stack::new();
        for (i, &spec) in std::iter::once(&main).chain(secs.iter()).enumerate() {
            let learned = self.b.abil(spec);
            let (tier, _) = self.effective_tier(spec, learned.max(1));
            let c = self.cfg(fb, spec, tier);
            let leftover = i > 0 && Some(spec) == implode;
            let blocked = !leftover && self.unaffordable(&c);
            if blocked {
                fb.note_unaffordable(spec);
            }
            if leftover || blocked {
                src.push(RotSrc { spec, i, c, leftover, blocked, implode_main: false, rank: 0.0, raw: 0.0, need: 0.0, rate: 0.0, k: 0.0, share: 0.0, pd: false, hold: false, pd_inst: false, done: None });
                continue;
            }
            let implode_main = i == 0 && Some(spec) == implode;
            bf.raw_probe.set(f64::NAN);
            bf.weave_drop.set(0.0);
            if fast {
                Self::echo_arm(&bf);
            }
            bf.dry.set(!fast);
            let probe = self.model(fb, spec, &c, &bf);
            bf.dry.set(false);
            let (dps, pack, need, rate) = probe?;
            let ep = Self::echo_take(&bf);
            let raw = bf.raw_probe.get();
            bf.raw_probe.set(0.0);
            let raw = if raw.is_nan() { 0.0 } else { raw };
            let (leech_ok, lucky_ok) = self.leech_lucky_ok(spec);
            let k = if leech_ok && lucky_ok { self.per_cast_rates(fb, spec, &c, rate)?.1 * d.mana_max * self.mana_steal_pct()? } else { 0.0 } + bf.mana_back.replace(0.0);
            let hold = p.spec_pd_hold[spec];
            let pd = pdx != 0.0 && self.b.pd >> spec & 1 == 1
                && (hold || (c.or(K::manaCost, 0.0) > 0.0 && c.or(K::manaCostPerSecond, 0.0) == 0.0));
            let need = if implode_main { d.mana_regen } else { need };
            let need = if pd && hold { need.max(p.knobs.pd_hold_drain * d.mana_max) } else { need };
            let need = need * (1.0 - eth);
            src.push(RotSrc { spec, i, c, leftover, blocked, implode_main, rank: dps.0[0], raw, need, rate, k, share: 0.0, pd, hold, pd_inst: false, done: if fast { Some((dps, pack, rate, bf.weave_drop.get(), ep)) } else { None } });
        }
        let mut avail = base - demand;
        let mut short = 0.0;
        for x in src.iter().filter(|x| !x.leftover && !x.blocked) {
            short += f64::max(0.0, x.need - x.k);
        }
        if short > 0.0 {
            avail = self.starved_free(avail, short);
        }
        if !fast {
            // The probe ran dry (mana need, rate, steal and raw only); damage ranks matter only when some cast cannot
            // run at its full rate, so they are computed just then (with a margin that keeps boundary cases exact).
            let (mut sur, mut pos, mut n_pos) = (0.0, 0.0, 0);
            for x in src.iter().filter(|x| !x.leftover && !x.blocked) {
                let net = x.need - x.k;
                if net <= 0.0 {
                    sur -= net;
                } else {
                    pos += net;
                    n_pos += 1;
                }
            }
            if n_pos >= 2 && avail + sur < pos * (1.0 + 1e-9) + 1e-9 {
                for j in 0..src.len() {
                    if !src[j].leftover && !src[j].blocked && src[j].need - src[j].k > 0.0 {
                        let (dps, _, _, _) = self.model(fb, src[j].spec, &src[j].c, &bf)?;
                        src[j].rank = dps.0[0];
                    }
                }
            }
        }
        let eff = |x: &RotSrc| {
            let net = x.need - x.k;
            if net <= 0.0 { f64::INFINITY } else { x.rank / net }
        };
        let mut order: Stack<usize, { crate::model::MAX_ROT }> = Stack::new();
        for j in 0..src.len() {
            if !src[j].leftover && !src[j].blocked {
                order.push(j);
            }
        }
        order.sort_by(|&a, &b| crate::procs::tie_key(eff(&src[b])).cmp(&crate::procs::tie_key(eff(&src[a]))).then(src[a].i.cmp(&src[b].i)));
        for &j in order.iter() {
            let net = src[j].need - src[j].k;
            if net <= 0.0 {
                src[j].share = 1.0;
                avail -= net;
            } else {
                src[j].share = if avail >= net * (1.0 - 1e-12) { 1.0 } else if avail <= net * 1e-12 { 0.0 } else { avail / net };
                if src[j].share > 0.0 {
                    avail = f64::max(0.0, avail - src[j].share * net);
                }
            }
        }
        let (mut sk, mut sn, mut sr) = (0.0, 0.0, 0.0);
        for x in src.iter().filter(|x| !x.leftover) {
            sk += x.k * x.share;
        }
        for x in src.iter().filter(|x| !x.leftover) {
            sn += x.need * x.share;
        }
        for x in src.iter().filter(|x| !x.leftover) {
            sr += x.rate * x.share;
        }
        let free = f64::max(0.0, base + sk - demand - sn);
        let fury = if bf.rampage.is_some() || fury_on { self.fury_for(sr + aps0) } else { None };
        let free = Self::empower_toggle(&mut bf, free, eth);
        let mut left = free;
        let mut drain = 0.0;
        if !fast {
            if bf.rampage.is_some() {
                let free_r = self.starved_free(free, ramp_cost);
                let up = Self::rampage_uptime(&mut bf, free_r, fury);
                left = f64::max(0.0, free - up * ramp_cost);
                drain = up * ramp_cost;
            } else {
                Self::rampage_uptime(&mut bf, free, fury);
            }
        }
        let mut pd_rate = 0.0;
        let mut pd_in = left;
        let mut pd_bar = 0.0;
        let mut mode = 0u8;
        let mut duty = 1.0;
        let mut sneak = 0.0;
        let mut nova_rate = 0.0;
        let mmax = d.mana_max;
        let mut dump = [0.0f64; crate::model::MAX_ROT];
        let mut cpf = [0.0f64; crate::model::MAX_ROT];
        let mut udt = [1.0f64; crate::model::MAX_ROT];
        for x in src.iter_mut().filter(|x| !x.leftover) {
            x.pd_inst = x.pd && !x.hold && x.c.or(K::manaCost, 0.0) > 0.0 && x.need > 0.0 && x.share > 0.0;
        }
        if pdx != 0.0 {
            let nova_bit = self.b.pd & PD_NOVA != 0;
            let mut any_hold = false;
            let mut hold_sh = 0.0;
            for x in src.iter().filter(|x| !x.leftover && !x.blocked) {
                if x.pd && x.hold && x.share > 0.0 {
                    any_hold = true;
                    hold_sh += x.share;
                }
            }
            if src.iter().any(|x| !x.leftover && x.pd_inst) || any_hold || nova_bit {
                let all = src.iter()
                    .filter(|x| !x.leftover && !x.blocked && x.share > 0.0 && x.need > 0.0 && Some(x.spec) != implode
                        && (x.hold || (x.c.or(K::manaCost, 0.0) > 0.0 && x.c.or(K::manaCostPerSecond, 0.0) == 0.0)))
                    .all(|x| x.pd);
                mode = if all { 1 } else { 2 };
            }
            if mode == 1 {
                for x in src.iter().filter(|x| !x.leftover) {
                    if x.pd_inst {
                        pd_rate += x.share * x.need / (x.c.or(K::manaCost, 0.0) * (1.0 - eth));
                        pd_in += x.share * x.need;
                    }
                }
                nova_rate = self.nova_pd_rate(aps0);
                pd_rate += nova_rate;
                if pd_rate > 0.0 {
                    pd_rate += bf.inst_buff_rate;
                }
                pd_bar = if pd_rate > 0.0 { pd_in / pd_rate } else { 0.0 };
                sneak = 1.0;
            } else if mode == 2 {
                let mut cr = 0.0;
                let mut has = false;
                for j in 0..src.len() {
                    let x = &src[j];
                    if !x.leftover && x.pd_inst {
                        cpf[j] = x.need / (x.c.or(K::manaCost, 0.0) * (1.0 - eth));
                        cr += x.share * cpf[j];
                        has = true;
                    }
                }
                if has && cr > 0.0 && left > 1e-9 * f64::max(1.0, mmax) {
                    let w = p.knobs.pd_sneak_window_s;
                    let t = f64::max(f64::max(mmax / left, w), 1.0 / cr);
                    duty = f64::max(0.0, 1.0 - w / t);
                    pd_bar = f64::min(mmax, left * t);
                    pd_rate = 1.0 / t;
                    for j in 0..src.len() {
                        let x = &src[j];
                        if !x.leftover && x.pd_inst {
                            dump[j] = f64::min(pd_rate * x.share * cpf[j] / cr, x.share * cpf[j]);
                        }
                    }
                    sneak = w / t;
                    for j in 0..src.len() {
                        let x = &src[j];
                        if x.leftover || x.blocked {
                            continue;
                        }
                        let cd = self.cd_s(&x.c);
                        let cps = if x.implode_main {
                            Some(if cd > 0.0 { 1.0 / cd } else { 0.0 })
                        } else if x.hold {
                            None
                        } else if x.c.or(K::manaCost, 0.0) > 0.0 && x.need > 0.0 {
                            Some(x.share * x.need / (x.c.or(K::manaCost, 0.0) * (1.0 - eth)))
                        } else {
                            Some(if cd > 0.0 { x.share / cd } else { 0.0 })
                        };
                        let interval = match cps { None => 0.0, Some(c) if c > 0.0 => 1.0 / c, _ => f64::INFINITY };
                        udt[j] = if interval < f64::INFINITY { f64::max(0.0, 1.0 - Self::window_loss(interval, w) / t) } else { 1.0 };
                    }
                } else {
                    for x in src.iter_mut().filter(|x| !x.leftover) {
                        x.pd_inst = false;
                    }
                }
                sneak = f64::min(1.0, sneak + hold_sh);
            }
        }
        bf.pd_always = mode == 1;
        bf.sneak_share = sneak;
        bf.pd_bar = pd_bar;
        bf.u_half_mana = if src.iter().any(|x| !x.leftover && x.share < 1.0) {
            1.0
        } else if pd_rate > 0.0 {
            if pd_in > 0.0 { f64::min(1.0, pd_rate * 0.5 * d.mana_max / pd_in) } else { 1.0 }
        } else {
            0.0
        };
        let limited = src.iter().any(|x| !x.leftover && !x.blocked && x.share < 1.0);
        bf.u_low_mana = self.low_mana_state(limited, pd_rate, base + sk - demand - drain - bf.emp_paid);
        if !fast {
            if let Some(sq) = bf.spectral {
                let mut hq = aps0;
                for x in src.iter().filter(|x| !x.leftover) {
                    if self.spectral_ok(x.spec) {
                        hq += x.rate * x.share;
                    }
                }
                bf.spectral_s = Self::spectral_stacks(sq, hq);
            }
        }
        if duty < 1.0 {
            for j in 0..src.len() {
                let x = &mut src[j];
                if !x.leftover && !x.blocked && !x.implode_main {
                    x.share = if dump[j] > 0.0 { f64::min(x.share, x.share * udt[j] + dump[j] / cpf[j]) } else { x.share * udt[j] };
                }
            }
            sn = 0.0;
            for x in src.iter().filter(|x| !x.leftover) {
                sn += x.need * x.share;
            }
        }
        let dotf = self.dot_slot(src.iter().map(|x| (x.spec, x.c, if x.leftover { 0.0 } else { x.share })))?;
        if !fast {
            if lucky_only {
                if weave {
                    let weapon = p.types[self.b.items[MAINHAND].ty()].name.as_str();
                    bf.dry.set(true);
                    mel = Some(self.melee_swings(&bf, p.type_combo_avg(self.b.items[MAINHAND].ty()), weapon)?);
                    bf.dry.set(false);
                }
                return self.rotation_lucky(fb, &src, mel, &dotf, &mut bf);
            }
            let mut lucky = if weave { aps0 * self.melee_lucky(&bf, aps0) } else { 0.0 };
            let mut sl = 0.0;
            for x in src.iter().filter(|x| !x.leftover && p.lucky_spec[x.spec]) {
                sl += x.rate * x.share * d.lucky_hit_chance;
            }
            lucky += sl;
            for x in src.iter().filter(|x| !x.leftover) {
                self.cast_debuffs(&mut bf, x.spec, &x.c, x.share)?;
            }
            self.finalize(&mut bf, aps0, lucky)?;
            if weave {
                let weapon = p.types[self.b.items[MAINHAND].ty()].name.as_str();
                Self::echo_arm(&bf);
                let m = self.melee_swings(&bf, p.type_combo_avg(self.b.items[MAINHAND].ty()), weapon)?;
                if let Some((s, r, _, _, _)) = Self::echo_take(&bf) {
                    es.push(crate::procs::EchoSrc { rate: m.1, s, r: if sw_on { r } else { zero }, targets: if sw_on { m.4 } else { 1.0 }, main: false });
                }
                mel = Some(m);
            }
        }
        let mut dps = V4([0.0; 4]);
        let mut pack = V4([0.0; 4]);
        let (mut leech, mut luck, mut supply, mut implode_used) = (0.0, 0.0, base, 0.0);
        let mut sq = 0.0;
        let mut lucky_hits = 0.0;
        let mut lucky_ext = 0.0;
        let (mut main0, mut total0) = (0.0, 0.0);
        for j in 0..src.len() {
            bf.weave_src.clear();
            if let Some(m) = mel {
                bf.weave_src.push((m.1, m.3));
            }
            for (o, y) in src.iter().enumerate() {
                if o != j && !y.leftover && !y.blocked && !p.spec_free[y.spec] {
                    assert!(bf.weave_src.len() < 24, "too many hit streams");
                    bf.weave_src.push((y.rate * y.share, y.raw));
                }
            }
            bf.weave_f = Self::window_share(&bf.weave_src, p.knobs.weave_window_s);
            bf.weave_drop.set(0.0);
            let x = &src[j];
            if x.blocked {
                continue;
            }
            if x.leftover {
                let cd = self.cd_s(&x.c);
                let mana = d.mana_max.min(if pd_rate > 0.0 { 0.0 } else { left } * cd);
                if mana > 0.0 {
                    let per = self.hit(mana * x.c.req(K::percentManaDealt)?, 0.0, Hc { aoe: true, clip: true, ..Hc::default() }, 0.0, &bf)?;
                    let per = per * self.implode_pd(mana);
                    let rate = 1.0 / cd;
                    for l in 0..4 {
                        dps.0[l] += per.0[l] * rate;
                        pack.0[l] += per.0[l] * rate;
                    }
                    total0 += per.0[0] * rate;
                    implode_used += mana * rate;
                    let v = per.0[0] * rate;
                    sq += v * v / rate;
                }
                continue;
            }
            let (mut odps, mut opack, mut orate, ep) = if x.implode_main {
                let cd = self.cd_s(&x.c);
                let mana = d.mana_max.min(x.share * d.mana_regen * cd);
                let per = self.hit(mana * x.c.req(K::percentManaDealt)?, 0.0, Hc { aoe: true, clip: true, ..Hc::default() }, d.lucky_hit_chance, &bf)?;
                let a = per * (self.implode_pd(mana) / cd);
                (a, a * self.pack_targets(x.c.req(K::radius)?), 1.0 / cd, None)
            } else {
                match x.done {
                    Some((a, b, r, drop, ep)) => {
                        bf.weave_drop.set(drop);
                        (a, b, r, ep)
                    }
                    None => {
                        Self::echo_arm(&bf);
                        let (a, b, _, r) = self.model(fb, x.spec, &x.c, &bf)?;
                        (a, b, r, Self::echo_take(&bf))
                    }
                }
            };
            if dotf[j] != 1.0 {
                odps *= dotf[j];
                opack *= dotf[j];
                orate *= dotf[j];
            }
            let mut pmult = 1.0;
            if x.pd_inst && mode == 1 {
                let cost = x.c.or(K::manaCost, 0.0);
                let s_paid = if pd_bar - cost <= 1e-9 * cost.max(1.0) { 0.0 } else { f64::min(pd_bar - cost, f64::max(0.0, d.mana_max - cost)) };
                let s_free = f64::min(pd_bar, d.mana_max);
                pmult = (1.0 - eth) * self.pd_curve(s_paid) + eth * self.pd_curve(s_free);
            } else if x.pd_inst {
                let casts = x.share * cpf[j];
                if casts > 0.0 {
                    pmult = 1.0 + (self.pd_curve(pd_bar) - 1.0) * f64::min(1.0, dump[j] / casts);
                }
            } else if x.pd && x.hold {
                pmult = self.pd_curve(f64::max(0.0, p.knobs.pd_hold_drain * d.mana_max - x.c.or(K::manaCostPerSecond, 0.0)));
            }
            let sh = x.share;
            let dm = if x.implode_main { udt[j] } else { sh };
            for l in 0..4 {
                dps.0[l] += odps.0[l] * dm * pmult;
                pack.0[l] += opack.0[l] * dm * pmult;
            }
            total0 += odps.0[0] * dm * pmult;
            if orate * sh > 0.0 {
                let v = odps.0[0] * sh * pmult;
                sq += v * v / (orate * sh);
            }
            if x.i == 0 {
                main0 = odps.0[0] * dm * pmult;
            }
            supply += x.k * sh;
            let wf = if bf.weave_drop.get() > 0.0 { bf.weave_f * bf.weave_drop.get() } else { 0.0 };
            if let Some((s, r, cu, cf, cr)) = ep {
                let t = if odps.0[0] > 0.0 { opack.0[0] / odps.0[0] } else { 1.0 };
                let ex = bf.echo_extra.replace(0.0);
                let eo = bf.echo_orig.replace(1.0);
                let parts = [(cu, 1.0), (cf, cr)];
                let np = if cf > 0.0 && cr > 0.0 { 2 } else { 1 };
                for &(frac, m) in &parts[..np] {
                    let rt = orate * sh * frac / (eo + ex);
                    es.push(crate::procs::EchoSrc { rate: rt * eo, s: s * pmult * m, r: r * pmult * m, targets: t, main: x.i == 0 });
                    if ex > 0.0 {
                        es.push(crate::procs::EchoSrc { rate: rt, s: s * pmult * m * ex, r: r * pmult * m * ex, targets: t, main: x.i == 0 });
                    }
                }
            }
            let (leech_ok, lucky_ok) = self.leech_lucky_ok(x.spec);
            if leech_ok {
                let (lr, kr) = self.per_cast_rates(fb, x.spec, &x.c, orate)?;
                leech += lr * sh * (1.0 - wf);
                if lucky_ok {
                    luck += kr * sh * (1.0 - wf);
                    lucky_hits += orate * sh * d.lucky_hit_chance * (1.0 - wf);
                    if x.spec != d.reset_spec {
                        if d.reset_spec != usize::MAX && self.p.godtree.is_some() {
                            lucky_ext += kr * sh * (1.0 - wf);
                        } else {
                            lucky_ext += orate * sh * d.lucky_hit_chance * (1.0 - wf);
                        }
                    }
                }
            }
        }
        bf.weave_src.clear();
        bf.weave_f = 0.0;
        let mut nova0 = 0.0;
        if let Some((per_hit, aps, lk, raw, targets)) = mel {
            let mut nova = self.arcane_nova(&bf, aps, raw)?;
            if nova.0[0] != 0.0 && nova_rate > 0.0 {
                let pm = self.pd_curve(self.snap_bar(f64::min(pd_bar, d.mana_max)));
                for l in 0..4 {
                    nova.0[l] *= pm;
                }
            }
            if nova.0[0] > 0.0 {
                sq += nova.0[0] * nova.0[0] / (aps / self.st.nova_hits);
            }
            for l in 0..4 {
                dps.0[l] += nova.0[l];
                pack.0[l] += nova.0[l];
            }
            nova0 = nova.0[0];
            leech += aps;
            luck += aps * lk;
            lucky_hits += aps * lk;
            if sw_on {
                let sw = per_hit * aps;
                for l in 0..4 {
                    dps.0[l] += sw.0[l];
                    pack.0[l] += sw.0[l] * targets;
                }
                total0 += sw.0[0];
                if aps > 0.0 {
                    sq += sw.0[0] * sw.0[0] / aps;
                }
            }
            lucky_ext += aps * lk;
        }
        let (ed, epack, emain, esq) = crate::procs::echo(p, self.st, d.echo_chance, d.effect_duration, &es, fb, self.st.etch(p, Etq::Reverb).map_or(false, |e| e.1));
        for l in 0..4 {
            dps.0[l] += ed.0[l];
            pack.0[l] += epack.0[l];
        }
        main0 += emain;
        total0 += ed.0[0];
        total0 += nova0;
        total0 += bf.cloud_dps.0[0];
        for l in 0..4 {
            dps.0[l] += bf.cloud_dps.0[l];
            pack.0[l] += bf.cloud_dps.0[l];
        }
        Ok(FamOut {
            fury_inc: fury.map_or(1.0, |f| f.2),
            dps, pack, resist_bonus: bf.colossus_resist, leech_rate: leech, lucky_rate: luck, lucky_hits, lucky_ext, supply,
            demand: demand + sn + if bf.rampage.is_some() { ramp_cost } else { 0.0 } + implode_used + bf.emp_paid,
            main_share: if total0 > 1e-9 { main0 / total0 } else { 1.0 },
            u_low_mana: bf.u_low_mana, hit_sq: sq + esq, surv: self.surv_out(&bf),
        })
    }

    /// The lucky-only tail of rotation(): each cast's rate (the final evaluation's, dry with the i-frame drops kept) and
    /// the lucky hits it lands, in the full evaluation's order.
    fn rotation_lucky(&self, fb: &mut Fallbacks, src: &[RotSrc], mel: Option<(V4, f64, f64, f64, f64)>, dotf: &[f64], bf: &mut Buffs) -> R<FamOut> {
        let p = self.p;
        let d = self.d;
        let (mut lucky_hits, mut lucky_ext) = (0.0, 0.0);
        for j in 0..src.len() {
            bf.weave_src.clear();
            if let Some(m) = mel {
                bf.weave_src.push((m.1, m.3));
            }
            for (o, y) in src.iter().enumerate() {
                if o != j && !y.leftover && !y.blocked && !p.spec_free[y.spec] {
                    assert!(bf.weave_src.len() < 24, "too many hit streams");
                    bf.weave_src.push((y.rate * y.share, y.raw));
                }
            }
            bf.weave_f = Self::window_share(&bf.weave_src, p.knobs.weave_window_s);
            bf.weave_drop.set(0.0);
            let x = &src[j];
            if x.blocked || x.leftover {
                continue;
            }
            bf.dry.set(true);
            bf.clip_only.set(true);
            let mut orate = if x.implode_main {
                let cd = self.cd_s(&x.c);
                let mana = d.mana_max.min(x.share * d.mana_regen * cd);
                self.hit(mana * x.c.req(K::percentManaDealt)?, 0.0, Hc { aoe: true, clip: true, ..Hc::default() }, d.lucky_hit_chance, bf)?;
                1.0 / cd
            } else {
                match x.done {
                    Some((_, _, r, drop, _)) => {
                        bf.weave_drop.set(drop);
                        r
                    }
                    None => self.model(fb, x.spec, &x.c, bf)?.3,
                }
            };
            bf.dry.set(false);
            bf.clip_only.set(false);
            if dotf[j] != 1.0 {
                orate *= dotf[j];
            }
            let sh = x.share;
            let wf = if bf.weave_drop.get() > 0.0 { bf.weave_f * bf.weave_drop.get() } else { 0.0 };
            let (leech_ok, lucky_ok) = self.leech_lucky_ok(x.spec);
            if leech_ok && lucky_ok {
                lucky_hits += orate * sh * d.lucky_hit_chance * (1.0 - wf);
                if x.spec != d.reset_spec {
                    if d.reset_spec != usize::MAX && self.p.godtree.is_some() {
                        lucky_ext += self.per_cast_rates(fb, x.spec, &x.c, orate)?.1 * sh * (1.0 - wf);
                    } else {
                        lucky_ext += orate * sh * d.lucky_hit_chance * (1.0 - wf);
                    }
                }
            }
        }
        if let Some((_, aps, lk, _, _)) = mel {
            lucky_hits += aps * lk;
            lucky_ext += aps * lk;
        }
        let zero = V4([0.0; 4]);
        Ok(FamOut {
            fury_inc: 1.0, dps: zero, pack: zero, resist_bonus: 0.0, leech_rate: 0.0, lucky_rate: 0.0, lucky_hits, lucky_ext,
            supply: 0.0, demand: 0.0, main_share: 1.0, u_low_mana: bf.u_low_mana, hit_sq: 0.0, surv: crate::buffs::SurvOut::default(),
        })
    }

    /// rotation.per_cast_rates: (leech procs/s, lucky procs/s); rework fang casts proc at most once per cast.
    fn per_cast_rates(&self, fb: &mut Fallbacks, spec: usize, c: &CfgM, rate: f64) -> R<(f64, f64)> {
        let p = self.d.lucky_hit_chance;
        let n = if self.p.godtree.is_some() { self.fang_hits(fb, spec, c)? } else { 0.0 };
        if n > 0.0 {
            let casts = rate / n;
            return Ok((casts, casts * (1.0 - (1.0 - p).powf(n))));
        }
        Ok((rate, rate * p))
    }

    /// rotation.window_loss: casting time a source loses per sneak window (its mean delay).
    fn window_loss(interval: f64, w: f64) -> f64 {
        if interval >= w { w * w / (2.0 * interval) } else { w - interval / 2.0 }
    }

    /// buffs.pd_bmax: the largest Power Dump bonus, per_mana x sqrt(ref x max mana).
    pub(crate) fn pd_bmax(&self) -> f64 {
        self.st.gfx.map_or(0.0, |fx| fx.power_dump) * (self.p.knobs.power_dump_sqrt_ref * self.d.mana_max).sqrt()
    }

    /// buffs.pd_curve: 1 + pd_bmax x (surplus, at most the bar) / max mana.
    pub(crate) fn pd_curve(&self, surplus: f64) -> f64 {
        let m = self.d.mana_max;
        if m <= 0.0 {
            return 1.0;
        }
        1.0 + self.pd_bmax() * surplus.min(m) / m
    }

    /// abilities.implode_pd: Power Dump on every Implode, for the mana it consumes (1 without the node).
    pub(crate) fn implode_pd(&self, mana: f64) -> f64 {
        if self.st.gfx.map_or(0.0, |fx| fx.power_dump) == 0.0 {
            return 1.0;
        }
        self.pd_curve(mana)
    }

    /// buffs.spectral_ad
    pub(crate) fn spectral_ad(&self, bf: &Buffs) -> f64 {
        self.p.knobs.spectral_ap_per_stack * bf.spectral_s * self.d.ap_base
    }

    /// buffs.spectral_ok (the spec table's spectral rule).
    fn spectral_ok(&self, spec: usize) -> bool {
        match self.p.spec_spectral[spec] {
            RULE_YES => true,
            RULE_LUCKY_BOLT => self.st.etch(self.p, Etq::IceBoltLucky).is_some(),
            _ => false,
        }
    }

    /// buffs.spectral_stacks
    pub(crate) fn spectral_stacks(sp: (f64, f64, f64, f64), hits: f64) -> f64 {
        let (stacks, u, pp, dd) = sp;
        if hits <= 0.0 {
            return 0.0;
        }
        f64::min(stacks, hits * u * f64::min(pp, dd)) / (pp * hits)
    }

    /// Leech and lucky eligibility (the spec table's leech_ok / lucky_ok; Lucky Bolt leeches but its lucky hits fail).
    pub(crate) fn leech_lucky_ok(&self, main: usize) -> (bool, bool) {
        let p = self.p;
        match p.leech_spec[main] {
            RULE_LUCKY_BOLT => (self.st.etch(p, Etq::IceBoltLucky).is_some(), false),
            r => (r == RULE_YES, p.lucky_spec[main]),
        }
    }
}
