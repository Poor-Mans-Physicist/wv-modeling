"""Directed parity cases: named builds that exercise one model feature each, scored by both engines.

tools/kernel_parity.py runs every case registered for its ledger, stage and mode next to its random builds;
`python tools/parity_cases.py` lists them with their Python scores, with and without the case's feature where the case
names a control (the feature must move the score or the case is reported as idle).

A case is registered with @case(name, stage, family, mode="intended", ledgers=("rework", "0.34.1"), knobs=None) on a
function make(ctx) -> Build or None (None = the stage or catalog lacks what the case needs; it is skipped and
counted). knobs override the Context's knobs for that case only. A make function may return (build, control) to
name a control build without the feature.
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CASES = []


class Case:
    def __init__(self, name, stage, family, mode, ledgers, knobs, make):
        self.name, self.stage, self.family, self.mode = name, stage, family, mode
        self.ledgers, self.knobs, self.make = tuple(ledgers), dict(knobs or {}), make


def case(name, stage, family, mode="intended", ledgers=("rework", "0.34.1"), knobs=None):
    def deco(fn):
        CASES.append(Case(name, stage, family, mode, ledgers, knobs, fn))
        return fn
    return deco


def ledger():
    from model import stages
    return stages.LEDGER


def cases_for(stage, mode):
    lg = ledger()
    return [c for c in CASES if c.stage == stage and c.mode == mode and lg in c.ledgers]


def context(c, base_ctx):
    """The case's Context: base_ctx, or a fresh one with the case's knob overrides."""
    if not c.knobs:
        return base_ctx
    from model.context import Context
    ctx = Context(c.stage, c.mode)
    ctx.knobs.update(c.knobs)
    return ctx


def base(ctx, family, seed):
    from model import families, search
    rng = random.Random(seed)
    fam = families.get(family)
    if not ctx.mainhands(fam):
        return None, rng
    return search.initial_build(ctx, fam, rng), rng


def etch(ctx, b, attr, slots=None):
    """Put the etching whose attribute ends with `attr` on the first slot (in `slots` order, default every slot) whose
    gear type offers it, at the stage's rolled value. Returns the slot or None."""
    for slot in slots or list(b.items):
        it = b.items[slot]
        if it.unique is not None:
            continue
        for e in ctx.etchings.get(it.type, []):
            if e["attribute"].split(":")[-1] == attr.split(":")[-1]:
                it.etching = e
                return slot
    return None


def learn(ctx, b, spec, tier=None):
    if spec not in ctx.abilities:
        return False
    b.abilities[spec] = tier or ctx.ability_max(spec)
    return True


def talent(ctx, b, tid, tier=None):
    if tid not in ctx.talents:
        return False
    b.talents[tid] = tier or ctx.talent_max(tid)
    return True


def give_affix(ctx, b, attribute, slots=None, pick=max):
    """Put an affix with this attribute (the strongest by value) in place of one prefix/suffix of the first slot whose
    pools offer it. Returns the slot or None."""
    for slot in slots or list(b.items):
        it = b.items[slot]
        if it.unique is not None:
            continue
        pools = ctx.pools[it.type]
        for side, lst in (("PREFIX", it.prefixes), ("SUFFIX", it.suffixes)):
            opts = [a for a in pools[side] if a.attribute == attribute and a.value is not None]
            if opts and lst:
                a = pick(opts, key=lambda x: x.value)
                keep = [x for x in lst if x.group != a.group]
                lst[:] = (keep[:len(lst) - 1] if len(keep) >= len(lst) else keep) + [a]
                return slot
    return None


def boost(ctx, b, *attributes):
    """give_affix for each attribute on every slot that offers it (a stat-heavy build, so damage is not zero)."""
    for attribute in attributes:
        for slot in list(b.items):
            give_affix(ctx, b, attribute, slots=[slot])


AP = ("the_vault:ability_power", "the_vault:ability_power_percent", "the_vault:cooldown_reduction",
      "the_vault:mana_regen")
AD = ("the_vault:attack_damage", "the_vault:attack_speed", "the_vault:lucky_hit_chance")


def offhand(ctx, b, gear_type, rng):
    from model import search
    if gear_type not in ctx.offhand_types:
        return False
    b.items["offhand"] = search.new_item(ctx, "offhand", gear_type, rng, b)
    return True


