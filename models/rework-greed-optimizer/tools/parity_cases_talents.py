"""Directed parity cases: named builds that exercise one feature each, scored by both engines next to the random builds
of tools/kernel_parity.py.

A case is (name, stage, family id, ledgers, make): make(ctx, fam, rng) returns (build, live), the build from
search.initial_build plus forced parts and live(Python result), which says whether the feature the case targets is
active, so a case that silently stopped exercising its feature is reported instead of passing.
"""
import zlib

CASES = []


def case(name, stage, family, ledgers=("rework", "0.34.1")):
    def reg(fn):
        CASES.append((name, stage, family, ledgers, fn))
        return fn
    return reg


def seed(name):
    return zlib.crc32(name.encode())


def best_affix(pool, attribute, key=None):
    opts = [a for a in pool if a.attribute == attribute and (key is None or key(a))]
    return max(opts, key=lambda a: a.value if isinstance(a.value, (int, float)) else 0.0) if opts else None


def deck_mana_regen(ctx, b):
    """Rework deck menus: put a mana regeneration card in every stat slot that offers one (the menu designs' own
    cards are chosen for value and carry none, so mana-gated cases would idle on them). No-op on fixed layouts."""
    if not getattr(ctx, "designs", None) or not b.dk:
        return
    d = ctx.designs[b.design]
    for i, opts in enumerate(d["opts"]):
        k = next((j for j, c in enumerate(opts) if c.get("attr") == "the_vault:mana_regen"), None)
        if k is not None:
            b.dk[i] = k


def put_affix(ctx, b, slot, attribute, key=None):
    """Put the pool's strongest `attribute` affix on the slot's item (an implicit group, else the first prefix or
    suffix, replacing an affix of the same group); False when the item cannot roll it."""
    it = b.items[slot]
    pools = ctx.pools[it.type]
    for g, a in list(it.implicits.items()):
        alt = best_affix([x for x in pools["IMPLICIT"] if (x.group or x.ident) == g], attribute, key)
        if alt is not None:
            it.implicits[g] = alt
            return True
    for side, lst in (("PREFIX", it.prefixes), ("SUFFIX", it.suffixes)):
        a = best_affix(pools[side], attribute, key)
        if a is None:
            continue
        same = [i for i, x in enumerate(lst) if x.group == a.group]
        if same:
            lst[same[0]] = a
        elif lst:
            lst[0] = a
        else:
            lst.append(a)
        return True
    return False


def put_seal(ctx, b, slot, attribute, key=None):
    it = b.items[slot]
    a = best_affix(ctx.pools[it.type]["CORRUPTED_IMPLICIT"], attribute, key)
    if a is None:
        return False
    it.seal = a
    return True


def put_etching(ctx, b, slot, eid):
    for e in ctx.etchings.get(b.items[slot].type, []):
        if e["id"] == eid:
            b.items[slot].etching = e
            return True
    return False


def drop_negation(b):
    from model import catalog
    b.trinkets = [t for t in b.trinkets if t not in catalog.NEGATION_TRINKETS]
    for it in b.items.values():
        if it.etching and it.etching["attribute"] == "woldsvaults:divinity":
            it.etching = None


def force_god_node(ctx, b, god, effect):
    """Own `effect`'s node in `god`'s tree at the top level, with that god's charm (parity only: legality is not kept)."""
    from model import gods
    t = ctx.gods.trees[god]
    target = next(i for i, e in enumerate(t.effect) if e == effect)
    b.gods["levels"][god] = ctx.gods.max_level
    b.gods["nodes"][god] |= set(t.path_to(b.gods["nodes"][god], target) or [])
    b.charm = {"god": gods.CHARM_GOD[god], "mods": list(ctx.charms[gods.CHARM_GOD[god]])[:ctx.stage["charm_prefixes"]]}


def ap_gear(ctx, b):
    for slot in ("helmet", "chestplate", "leggings", "boots", "mainhand", "offhand", "necklace"):
        put_affix(ctx, b, slot, "the_vault:ability_power")


def base(ctx, fam, rng):
    from model import search
    return search.initial_build(ctx, fam, rng)


def _learn(ctx, b, sid, tier=None):
    if sid in ctx.abilities:
        skill = ctx.abilities[sid]["skill"]
        for other in list(b.abilities):
            if other != sid and ctx.abilities[other]["skill"] == skill:
                b.abilities[other] = 0
        b.abilities[sid] = tier or ctx.ability_max(sid)


def fr(r):
    return r["family_result"]


@case("speed-stoneskin-laststand", "max", "melee:sword")
def _speed(ctx, fam, rng):
    b = base(ctx, fam, rng)
    b.talents.update(Speed=4, Stoneskin=4, Last_Stand=1)
    return b, lambda r: r["derived"]["move_speed"] > 1.0 and r["stats"].attr.get("x:talent_tier:Last_Stand", 0) > 0


