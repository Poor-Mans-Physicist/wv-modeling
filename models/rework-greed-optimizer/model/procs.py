"""Events, triggers and debuffs on the hyperboss (the proc layer; the kernel mirrors it in kernel/src/procs.rs).

The family first works out its event rates per second: boss hits that land on the player, boss hits blocked or dodged,
normal weapon hits on the boss, lucky hits, and casts. Every trigger the build owns turns those rates into debuff
sources on the boss, each an (effect kind, amplifier, uptime). Sources of one kind combine:

* Vulnerable keeps the strongest active application (vanilla MobEffectInstance.update keeps the higher amplifier), so
  its expected bonus sums over the sources sorted by amplifier: each counts while it is up and every stronger one is
  down. Sources are independent. The proposed Frozen Orb rule (knob frozen_orb_additive) adds its value once to
  whatever Vulnerable is up instead. No source is capped (user ruling 2026-10-15; the game clamps the Totem damage
  etching, Lucky Hit and Blizzard shards to min(8, level) - 1).
* Every other kind only counts for Sneaky Advantage (IdonaTargeting.countNegativeEffects: one per harmful effect type
  on the target), as the chance at least one source is up.

Uptimes: a cast on cooldown keeps its effect up min(1, duration / period); random procs at rate r keep it up
1 - exp(-r x duration); a mix is 1 - (1 - periodic) x exp(-r x duration)."""
import math

# Effect kinds (kernel procs.rs uses the same order).
VULNERABLE, HYPOTHERMIA, SLOWNESS, POISON, WITHER, WEAKNESS, UNLUCK, FEAR, BURN, CURSE, MARK, CHILLED = range(12)
KIND_NAMES = ["Vulnerable", "Hypothermia", "Slowness", "Poison", "Wither", "Weakness", "Bad Luck", "Fear", "Burn",
              "Necromancy curse", "Totem of Hatred mark", "Chilled"]
N_KINDS = len(KIND_NAMES)
# Vanilla effect ids carried by effect clouds -> kind (Bleed is denied on hyperbosses; healing clouds hit the player).
CLOUD_KINDS = {"minecraft:poison": POISON, "minecraft:wither": WITHER, "minecraft:weakness": WEAKNESS,
               "minecraft:slowness": SLOWNESS, "minecraft:unluck": UNLUCK, "the_vault:chilled": CHILLED}
# Chilled lands on the hyperboss only in the rework (proposed, user 2026-10-21; VaultBossEntity rejects it as shipped).
CHILL_REWORK_ONLY = {"the_vault:chilled"}


def cloud_kinds():
    """CLOUD_KINDS this ledger reads: the Chilling clouds only in the rework (the boss rejects Chilled in 0.34.1)."""
    from . import stages
    if stages.LEDGER == "rework":
        return CLOUD_KINDS
    return {k: v for k, v in CLOUD_KINDS.items() if k not in CHILL_REWORK_ONLY}


def boss_chill(sources):
    """The boss's chill level from the debuff sources (label, kind, level, uptime, add): the strongest Chilled source
    that is up at all (boss_chill_level); 0 without one."""
    return boss_chill_level(max([s[2] for s in sources if s[1] == CHILLED and s[3] > 0], default=0))


