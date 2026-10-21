"""Directed parity cases for the rotation package: builds that exercise the main-share rule, scored woven swings,
Implode as the main ability, the cast guard, the low-mana uptime under sneaking Power Dump, Hexbreaker from the debuff
layer, kill and hit stacks with Berserk, Concentrate's Empower chain, the Starforge sneak nova and Pyramid Scheme.
Each case builds a few random builds per stage, forces the feature onto them, checks in the Python record that the
feature is active, and scores them with model/evaluate.py and the kernel (same fields and tolerance as
tools/kernel_parity.py). The share rule is also checked on a short kernel anneal: its winner must meet
main_damage_share_min.

python tools/parity_cases_rotation.py [--ledger rework|0.34.1|both] [--per 4]
"""
import argparse
import copy
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PYRAMID = "woldsvaults:additional_stacking_stacks"


def _ctx(stage):
    from model.context import Context
    from model import stages
    return Context(stage, stages.MODES[-1])


def _base(ctx, fid, rng, steps=40):
    from model import families, search
    fam = families.get(fid)
    b = search.initial_build(ctx, fam, rng)
    for _ in range(rng.randrange(steps)):
        c = search.mutate(ctx, b, fam, rng)
        if c is not None:
            b = c
    return b


def _learn(ctx, b, sid, tier=None):
    skill = ctx.abilities[sid]["skill"]
    for other in list(b.abilities):
        if other != sid and ctx.abilities[other]["skill"] == skill:
            b.abilities[other] = 0
    b.abilities[sid] = tier or ctx.ability_max(sid)


def _talent(ctx, b, tid, tier=None):
    if tid not in ctx.talents:
        return False
    b.talents[tid] = tier or ctx.talent_max(tid)
    return True


def _mainhand(ctx, b, gtype, rng):
    from model import search
    if b.items["mainhand"].type != gtype:
        b.items["mainhand"] = search.new_item(ctx, "mainhand", gtype, rng, b)


def _gods(ctx, b, effects, rng, god="idona"):
    """Rework: a god state with `god` at max level owning the nodes of `effects`, with its charm active."""
    from model import gods, search
    G = ctx.gods
    if G is None:
        return False
    gs = gods.empty_state()
    gs["levels"][god] = G.max_level
    t = G.trees[god]
    owned = set()
    for e in effects:
        idx = [i for i, x in enumerate(t.effect) if x == e and t.usable[i]]
        if not idx:
            return False
        path = t.path_to(owned, idx[0])
        if path is None:
            return False
        owned.update(path)
    if len(owned) > G.points(G.max_level):
        return False
    gs["nodes"][god] = owned
    b.gods = gs
    b.charm = {"god": gods.CHARM_GOD[god], "mods": search.pick_charm_mods(ctx, gods.CHARM_GOD[god], rng)}
    if not search.time_lock_ok(ctx, b):
        b.items["offhand"] = search.new_item(ctx, "offhand", "plushie", rng, b)
    return True


def _starforge(ctx, b):
    pools = ctx.pools[b.items["mainhand"].type]
    sf = [a for a in pools["CORRUPTED_IMPLICIT"] if a.attribute == "the_vault:arcane_nova_on_hit"]
    if not sf:
        return False
    b.items["mainhand"].seal = sf[0]
    return True


def case_share_boundary(ctx, rng):
    """Hungry Maw main with three secondaries, tiers walked toward a 0.7 main share."""
    from model.evaluate import evaluate
    b = _base(ctx, "ability:Fangs_Maw", rng)
    secs = ["Smite_Base", "Javelin_Base", "Fireball_Base"]
    for s in secs:
        _learn(ctx, b, s)
    best = None
    for t in range(1, ctx.ability_max(secs[0]) + 1):
        c = b.clone()
        for s in secs:
            c.abilities[s] = min(t, ctx.ability_max(s))
        share = evaluate(c, ctx)["family_result"]["main_share"]
        if best is None or abs(share - 0.7) < best[0]:
            best = (abs(share - 0.7), c)
    return "ability:Fangs_Maw", best[1], lambda r: len([x for x in r["family_result"]["rotation"] if x["kind"] == "secondary"]) == 3