def mainhand(ctx, b, gear_type, rng):
    from model import search, families
    if gear_type not in ctx.mainhands(families.get(b.family)):
        return False
    b.items["mainhand"] = search.new_item(ctx, "mainhand", gear_type, rng, b)
    return True


def unique(ctx, b, uid, rng):
    from model import search
    for slot, lst in ctx.unique_slots.items():
        for u in lst:
            if u["id"] == uid:
                search.equip_unique(ctx, b, slot, u, rng)
                return slot
    return None


def trinket(ctx, b, tid):
    t = ctx.trinkets_by_id.get(tid)
    if t is None:
        return False
    same = [x for x in b.trinkets if ctx.trinkets_by_id[x]["slot"] == t["slot"]]
    if same:
        b.trinkets[b.trinkets.index(same[0])] = tid
    else:
        b.trinkets.append(tid)
    return True


def seal(ctx, b, key, slot):
    """Seal `slot` with the corrupted implicit whose special modification key is `key`."""
    it = b.items[slot]
    for a in ctx.pools[it.type]["CORRUPTED_IMPLICIT"]:
        if isinstance(a.raw, dict) and a.raw.get("specialModificationKey") == key:
            it.seal = a
            return True
    return False


def strip_etchings(b):
    for it in b.items.values():
        it.etching = None


def control(b, fn):
    """A copy of b with fn(copy) applied (the case's build without its feature)."""
    c = b.clone()
    fn(c)
    return c


# ---------------- DoTs and clouds (A2) ----------------

@case("dot-slot: Nova Dot main + Toxic Grenade secondary", "max", "ability:Nova_Dot")
def _dot_slot(ctx):
    b, rng = base(ctx, "ability:Nova_Dot", 101)
    if b is None or not learn(ctx, b, "Toxic_Grenade"):
        return None
    boost(ctx, b, *AP)
    talent(ctx, b, "Hexbreaker")
    return b, control(b, lambda c: c.abilities.pop("Toxic_Grenade"))


@case("dot-slot: Toxic Grenade main + Nova Dot secondary", "end", "ability:Toxic_Grenade")
def _dot_slot2(ctx):
    b, rng = base(ctx, "ability:Toxic_Grenade", 102)
    if b is None or not learn(ctx, b, "Nova_Dot"):
        return None
    boost(ctx, b, *AP)
    return b, control(b, lambda c: c.abilities.pop("Nova_Dot"))



# ---------------- etchings on abilities (A2) ----------------

def _etched(ctx, family, seed, etchings, learn_specs=(), stats=AP, slots=None, talents=()):
    """A family build with the given etchings (each on the first slot that offers it), extra learned specs and
    talents, boosted stats; the control is the same build with those etchings removed. None when one is missing."""
    b, rng = base(ctx, family, seed)
    if b is None:
        return None
    for s in learn_specs:
        if not learn(ctx, b, s):
            return None
    for t in talents:
        talent(ctx, b, t)
    boost(ctx, b, *stats)
    strip_etchings(b)
    used = []
    for e in etchings:
        slot = etch(ctx, b, e, slots=[x for x in (slots or list(b.items)) if x not in used])
        if slot is None:
            return None
        used.append(slot)
    return b, control(b, lambda c: [setattr(c.items[x], "etching", None) for x in used])


@case("strike delay: Smite with Echoing Smite and echo chance", "max", "ability:Smite_Base")
def _smite_delay(ctx):
    out = _etched(ctx, "ability:Smite_Base", 201, ["etching_lightning_strike_delay", "etching_smite_echo"],
                  slots=["helmet", "chestplate", "boots", "leggings", "mainhand", "offhand"])
    if out is None:
        return None
    for b in out:
        mainhand(ctx, b, "sword", random.Random(5)) and give_affix(ctx, b, "the_vault:echoing_chance", slots=["mainhand"])
    return out


@case("strike delay: Smite Archon", "end", "ability:Smite_Archon")
def _archon_delay(ctx):
    return _etched(ctx, "ability:Smite_Archon", 202, ["etching_lightning_strike_delay"])


@case("strike delay: Chain Lightning", "max", "ability:Chain_Lightning_Base")
def _chain_delay(ctx):
    return _etched(ctx, "ability:Chain_Lightning_Base", 203, ["etching_lightning_strike_delay"])


@case("blizzard: Shard Blizzard secondary with Blizzard Effect and Blizzard Damage", "max", "ability:Nova_Base")
def _blizzard(ctx):
    return _etched(ctx, "ability:Nova_Base", 204, ["etching_storm_arrow_blizzard_effect", "etching_storm_arrow_blizzard_damage"],
                   learn_specs=["Shard_Blizzard"], stats=AP + AD)


