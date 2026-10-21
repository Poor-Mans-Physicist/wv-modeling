"""Directed parity cases for the survival package: a ranged caster, crit mitigation on helmet and chest, the Immortal
Seal, a Swift Amulet + Velara dodge build near the cap, Sorcery + Last Stand (the HP-state second pass), a slow
Arcane Rail with The Dice (percentile), Hungry Maw and Wall of Fangs (fang table) and Shell Porcupine (thorns on the
reaching attacks). Each case forces its feature onto a few random builds per case, checks in the Python result that
the feature is active (a case that stopped exercising its feature is reported, not passed), and scores the builds with
model/evaluate.py and the kernel (same fields and tolerance as tools/kernel_parity.py).

python tools/parity_cases_survival.py [--ledger rework|0.34.1|both] [--per 4]

The CASES entries use the (name, stage, family, ledgers, make) shape of tools/parity_cases.py on master, where
make(ctx, fam, rng) returns (build, live) and live(Python result) says whether the feature is active.
"""
import argparse
import os
import random
import subprocess
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CASES = []


def case(name, stage, family, ledgers=("rework", "0.34.1")):
    def reg(fn):
        CASES.append((name, stage, family, ledgers, fn))
        return fn
    return reg


def base(ctx, fam, rng, steps=40):
    from model import search
    b = search.initial_build(ctx, fam, rng)
    for _ in range(rng.randrange(steps)):
        c = search.mutate(ctx, b, fam, rng)
        if c is not None:
            b = c
    return b


def best_affix(pool, attribute):
    opts = [a for a in pool if a.attribute == attribute]
    return max(opts, key=lambda a: a.value if isinstance(a.value, (int, float)) else 0.0) if opts else None


def put_affix(ctx, b, slot, attribute):
    """The pool's strongest `attribute` affix on the slot's item: an implicit of its group, else the first prefix or
    suffix (replacing one of the same group); False when the item cannot roll it."""
    it = b.items[slot]
    pools = ctx.pools[it.type]
    for g in list(it.implicits):
        alt = best_affix([x for x in pools["IMPLICIT"] if (x.group or x.ident) == g], attribute)
        if alt is not None:
            it.implicits[g] = alt
            return True
    for side, lst in (("PREFIX", it.prefixes), ("SUFFIX", it.suffixes)):
        a = best_affix(pools.get(side, []), attribute)
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


def put_trinket(ctx, b, tid):
    """Wear trinket tid in place of a worn trinket of its colour (or add it); False when the stage does not offer it."""
    t = ctx.trinkets_by_id.get(tid)
    if t is None or not any(x["id"] == tid for x in ctx.trinkets):
        return False
    same = [i for i, x in enumerate(b.trinkets) if ctx.trinkets_by_id[x]["slot"] == t["slot"]]
    if tid in b.trinkets:
        return True
    if same:
        b.trinkets[same[0]] = tid
    else:
        b.trinkets.append(tid)
    return True


def no_secondaries(ctx, fam, b):
    for x in fam.secondaries(b, ctx):
        b.abilities[x] = 0
    b.weave = False


def force_god_node(ctx, b, god, effect):
    """Own `effect`'s node in `god`'s tree (parity only: legality is not kept); False without god trees."""
    if ctx.gods is None or b.gods is None:
        return False
    from model import gods
    t = ctx.gods.trees[god]
    if effect not in t.effect:
        return False
    target = t.effect.index(effect)
    b.gods["levels"][god] = ctx.gods.max_level
    b.gods["nodes"][god] |= set(t.path_to(b.gods["nodes"][god], target) or [])
    b.charm = {"god": gods.CHARM_GOD[god], "mods": list(ctx.charms[gods.CHARM_GOD[god]])[:ctx.stage["charm_prefixes"]]}
    return True


@case("ranged-fireball", "max", "ability:Fireball_Base")
def _ranged(ctx, fam, rng):
    b = base(ctx, fam, rng)
    no_secondaries(ctx, fam, b)
    return b, lambda r: r["attack_stream"]["ranged"] and r["derived"]["p_land"] <= 0.5 * (1.0 - r["derived"]["p_avoid"]) + 1e-12


