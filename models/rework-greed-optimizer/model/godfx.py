"""Combat effects of the custom god-node handlers (formulas in the greed-rework design notes, read from
extraction-vaults 15fe4621). Plain gear-attribute nodes are summed in build.add_god_nodes; this module turns the custom
ones into a small set of numbers the hit / mana / survival code applies:

- adds: fixed attribute additions (Grand Archmage mana and CDR cap, Deep Reserves, Speed Demon armor %, Immortal flat).
- global: GlobalDamageMultiplierRegistry factors on every non-percent player hit (situational ones at knob averages).
- stack: Crushing Blows on the non-AP multiplier registry; direct: Weaponmaster / Pincushion on direct melee.
- ap: Grand Archmage's final AP multiplier; lucky: Overcrit / Luckiest Hit; fury: Ultra Rampaging steady state.
- survival: Adaptive Armor, Temporal Shielding, Fleeting Physicality, Fury incoming, Immortal revive, regeneration.
Effects that do nothing against a hyperboss (King Hunter, Enforcer, Greedbane, Prison Warden, Magic Armor) or need an
item the model has no slot for (Sack of Mobs) stay at 1 and are listed in NOT_COUNTED.
"""
import math

from .log import fallback

# Effect -> [(attribute, param name or fixed value)] added once when the effect applies (all are minors or majors).
CUSTOM_ADDS = {
    "idona_grand_archmage": [("the_vault:mana_additive_percentile", "mana_percentile"), ("the_vault:mana_additive", "mana_flat"),
                             ("the_vault:mana_regen", "mana_regen"), ("the_vault:cooldown_reduction_cap", "cooldown_reduction_cap")],
    "tenos_deep_reserves": [("the_vault:mana_additive_percentile", "multiplier")],
    "velara_immortal": [("the_vault:health", "flat_health"), ("the_vault:armor", "flat_armor")],
}
NOT_COUNTED = {
    "idona_king_hunter": "champion/tank damage: hyperbosses are in none of those groups",
    "idona_enforcer": "horde/assassin damage: hyperbosses are in none of those groups",
    "idona_greedbane": "greed assassins and champions only",
    "idona_prison_warden": "needs Glacial Shatter expiring on the target",
    "velara_magic_armor": "hyperboss melee is not magic damage",
    "tenos_sack_of_mobs": "needs a loot sack in the offhand",
    "wendarr_the_deckless": "needs empty deck slots (the model fills the deck)",
    "velara_bounce_back": "only below 10% HP",
    "idona_cleave_expert": "cleave spill only (pack damage)",
    "idona_stack_hoarder": "stack duration x1.5^pts: stacking talents run at the uptime_kill_stacks knob",
    "tenos_gold_plating": "x0.25 damage taken, banned in the ledger",
    "tenos_domain_expansion": "x1 in the boss arena",
    "wendarr_master_imbuer": "time or loot only",
    "wendarr_legend_of_the_pear": "time or loot only",
    "wendarr_clock_artificier": "time or loot only",
    "wendarr_extender": "time or loot only",
}
# Loot-only Tenos effects (GODNODE_FORMULAS.md).
for _e in ("barter_expert", "cash_hunter", "challenge_tackler", "drillmaster", "expert_looter", "global_veins", "indiana_jones",
           "looting_engine", "massive_chests", "master_of_chests", "nose_for_treasure", "omega_vault", "wealthy_patron"):
    NOT_COUNTED["tenos_" + _e] = "loot only"
# Custom effects with no combat formula reviewed yet (none is in GODNODE_FORMULAS.md); counted as no effect.
for _e in ("idona_soulstealer", "idona_thwack", "tenos_sacked", "velara_defender_of_the_faith", "velara_field_medic",
           "velara_immune", "velara_immune_ii", "velara_perserverence", "velara_presence", "velara_sacrifice",
           "velara_sanitation", "wendarr_armored_extractors", "wendarr_efficient_steps", "wendarr_expert_eater",
           "wendarr_extraction_superviser", "wendarr_gardener", "wendarr_glutton", "wendarr_pristine_condition",
           "wendarr_pylon_whisperer", "wendarr_quick_search", "wendarr_tough_stomach"):
    NOT_COUNTED[_e] = "not reviewed for combat (no formula in GODNODE_FORMULAS.md); counted as no effect"
