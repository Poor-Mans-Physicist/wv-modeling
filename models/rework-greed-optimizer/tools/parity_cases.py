"""Directed parity cases: named builds that each exercise one feature, scored by both engines in kernel_parity.py next
to the random builds.

Register a case with the `case` decorator on a constructor `fn(ctx, fam, rng) -> Build | None`:

    @case("frost-nova-seal-weapon", "ability:Smite_Base", stage="max", ledgers=("rework", "0.34.1"),
          check=lambda r, b, ctx: None if ... else "feature not exercised")
    def _frost_nova_seal_weapon(ctx, fam, rng):
        b = base(ctx, fam, rng)
        ...
        return b

The constructor returns None when the context cannot express the feature (reported as a skip, which fails the run
unless the case sets optional=True). `check(result, build, ctx)` runs on the Python evaluate() result and returns an
error string when the build no longer exercises the feature, so a case cannot silently stop testing what it names.
Feature packages append their cases at the end of this file (one block per package); kernel_parity runs every case
whose ledger matches WV_LEDGER, for each of its modes.
"""
CASES = []


def case(name, family, stage="max", modes=("bugged", "intended"), ledgers=("rework", "0.34.1"), check=None, optional=False):
    if any(c["name"] == name for c in CASES):
        raise ValueError(f"parity case {name!r} registered twice")

    def deco(fn):
        CASES.append({"name": name, "family": family, "stage": stage, "modes": tuple(modes), "ledgers": tuple(ledgers),
                      "build": fn, "check": check, "optional": optional})
        return fn
    return deco


def base(ctx, fam, rng, steps=60):
    """A random legal build of the family (search.initial_build plus a short random walk)."""
    from model import search
    b = search.initial_build(ctx, fam, rng)
    for _ in range(steps):
        c = search.mutate(ctx, b, fam, rng)
        if c is not None:
            b = c
    return b


def pool_affix(ctx, gear_type, pool, pred):
    """First affix of ctx.pools[gear_type][pool] matching pred, or None."""
    return next((a for a in ctx.pools.get(gear_type, {}).get(pool, []) if pred(a)), None)


def learn(ctx, b, spec, tier=None):
    """Learn `spec` at `tier` (default its max), dropping other specs of the same skill."""
    skill = ctx.abilities[spec]["skill"]
    for s in [s for s in b.abilities if s != spec and ctx.abilities.get(s, {}).get("skill") == skill]:
        del b.abilities[s]
    b.abilities[spec] = ctx.ability_max(spec) if tier is None else tier


def raw_key(a, key):
    return a.raw.get(key) if isinstance(a.raw, dict) else None


# F-b (contract, solver, housekeeping) -------------------------------------------------------------------------------

def _special(r, name):
    return r["stats"].special.get(name, 0.0)


def _frost_nova_once(ctx, fam, rng, seal_slot):
    from model import search
    b = base(ctx, fam, rng)
    learn(ctx, b, "Nova_Slow", 1)
    for slot in (seal_slot, "helmet", "boots"):
        if b.items[slot].unique is not None:
            b.items[slot] = search.new_item(ctx, slot, b.items[slot].type, rng, b)
    want = lambda a: a.attribute == "the_vault:ability_on_damage" and raw_key(a, "abilityId") == "Nova_Slow"
    types = {"mainhand": ctx.mainhands(fam), "offhand": ctx.offhand_types}.get(seal_slot, [])
    for ty in [b.items[seal_slot].type] + list(types):
        if pool_affix(ctx, ty, "CORRUPTED_IMPLICIT", want) is not None:
            if ty != b.items[seal_slot].type:
                b.items[seal_slot] = search.new_item(ctx, seal_slot, ty, rng, b)
            break
    it = b.items[seal_slot]
    on_dmg = pool_affix(ctx, it.type, "CORRUPTED_IMPLICIT", want)
    helm = b.items["helmet"] if seal_slot != "helmet" else b.items["boots"]
    vuln = pool_affix(ctx, helm.type, "CORRUPTED_IMPLICIT",
                      lambda a: raw_key(a, "specialModificationKey") == "the_vault:frost_nova_vulnerability")
    if on_dmg is None or vuln is None:
        return None
    for x in b.items.values():
        if x.unique is None:
            x.seal = None
    it.seal = on_dmg
    helm.seal = vuln
    return b