@case("blizzard: Shard Blizzard main with both Blizzard etchings", "end", "ability:Shard_Blizzard")
def _blizzard_main(ctx):
    return _etched(ctx, "ability:Shard_Blizzard", 205, ["etching_storm_arrow_blizzard_effect",
                                                         "etching_storm_arrow_blizzard_damage"], stats=AP + AD)


@case("earthquake recast", "max", "ability:Earthquake_Tremor")
def _quake(ctx):
    return _etched(ctx, "ability:Earthquake_Tremor", 206, ["etching_earthquake_recast_delay"], stats=AD + AP)


@case("prism split (no boss damage)", "max", "ability:Arcane_Prism")
def _prism(ctx):
    return _etched(ctx, "ability:Arcane_Prism", 207, ["etching_prism_split"])


@case("greedball: Fireball with item quantity and rarity", "max", "ability:Fireball_Base", ledgers=("rework",))
def _greedball(ctx):
    out = _etched(ctx, "ability:Fireball_Base", 208, ["woldsvaults:fireball_greedball"],
                  stats=AP + ("the_vault:item_quantity", "the_vault:item_rarity"), slots=["offhand"])
    if out is None:
        b, rng = base(ctx, "ability:Fireball_Base", 208)
        if b is None or not offhand(ctx, b, "focus", rng):
            return None
        boost(ctx, b, *(AP + ("the_vault:item_quantity", "the_vault:item_rarity")))
        if etch(ctx, b, "woldsvaults:fireball_greedball", slots=["offhand"]) is None:
            return None
        return b, control(b, lambda c: setattr(c.items["offhand"], "etching", None))
    return out


@case("greedball: Volley pack radius", "end", "ability:Fireball_Volley", ledgers=("rework",))
def _greedball_volley(ctx):
    b, rng = base(ctx, "ability:Fireball_Volley", 209)
    if b is None or not offhand(ctx, b, "focus", rng):
        return None
    boost(ctx, b, *AP)
    if etch(ctx, b, "woldsvaults:fireball_greedball", slots=["offhand"]) is None:
        return None
    return b


@case("AP Totem: Wrath totem raises ability power", "max", "ability:Fireball_Base")
def _ap_totem(ctx):
    return _etched(ctx, "ability:Fireball_Base", 210, ["etching_totem_player_damage_ap"], learn_specs=["Totem_Player_Damage"])


@case("AP Totem: Starforge-free melee with AP Totem (AP unused)", "end", "melee:sword")
def _ap_totem_melee(ctx):
    return _etched(ctx, "melee:sword", 211, ["etching_totem_player_damage_ap"], learn_specs=["Totem_Player_Damage"], stats=AD)


@case("Colossus Titan: resistance over the cap while Colossus is up", "max", "melee:axe", ledgers=("rework",))
def _titan(ctx):
    return _etched(ctx, "melee:axe", 212, ["woldsvaults:colossus_titan_resistance"], learn_specs=["Colossus_Base"],
                   stats=AD + ("the_vault:resistance",) * 3)


@case("Colossus Titan: Colossus not learned", "end", "melee:sword", ledgers=("rework",))
def _titan_idle(ctx):
    return _etched(ctx, "melee:sword", 213, ["woldsvaults:colossus_titan_resistance"], stats=AD + ("the_vault:resistance",))


@case("Taunt nerf knob on", "max", "ability:Smite_Base", knobs={"taunt_amp_half_above": 8})
def _taunt_knob(ctx):
    b, rng = base(ctx, "ability:Smite_Base", 214)
    if b is None or not learn(ctx, b, "Taunt_Base", tier=20):
        return None
    boost(ctx, b, *AP)
    return b


@case("Taunt nerf knob off (control)", "max", "ability:Smite_Base")
def _taunt_plain(ctx):
    return _taunt_knob(ctx)



@case("Lucky Nova: melee lucky hits cast Frost Nova with the Frozen Orb seal", "max", "melee:sword")
def _lucky_nova(ctx):
    out = _etched(ctx, "melee:sword", 215, ["etching_lucky_frost_nova"], stats=AD, slots=["chestplate", "leggings"])
    if out is None:
        return None
    for b in out:
        seal(ctx, b, "the_vault:frost_nova_vulnerability", "helmet")
    return out


