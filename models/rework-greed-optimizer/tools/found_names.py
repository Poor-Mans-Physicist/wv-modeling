"""Translate out/found_rework.json across a change that moves the export's affix indices (ENGINEERING_PRACTICES
section 7): a pool affix added or removed (a seal leaving a pool, a gear attribute becoming a combat stat) shifts every
later affix index, so the stored build docs would decode to the wrong affixes.

    (old checkout)  WV_LEDGER=rework WV_DECK_MENUS=1 python tools/found_names.py --dump names.json
    (new checkout)  WV_LEDGER=rework WV_DECK_MENUS=1 python tools/found_names.py --load names.json --out out/found_rework.json

--dump replaces every affix index of each row's doc (implicits, prefixes, suffixes, seal, unusual and the unique
shadow's seal and unusual) by a key naming the affix (kind, attribute, identifier, group, raw roll). --load maps the
keys back to the new export's indices; a key the new export lacks (an affix that left its pool) is dropped from the
row with a fallback line, and the row keeps the rest of its build. Every other index space (types, uniques, etchings,
trinkets, cards, talents, specs, god nodes) must be unchanged between the two checkouts: the tool checks the type,
unique, spec and talent name lists and refuses to load when they differ.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if os.environ.setdefault("WV_LEDGER", "rework") != "rework":
    sys.exit(f"found_names: out/found_rework.json holds rework builds; WV_LEDGER is {os.environ['WV_LEDGER']!r}")

import run  # noqa: E402
from model import kernel  # noqa: E402

FOUND = os.path.join(ROOT, "out", "found_rework.json")


def akey(a):
    return "|".join([str(a.kind), a.attribute, str(a.ident), str(a.group), json.dumps(a.raw, sort_keys=True)])


def names_of(ex):
    return {"types": list(ex.type_names), "uniques": list(ex.unique_ids), "specs": list(ex.spec_ids),
            "talents": list(ex.talent_ids)}


def _map_doc(doc, f):
    out = json.loads(json.dumps(doc))
    for it in out["items"]:
        it["implicits"] = [f(i) for i in it["implicits"]]
        it["prefixes"] = [f(i) for i in it["prefixes"]]
        it["suffixes"] = [f(i) for i in it["suffixes"]]
        for k in ("seal", "unusual"):
            if it.get(k) is not None:
                it[k] = f(it[k])
        sh = it.get("shadow")
        if sh:
            it["shadow"] = [f(sh[0]) if sh[0] is not None else None, sh[1], f(sh[2]) if sh[2] is not None else None]
    return out


def exports(rows):
    cache = {}
    for r in rows:
        key = (r["stage"], r["mode"], r["variant"], r["deck"])
        if key not in cache:
            cache[key] = kernel.export_for(run.ctx_for(*key))
        yield r, cache[key]


def dump(rows):
    out = []
    for r, ex in exports(rows):
        objs = ex.affix_objs
        out.append(dict(r, doc=_map_doc(r["doc"], lambda i: akey(objs[i])), names=names_of(ex)))
    return out


def load(rows):
    out = []
    for r, ex in exports(rows):
        if r["names"] != names_of(ex):
            sys.exit(f"found_names: {r['stage']} {r['family']}: type, unique, spec or talent lists differ between the "
                     "checkouts; this tool only translates affix indices")
        idx = {akey(a): i for i, a in enumerate(ex.affix_objs)}
        lost = []

        def f(k):
            if k in idx:
                return idx[k]
            lost.append(k)
            return None
        doc = _map_doc(r["doc"], f)
        for it in doc["items"]:
            if any(x is None for x in it["implicits"]):
                sys.exit(f"found_names: {r['stage']} {r['family']}: an implicit left the export; cannot translate")
            it["prefixes"] = [x for x in it["prefixes"] if x is not None]
            it["suffixes"] = [x for x in it["suffixes"] if x is not None]
        for k in lost:
            print(f"[found_names][FALLBACK] {r['stage']} {r['family']} {r['variant']}: affix {k.split('|')[2]} is gone; dropped "
                  "from the build", file=sys.stderr)
        rec = {k: v for k, v in r.items() if k != "names"}
        rec["doc"] = doc
        out.append(rec)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=None, help="write name docs of --found here")
    ap.add_argument("--load", default=None, help="name docs written by --dump in the old checkout")
    ap.add_argument("--found", default=FOUND)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if bool(a.dump) == bool(a.load):
        sys.exit("found_names: give exactly one of --dump and --load")
    if a.dump:
        with open(a.found, encoding="utf-8") as fh:
            res = dump(json.load(fh))
        dest = a.dump
    else:
        with open(a.load, encoding="utf-8") as fh:
            res = load(json.load(fh))
        dest = a.out or FOUND
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    print(f"found_names: {len(res)} rows -> {dest}", file=sys.stderr)