def _frost_nova(ctx, fam, rng, seal_slot):
    """Frost Nova at tier 1 with the Frozen Orb vulnerability seal on armour and the on-damage seal in `seal_slot`, on
    a build whose cooldown reduction is low enough that casts alone leave the Vulnerable uptime below 1 (so the
    seal's proc chance moves the score)."""
    from model.evaluate import evaluate
    if "Nova_Slow" not in ctx.abilities:
        return None
    for _ in range(40):
        b = _frost_nova_once(ctx, fam, rng, seal_slot)
        if b is None:
            return None
        r = evaluate(b, ctx)
        if r["derived"]["cooldown_reduction"] >= 0.4:
            continue
        if seal_slot == "chestplate" or _seal_moves_score(ctx, b, seal_slot, r):
            return b
    return None


def _seal_moves_score(ctx, b, seal_slot, r):
    """The on-damage seal moved from `seal_slot` to the chestplate changes the Python DPS (so an engine that counted
    it on `seal_slot` would disagree)."""
    from model.evaluate import evaluate
    c = b.clone()
    if c.items["chestplate"].unique is not None:
        return False
    c.items["chestplate"].seal = c.items[seal_slot].seal
    c.items[seal_slot].seal = None
    a, x = r["dps_no_hp_scaling"], evaluate(c, ctx)["dps_no_hp_scaling"]
    return abs(a - x) > 1e-6 * max(1.0, abs(a))


def _fn_ignored(r, b, ctx):
    ok = _special(r, "frost_nova_vulnerability") > 0 and _special(r, "frost_nova_on_damage") == 0
    return None if ok else "the non-armour seal counted (or the vulnerability seal is missing)"


for _stage in ("early", "end"):
    for _slot in ("mainhand", "offhand"):
        case(f"frost-nova-seal {_slot} {_stage}", "melee:sword", stage=_stage, check=_fn_ignored, optional=True)(
            lambda ctx, fam, rng, _s=_slot: _frost_nova(ctx, fam, rng, _s))
    case(f"frost-nova-seal chestplate {_stage}", "melee:sword", stage=_stage,
         check=lambda r, b, ctx: None if _special(r, "frost_nova_on_damage") > 0 else "the armour seal did not count")(
        lambda ctx, fam, rng: _frost_nova(ctx, fam, rng, "chestplate"))


def _cascade_check(r, b, ctx):
    return None if r["derived"].get("cascade_l", 0.0) > 0 else "Arcane Cascade rate is zero"


def _arcane_strike(ctx, fam, rng):
    """A build with Arcane Strike at its top tier and lucky hits (so the cascade solver runs)."""
    from model.evaluate import evaluate
    if "Arcane_Strike" not in ctx.talents:
        return None
    for _ in range(20):
        b = base(ctx, fam, rng)
        b.talents["Arcane_Strike"] = ctx.talent_max("Arcane_Strike")
        if evaluate(b, ctx)["derived"].get("cascade_l", 0.0) > 0:
            return b
    return None


for _fam in ("ability:Fangs_Maw", "melee:sword"):
    for _stage in ("end", "max"):
        case(f"arcane-cascade {_fam} {_stage}", _fam, stage=_stage, check=_cascade_check)(_arcane_strike)


def _spectral(ctx, fam, rng):
    if "Battle_Cry_Spectral_Strike" not in ctx.abilities:
        return None
    b = base(ctx, fam, rng)
    learn(ctx, b, "Battle_Cry_Spectral_Strike")
    return b


case("spectral-strike", "ability:Smite_Base", ledgers=("rework",),
     check=lambda r, b, ctx: None if "Battle_Cry_Spectral_Strike" in b.abilities else "not learned")(_spectral)
case("spectral-strike melee", "melee:axe", ledgers=("rework",),
     check=lambda r, b, ctx: None if "Battle_Cry_Spectral_Strike" in b.abilities else "not learned")(_spectral)


# Unique and Fireball pass (user rulings 2026-10-21): Steel Inferno, Fireball i-frames, storm occupancy, Stormcrown
# tracking, homing Scatter javelins, hyperboss chill ---------------------------------------------------------------

def _uq(ctx, b, uid, rng):
    from parity_cases_etchings import unique
    return unique(ctx, b, uid, rng)


def _note(r, text):
    """True when some rotation row's notes contain `text`."""
    return any(text in n for x in r["family_result"].get("rotation", []) for n in x.get("notes", []))


def _chill_src(r, label):
    from model import procs
    return any(s[1] == procs.CHILLED and label in s[0] and s[3] > 0 for s in r["family_result"].get("debuffs", []))