@case("Lucky Nova: merged with a learned Frost Nova", "end", "melee:axe")
def _lucky_nova_merge(ctx):
    out = _etched(ctx, "melee:axe", 216, ["etching_lucky_frost_nova"], learn_specs=["Nova_Slow"], stats=AD,
                  slots=["chestplate", "leggings"])
    if out is None:
        return None
    for b in out:
        seal(ctx, b, "the_vault:frost_nova_vulnerability", "helmet")
    return out


@case("Lucky Reset: Hungry Maw with Arcane Strike", "max", "ability:Fangs_Maw")
def _lucky_reset_cascade(ctx):
    return _etched(ctx, "ability:Fangs_Maw", 217, ["etching_lucky_cooldown_reset"], stats=AD, talents=["Arcane_Strike"])


@case("Lucky Reset: Hungry Maw without Arcane Strike", "end", "ability:Fangs_Maw")
def _lucky_reset_alone(ctx):
    out = _etched(ctx, "ability:Fangs_Maw", 218, ["etching_lucky_cooldown_reset"], stats=AD)
    if out is None:
        return None
    for b in out:
        b.talents.pop("Arcane_Strike", None)
    return out


@case("Lucky Reset: best pooled secondary of a weaving Smite build", "max", "ability:Smite_Base")
def _lucky_reset_secondary(ctx):
    out = _etched(ctx, "ability:Smite_Base", 219, ["etching_lucky_cooldown_reset"],
                  learn_specs=["Fireball_Base", "Nova_Base"], stats=AP + AD)
    if out is None:
        return None
    for b in out:
        mainhand(ctx, b, "sword", random.Random(7))
        give_affix(ctx, b, "the_vault:lucky_hit_chance", slots=["mainhand"])
        b.weave = True
    return out



def _echo_melee(ctx, family, seed, etchings, learn_specs=(), weapon_slot="mainhand"):
    out = _etched(ctx, family, seed, etchings, learn_specs=learn_specs, stats=AD,
                  slots=["chestplate", "helmet", "leggings", "boots", "offhand"])
    if out is None:
        return None
    for b in out:
        give_affix(ctx, b, "the_vault:echoing_chance", slots=[weapon_slot])
    return out


@case("Reverberating Echo: melee echo build", "max", "melee:sword", ledgers=("rework",))
def _reverb_melee(ctx):
    return _echo_melee(ctx, "melee:sword", 220, ["woldsvaults:reverberation"])


@case("Reverberating Echo: weaving Smite echo streams", "end", "ability:Smite_Base", ledgers=("rework",))
def _reverb_smite(ctx):
    out = _etched(ctx, "ability:Smite_Base", 221, ["woldsvaults:reverberation", "etching_smite_echo"], stats=AP + AD,
                  slots=["chestplate", "mainhand", "helmet", "boots"])
    if out is None:
        return None
    for b in out:
        mainhand(ctx, b, "sword", random.Random(9))
        give_affix(ctx, b, "the_vault:echoing_chance", slots=["mainhand"])
        b.weave = True
    return out


@case("echo inherits Rampage: melee echo build with Rampage", "max", "melee:axe")
def _echo_rampage(ctx):
    b, rng = base(ctx, "melee:axe", 222)
    if b is None or not learn(ctx, b, "Rampage_Base"):
        return None
    boost(ctx, b, *AD)
    give_affix(ctx, b, "the_vault:echoing_chance", slots=["mainhand"])
    return b, control(b, lambda c: c.abilities.pop("Rampage_Base"))


@case("echo inherits Rampage: knob off", "max", "melee:axe", knobs={"echo_inherits_rampage": False})
def _echo_rampage_off(ctx):
    return _echo_rampage(ctx)



@case("cloud poison: Zombie Horse Axe melee", "max", "melee:axe")
def _zombie_axe(ctx):
    b, rng = base(ctx, "melee:axe", 230)
    if b is None:
        return None
    boost(ctx, b, *AD)
    ctl = b.clone()
    if unique(ctx, b, "woldsvaults:zombie_horse_axe", rng) is None:
        return None
    return b, ctl