@case("crit-mitigation-helmet-chest", "max", "melee:sword")
def _crit(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ok = put_affix(ctx, b, "helmet", "the_vault:critical_hit_mitigation")
    ok = put_affix(ctx, b, "chestplate", "the_vault:critical_hit_mitigation") and ok
    k = ctx.knobs["hyper_boss_crit_mult"]
    return b, lambda r: ok and r["derived"]["crit_mitigation"] > 0 and r["survival"]["crit_mult"] < k


@case("immortal-seal", "mid", "melee:axe")
def _seal(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ok = put_trinket(ctx, b, "the_vault:immortal_seal")
    return b, lambda r: ok and r["derived"]["p_seal"] < 1.0


@case("swift-amulet-velara-dodge", "max", "melee:sword", ledgers=("rework",))
def _dodge(ctx, fam, rng):
    b = base(ctx, fam, rng)
    ok = put_trinket(ctx, b, "the_vault:swift_amulet")
    put_affix(ctx, b, "chestplate", "the_vault:dodge_percent")
    force_god_node(ctx, b, "velara", "velara_fast_reflexes_ii")
    force_god_node(ctx, b, "velara", "velara_fast_reflexes")
    put_trinket(ctx, b, "the_vault:lucky_coins")
    return b, lambda r: ok and r["derived"]["dodge"] >= 0.3


@case("immortal-revive", "max", "melee:axe", ledgers=("rework",))
def _revive(ctx, fam, rng):
    """Immortal's revive: the window needs floor(k_q / 2) landed hits from full health and the one-shot test checks
    the fight's landed hits (evaluate.survival)."""
    b = base(ctx, fam, rng)
    ok = force_god_node(ctx, b, "velara", "velara_immortal")
    return b, lambda r: ok and r["survival"].get("revive_hits") is not None and r["survival"].get("k_fight") is not None


@case("immortal-revive-dodge", "end", "melee:sword", ledgers=("rework",))
def _revive_dodge(ctx, fam, rng):
    """Immortal with dodge and the Immortal Seal: few landed hits, so k_q // 2 reaches 0 or 1."""
    b = base(ctx, fam, rng)
    ok = force_god_node(ctx, b, "velara", "velara_immortal")
    put_trinket(ctx, b, "the_vault:swift_amulet")
    put_trinket(ctx, b, "the_vault:immortal_seal")
    put_affix(ctx, b, "chestplate", "the_vault:dodge_percent")
    force_god_node(ctx, b, "velara", "velara_fast_reflexes")
    return b, lambda r: ok and r["survival"].get("revive_hits") is not None and r["survival"]["revive_hits"] <= 1


@case("sorcery-last-stand", "max", "ability:Smite_Archon")
def _hp_state(ctx, fam, rng):
    b = base(ctx, fam, rng)
    b.talents["Berserking"] = 0
    b.talents["Sorcery"] = ctx.talent_max("Sorcery")
    b.talents["Last_Stand"] = 1
    return b, lambda r: r["survival"].get("hp_state", {}).get("passes") == 2 and "Last_Stand" in r["survival"]["hp_state"]["talents"]


@case("berserking-hp-state", "end", "melee:axe")
def _berserk(ctx, fam, rng):
    b = base(ctx, fam, rng)
    b.talents["Last_Stand"] = 0
    b.talents["Berserking"] = ctx.talent_max("Berserking")
    b.talents["Prime_Amplification"] = ctx.talent_max("Prime_Amplification")
    return b, lambda r: "hp_state" in r["survival"]


VARIANT = {}
FOUND = os.path.join(ROOT, "out", "found_rework.json")


@case("last-stand-survival-redo", "max", "ability:Javelin_Piercing", ledgers=("rework",))
def _last_stand(ctx, fam, rng):
    """A saved Edge of Time winner with Last Stand in place of the HP talents and a Swift Amulet, dodge and speed: one
    landed hit per window, so a hit takes > 80% HP and only the survival test is redone (hp_state_redo "survival")."""
    import json
    from model import kernel
    with open(FOUND, encoding="utf-8") as fh:
        rows = [r for r in json.load(fh) if (r["stage"], r["family"], r["variant"]) == ("max", fam.id, "eot")]
    b = kernel.export_for(ctx).to_build(rows[0]["doc"], fam)
    for t in ("Berserking", "Sorcery", "Prime_Amplification"):
        b.talents[t] = 0
    b.talents["Last_Stand"] = 1
    put_trinket(ctx, b, "the_vault:swift_amulet")
    put_affix(ctx, b, "chestplate", "the_vault:dodge_percent")
    put_affix(ctx, b, "leggings", "the_vault:movement_speed")
    put_affix(ctx, b, "boots", "the_vault:movement_speed")
    return b, lambda r: r["survival"].get("hp_state", {}).get("u_low", 0.0) > 0 and r["survival"]["hp_state"]["passes"] == 1


VARIANT["last-stand-survival-redo"] = "eot"


@case("dice-arcane-rail", "max", "ability:Arcane_Rail")
def _dice(ctx, fam, rng):
    b = base(ctx, fam, rng)
    no_secondaries(ctx, fam, b)
    ok = put_trinket(ctx, b, "the_vault:the_dice")
    return b, lambda r: ok and r["family_result"].get("dice") is not None and r["family_result"]["dice"]["mult"] < 1.505 - 1e-6


@case("hungry-maw", "max", "ability:Fangs_Maw")
def _maw(ctx, fam, rng):
    b = base(ctx, fam, rng)
    return b, lambda r: r["family_result"]["rotation"][0]["rate"] > 0


@case("wall-of-fangs", "early", "ability:Fangs_Base")
def _wall(ctx, fam, rng):
    b = base(ctx, fam, rng)
    return b, lambda r: r["family_result"]["rotation"][0]["rate"] > 0


@case("shell-porcupine", "end", "ability:Shell_Porcupine")
def _porcupine(ctx, fam, rng):
    b = base(ctx, fam, rng)
    return b, lambda r: abs(r["family_result"]["rotation"][0]["rate"] - r["derived"]["boss_hits_reaching"]
                            * r["family_result"]["rotation"][0]["share"]) < 1e-12


def run_ledger(per):
    from model.context import Context
    from model import families, kernel, stages
    from model.evaluate import evaluate
    from tools.kernel_parity import FIELDS, close, py_fields, kernel_fields
    bad = dead = n = 0
    for name, stage, fid, ledgers, make in CASES:
        if stages.LEDGER not in ledgers:
            continue
        ctx = Context(stage, stages.MODES[-1], VARIANT.get(name, "all"))
        fam = families.get(fid)
        if not ctx.mainhands(fam):
            print(f"  {name}: family {fid} not offered at {stage}; skipped")
            continue
        rng = random.Random(zlib.crc32(name.encode()))
        made = [make(ctx, fam, rng) for _ in range(per)]
        builds = [m[0] for m in made]
        ks = kernel.evaluate_many(ctx, fam, builds)
        live = mism = 0
        for (b, alive), kr in zip(made, ks):
            n += 1
            pr = evaluate(b, ctx)
            live += bool(alive(pr))
            if not kr["ok"]:
                mism += 1
                print(f"  {name}: kernel error {kr.get('error')}")
                continue
            p = py_fields(pr)
            kf = kernel_fields(kr)
            diffs = [(f, p[f], kf[f]) for f in FIELDS if not close(p[f], kf[f])]
            if diffs:
                mism += 1
                print(f"  {name}: mismatch {diffs}")
        bad += mism
        dead += live == 0
        print(f"  {name:30s} {stage:5s} {per} builds, feature live in {live}, {mism} mismatches")
    print(f"{stages.LEDGER}: {n} directed builds, {bad} mismatches, {dead} cases with the feature never live")
    return bad + dead


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="both", choices=("rework", "0.34.1", "both"))
    ap.add_argument("--per", type=int, default=4)
    a = ap.parse_args()
    if a.ledger == "both":
        rc = 0
        for led in ("rework", "0.34.1"):
            rc |= subprocess.run([sys.executable, os.path.abspath(__file__), "--ledger", led, "--per", str(a.per)],
                                 env=dict(os.environ, WV_LEDGER=led)).returncode
        sys.exit(rc)
    if os.environ.get("WV_LEDGER", "0.34.1") != a.ledger:
        sys.exit(subprocess.run([sys.executable, os.path.abspath(__file__), "--ledger", a.ledger, "--per", str(a.per)],
                                env=dict(os.environ, WV_LEDGER=a.ledger)).returncode)
    sys.exit(1 if run_ledger(a.per) else 0)
