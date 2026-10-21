"""Multi-ability rotation (the kernel mirrors it in rotation.rs Ev::rotation).

An ability build casts every learned ability as it comes off cooldown, from one mana pool:

1. Maintenance buffs (Taunt, totems, Colossus, Battle Cry, Frost Nova, toggles other than Rampage) are paid first, as
   in buffs_for. An instant cast that costs more than the whole bar is never cast (buffs.unaffordable), damage
   abilities included.
2. Damage abilities (the main one and every learned secondary) share what is left. Each is first evaluated alone to
   get its damage, hit rate, mana need at its full cooldown rate, its own Mana Steal and the raw amount its hits carry.
   When mana runs short the rotation drops the least mana-efficient casts first (greedy by damage per net mana, which
   is optimal because every ability's damage is linear in the share of casts it keeps); ties keep the main ability
   first. An ability whose Mana Steal exceeds its cost runs at its full rate and adds the surplus to the shared bar.
   A Mana Starved node covers the shortfall first. Implode as the main ability asks for the whole regeneration and
   casts on cooldown with mana per cast = min(bar, share x regen x cooldown).
3. Rampage runs on what is left, then a non-main Implode (it consumes the whole bar per cast, so it takes the
   leftover: mana per cast = min(bar, leftover x cooldown)).
4. The low-mana uptime (Depleted, Methodical) follows from the mana split: 1 when a damage ability is mana-limited,
   else the refill time below 20% of the bar after each sneaking Power Dump cast, else 0. Debuffs are finalised from
   the event rates (procs.py), then every ability is evaluated again with the i-frame context of all the other
   sources: a clippable hit lands inside another source's 0.5 s window with probability min(1, window x the other
   sources' hit rate) and keeps only its raw above theirs (rate-weighted raw of the other sources, woven swings
   included).
5. Power Dump (user, 2026-10-21): a sneaking cast gets 1 + pd_bmax x the share of the bar it spends above its cost.
   Sneaking the whole fight (every instant damage cast on Power Dump): every instant cast, buffs included, empties the
   bar, so each damage cast gets the spare mana flow divided by all instant casts per second, minus its own cost (at
   most the bar); a sneaking Starforge nova is one of those casts at its proc rate and costs nothing. Mixing sneaking
   and standing casts: one dump per bar fill (or per window or cast interval), each costing pd_sneak_window_s without
   casts. Either way the sneaking share halves the speed avoidance. Every Implode gets the bonus for the share of the
   bar it consumes.
6. Woven swings are scored (knob score_swings) and count as a secondary source in the main-share rule.

Assumptions: abilities are instant (no cast lock or global cooldown); a source's raw for clipping is its first hit's
raw; Implode's cast damage uses the leftover mana flow."""
import math

from . import abilities as ab
from . import buffs as bf
from . import families as fm
from . import procs
from .log import fallback


def per_cast_rates(out, p):
    """(leech procs/s, lucky procs/s) of one source. A per-cast source (fang casts in the rework) procs at most once per
    cast: leech once, a lucky proc when any of its hits is lucky."""
    n = out.get("per_cast", 0.0)
    r = out.get("rate", 0.0)
    if n > 0:
        casts = r / n
        return casts, casts * (1.0 - (1.0 - p) ** n)
    return r, r * p


def _steal_k(spec, out, d, st, ctx):
    lucky_ok = out.get("leech_ok", spec in ab.LEECH_SPECS) and out.get("lucky_ok", spec in ab.LUCKY_SPECS)
    return per_cast_rates(out, d["lucky_hit_chance"])[1] * d["mana_max"] * bf.mana_steal_pct(st, ctx) if lucky_ok else 0.0


def window_share(others, window):
    """Chance a hit lands inside another stream's i-frame window: 1 - prod(1 - min(1, window x rate_j)), exact for
    independent periodic streams with random phase."""
    miss = 1.0
    for rate, _ in others:
        miss *= 1.0 - min(1.0, window * rate)
    return 1.0 - miss


IMPLODE = "Mana_Shield_Implode"
SHARE_MIN_DPS = 1e-9