def _with_unique(uid, specs=(), etchings=(), stats=("the_vault:ability_power", "the_vault:cooldown_reduction", "the_vault:mana_regen")):
    def make(ctx, fam, rng):
        from parity_cases_etchings import etch, boost
        b = base(ctx, fam, rng)
        for s in specs:
            if s not in ctx.abilities:
                return None
            learn(ctx, b, s)
        if uid is not None and _uq(ctx, b, uid, rng) is None:
            return None
        boost(ctx, b, *stats)
        for e in etchings:
            if etch(ctx, b, e) is None:
                return None
        return b
    return make


case("steel-inferno fireball", "ability:Fireball_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _special(r, "steel_inferno") > 0 and _note(r, "Steel Inferno") else "Steel Inferno not worn")(
    _with_unique("the_vault:inferno_reach"))
case("steel-inferno ancient volley mitosis", "ability:Fireball_Volley", stage="max", ledgers=("rework",),
     check=lambda r, b, ctx: None if _special(r, "steel_inferno") > 0 and _note(r, "Mitosis") else "Steel Inferno or Mitosis missing")(
    _with_unique("ancient:the_vault:inferno_reach", etchings=("woldsvaults:fireball_volley_mitosis",)))
case("volley no i-frames", "ability:Fireball_Volley", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _note(r, "every one counted on the boss") else "Volley still i-frame capped")(
    _with_unique(None))
def _fireball_secondary(ctx, fam, rng):
    """Nova (plain hits) with a Fireball secondary and no weaving: Fireball is the only other stream, so Nova's i-frame
    window is zero only while Fireball hits open none."""
    from model import abilities as ab
    from model.evaluate import evaluate
    if "Fireball_Base" not in ctx.abilities:
        return None
    for _ in range(30):
        b = _with_unique(None)(ctx, fam, rng)
        if b is None:
            return None
        for s in [s for s in b.abilities if s in ab.MODELS and s != fam.main_spec]:
            del b.abilities[s]
        learn(ctx, b, "Fireball_Base")
        b.weave = False
        if _fb_live(evaluate(b, ctx)) is None:
            return b
    return None


def _fb_live(r):
    rows = {x["spec"]: x for x in r["family_result"]["rotation"]}
    nova, fb = rows.get("Nova_Base"), rows.get("Fireball_Base")
    if nova is None or fb is None or nova["share"] <= 0 or fb["share"] <= 0:
        return "Nova or its Fireball secondary not cast"
    return None if nova.get("window", 0.0) == 0.0 else "Fireball hits opened a window on Nova"


