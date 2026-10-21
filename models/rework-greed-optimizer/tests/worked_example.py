"""The pinned worked example (tests/test_worked_example.py): a saved search winner stored by name (affix idents,
etching / unique / trinket / card / talent / spec ids, god nodes), so it survives changes to the kernel export order.

WV_LEDGER=rework python tests/worked_example.py --write   re-pins tests/data/worked_example.json from the current
                                                         out/found_rework.json row and the current scores.
Re-pin only when a behaviour change is intended, and say so in the commit.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PIN = os.path.join(ROOT, "tests", "data", "worked_example.json")
ROW = {"family": "ability:Smite_Base", "stage": "max", "variant": "all"}
FIELDS = ("score", "cycle", "cycle_damage", "cycle_survival", "dps", "pack_dps", "ehp")


def _ident(a):
    return None if a is None else a.ident


def _etch(e):
    return None if e is None else e["id"]


def to_named(b):
    items = {}
    for slot, it in b.items.items():
        items[slot] = {"type": it.type, "implicits": [[g, a.ident] for g, a in it.implicits.items()],
                       "prefixes": [a.ident for a in it.prefixes], "suffixes": [a.ident for a in it.suffixes],
                       "seal": _ident(it.seal), "unusual": _ident(it.unusual), "etching": _etch(it.etching),
                       "unique": it.unique, "udrop": it.udrop,
                       "shadow": None if it.shadow is None else [_ident(it.shadow[0]), _etch(it.shadow[1]), _ident(it.shadow[2])]}
    gods = None
    if b.gods is not None:
        gods = {"levels": dict(b.gods["levels"]), "nodes": {g: sorted(v) for g, v in b.gods["nodes"].items()},
                "mts": {g: list(v) for g, v in b.gods["mts"].items()}}
    return {"items": items, "trinkets": list(b.trinkets), "charm_god": b.charm["god"],
            "charm_mods": [[m["attribute"], m["group"], m["value"]] for m in b.charm["mods"]],
            "deck": list(b.deck), "talents": dict(b.talents), "abilities": dict(b.abilities), "greed": sorted(b.greed),
            "weave": bool(b.weave), "pd": sorted(b.pd), "gods": gods}


def _find(lst, ident, what):
    for a in lst:
        if a.ident == ident:
            return a
    raise KeyError(f"{what}: affix {ident} not offered")


def from_named(ctx, fam, d):
    from model.build import Build, Item
    from model import catalog
    b = Build(ctx.stage_name, ctx.stage, ctx.mode, fam.id)
    for slot, x in d["items"].items():
        it = Item(slot, x["type"])
        pools = ctx.pools[x["type"]]
        groups = catalog.implicit_groups(pools)
        for g, ident in x["implicits"]:
            it.implicits[g] = _find(groups[g], ident, f"{slot} implicit {g}")
        it.prefixes = [_find(pools["PREFIX"], i, f"{slot} prefix") for i in x["prefixes"]]
        it.suffixes = [_find(pools["SUFFIX"], i, f"{slot} suffix") for i in x["suffixes"]]
        unusual = pools["UNUSUAL_PREFIX"] + pools["UNUSUAL_SUFFIX"]
        etchings = ctx.etchings.get(x["type"], [])

        def etch(eid):
            return None if eid is None else next(e for e in etchings if e["id"] == eid)
        it.unusual = None if x["unusual"] is None else _find(unusual, x["unusual"], f"{slot} unusual")
        it.etching = etch(x["etching"])
        if x["unique"] is not None:
            u = ctx.unique_by_id[x["unique"]]
            it.unique, it.uaffixes, it.udrop = u["id"], u["affixes"], x["udrop"]
            it.seal = None if x["seal"] is None else _find(ctx.unique_seals, x["seal"], f"{slot} unique seal")
            sh = x["shadow"]
            it.shadow = (None if sh[0] is None else _find(pools["CORRUPTED_IMPLICIT"], sh[0], f"{slot} parked seal"),
                         etch(sh[1]), None if sh[2] is None else _find(unusual, sh[2], f"{slot} parked unusual"))
        else:
            it.seal = None if x["seal"] is None else _find(pools["CORRUPTED_IMPLICIT"], x["seal"], f"{slot} seal")
        b.items[slot] = it
    b.trinkets = list(d["trinkets"])
    god = d["charm_god"]
    mods = []
    for attr, group, value in d["charm_mods"]:
        mods.append(next(m for m in ctx.charms[god] if m["attribute"] == attr and m["group"] == group and m["value"] == value))
    b.charm = {"god": god, "mods": mods}
    b.deck = list(d["deck"])
    b.talents = dict(d["talents"])
    b.abilities = dict(d["abilities"])
    b.greed = set(d["greed"])
    b.prestige = ctx.prestige
    b.weave = d["weave"]
    b.pd = set(d["pd"])
    if d["gods"] is not None:
        from model import gods as godmod
        gs = godmod.empty_state()
        for g in godmod.GODS:
            gs["levels"][g] = d["gods"]["levels"][g]
            gs["nodes"][g] = set(d["gods"]["nodes"][g])
            gs["mts"][g] = list(d["gods"]["mts"][g])
        b.gods = gs
    return b


def load():
    """(ctx, fam, build, pins) of the pinned example."""
    import run
    from model import families
    with open(PIN, encoding="utf-8") as fh:
        doc = json.load(fh)
    ctx = run.ctx_for(doc["stage"], doc["mode"], doc["variant"], doc["deck"])
    fam = families.get(doc["family"])
    return ctx, fam, from_named(ctx, fam, doc["build"]), doc["pins"]


def scores(ctx, fam, b, exe=None):
    """{"python": fields, "kernel": fields} for one build."""
    from model import kernel
    from model.evaluate import evaluate
    r = evaluate(b, ctx)
    py = {"score": r["score"], "cycle": r["cycle"], "cycle_damage": r["cycle_damage"], "cycle_survival": r["cycle_survival"],
          "dps": r["dps_no_hp_scaling"], "pack_dps": r["pack_dps"], "ehp": r["ehp"]}
    kr = kernel.evaluate_many(ctx, fam, [b], exe=exe)[0]
    if not kr["ok"]:
        raise RuntimeError(f"kernel error: {kr['error']}")
    return {"python": py, "kernel": {f: kr[f] for f in FIELDS}}


def write():
    import run
    from model import families, kernel
    with open(os.path.join(ROOT, "out", "found_rework.json"), encoding="utf-8") as fh:
        rows = json.load(fh)
    row = next(r for r in rows if all(r[k] == v for k, v in ROW.items()))
    ctx = run.ctx_for(row["stage"], row["mode"], row["variant"], row["deck"])
    fam = families.get(row["family"])
    ex = kernel.export_for(ctx)
    b = ex.to_build(row["doc"], fam)
    named = to_named(b)
    b2 = from_named(ctx, fam, named)
    if ex.build_doc(b2) != ex.build_doc(b):
        sys.exit("worked_example: the named form does not round-trip this build")
    pins = scores(ctx, fam, b2)
    doc = {"family": row["family"], "stage": row["stage"], "mode": row["mode"], "variant": row["variant"], "deck": row["deck"],
           "build": named, "pins": pins}
    os.makedirs(os.path.dirname(PIN), exist_ok=True)
    with open(PIN, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"pinned {row['family']} {row['stage']} {row['variant']}: score {pins['python']['score']!r} (Python), "
          f"{pins['kernel']['score']!r} (kernel) -> {PIN}")


if __name__ == "__main__":
    if os.environ.setdefault("WV_LEDGER", "rework") != "rework":
        sys.exit("worked_example: the pinned build is a rework build; set WV_LEDGER=rework")
    if "--write" in sys.argv:
        write()
    else:
        ctx, fam, b, pins = load()
        print(json.dumps({"now": scores(ctx, fam, b), "pinned": pins}, indent=1))