def _implode_dps(build, ctx, st, d, c, buffs, target_hp, leftover):
    """Non-main Implode on the leftover mana flow (abilities.implode with mana per cast = min(bar, leftover x cd))."""
    cd = ab.cd_s(c, d)
    mana = min(d["mana_max"], leftover * cd)
    if mana <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    hc = ab.HitClass("AOE", aoe_flag=True, iframe="plain")
    per, lo, hi, _ = ab.evaluate_hit(mana * c["percentManaDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, False)
    rate = ab.implode_pd(st, ctx, d, mana) / cd
    return per * rate, lo * rate, hi * rate, 1.0 / cd, mana / cd


def _implode_main(build, ctx, st, d, c, buffs, target_hp, share, record):
    """Implode as the main ability at its share of the regeneration: one cast per cooldown with mana per cast =
    min(bar, share x regen x cooldown) (abilities.implode at share 1)."""
    if d.get("reset_spec") == IMPLODE:
        c = dict(c, _reset=True)
    cd = ab.cd_s(c, d)
    mana = min(d["mana_max"], share * d["mana_regen"] * cd)
    hc = ab.HitClass("AOE", aoe_flag=True, iframe="plain")
    per, lo, hi, steps = ab.evaluate_hit(mana * c["percentManaDealt"], 0.0, hc, d, st, build, ctx, target_hp, buffs, record)
    notes = [f"consumes all mana: {mana:,.0f} mana per cast ({share:.0%} of the regeneration over one cooldown)"]
    pm = ab.implode_pd(st, ctx, d, mana)
    if pm != 1.0:
        per, lo, hi = per * pm, lo * pm, hi * pm
        notes.append(f"Power Dump on every Implode: x{pm:.2f} for the {mana / d['mana_max']:.0%} of the bar it consumes")
        if steps is not None:
            steps = list(steps) + [("Power Dump", f"x{pm:.2f}", "share of the bar the Implode consumes")]
    return ab.finish(per, 1.0 / cd, steps, ab.pack_targets(c["radius"], d, ctx), d["mana_regen"], notes, lo=lo, hi=hi)


def _casts_per_s(x, d, eth):
    """Casts per second of a rotation source (rates in rows are hits per second): Implode as the main ability casts on
    cooldown at its sneak-window duty; an instant cast at share x need / (cost x (1 - Ethereal)); a held channel has no
    casts (None); anything else at share / cooldown."""
    cd = ab.cd_s(x["c"], d)
    if x["implode_main"]:
        return x.get("duty", 1.0) / cd if cd > 0 else 0.0
    if x["hold"]:
        return None
    cost = x["c"].get("manaCost", 0.0)
    if cost > 0 and x["need"] > 0:
        return x["share"] * x["need"] / (cost * (1.0 - eth))
    return x["share"] / cd if cd > 0 else 0.0


def window_loss(interval, w):
    """Casting time a source loses per sneak window of w seconds, for a cast interval with random phase: a cast that
    comes due inside the window waits for its end, so the loss is the mean delay, w^2 / (2 x interval) when the interval
    is at least w, else w - interval / 2 (every window then delays it); a held channel (interval 0) loses all of w."""
    if interval >= w:
        return w * w / (2.0 * interval)
    return w - interval / 2.0


def _row(spec, kind, **kw):
    """One rotation row (plan section 13: spec, share, rate, dps, mana, reach, kind, debuffs)."""
    rec = ab.SPEC_TABLE.get(spec)
    row = {"spec": spec, "kind": kind, "tier": None, "share": 1.0, "rate": 0.0, "dps": 0.0, "pmult": 1.0, "mana": 0.0,
           "leftover": False, "steps": None, "notes": [], "flags": [], "hit": None, "targets": 1.0, "window": 0.0,
           "dropped": 0.0, "pd": False, "reach": rec["reach"] if rec else None,
           "debuffs": [k for k, _, _ in rec["cast_debuffs"]] if rec else []}
    row.update(kw)
    return row


def _secondary_full(src, sw0, ctx, flags):
    """secondary_share_unconstrained: the secondaries' (and swings') damage at their full cooldown rate from the probe
    pass, over the main's plus theirs; any single one above secondary_flag_share is flagged secondary-hungry."""
    main_full = 0.0 if src[0]["blocked"] else src[0]["full"]
    parts = [(x["spec"], x["full"]) for x in src[1:] if not x["blocked"]]
    if sw0 is not None:
        parts.append(("melee swings", sw0))
    total = main_full
    for _, v in parts:
        total += v
    sec = total - main_full
    out = []
    for spec, v in parts:
        share = v / total if total > 0 else 0.0
        label = ab.SPEC_TABLE[spec]["label"] if spec in ab.SPEC_TABLE else spec
        out.append({"spec": spec, "label": label, "share": share})
        if share > ctx.knobs["secondary_flag_share"]:
            flags.append(f"secondary-hungry: {label} would deal {share:.0%} uncapped")
    return (sec / total if total > 0 else 0.0), out


def compute(fam, secs, build, ctx, st, d, target_hp, record):
    buffs, demand, notes, flags = bf.buffs_for(build, ctx, st, d, record)
    eth = bf.ethereal_chance(st, ctx)
    demand *= 1.0 - eth
    if "rampage_cost" in buffs:
        buffs["rampage_cost"] *= 1.0 - eth
    pdx = st.gfx["power_dump"] if st.gfx else 0.0
    weave = bool(getattr(build, "weave", False))
    score_sw = weave and bool(ctx.knobs["score_swings"])
    aps0 = fm.swing_rate(d, ctx) if weave else 0.0
    steal_m = aps0 * fm.melee_lucky(d, buffs, aps0) * d["mana_max"] * bf.mana_steal_pct(st, ctx) if weave else 0.0
    base = bf.mana_supply(d, buffs, ctx) + steal_m
    ramp_cost = buffs.get("rampage_cost", 0.0)

    # 1. Each damage ability alone (no i-frame context): damage, hit rate, mana need, Mana Steal, raw.
    src = []
    for i, spec in enumerate([fam.main_spec] + secs):
        learned = build.abilities.get(spec, 0)
        tier, bonus = fm.effective_tier(ctx, st, spec, max(1, learned))
        c = fm.spec_cfg(ctx, st, spec, tier, build)
        leftover = spec == IMPLODE and i > 0
        blocked = not leftover and bf.unaffordable(c, d)
        buffs["_raw_probe"] = None
        full = None
        if blocked:
            fallback(f"cast-unaffordable-{spec}", f"{spec} costs {c['manaCost']:.0f} mana, more than the bar holds; never cast")
            out = {"dps": 0.0, "rate": 0.0, "mana_per_s": 0.0}
            raw = 0.0
        elif leftover:
            out = {"dps": 0.0, "rate": 0.0, "mana_per_s": 0.0}
            raw = 0.0
            full = ab.MODELS[spec].compute(build, ctx, st, d, c, buffs, target_hp, False)["dps"]
        else:
            out = ab.MODELS[spec].compute(build, ctx, st, d, c, buffs, target_hp, False)
            raw = buffs["_raw_probe"] or 0.0
            # The greedy order uses the damage against a boss at 0 health with execution on (the kernel's lane 0), so
            # every lane of one evaluation shares one mana split.
            if secs and (target_hp != 0 or getattr(st, "no_exec", False)):
                ne = getattr(st, "no_exec", False)
                st.no_exec = False
                out["dps_rank"] = ab.MODELS[spec].compute(build, ctx, st, d, c, buffs, 0.0, False)["dps"]
                st.no_exec = ne
            else:
                out["dps_rank"] = out["dps"]
        buffs.pop("_raw_probe", None)
        if full is None:
            full = out["dps"]
        implode_main = spec == IMPLODE and i == 0 and not blocked
        pd = bool(pdx) and not leftover and not blocked and spec != IMPLODE and spec in getattr(build, "pd", set()) and bf.pd_eligible(spec, c)
        hold = spec in bf.PD_HOLD
        need = d["mana_regen"] if implode_main else out["mana_per_s"]
        if pd and hold:
            need = max(need, ctx.knobs["pd_hold_drain"] * d["mana_max"])
        need = need * (1.0 - eth)
        src.append({"spec": spec, "i": i, "tier": tier, "bonus": bonus, "learned": learned, "c": c, "leftover": leftover,
                    "blocked": blocked, "implode_main": implode_main, "probe": out, "full": full, "raw": raw,
                    "need": need, "rate": out.get("rate", 0.0),
                    "k": 0.0 if leftover or blocked else _steal_k(spec, out, d, st, ctx) + out.get("mana_back", 0.0), "share": 0.0, "pd": pd,
                    "hold": hold})
    sw0 = None
    if score_sw:
        sw = fm.melee_swings(build.items["mainhand"].type, build, ctx, st, d, buffs, target_hp, False, [])
        sw0 = sw["per_hit"] * sw["aps"]

    # 2. Mana: buffs first, then damage abilities greedy by damage per net mana.
    cast = [x for x in src if not x["leftover"] and not x["blocked"]]
    # Signed: buffs that regen alone cannot carry draw on the casts' Mana Steal surplus first.
    avail = base - demand
    short = sum(max(0.0, x["need"] - x["k"]) for x in cast)
    if short > 0:
        avail = bf.starved_free(st, d, avail, short)

    def eff(x):
        net = x["need"] - x["k"]
        return math.inf if net <= 0 else x["probe"]["dps_rank"] / net

    for x in sorted(cast, key=lambda x: (-procs.tie_key(eff(x)), x["i"])):
        net = x["need"] - x["k"]
        if net <= 0:
            # Its own Mana Steal more than pays for it; the rest goes into the shared bar (these sort first).
            x["share"] = 1.0
            avail -= net
        else:
            # A relative tolerance keeps exactly-covered abilities (e.g. Mana Starved filling the shortfall) at a full
            # share, so rounding noise cannot switch Power Dump off.
            x["share"] = 1.0 if avail >= net * (1.0 - 1e-12) else 0.0 if avail <= net * 1e-12 else avail / net
            if x["share"] > 0:
                avail = max(0.0, avail - x["share"] * net)

    # 3. Rampage on what is left, then a non-main Implode on the rest.
    free = max(0.0, base + sum(x["k"] * x["share"] for x in cast) - demand - sum(x["need"] * x["share"] for x in cast))
    fury = None
    if "rampage" in buffs or (st.gfx and st.gfx["fury"]):
        fury = bf.fury_for(st, ctx, sum(x["rate"] * x["share"] for x in cast) + aps0)
    free = bf.empower_toggle(buffs, free, eth, notes)
    left = free
    if "rampage" in buffs:
        free_r = bf.starved_free(st, d, free, ramp_cost)
        up = bf.rampage_uptime(buffs, free_r, notes, fury)
        left = max(0.0, free - up * ramp_cost)
    else:
        up = bf.rampage_uptime(buffs, free, notes, fury)

    # Spectral Strike: stacks per qualifying hit from the hit rates (woven swings spend them too).
    if buffs.get("spectral"):
        hq = aps0 + sum(x["rate"] * x["share"] for x in cast if bf.spectral_ok(x["spec"], build))
        buffs["spectral_s"] = bf.spectral_stacks(buffs["spectral"], hq)

    # Power Dump (sneak casts, user 2026-10-21). A cast spends the bar above its cost and gets 1 + pd_bmax x that share
    # of the bar. Always sneaking (every instant damage cast is a Power Dump cast): no sneak windows, every instant cast
    # empties the bar, instant buff casts included, so each one's surplus is the mana that came in since the last one
    # (any other spending, toggles included, is paid first) minus its cost; free (Ethereal) casts keep the whole amount.
    # A sneaking held channel drains pd_hold_drain of the bar per second for that surplus, and a sneaking Starforge nova
    # is a free dump at its proc rate. Mixing sneaking and standing casts: the Power Dump abilities dump once per period
    # T = max(bar fill time, window, their cast interval), each dump spending the full spare mana since the last (at
    # most the bar); every dump costs pd_sneak_window_s with no casts (swings go on), so every cast runs at the duty
    # 1 - window / T, and the mana those lost casts would have spent is not returned to the bar. The Starforge nova bit
    # does nothing then (the player stands between dumps).
    pd_rate = 0.0
    pd_in = left
    pd_bar = 0.0
    mode = None
    duty = 1.0
    sneak = 0.0
    nova_rate = 0.0
    mmax = d["mana_max"]
    for x in cast:
        x["pd_inst"] = x["pd"] and not x["hold"] and x["c"].get("manaCost", 0.0) > 0 and x["need"] > 0 and x["share"] > 0
        x["dump_rate"] = 0.0
    if pdx:
        elig = [x for x in cast if x["share"] > 0 and x["need"] > 0 and x["spec"] != IMPLODE and bf.pd_eligible(x["spec"], x["c"])]
        holds = [x for x in cast if x["pd"] and x["hold"] and x["share"] > 0]
        nova_bit = "Starforge_Nova" in getattr(build, "pd", set())
        if any(x["pd_inst"] for x in cast) or holds or nova_bit:
            mode = "always" if all(x["pd"] for x in elig) else "mixed"
        if mode == "always":
            for x in cast:
                if x["pd_inst"]:
                    pd_rate += x["share"] * x["need"] / (x["c"]["manaCost"] * (1.0 - eth))
                    pd_in += x["share"] * x["need"]
            nova_rate = bf.nova_pd_rate(build, st, aps0)
            pd_rate += nova_rate
            if pd_rate > 0:
                pd_rate += buffs.get("_inst_buff_rate", 0.0)
            pd_bar = pd_in / pd_rate if pd_rate > 0 else 0.0
            sneak = 1.0
        elif mode == "mixed":
            pdc = [x for x in cast if x["pd_inst"]]
            for x in pdc:
                x["cpf"] = x["need"] / (x["c"]["manaCost"] * (1.0 - eth))
            cr = sum(x["share"] * x["cpf"] for x in pdc)
            if pdc and cr > 0 and left > 1e-9 * max(1.0, mmax):
                w = ctx.knobs["pd_sneak_window_s"]
                T = max(mmax / left, w, 1.0 / cr)
                duty = max(0.0, 1.0 - w / T)
                pd_bar = min(mmax, left * T)
                pd_rate = 1.0 / T
                for x in pdc:
                    x["dump_rate"] = min(pd_rate * x["share"] * x["cpf"] / cr, x["share"] * x["cpf"])
                sneak = w / T
                buffs["pd_period"] = T
                for x in cast:
                    cps = _casts_per_s(x, d, eth)
                    x["interval"] = 0.0 if cps is None else 1.0 / cps if cps > 0 else math.inf
                    x["duty"] = max(0.0, 1.0 - window_loss(x["interval"], w) / T) if x["interval"] < math.inf else 1.0
            else:
                for x in pdc:
                    x["pd_inst"] = False
            sneak = min(1.0, sneak + sum(x["share"] for x in holds))
    buffs["pd_mode"] = mode
    buffs["sneak_share"] = sneak
    steal_in = 0.0
    for x in cast:
        steal_in += x["k"] * x["share"]
    inflow = base + steal_in - demand - (up * ramp_cost if "rampage" in buffs else 0.0) - buffs.get("empower_paid", 0.0)
    buffs["u_low_mana"] = bf.low_mana_state(ctx, d, any(x["share"] < 1.0 for x in cast), pd_rate, inflow)
    buffs["pd_bar"] = pd_bar
    buffs["u_half_mana"] = bf.mana_below_uptime(cast, pd_rate, pd_in, d["mana_max"], 0.5)
    if duty < 1.0:
        # The dump itself is cast inside its window, on top of the standing casts, but never above the full rate.
        for x in cast:
            if not x["implode_main"]:
                u = x["duty"]
                x["share"] = min(x["share"], x["share"] * u + x["dump_rate"] / x["cpf"]) if x["dump_rate"] > 0 else x["share"] * u
        notes.append(f"Power Dump sneak windows: one dump every {buffs['pd_period']:.2f} s, {ctx.knobs['pd_sneak_window_s']:.2f} s "
                     f"each without casts ({duty:.0%} of the time standing); a cast due inside a window waits for its end")

    # 4. Debuffs from the event rates, then every ability with the i-frame context of the others.
    lucky = aps0 * fm.melee_lucky(d, buffs, aps0) if weave else 0.0
    lucky += sum(x["rate"] * x["share"] * d["lucky_hit_chance"] for x in cast if x["spec"] in ab.LUCKY_SPECS)
    for x in cast:
        bf.cast_debuffs(x["spec"], x["c"], d, x["share"], buffs["_cast_debuffs"], buffs)
    procs.finalize(build, ctx, st, d, buffs, procs.events(d, ctx, aps0, lucky), notes)
    echo_src = []
    m = None
    if weave:
        buffs["_echo_probe"] = None
        m = fm.melee_swings(build.items["mainhand"].type, build, ctx, st, d, buffs, target_hp, record, notes)
        if buffs["_echo_probe"]:
            if score_sw:
                echo_src.append(("woven swings", m["aps"]) + buffs["_echo_probe"] + (m["targets"], False))
            else:
                # Unscored swings: their echoes still hold the target's Echoing slot.
                echo_src.append(("woven swings", m["aps"], buffs["_echo_probe"][0], 0.0, 0.0, 0.0, 1.0, False))
        buffs.pop("_echo_probe")


    dps = lo = hi = pack = rate = leech = luck = lucky_hits = sq = lucky_ext = 0.0
    dot_f = ab.dot_slot([(x["spec"], x["share"], x["probe"].get("dot")) for x in cast])
    supply = base
    rows = []
    clip_loss = 0.0
    for x in src:
        kind = "main" if x["i"] == 0 else "secondary"
        if x["blocked"]:
            rows.append(_row(x["spec"], kind, tier=x["tier"], share=0.0, blocked=True))
            continue
        others = ([(m["aps"], m["raw"])] if m else []) + [(y["rate"] * y["share"], y["raw"]) for y in cast
                                                         if y is not x and not ab.free_iframes(y["spec"])]
        f = window_share(others, ctx.knobs["weave_window_s"])
        if f > 0:
            buffs["_weave"] = {"src": others, "f": f, "dropped": 0.0}
        else:
            buffs.pop("_weave", None)
        if x["leftover"]:
            xd, xlo, xhi, xr, used = _implode_dps(build, ctx, st, d, x["c"], buffs, target_hp, 0.0 if pd_rate > 0 else left)
            x["share"] = 1.0 if xr > 0 else 0.0
            rows.append(_row(x["spec"], kind, tier=x["tier"], share=x["share"], rate=xr, dps=xd, mana=used, leftover=True,
                             casts=xr, cd=ab.cd_s(x["c"], d), cost=x["c"].get("manaCost", 0.0)))
            dps, lo, hi, pack = dps + xd, lo + xlo, hi + xhi, pack + xd
            rate += xr
            if xr > 0:
                sq += xd * xd / xr
            continue
        buffs["_echo_probe"] = None
        buffs.pop("_echo_clip", None)
        if x["implode_main"]:
            out = _implode_main(build, ctx, st, d, x["c"], buffs, target_hp, x["share"], record)
            dmul = x.get("duty", 1.0)
        else:
            out = ab.dot_slot_apply(ab.MODELS[x["spec"]].compute(build, ctx, st, d, x["c"], buffs, target_hp, record and x["i"] == 0), dot_f.get(x["spec"], 1.0))
            dmul = x["share"]
        ep = buffs.pop("_echo_probe")
        cu, cf, cr = buffs.pop("_echo_clip", (1.0, 0.0, 1.0))
        pmult = 1.0
        if x["pd_inst"] and mode == "always":
            cost = x["c"]["manaCost"]
            # Snapped near zero so rounding noise cannot move a cast that spends nothing above its cost.
            s_paid = 0.0 if pd_bar - cost <= 1e-9 * max(cost, 1.0) else min(pd_bar - cost, max(0.0, d["mana_max"] - cost))
            s_free = min(pd_bar, d["mana_max"])
            pmult = (1.0 - eth) * bf.pd_curve(st, ctx, d, s_paid) + eth * bf.pd_curve(st, ctx, d, s_free)
        elif x["pd_inst"]:
            # Mixed: dump_rate of this ability's casts per second spend pd_bar each; the rest are standing casts.
            casts = x["share"] * x["cpf"]
            if casts > 0:
                pmult = 1.0 + (bf.pd_curve(st, ctx, d, pd_bar) - 1.0) * min(1.0, x["dump_rate"] / casts)
        elif x["pd"] and x["hold"]:
            pmult = bf.pd_curve(st, ctx, d, max(0.0, ctx.knobs["pd_hold_drain"] * d["mana_max"] - x["c"].get("manaCostPerSecond", 0.0)))
        sh = x["share"]
        dps += out["dps"] * dmul * pmult
        lo += out.get("dps_lo", out["dps"]) * dmul * pmult
        hi += out.get("dps_hi", out["dps"]) * dmul * pmult
        pack += out["pack_dps"] * dmul * pmult
        rate += out.get("rate", 0.0) * sh
        if out.get("rate", 0.0) * sh > 0:
            v = out["dps"] * sh * pmult
            sq += v * v / (out.get("rate", 0.0) * sh)
        supply += x["k"] * sh
        dropped = buffs.get("_weave", {}).get("dropped", 0.0)
        wf = buffs["_weave"]["f"] * dropped if dropped else 0.0
        if ep:
            # Power Dump raises the cast's ability power, so its echoes store and replay the larger hit. Echoing Smite
            # re-strikes (echo_extra) are their own eligible hits at that multiple of the bolt. Hits clipped by another
            # source's i-frames (share cf) carry only cr of their raw into the hurt event, so their echoes store cr x S.
            ex = out.get("echo_extra", 0.0)
            eo = out.get("echo_orig", 1.0)
            label = x["spec"].replace("_", " ")
            parts = [(cu, 1.0, "")] + ([(cf, cr, " (clipped)")] if cf > 0 and cr > 0 else [])
            for frac, keep, tag in parts:
                rt = out.get("rate", 0.0) * sh * frac / (eo + ex)
                echo_src.append((label + tag, rt * eo) + tuple(e * pmult * keep for e in ep) + (out.get("targets", 1.0), x["i"] == 0))
                if ex > 0:
                    echo_src.append((label + tag + " re-strikes", rt) + tuple(e * pmult * keep * ex for e in ep)
                                    + (out.get("targets", 1.0), x["i"] == 0))
        if out.get("leech_ok", x["spec"] in ab.LEECH_SPECS):
            lr, kr = per_cast_rates(out, d["lucky_hit_chance"])
            leech += lr * sh * (1.0 - wf)
            if out.get("lucky_ok", x["spec"] in ab.LUCKY_SPECS):
                luck += kr * sh * (1.0 - wf)
                lucky_hits += out.get("rate", 0.0) * sh * d["lucky_hit_chance"] * (1.0 - wf)
                if x["spec"] != d.get("reset_spec"):
                    if d.get("reset_rework"):
                        lucky_ext += kr * sh * (1.0 - wf)
                    else:
                        lucky_ext += out.get("rate", 0.0) * sh * d["lucky_hit_chance"] * (1.0 - wf)
        if record and x["i"] == 0 and pmult != 1.0 and out.get("steps") is not None:
            out["steps"] = list(out["steps"]) + [("Power Dump", f"x{pmult:.2f}", "ability power from the mana spent per cast")]
        if record and buffs.get("_weave"):
            b0 = dict(buffs)
            b0.pop("_weave")
            if x["implode_main"]:
                unclipped = _implode_main(build, ctx, st, d, x["c"], b0, target_hp, sh, False)["dps"]
            else:
                unclipped = ab.dot_slot_apply(ab.MODELS[x["spec"]].compute(build, ctx, st, d, x["c"], b0, target_hp, False), dot_f.get(x["spec"], 1.0))["dps"]
            clip_loss += (unclipped - out["dps"]) * dmul * pmult
        rows.append(_row(x["spec"], kind, tier=x["tier"], share=sh, rate=out.get("rate", 0.0) * sh,
                         dps=out["dps"] * dmul * pmult, pmult=pmult, mana=x["need"] * sh,
                         steps=out.get("steps") if record and x["i"] == 0 else None, notes=out.get("notes", []),
                         flags=out.get("flags", []), hit=out.get("hit"), targets=out.get("targets", 1.0),
                         window=buffs["_weave"]["f"] if buffs.get("_weave") else 0.0, dropped=dropped, pd=x["pd"],
                         pd_dumps=(x["dump_rate"] if mode == "mixed" else out.get("rate", 0.0) * sh) if x["pd_inst"] else 0.0,
                         pd_dump_mult=(bf.pd_curve(st, ctx, d, pd_bar) if mode == "mixed" else pmult) if x["pd_inst"] else pmult,
                         cd=ab.cd_s(x["c"], d), cost=x["c"].get("manaCost", 0.0), hold=x["hold"],
                         casts=_casts_per_s(x, d, eth), interval=x.get("interval")))
    buffs.pop("_weave", None)
    nd = nlo = nhi = 0.0
    sw = swlo = swhi = 0.0
    if weave:
        nd, nlo, nhi = fm.arcane_nova_dps(build, ctx, st, d, buffs, target_hp, m["aps"], m["raw"], notes)
        if nd and nova_rate > 0:
            # Sneaking nova: a free dump at its proc rate, sharing the refill between dumps with the sneaking casts.
            pm = bf.pd_curve(st, ctx, d, bf.snap_bar(min(pd_bar, d["mana_max"]), d))
            nd, nlo, nhi = nd * pm, nlo * pm, nhi * pm
        if nd > 0:
            sq += nd * nd / (m["aps"] / st.special["arcane_nova_hits"])
        leech += m["aps"]
        luck += m["aps"] * m["lucky"]
        lucky_hits += m["aps"] * m["lucky"]
        lucky_ext += m["aps"] * m["lucky"]
        if score_sw:
            sw, swlo, swhi = m["per_hit"] * m["aps"], m["lo"] * m["aps"], m["hi"] * m["aps"]
            rows.append(_row("melee swings", "swings", rate=m["aps"], dps=sw, hit=m["per_hit"], targets=m["targets"],
                             reach="zero"))
            if m["aps"] > 0:
                sq += sw * sw / m["aps"]
    er = {}
    ed, elo, ehi, epack, emain = procs.echo(echo_src, d, st, notes, er)
    dps += nd + ed + sw
    lo += elo
    hi += ehi
    pack += epack
    lo += nlo + swlo
    hi += nhi + swhi
    pack += nd + sw * (m["targets"] if score_sw else 1.0)
    implode_used = sum(r["mana"] for r in rows if r["leftover"])
    main = rows[0]
    abil = [r for r in rows if r["kind"] in ("main", "secondary")]
    if secs or score_sw:
        notes = [f"Rotation: the main ability deals {(rows[0]['dps'] + emain) / max(sum(r['dps'] for r in rows) + ed, 1e-300):.0%} of the build's damage (its echoes included)"] + [
            f"Rotation: {r['spec'].replace('_', ' ')} t{r['tier']} "
            + ("is never cast (costs more than the whole bar)" if r.get("blocked") else
               (f"on the leftover mana ({r['mana']:.1f}/s), " if r["leftover"] else f"at {r['share']:.0%} of its cooldown rate, ")
               + f"{r['dps']:,.0f} DPS" + (f" (Power Dump x{r['pmult']:.2f})" if r["pmult"] != 1.0 else "")) for r in abil] + notes
    else:
        if main["share"] < 1.0:
            notes.append(f"mana-limited: casting at {main['share']:.0%} of the cooldown rate")
        if main["pmult"] != 1.0:
            notes.append(f"Power Dump (sneak cast): each cast spends the mana that came in since the last sneaking cast (at most the bar), damage x{main['pmult']:.2f}")
    u = buffs["u_low_mana"]
    if st.attr.get("x:talent_tier:Depleted", 0) or st.attr.get("x:talent_tier:Methodical", 0):
        notes.append(f"below 20% mana {u:.0%} of the time (Depleted, Methodical)"
                     + ("" if ctx.knobs["mana_state_heuristic"] else " (uptime_low_mana knob)"))
    res = {
        "dps": dps, "dps_lo": lo, "dps_hi": hi, "pack_dps": pack, "targets": main["targets"],
        "hit": main["hit"] * main["pmult"] if main["hit"] is not None else None, "rate": rate,
        "steps": main.get("steps") if record else None,
        "notes": [f"{fam.main_spec} effective tier {src[0]['tier']} (learned {src[0]['learned']} + {src[0]['bonus']:+.0f} from gear)"]
        + main.get("notes", []) + notes,
        "flags": flags + [f for r in rows if not r["leftover"] for f in r["flags"]], "mana_supply": supply,
        "mana_demand": demand + sum(x["need"] * x["share"] for x in cast) + (ramp_cost if "rampage" in buffs else 0.0) + implode_used
        + buffs.get("empower_paid", 0.0),
        "source": fam.label, "resist_bonus": buffs.get("colossus_resist", 0.0), "leech_rate": leech, "lucky_rate": luck, "lucky_hits": lucky_hits, "lucky_hits_ext": lucky_ext,
        "mana_sources": fm.mana_sources(d, buffs, ctx, supply - base + steal_m),
        "mana_drains": buffs["_drains"] + ([[f"Rampage ({up:.0%} uptime)", ramp_cost]] if "rampage" in buffs else [])
        + ([["Empower", buffs["empower_paid"]]] if "empower" in buffs else [])
        + [[f"{r['spec'].replace('_', ' ')} in the rotation", r["mana"]] for r in abil],
        "rotation": rows, "fury_inc": fury[2] if fury else 1.0, "u_low_mana": u,
        "debuffed_uptime": buffs.get("debuffed_uptime", 0.0), "hit_sq": sq + er.get("sq", 0.0),
        "pd_bar": pd_bar, "debuffs": buffs.get("debuffs", []),
        "pd_mode": mode, "pd_period": buffs.get("pd_period"), "pd_duty": duty, "buff_casts": buffs.get("_buff_casts", []),
    }
    res.update(bf.survival_buffs(buffs, d, ctx))
    if weave:
        mdps = m["per_hit"] * m["aps"]
        res["weave"] = {"weapon": build.items["mainhand"].type, "swings": m["aps"], "melee_dps": mdps, "ability_dps": dps - sw,
                        "melee_raw": m["raw"], "window_share": main["window"], "dropped": main["dropped"], "mana_steal": steal_m,
                        "melee_lucky": m["lucky"], "scored": score_sw}
        if record:
            res["weave"]["clip_loss"] = clip_loss
            res["weave"]["melee_steps"] = m["steps"]
        if score_sw:
            res["notes"].append(f"weaving {build.items['mainhand'].type} swings: {m['aps']:.2f}/s, {mdps:,.0f} DPS (scored, a secondary "
                                f"source for the main-share rule); {main['window']:.0%} of plain ability hits land inside another hit's i-frame window")
        else:
            res["notes"].append(f"weaving {build.items['mainhand'].type} swings: {m['aps']:.2f}/s for leech and Mana Steal (their {mdps:,.0f} DPS is not scored); "
                                f"{main['window']:.0%} of plain ability hits land inside another hit's i-frame window")
    total = 0.0
    for r in rows:
        total += r["dps"]
    total += ed
    # The Starforge nova (a proc of the woven swings) and cloud poison are not the main ability: both count on the
    # secondary side of the main-share rule.
    cdot = buffs.get("cloud_dot")
    total += nd
    if cdot:
        total += cdot[0]
    main_part = rows[0]["dps"] + emain
    # A total below SHARE_MIN_DPS is rounding residue (leftover-mana Implode at ~1e-15), not damage: the share is 1.
    res["main_share"] = main_part / total if total > SHARE_MIN_DPS else 1.0
    res["secondary_share"] = (total - main_part) / total if total > SHARE_MIN_DPS else 0.0
    unc, full_rows = _secondary_full(src, sw0, ctx, res["flags"])
    res["secondary_share_unconstrained"] = unc
    res["secondary_full"] = full_rows
    if score_sw and total > 0 and sw / total > ctx.knobs["secondary_flag_share"]:
        res["flags"].append(f"swings: {sw / total:.0%} of damage")
    res["echo_dps"] = ed
    res["echo_main"] = emain
    if total > 0 and nd / total > ctx.knobs["secondary_flag_share"]:
        res["flags"].append(f"starforge: {nd / total:.0%} of damage")
    rows.append(_row("echo", "echo", dps=ed, reach=None))
    if nd > 0:
        rows.append(_row("Starforge nova", "nova", rate=m["aps"] / st.special["arcane_nova_hits"], dps=nd, reach=None))
    if cdot:
        res["dps"] += cdot[0]
        res["dps_lo"] += cdot[1]
        res["dps_hi"] += cdot[2]
        res["pack_dps"] += cdot[0]
        rows.append({"spec": "cloud poison", "kind": "dot", "tier": 0, "share": 1.0, "rate": cdot[3], "dps": cdot[0],
                     "pmult": 1.0, "mana": 0.0, "leftover": False, "notes": [], "flags": []})
    return res