@case("blizzard-frozen-orb", "max", "melee:axe")
def _blizzard(ctx, fam, rng):
    b = base(ctx, fam, rng)
    _learn(ctx, b, "Nova_Slow")
    b.talents["Blizzard"] = 4
    put_seal(ctx, b, "helmet", "the_vault:ability_special_modification",
             key=lambda a: a.raw.get("specialModificationKey") == "the_vault:frost_nova_vulnerability")
    return b, lambda r: any("Frozen Impact" in n for n in fr(r)["notes"])


@case("empower-paid", "max", "melee:sword")
def _emp_paid(ctx, fam, rng):
    b = base(ctx, fam, rng)
    _learn(ctx, b, "Empower_Base")
    put_affix(ctx, b, "necklace", "the_vault:mana_regen")
    put_affix(ctx, b, "helmet", "the_vault:mana_regen")
    deck_mana_regen(ctx, b)
    return b, lambda r: fr(r).get("empower_speed", 0.0) > 0


@case("empower-short", "early", "melee:sword")
def _emp_short(ctx, fam, rng):
    b = base(ctx, fam, rng)
    _learn(ctx, b, "Empower_Base")
    _learn(ctx, b, "Rampage_Base")
    return b, lambda r: any("Empower runs at" in n for n in fr(r)["notes"])


@case("slowness-aura-caster", "max", "ability:Smite_Base")
def _aura(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    _learn(ctx, b, "Empower_Slowness_Aura")
    return b, lambda r: any("Slowness Aura" in n for n in fr(r)["notes"])


@case("sneaky-getaway-ninja", "max", "melee:sword")
def _sneaky(ctx, fam, rng):
    b = base(ctx, fam, rng)
    _learn(ctx, b, "Sneaky_Getaway")
    put_etching(ctx, b, "boots", "woldsvaults:sneaky_getaway_ninja")
    return b, lambda r: fr(r).get("sneaky_dodge", 0.0) > 0


@case("totem-mana-shield", "end", "ability:Fireball_Base")
def _totem(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    _learn(ctx, b, "Totem_Base")
    _learn(ctx, b, "Mana_Shield_Base")
    return b, lambda r: r["healing"].get("totem", 0.0) > 0 and fr(r).get("mana_shield_pool", 0.0) > 0


@case("ultimate-shield", "max", "ability:Fireball_Base")
def _us(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    _learn(ctx, b, "UltimateShield_Base")
    for slot in ("helmet", "chestplate", "necklace"):
        put_affix(ctx, b, slot, "the_vault:mana_additive")
        put_affix(ctx, b, slot, "the_vault:mana_regen")
    return b, lambda r: fr(r).get("us_uptime", 0.0) > 0 and r["survival"]["us_absorb"] > 0


@case("ultimate-shield-power-dump", "max", "ability:Fireball_Base", ledgers=("rework",))
def _us_pd(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    _learn(ctx, b, "UltimateShield_Base")
    for slot in ("helmet", "chestplate", "necklace"):
        put_affix(ctx, b, slot, "the_vault:mana_additive")
        put_affix(ctx, b, slot, "the_vault:mana_regen")
    force_god_node(ctx, b, "idona", "idona_power_dump")
    deck_mana_regen(ctx, b)
    b.pd = {"Fireball_Base"}
    return b, lambda r: (fr(r).get("us_uptime", 0.0) > 0 and any("Power Dump" in n for n in fr(r)["notes"])
                         and r["derived"]["mana_regen"] >= fr(r)["us_drain"])


@case("golem-main", "max", "ability:Necromancy_Golem")
def _golem(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    return b, lambda r: r["dps_no_hp_scaling"] > 0


@case("swiftness", "end", "melee:axe")
def _swift(ctx, fam, rng):
    b = base(ctx, fam, rng)
    return b, lambda r: "swiftness" in r["stats"].flags


@case("no-negation", "mid", "melee:sword")
def _no_neg(ctx, fam, rng):
    b = base(ctx, fam, rng)
    drop_negation(b)
    return b, lambda r: not r["requirements"]["effect_negation_met"]


@case("blessed", "mid", "ability:Fireball_Base")
def _blessed(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ap_gear(ctx, b)
    drop_negation(b)
    put_etching(ctx, b, "helmet", "woldsvaults:divinity")
    return b, lambda r: r["requirements"]["effect_negation_met"]


@case("tri-rang", "max", "ranged:rang")
def _rang(ctx, fam, rng):
    from model import search
    b = base(ctx, fam, rng)
    u = next(u for u in ctx.unique_choices("mainhand", fam) if u["id"].endswith("trirang"))
    search.equip_unique(ctx, b, "mainhand", u, rng)
    return b, lambda r: r["dps_no_hp_scaling"] > 0 and fr(r)["source"] == "vaultarang"


@case("rang-rolled", "end", "ranged:rang")
def _rang2(ctx, fam, rng):
    b = base(ctx, fam, rng)
    for attr in ("the_vault:returning_damage", "the_vault:piercing", "the_vault:ricochet"):
        put_affix(ctx, b, "mainhand", attr)
    _learn(ctx, b, "Rampage_Base")
    return b, lambda r: r["dps_no_hp_scaling"] > 0 and r["pack_dps"] > r["dps_no_hp_scaling"]