case("fireball secondary opens no window", "ability:Nova_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: _fb_live(r))(_fireball_secondary)
case("storm occupancy", "ability:Storm_Arrow_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _note(r, "holds the boss") else "occupancy not applied")(_with_unique(None))
case("stormcrown tracking", "ability:Storm_Arrow_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if "storm_tracking" in r["stats"].flags and _note(r, "follows the boss") else "Stormcrown not worn")(
    _with_unique("the_vault:stormcrown"))
case("shard blizzard occupancy and chill", "ability:Shard_Blizzard", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _note(r, "holds the boss") and _chill_src(r, "Shard Blizzard") else "occupancy or chill missing")(
    _with_unique(None, stats=("the_vault:attack_damage", "the_vault:cooldown_reduction", "the_vault:mana_regen")))
case("ice bolt chill", "ability:Ice_Bolt_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _chill_src(r, "Ice Bolt") else "Ice Bolt chill missing")(
    _with_unique(None, stats=("the_vault:attack_damage", "the_vault:cooldown_reduction", "the_vault:mana_regen")))
case("frost nova chill", "ability:Smite_Base", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _chill_src(r, "Frost Nova") else "Frost Nova chill missing")(
    _with_unique(None, specs=("Nova_Slow",)))
case("homing scatter ancient warbound", "ability:Javelin_Scatter", stage="max", ledgers=("rework",),
     check=lambda r, b, ctx: None if "javelin_multi_hit" in r["stats"].flags and r["stats"].attr.get("the_vault:splitting_javelins", 0) >= 2
     and _note(r, "Splitting Javelins") else "homing or splitting not live")(
    _with_unique("ancient:the_vault:warbound_helmet", stats=("the_vault:attack_damage", "the_vault:cooldown_reduction", "the_vault:mana_regen")))
case("homing scatter warbound", "ability:Javelin_Scatter", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if "javelin_multi_hit" in r["stats"].flags and _note(r, "Homing javelins") else "homing not live")(
    _with_unique("the_vault:warbound_helmet", stats=("the_vault:attack_damage", "the_vault:cooldown_reduction", "the_vault:mana_regen")))


def _chilling_cloud(ctx, fam, rng):
    from parity_cases_etchings import give_affix
    b = base(ctx, fam, rng)
    want = lambda a: a.attribute == "the_vault:effect_cloud" and "Chilling" in str((a.raw or {}).get("tooltipDisplayName", ""))
    it = b.items["mainhand"]
    if it.unique is not None:
        return None
    opts = [a for a in ctx.pools[it.type]["SUFFIX"] if want(a)]
    if not opts or not it.suffixes:
        return None
    it.suffixes = [x for x in it.suffixes if x.group != opts[0].group][:max(0, len(it.suffixes) - 1)] + [opts[0]]
    return b


case("chilling cloud", "melee:sword", stage="end", ledgers=("rework",),
     check=lambda r, b, ctx: None if _chill_src(r, "Chilling") else "Chilling cloud not live")(_chilling_cloud)


# Power Dump sneak rework (2026-10-21) -------------------------------------------------------------------------------

def _pd_gods(ctx, b, rng):
    import parity_cases_rotation as pr
    return pr._gods(ctx, b, ["idona_power_dump"], rng)


def _pd_try(ctx, fam, rng, setup, ok, tries=30):
    """A random build of fam with the Power Dump node, set up by setup(b) (False = skip this draw), kept once ok(result)."""
    from model.evaluate import evaluate
    for _ in range(tries):
        b = base(ctx, fam, rng)
        if not _pd_gods(ctx, b, rng) or setup(b) is False:
            continue
        if ok(evaluate(b, ctx)["family_result"]):
            return b
    return None


def _fr(r):
    return r["family_result"]


def _notes(r):
    return [n for n in list(_fr(r).get("notes", [])) + list(r.get("notes", [])) if isinstance(n, str)]


def _mixed_setup(ctx):
    def fn(b):
        learn(ctx, b, "Storm_Arrow_Base")
        b.pd = {"Fangs_Maw"}
    return fn


case("pd-mixed-windows", "ability:Fangs_Maw", modes=("intended",), ledgers=("rework",),
     check=lambda r, b, ctx: None if _fr(r).get("pd_mode") == "mixed" and _fr(r).get("pd_duty", 1.0) < 1.0 else "no sneak windows")(
    lambda ctx, fam, rng: _pd_try(ctx, fam, rng, _mixed_setup(ctx),
                                  lambda fr: fr.get("pd_mode") == "mixed" and fr.get("pd_duty", 1.0) < 1.0))


def _always_setup(ctx):
    def fn(b):
        if "Battle_Cry_Base" not in ctx.abilities:
            return False
        learn(ctx, b, "Battle_Cry_Base")
        b.pd = {"Fireball_Base"}
    return fn


case("pd-always-buff-dumps", "ability:Fireball_Base", modes=("intended",), ledgers=("rework",),
     check=lambda r, b, ctx: None if _fr(r).get("pd_mode") == "always" and _fr(r).get("sneak_share") == 1.0 else "not always sneaking")(
    lambda ctx, fam, rng: _pd_try(ctx, fam, rng, _always_setup(ctx), lambda fr: fr.get("pd_mode") == "always"))


case("pd-every-implode", "ability:Mana_Shield_Implode", modes=("intended",), ledgers=("rework",),
     check=lambda r, b, ctx: None if any("Power Dump on every Implode" in n for n in _notes(r)) else "no Implode bonus")(
    lambda ctx, fam, rng: _pd_try(ctx, fam, rng, lambda b: None,
                                  lambda fr: any("Power Dump on every Implode" in n for n in fr.get("notes", []) if isinstance(n, str))))


def _penguin_setup(ctx, rng):
    from model import search

    def fn(b):
        u = ctx.unique_by_id.get("woldsvaults:pocket_penguin")
        if u is None or "Mana_Shield_Implode" not in ctx.abilities:
            return False
        search.equip_unique(ctx, b, "offhand", u, rng)
        learn(ctx, b, "Mana_Shield_Implode")
        for s in [s for s in b.abilities if s not in ("Javelin_Base", "Mana_Shield_Implode") and s in ctx.abilities
                  and ctx.abilities[s].get("skill") not in ("Vein_Miner", "Heal", "Dash")]:
            b.abilities[s] = 0
        b.pd = {"Javelin_Base"}
    return fn


case("pd-penguin-sneak-throw", "ability:Javelin_Base", modes=("intended",), ledgers=("rework",),
     check=lambda r, b, ctx: None if any("the mana the sneaking throw consumed" in n for n in _notes(r)) else "no sneaking Penguin throw")(
    lambda ctx, fam, rng: _pd_try(ctx, fam, rng, _penguin_setup(ctx, rng), lambda fr: fr.get("pd_mode") == "always"))