def case_weave_fireball(ctx, rng):
    b = _base(ctx, "ability:Fireball_Base", rng)
    b.weave = True
    return "ability:Fireball_Base", b, lambda r: any(x["kind"] == "swings" and x["dps"] > 0 for x in r["family_result"]["rotation"])


def case_implode_main(ctx, rng):
    b = _base(ctx, "ability:Mana_Shield_Implode", rng)
    _learn(ctx, b, "Smite_Base")
    return "ability:Mana_Shield_Implode", b, lambda r: r["family_result"]["rotation"][0]["mana"] > 0


def case_unaffordable(ctx, rng):
    """Storm Arrow (secondary) and Taunt (buff) at 1e6 mana per cast: never cast (the context's configs are inflated
    by run_cases)."""
    b = _base(ctx, "ability:Smite_Base", rng)
    _learn(ctx, b, "Storm_Arrow_Base")
    _learn(ctx, b, "Taunt_Base")
    return "ability:Smite_Base", b, lambda r: any(x.get("blocked") for x in r["family_result"]["rotation"])


def case_pd_depleted(ctx, rng):
    b = _base(ctx, "ability:Fireball_Base", rng)
    if not _talent(ctx, b, "Depleted"):
        return None
    _talent(ctx, b, "Methodical")
    if ctx.gods is not None:
        if not _gods(ctx, b, ["idona_power_dump"], rng):
            return None
        b.pd = {"Fireball_Base"}
    return "ability:Fireball_Base", b, lambda r: 0.0 < r["family_result"]["u_low_mana"] <= 1.0 or ctx.gods is None


def case_hexbreaker(ctx, rng):
    b = _base(ctx, "ability:Smite_Base", rng)
    if not _talent(ctx, b, "Hexbreaker"):
        return None
    _learn(ctx, b, "Fangs_Maw")
    _learn(ctx, b, "Taunt_Base")
    return "ability:Smite_Base", b, lambda r: r["family_result"]["debuffed_uptime"] > 0


def case_stacks(ctx, rng):
    """Arcana and Blood Rush (kill stacks), Battle Trance (hit stacks) on a sword build; Berserk is a context power;
    Stack Hoarder and Super Stacker in the rework."""
    b = _base(ctx, "melee:sword", rng)
    for t in ("Arcana", "Blood_Rush", "Battle_Trance"):
        _talent(ctx, b, t, rng.randint(1, 3))
    if ctx.gods is not None:
        _gods(ctx, b, ["idona_stack_hoarder", "idona_super_stacker"], rng)
    return "melee:sword", b, lambda r: any(k.startswith("x:stack_talent:") for k in r["stats"].attr)


def case_concentrate(ctx, rng):
    b = _base(ctx, "melee:axe", rng)
    _learn(ctx, b, "Concentrate_Base", rng.randint(1, ctx.ability_max("Concentrate_Base")))
    return "melee:axe", b, lambda r: any(f.startswith("concentrate:") for f in r["family_result"]["flags"])


def case_starforge(ctx, rng):
    """Sneaking Starforge nova on a sword build and on a weaving Fireball build (rework: Power Dump node)."""
    out = []
    for fid in ("melee:sword", "ability:Fireball_Base"):
        b = _base(ctx, fid, rng)
        _mainhand(ctx, b, "sword", rng)
        if not _starforge(ctx, b):
            return None
        b.weave = fid.startswith("ability")
        if ctx.gods is not None:
            if not _gods(ctx, b, ["idona_power_dump"], rng):
                return None
            b.pd = {"Starforge_Nova", "Fireball_Base"} if b.weave else {"Starforge_Nova"}
        out.append((fid, b, lambda r: r["stats"].special.get("arcane_nova_hits", 0) > 0))
    return out


def case_pyramid(ctx, rng):
    b = _base(ctx, "melee:sword", rng)
    _mainhand(ctx, b, "sword", rng)
    pyr = [e for e in ctx.etchings.get("sword", []) if e["attribute"] == PYRAMID]
    if not pyr or not _talent(ctx, b, "Battle_Trance", rng.randint(1, 4)):
        return None
    b.items["mainhand"].etching = pyr[0]
    return "melee:sword", b, lambda r: r["stats"].attr.get("x:extra_stacks", 0) >= pyr[0]["value"]