@case("cloud poison: weaving Chroma Brew caster with the Chromatic Diffuser and Poison Mana", "max", "ability:Fireball_Base")
def _chroma(ctx):
    b, rng = base(ctx, "ability:Fireball_Base", 231)
    if b is None:
        return None
    boost(ctx, b, *AP)
    mainhand(ctx, b, "sword", rng)
    b.weave = True
    if unique(ctx, b, "woldsvaults:chroma_brew", rng) is None:
        return None
    trinket(ctx, b, "the_vault:chromatic_diffuser")
    etch(ctx, b, "etching_poison_mana", slots=["chestplate", "helmet", "leggings", "boots"])
    return b


@case("Poison Mana: Nova Dot ticks return mana", "end", "ability:Nova_Dot")
def _poison_mana(ctx):
    return _etched(ctx, "ability:Nova_Dot", 232, ["etching_poison_mana"])


@case("Blocking Mana: shield offhand", "max", "melee:sword")
def _blocking_mana(ctx):
    b, rng = base(ctx, "melee:sword", 233)
    if b is None or not offhand(ctx, b, "shield", rng):
        return None
    learn(ctx, b, "Rampage_Base")
    boost(ctx, b, *AD)
    if etch(ctx, b, "etching_blocking_mana", slots=["helmet", "boots", "offhand"]) is None:
        return None
    return b, control(b, strip_etchings)


@case("Blocking Mana: no shield (idle)", "end", "melee:sword")
def _blocking_mana_idle(ctx):
    b, rng = base(ctx, "melee:sword", 234)
    if b is None or not offhand(ctx, b, "focus", rng):
        return None
    boost(ctx, b, *AD)
    if etch(ctx, b, "etching_blocking_mana", slots=["helmet", "boots"]) is None:
        return None
    return b


@case("Scatter Mana: Scatter Javelin", "max", "ability:Javelin_Scatter")
def _scatter_mana(ctx):
    return _etched(ctx, "ability:Javelin_Scatter", 235, ["etching_scatter_mana"], stats=AD)



def _nova_low_build(ctx, seed, secs):
    b, rng = base(ctx, "ability:Nova_Base", seed)
    if b is None:
        return None
    for s in secs:
        learn(ctx, b, s)
    boost(ctx, b, "the_vault:ability_power", "the_vault:ability_power_percent")
    strip_etchings(b)
    if etch(ctx, b, "etching_nova_low_mana_damage", slots=["helmet"]) is None:
        return None
    etch(ctx, b, "etching_nova_low_mana_damage", slots=["chestplate"])
    return b, control(b, strip_etchings)


@case("Nova Damage Increase: mana-limited Nova main with Smite", "max", "ability:Nova_Base")
def _nova_low(ctx):
    return _nova_low_build(ctx, 249, ["Smite_Base"])


@case("Nova Damage Increase: mana-limited Nova main with Arcane Beam", "end", "ability:Nova_Base")
def _nova_low_beam(ctx):
    return _nova_low_build(ctx, 243, ["Arcane_Base"])


@case("Nova Damage Increase: Nova secondary of a Smite build", "end", "ability:Smite_Base")
def _nova_low_sec(ctx):
    return _etched(ctx, "ability:Smite_Base", 241, ["etching_nova_low_mana_damage"], learn_specs=["Nova_Base", "Fireball_Base"])


@case("Lightning Orb Size (pack only)", "max", "ability:Chain_Lightning_Orbs")
def _orb_size(ctx):
    return _etched(ctx, "ability:Chain_Lightning_Orbs", 242, ["etching_lightning_orb_size"])


if __name__ == "__main__":
    from model.context import Context
    from model.evaluate import evaluate
    ctxs = {}
    for c in CASES:
        if ledger() not in c.ledgers:
            continue
        key = (c.stage, c.mode)
        if key not in ctxs:
            ctxs[key] = Context(c.stage, c.mode)
        ctx = context(c, ctxs[key])
        out = c.make(ctx)
        if out is None:
            print(f"SKIP  {c.name}")
            continue
        b, ctl = out if isinstance(out, tuple) else (out, None)
        r = evaluate(b, ctx)
        s, dps = r["score"], r["dps_no_hp_scaling"]
        if ctl is None:
            print(f"      {c.name}: score {s:.4f}, dps {dps:,.0f}")
        else:
            r0 = evaluate(ctl, ctx)
            s0, d0 = r0["score"], r0["dps_no_hp_scaling"]
            idle = abs(s - s0) <= 1e-12 and abs(dps - d0) <= 1e-9 * max(1.0, abs(d0))
            print(f"{'IDLE ' if idle else '     '} {c.name}: score {s:.4f} (control {s0:.4f}), dps {dps:,.0f} (control {d0:,.0f})")