# Custom effects other modules apply (build.add_god_nodes, the search time lock); handler "piety" is gods.piety_units.
COUNTED_ELSEWHERE = {"idona_stack_stack_stack": "build.add_god_nodes extra stacks",
                     "wendarr_temporal_breaking": "search time lock"}
FURY_H = [0.0, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 60]


def adds(effect, params):
    out = []
    for attr, key in CUSTOM_ADDS.get(effect, []):
        out.append((attr, float(params[key]) if isinstance(key, str) else float(key)))
    if effect == "wendarr_speed_demon":
        out.append(("the_vault:armor_percentile", float(params["stat_multiplier"]) - 1.0))
    return out


def fury_table(p, kills_per_s=None):
    """Steady-state Fury vs a boss for player hits/s on it, plus fury_per_kill x kills_per_s per second from the adds
    killed during the fight (not bosses, so no kill multiplier; damage taken ignored): averages of M(F),
    M(F) cbrt(F/6500) and the incoming factor 1 + 0.5 sqrt(F/3000), simulated tick by tick over 300 s after 300 s.
    kills_per_s defaults to the stages knob."""
    if kills_per_s is None:
        from .stages import KNOBS
        kills_per_s = KNOBS["kills_per_s"]
    rows = []
    gain = p["fury_per_hit"] * p["fury_boss_hit_multiplier"]
    kill = p.get("fury_per_kill", 0.0) * kills_per_s / 20.0
    for h in FURY_H:
        f, acc, sm = 0.0, 0.0, [0.0, 0.0, 0.0]
        n = 0
        for tick in range(12000):
            acc += h / 20.0
            while acc >= 1.0:
                acc -= 1.0
                f = min(p["fury_cap"], f + gain)
            if kill > 0:
                f = min(p["fury_cap"], f + kill)
            if tick % 10 == 9:
                e = 1.0 if f <= p["fury_decay_scale_from"] else f / p["fury_decay_scale_divisor"]
                f *= (p["fury_decay_per_tick"] ** e) ** 10
                if f < p["fury_floor"]:
                    f = 0.0
            if tick >= 6000:
                m = fury_m(f, p)
                sm[0] += m
                sm[1] += m * (f / p["cdm_additive_divisor"]) ** (1.0 / 3.0)
                sm[2] += 1.0 + p["incoming_scale"] * math.sqrt(f / p["incoming_divisor"])
                n += 1
        rows.append([h, sm[0] / n, sm[1] / n, sm[2] / n])
    return rows


CONCENTRATE_MAX_TICKS = 600.0
CONCENTRATE_STEPS = 20


def concentrate_steps(tier, rework):
    """Procs one Empower chain can take: rework (user ruling 2026-10-19) caps the stacks one effect can add at
    10 + floor(ability level / 3) (a new Concentrate attribute; the level is the effective tier); 0.34.1 has no cap in
    game (the chain never ends at 80%+ cooldown reduction), so the model caps it at CONCENTRATE_STEPS."""
    return 10 + int(tier) // 3 if rework else CONCENTRATE_STEPS


def concentrate_ladder(base_amp, chance, D, period, K, steps=CONCENTRATE_STEPS):
    """Concentrate's Empower chain (ConcentrateAbility.doAction): (average amplifier while active, active share).

    Empower procs arrive every period / K ticks (casts every `period` ticks bring K procs each). The first starts
    Empower at base_amp for min(600, D) ticks; each later one, while the remaining duration d is below 600, raises the
    amplifier by min(chance / (1 + 0.1 amp), 2) and sets d to min(600, d + max(1, D (1 - d / 600))). A chain ends when
    its remaining duration runs out before the next proc, or after `steps` procs (then it runs out unrefreshed);
    the next proc after it ends restarts at base_amp. Time averages over one chain and the gap to that proc."""
    if K <= 0 or period <= 0 or D <= 0:
        return 0.0, 0.0
    dt = period / K
    amp = float(base_amp)
    rem = min(CONCENTRATE_MAX_TICKS, D)
    area = t_act = 0.0
    for _ in range(1, steps):
        if rem <= dt * (1.0 + 1e-9):
            break
        area += amp * dt
        t_act += dt
        rem -= dt
        amp += min(chance / (1.0 + 0.1 * amp), 2.0)
        rem = min(CONCENTRATE_MAX_TICKS, rem + max(1.0, D * (1.0 - rem / CONCENTRATE_MAX_TICKS)))
    area += amp * rem
    t_act += rem
    span = math.ceil(t_act / dt - 1e-9) * dt
    return area / t_act, t_act / span