CASES = [case_share_boundary, case_weave_fireball, case_implode_main, case_unaffordable, case_pd_depleted,
         case_hexbreaker, case_stacks, case_concentrate, case_starforge, case_pyramid]


def inflate(ctx, specs, cost):
    """A context whose `specs` cost `cost` mana per cast at every tier (the cast-guard case)."""
    from model import families
    ctx.abilities = copy.deepcopy(ctx.abilities)
    for sid in specs:
        for t in ctx.abilities[sid].get("tiers", [ctx.abilities[sid]]):
            t["manaCost"] = cost
    families._tier_cache.clear()
    return ctx


def run_cases(per):
    from model import families, kernel, stages
    from model.evaluate import evaluate
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from kernel_parity import FIELDS, close, py_fields, kernel_fields
    total = bad = 0
    report = []
    for case in CASES:
        n = hit = 0
        for stage in ("early", "mid", "end", "max"):
            ctx = _ctx(stage)
            if case is case_unaffordable:
                inflate(ctx, ["Storm_Arrow_Base", "Taunt_Base"], 1e6)
            rng = random.Random(f"{case.__name__}|{stage}|{stages.LEDGER}")
            groups = {}
            for _ in range(per):
                got = case(ctx, rng)
                if got is None:
                    continue
                for fid, b, check in (got if isinstance(got, list) else [got]):
                    groups.setdefault(fid, []).append((b, check))
            for fid, items in groups.items():
                fam = families.get(fid)
                ks = kernel.evaluate_many(ctx, fam, [b for b, _ in items])
                for (b, check), kr in zip(items, ks):
                    n += 1
                    r = evaluate(b, ctx, record=True)
                    hit += bool(check(r))
                    pr = py_fields(r)
                    if not kr["ok"]:
                        bad += 1
                        report.append((case.__name__, stage, fid, "kernel error", kr.get("error")))
                        continue
                    kf = kernel_fields(kr)
                    diffs = [(f, pr[f], kf[f]) for f in FIELDS if not close(pr[f], kf[f])]
                    if diffs:
                        bad += 1
                        report.append((case.__name__, stage, fid, diffs))
            if case is case_unaffordable:
                families._tier_cache.clear()
        total += n
        print(f"{case.__name__:22s} {n:3d} builds, feature active in {hit:3d}")
    return total, bad, report


def share_rule_anneal():
    """A short kernel anneal of Hungry Maw must return a build meeting main_damage_share_min (the hard prune)."""
    from model import families, kernel, stages
    from model.evaluate import evaluate
    ctx = _ctx("max")
    fam = families.get("ability:Fangs_Maw")
    res = kernel.anneal(ctx, fam, 20000, [7, 8])
    ms = stages.KNOBS["main_damage_share_min"]
    worst = min(evaluate(b, ctx)["family_result"]["main_share"] for _, b, _ in res)
    return worst, worst >= ms - 1e-9 * ms


def main_one(per):
    from model import stages
    total, bad, report = run_cases(per)
    worst, ok = share_rule_anneal()
    print(f"share rule after a kernel anneal: worst main share {worst:.3f} ({'ok' if ok else 'BELOW the minimum'})")
    for r in report[:12]:
        print("   ", r)
    print(f"{stages.LEDGER}: TOTAL {total} directed builds, {bad} mismatches")
    return 1 if bad or not ok else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", choices=("rework", "0.34.1", "both"), default="both")
    ap.add_argument("--per", type=int, default=4)
    a = ap.parse_args()
    if a.ledger == "both":
        rc = 0
        for led in ("rework", "0.34.1"):
            env = dict(os.environ, WV_LEDGER=led)
            rc |= subprocess.run([sys.executable, os.path.abspath(__file__), "--ledger", led, "--per", str(a.per)], env=env).returncode
        sys.exit(rc)
    if os.environ.get("WV_LEDGER", "0.34.1") != a.ledger:
        sys.exit(f"parity_cases_rotation: run with WV_LEDGER={a.ledger} (or --ledger both)")
    sys.exit(main_one(a.per))