def boss_chill_level(L):
    """Proposed hyperboss chill (user, 2026-10-21): a source applying chill level L (amplifier + 1) chills the boss at
    clamp(floor(L / 5), 1, 5); 0 without a source."""
    if L <= 0:
        return 0
    return max(1, min(5, L // 5))
# HexEffects.HEX_EFFECTS: nine equally weighted effects; (kind, ticks). Bleed (denied on hyperbosses) and, in 0.34.1,
# Chilled (rejected by VaultBossEntity) never land but still use up their share of the rolls. In the rework Chilled
# (amplifier 0, chill level 1) lands (HEX_EFFECTS_REWORK).
HEX_EFFECTS = [(SLOWNESS, 80), (POISON, 100), (WITHER, 80), (WEAKNESS, 80), (UNLUCK, 100), (None, 100), (None, 80),
               (VULNERABLE, 80), (FEAR, 60)]
HEX_EFFECTS_REWORK = HEX_EFFECTS[:6] + [(CHILLED, 80)] + HEX_EFFECTS[7:]
HEX_VULN_AMP = 0


def luck_mult(st):
    """LuckHelper.getLuckAffectedChance: x 1.15 per level of Luck the player has."""
    return 1.15 ** st.luck if st.luck > 0 else 1.0


def periodic_up(duration, period):
    return min(1.0, duration / period) if period > 0 else 0.0


def proc_up(rate, duration, base=0.0):
    """Uptime of an effect applied by random procs at `rate` per second on top of a periodic uptime `base`."""
    if rate <= 0 or duration <= 0:
        return base
    return 1.0 - (1.0 - base) * math.exp(-rate * duration)


def events(d, ctx, normal, lucky):
    """Event rates per second: normal weapon hits on the boss, lucky hits, boss hits that land (the attack stream's
    d["boss_hits_landed"]) and boss attacks that reach the player and are blocked or dodged."""
    guard = 1.0 - (1.0 - d["block"]) * (1.0 - d["dodge"])
    return {"normal": normal, "lucky": lucky, "hurt": d["boss_hits_landed"], "guarded": d["boss_hits_reaching"] * guard}


def gear_sources(st, d, ev, out):
    """Debuffs from on-hit gear: hexing, burning hits and effect clouds (normal non-projectile hits; luck-affected)."""
    from . import stages
    lm = luck_mult(st)
    hx = d.get("hexing", 0.0)
    if hx > 0 and ev["normal"] > 0:
        hexes = HEX_EFFECTS_REWORK if stages.LEDGER == "rework" else HEX_EFFECTS
        rate = min(1.0, hx * lm) * ev["normal"] / len(hexes)
        for kind, ticks in hexes:
            if kind is not None:
                out.append(("Hexing", kind, HEX_VULN_AMP if kind == VULNERABLE else 0, proc_up(rate, ticks / 20.0), False))
    br = d.get("burning", 0.0)
    if br > 0 and ev["normal"] > 0:
        out.append(("Burning hit", BURN, 0, proc_up(min(1.0, br * lm) * ev["normal"], 10.0), False))
    from .buffs import cloud_trigger
    for c in st.clouds:
        rate = cloud_trigger(c, d) * ev["normal"]
        for kind, ticks in c["effects"]:
            out.append((f"Effect cloud ({c['name']})", kind, c["chill"] if kind == CHILLED else 0,
                        proc_up(rate, (c["ticks"] + ticks) / 20.0), False))


LUCKY_NOVA_HYPOTHERMIA_S = 20.0


def lucky_nova(build, ctx, st, d, ev, sources, notes):
    """Lucky Nova etching (LuckyHitTalent): every lucky hit casts Frost Nova (Nova_Slow) at the etching's level for
    free: Hypothermia (400 ticks) on the boss and, with the Frozen Orb modification, its Vulnerable for 4 x the tier's
    duration. The Vulnerable procs merge into an existing Frost Nova source (same amplifier): u = 1 - (1 - u) e^(-L t)."""
    from .abilities import etch_value
    lv = etch_value(build, "etching_lucky_frost_nova")
    L = ev.get("lucky", 0.0)
    if not lv or L <= 0:
        return
    sources.append(("Lucky Nova etching (Frost Nova)", HYPOTHERMIA, 0, proc_up(L, LUCKY_NOVA_HYPOTHERMIA_S), False))
    lvl = st.special.get("frost_nova_vulnerability", 0.0)
    if lvl <= 0 and ctx.gods is None:
        return
    if "Nova_Slow" not in ctx.abilities:
        from .log import fallback
        fallback("lucky-nova-no-spec", "Lucky Nova etching: Nova_Slow is not in this context's abilities; its Frozen Orb Vulnerable is skipped")
        return
    from .families import tier_cfg
    n = len(ctx.abilities["Nova_Slow"].get("tiers", [ctx.abilities["Nova_Slow"]]))
    tier = max(1, min(int(lv), n))
    tc = tier_cfg(ctx, "Nova_Slow", tier, build)
    if ctx.gods is not None:
        sources.append(("Lucky Nova etching (Chilled)", CHILLED, int(tc["amplifier"]) + 1,
                        proc_up(L, tc["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0), False))
    if lvl <= 0:
        return
    dur = 4.0 * tc["durationTicks"] * (1.0 + d["effect_duration"]) / 20.0
    for i, (label, kind, amp, u, add) in enumerate(sources):
        if kind == VULNERABLE and add:
            sources[i] = (label + " + Lucky Nova", kind, amp, proc_up(L, dur, u), add)
            break
    else:
        sources.append(("Frozen Orb (Lucky Nova)", VULNERABLE, int(lvl), proc_up(L, dur), True))
    notes.append(f"Lucky Nova etching: {L:.2f} free Frost Novas (tier {tier}) per second from lucky hits")


def combine(sources, fixed, additive):
    """(Vulnerable multiplier, expected count of harmful effect kinds, per-kind uptime) from debuff sources
    (label, kind, amplifier, uptime, add_once). `fixed` = VULN-OFF1 fixed (x(1 + 0.1 (amp + 1)) instead of 0.1 amp)."""

    def bonus(a):
        return 0.1 * (a + 1) if fixed else 0.1 * a

    vul = sorted([s for s in sources if s[1] == VULNERABLE and not s[4]], key=lambda s: -s[2])
    adds = [s for s in sources if s[1] == VULNERABLE and s[4]]
    e_max, miss = 0.0, 1.0
    states = []
    for _, _, a, u, _ in vul:
        states.append((a, u * miss))
        e_max += bonus(a) * u * miss
        miss *= 1.0 - u
    e = e_max
    if adds and additive:
        lv, uo = adds[0][2], adds[0][3]
        with_add = sum(bonus(a + lv) * w for a, w in states) + miss * bonus(lv - 1)
        e = uo * with_add + (1.0 - uo) * e_max
    up = [0.0] * N_KINDS
    for _, kind, _, u, _ in sources:
        up[kind] = 1.0 - (1.0 - up[kind]) * (1.0 - u)
    return 1.0 + e, sum(up), up


def finalize(build, ctx, st, d, buffs, ev, notes):
    """Set buffs["vulnerable"] and buffs["sneaky"] from the cast debuffs buffs_for collected plus the event-driven ones."""
    from .damage import bug
    sources = list(buffs.get("_cast_debuffs", []))
    if ev.get("lucky", 0.0) > 0 and buffs.get("_lucky_vuln"):
        lvl, dur = buffs["_lucky_vuln"]
        sources.append(("Lucky Vulnerable etching", VULNERABLE, lvl - 1, proc_up(ev["lucky"], dur), False))
    lucky_nova(build, ctx, st, d, ev, sources, notes)
    gear_sources(st, d, ev, sources)
    from .buffs import frozen_impact
    frozen_impact(build, ctx, st, d, ev, sources)
    additive = bool(ctx.knobs["frozen_orb_additive"])
    if not additive:
        sources = [(n, k, a - 1 if add else a, u, False) if add else (n, k, a, u, add) for n, k, a, u, add in sources]
    vm, count, up = combine(sources, not bug(build, "VULN-OFF1"), additive)
    buffs["debuffs"] = sources
    miss = 1.0
    for u in up:
        miss *= 1.0 - u
    buffs["debuffed_uptime"] = 1.0 - miss
    if any(s[1] == VULNERABLE for s in sources):
        buffs["vulnerable"] = vm
        vs = [f"{n} (amp {a}{', added once' if add else ''}, {u:.0%})" for n, k, a, u, add in sources if k == VULNERABLE]
        notes.append(f"Vulnerable on the boss: x{vm:.2f} expected from " + ", ".join(vs)
                     + f" (VULN-OFF1 {'bugged' if bug(build, 'VULN-OFF1') else 'fixed'})")
    if st.gfx and st.gfx["sneaky"]:
        buffs["sneaky"] = 1.0 + st.gfx["sneaky"] * count
        terms = [(KIND_NAMES[k], up[k]) for k in range(N_KINDS) if up[k] > 0]
        buffs["sneaky_terms"] = terms
        notes.append("Sneaky Advantage: harmful effects on the boss (uptime) " + (", ".join(f"{n} {u:.0%}" for n, u in terms) or "none")
                     + f" -> x{buffs['sneaky']:.2f}")
    cloud_poison(build, ctx, st, d, buffs, notes)


def cloud_poison(build, ctx, st, d, buffs, notes):
    """Cloud poison as a damage stream (buffs["cloud_dot"] = (dps, lo, hi, hits/s)): flat amplifier + 1 per hit through
    the cloud column of the DoT multiplier table: no damage increase or normal registry; ability-power multipliers,
    the god global registry, Sneaky Advantage, Vulnerable, Hexbreaker, Executioner and the Dice apply; never lucky,
    echoed or clipped (shotgunAttack)."""
    cp = buffs.get("_cloud_poison")
    if not cp:
        return
    from .damage import HitClass, evaluate_hit
    raw, hits = cp
    v, lo, hi, _ = evaluate_hit(raw, 0.0, HitClass("cloud poison", ap=True, iframe="shotgun"), d, st, build, ctx, 0.0, buffs)
    buffs["cloud_dot"] = (v * hits, lo * hits, hi * hits, hits)
    notes.append(f"Cloud poison: {hits:.2f} hits/s of {raw:.0f} (the strongest poison amplifier + 1) -> {v * hits:,.1f} DPS")


def tie_key(x):
    """Sort key for a non-negative value rounded to 30 mantissa bits (~1e-9 relative), so values that are equal in
    exact arithmetic but differ by rounding noise sort as ties (the kernel's procs::tie_key does the same bit
    arithmetic)."""
    if x == math.inf:
        return 1 << 62
    import struct
    b = struct.unpack("<q", struct.pack("<d", x))[0]
    return (b + (1 << 21)) >> 22


_ECHO = None


def echo_table():
    """data/echo_table.json (tools/echo_table.py): V[N-1][a][q] on a = 10^(-2 + k/8), q = j/20, and the
    Reverberating Echo table v_reverb on the same grid (None when the file has none)."""
    global _ECHO
    if _ECHO is None:
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "echo_table.json")
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        _ECHO = {"n_max": t["n_max"], "na": len(t["a"]), "nq": len(t["q"]), "v": t["v"], "v_reverb": t.get("v_reverb")}
    return _ECHO


def echo_value(a, q, n, reverb=False):
    """Echo damage per second in units of the stored damage S for originals arriving at `a` per second, re-proc
    chance q and N pulses per echo: bilinear in (log10 a, q) on the simulated table; below the grid V is linear in a
    (lone chains; quadratic for Reverberating Echo, which needs a live echo when the next proc lands), above it the
    target is saturated at the grid's last column. reverb reads the Reverberating Echo table."""
    t = echo_table()
    if a <= 0 or n <= 0:
        return 0.0
    if n > t["n_max"]:
        from .log import fallback
        fallback(f"echo-n-{n}", f"echo pulses per proc {n} above the table's {t['n_max']}; using {t['n_max']}")
        n = t["n_max"]
    if reverb and t["v_reverb"] is None:
        from .log import fallback
        fallback("echo-reverb-table", "data/echo_table.json has no v_reverb (tools/echo_table.py --reverb); Reverberating Echo uses the pulse table")
        reverb = False
    v = (t["v_reverb"] if reverb else t["v"])[n - 1]
    na, nq = t["na"], t["nq"]
    x = (math.log10(a) + 2.0) * 8.0
    scale = 1.0
    if x < 0.0:
        scale = a / 0.01
        if reverb:
            scale = scale * scale
        x = 0.0
    x = min(x, na - 1.0)
    i = min(int(x), na - 2)
    fx = x - i
    y = min(max(q, 0.0), 1.0) * (nq - 1)
    j = min(int(y), nq - 2)
    fy = y - j
    lo = v[i][j] * (1.0 - fy) + v[i][j + 1] * fy
    hi = v[i + 1][j] * (1.0 - fy) + v[i + 1][j + 1] * fy
    return scale * (lo * (1.0 - fx) + hi * fx)


def echo(srcs, d, st, notes, rates=None):
    """Echoing on the boss from every eligible hit stream (the target holds one Echoing effect; tools/echo_table.py).

    srcs: (label, landing hits per second, stored damage S, pulse value, pulse lo, pulse hi, targets, main ability's?); a pulse value of 0
    marks a stream whose damage is not scored (woven swings) but whose echoes still occupy the slot. Originals arrive at
    rate x p with p = luck(chance); pulses re-proc at q = luck(sqrt(chance)); N = floor(1 + effect duration) pulses.
    A stronger echo takes the slot and a weaker one waits, so with streams sorted strongest first the slot holds one of
    the first j streams whenever their combined stream would keep it busy: stream j owns V(A_j) - V(A_(j-1)) of the
    pulse damage (A_j = summed arrival rate of the j strongest). Exact for one stream; against the simulator over random
    2-3 stream mixes the mean error is +2% (worst +14%, two streams of nearly equal strength at 50% echo chance).
    Returns (dps, lo, hi, pack, the main ability's part of dps); a `rates` dict gets "sq", the sum over streams of
    pulses/s x pulse damage^2 (The Dice's effective hit count, evaluate.dice_percentile)."""
    c = d["echo_chance"]
    if c <= 0 or not srcs:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    reverb = "etch:woldsvaults:reverberation" in st.flags
    lm = luck_mult(st)
    p = min(1.0, c * lm)
    q = min(1.0, math.sqrt(c) * lm)
    n = int(math.floor(1.0 + d["effect_duration"] + 1e-9))
    a = prev = 0.0
    dps = lo = hi = pack = main = sq = 0.0
    for j in sorted(range(len(srcs)), key=lambda j: -tie_key(srcs[j][2])):
        aj = srcs[j][1] * p
        if aj <= 0:
            continue
        a += aj
        v = echo_value(a, q, n, reverb)
        w = v - prev
        prev = v
        sq += w * srcs[j][3] * srcs[j][3]
        dps += w * srcs[j][3]
        if srcs[j][7]:
            main += w * srcs[j][3]
        lo += w * srcs[j][4]
        hi += w * srcs[j][5]
        pack += w * srcs[j][3] * srcs[j][6]
    if rates is not None:
        rates["sq"] = sq
    if prev > 0:
        mode = ("Reverberating Echo: each proc that finds a live echo deals it at once (decaying) instead of pulses; "
                if reverb else "")
        notes.append(f"Echo: {mode}{a:.2f} echoes/s on the boss (chance {p:.0%}, re-proc {q:.0%}, {n} pulses each) give {prev:.2f} x the stored "
                     f"damage per second -> {dps:,.0f} DPS (" + ", ".join(f"{s[0]} {s[1] * p:.2f}/s" for s in srcs) + ")")
    return dps, lo, hi, pack, main