def fury_m(f, p):
    if f <= p["cdm_sqrt_from"]:
        return p["cdm_multiplier_base"] ** (f / 100.0)
    return p["cdm_multiplier_base"] ** (p["cdm_sqrt_from"] / 100.0) + math.sqrt(f / p["cdm_sqrt_from"] - 1.0)


def fury_at(table, h):
    """(<M>, <M cbrt(F/6500)>, <incoming>) at h hits/s, linear between table rows, flat beyond the last."""
    if h <= table[0][0]:
        return tuple(table[0][1:])
    for a, b in zip(table, table[1:]):
        if h <= b[0]:
            t = (h - a[0]) / (b[0] - a[0])
            return tuple(a[i] + t * (b[i] - a[i]) for i in (1, 2, 3))
    return tuple(table[-1][1:])


def paced_avg(ref, minutes):
    """Average of sqrt((ref + m)/ref) for minutes left m uniform on [0, minutes]."""
    if minutes <= 0:
        return 1.0
    return (2.0 / (3.0 * minutes)) * ref * (((ref + minutes) / ref) ** 1.5 - 1.0)


def under_pressure_avg(window_min, minutes, mx):
    """Average of 1 + (mx - 1)(1 - min(m, window)/window) for m uniform on [0, minutes]."""
    if minutes <= 0:
        return mx
    w = min(window_min, minutes)
    return 1.0 + (mx - 1.0) * (w - w * w / (2.0 * window_min)) / minutes


def compute(effects, defs, knobs):
    """effects: effect -> (placements, scale) for the custom effects that apply; defs: effect -> params dict.
    Returns the constant part of the numbers (attribute-dependent ones are finished in stats.derive)."""
    read = set()

    def on(e):
        read.add(e)
        return effects.get(e, (0, 0.0))[0] > 0

    def prm(e, k):
        return float(defs[e][k])

    g = {}
    parts = []
    if on("idona_banked_anger"):
        b = prm("idona_banked_anger", "base")
        parts.append(("Banked Anger", math.log(b + knobs["god_banked_damage_taken"]) / math.log(b)))
    if on("idona_surrounded"):
        parts.append(("Surrounded", 1.0 + prm("idona_surrounded", "per_mob") * knobs["god_surrounded_mobs"]))
    if on("idona_under_pressure"):
        parts.append(("Under Pressure", under_pressure_avg(prm("idona_under_pressure", "window_ticks") / 1200.0,
                                                           knobs["god_vault_minutes"], prm("idona_under_pressure", "max"))))
    if on("wendarr_paced_strikes"):
        parts.append(("Paced Strikes", paced_avg(prm("wendarr_paced_strikes", "reference_minutes"), knobs["god_vault_minutes"])))
    if on("wendarr_edge_of_time"):
        parts.append(("Edge of Time", prm("wendarr_edge_of_time", "multiplier")))
    if on("wendarr_speed_demon"):
        parts.append(("Speed Demon", prm("wendarr_speed_demon", "stat_multiplier")))
    if on("velara_immortal"):
        parts.append(("Immortal (damage dealt)", prm("velara_immortal", "damage_multiplier")))
    glob = 1.0
    for _, x in parts:
        glob *= x
    g["global"] = glob
    g["global_parts"] = parts
    g["sneaky"] = prm("idona_sneaky_advantage", "per_effect") if on("idona_sneaky_advantage") else 0.0
    g["kinetic"] = prm("idona_kinetic_impact", "per_percent") if on("idona_kinetic_impact") else 0.0
    g["greed"] = prm("tenos_unstoppable_greed", "ratio") if on("tenos_unstoppable_greed") else 0.0
    g["stack"] = prm("idona_crushing_blows", "multiplier") if on("idona_crushing_blows") else 1.0
    g["weaponmaster"] = prm("idona_weaponmaster", "two_handed") if on("idona_weaponmaster") else 1.0
    g["pincushion"] = prm("idona_pincushion", "per_hit") if on("idona_pincushion") else 0.0
    g["true_rage"] = prm("idona_true_rage", "efficiency") if on("idona_true_rage") else 0.0
    g["overcrit"] = 1.0 if on("idona_overcrit") else 0.0
    g["luckiest"] = prm("idona_luckiest_hit", "chance_scale") if on("idona_luckiest_hit") else 0.0
    g["archmage"] = on("idona_grand_archmage")
    g["ap"] = prm("idona_grand_archmage", "ability_damage") if g["archmage"] else 1.0
    g["power_dump"] = prm("idona_power_dump", "per_mana") if on("idona_power_dump") else 0.0
    g["power_dump_ttl"] = prm("idona_power_dump", "surplus_ttl_ticks") if on("idona_power_dump") else 0.0
    g["fury"] = on("idona_ultra_rampaging")
    g["health_mult"] = prm("velara_immortal", "health_multiplier") if on("velara_immortal") else 1.0
    am = 1.0
    if on("velara_immortal"):
        am *= prm("velara_immortal", "armor_multiplier")
    if on("velara_the_stonewall"):
        am *= prm("velara_the_stonewall", "armor_multiplier")
    if on("velara_cactus"):
        am *= prm("velara_cactus", "armor_multiplier")
    g["armor_mult"] = am
    g["he_mult"] = prm("velara_immortal", "healing_multiplier") if on("velara_immortal") else 1.0
    g["he_flow"] = prm("velara_healing_flow", "per_mana_regen") if on("velara_healing_flow") else 0.0
    g["malediction"] = prm("velara_malediction", "forced_healing") if on("velara_malediction") else 0.0
    g["thorns_m"] = prm("velara_cactus", "thorns_multiplier") * 0.75 if on("velara_cactus") else 1.0
    g["thorns_f"] = prm("velara_cactus", "thorns_multiplier") if on("velara_cactus") else 1.0
    g["counter"] = prm("velara_counterstrike", "chance") if on("velara_counterstrike") else 0.0
    g["revive"] = on("velara_immortal")
    regen = 0
    if on("velara_indomitable"):
        regen += int(prm("velara_indomitable", "regeneration_levels"))
    if on("velara_immortal"):
        regen += int(prm("velara_immortal", "regeneration_levels"))
    g["regen_levels"] = regen
    g["adaptive"] = (prm("velara_adaptive_armor", "per_stack"), prm("velara_adaptive_armor", "max_stacks")) \
        if on("velara_adaptive_armor") else (0.0, 0.0)
    g["shield"] = prm("wendarr_temporal_shielding", "reduction") if on("wendarr_temporal_shielding") else 1.0
    g["fleeting"] = prm("velara_fleeting_physicality", "damage_multiplier") if on("velara_fleeting_physicality") else 0.0
    g["mana_starved"] = prm("tenos_mana_starved", "max_bonus") if on("tenos_mana_starved") else 0.0
    g["super_stacker"] = prm("idona_super_stacker", "multiplier") if on("idona_super_stacker") else 1.0
    g["stack_hoarder"] = prm("idona_stack_hoarder", "multiplier") if on("idona_stack_hoarder") else 1.0
    g["utilized"] = prm("velara_utilized", "ability_levels") if on("velara_utilized") else 0.0
    g["plushie_lover"] = on("wendarr_plushie_lover")
    g["steadfast"] = ((prm("velara_steadfast", "knockback_floor"), prm("velara_steadfast", "armor_per_excess"))
                      if on("velara_steadfast") else None)
    g["speed_mult"] = prm("velara_the_stonewall", "speed_multiplier") if on("velara_the_stonewall") else 1.0
    for e, (n, _) in effects.items():
        if (n > 0 and e not in read and e not in CUSTOM_ADDS and e not in NOT_COUNTED and e not in COUNTED_ELSEWHERE
                and defs.get(e, {}).get("handler") != "piety"):
            fallback(f"godfx-unknown-{e}", f"god effect {e} (handler {defs.get(e, {}).get('handler')}) has no combat "
                     "formula and no NOT_COUNTED reason; counted as no effect")
    return g


def regen_hps(levels):
    """Vanilla Regeneration with amplifier levels - 1: 1 HP every (50 >> amp) ticks (every tick at 0) [inferred]."""
    if levels <= 0:
        return 0.0
    period = 50 >> (levels - 1) if levels - 1 < 31 else 0
    return 20.0 / max(1, period)


def adaptive_factors(per, mx, hits):
    """Adaptive Armor: hit k of a run from one attacker takes x(1 - per min(k, mx)); (first hit, average over hits)."""
    if per <= 0:
        return 1.0, 1.0
    f = [1.0 - per * min(k, mx) for k in range(1, int(hits) + 1)]
    return f[0], sum(f) / len(f)
