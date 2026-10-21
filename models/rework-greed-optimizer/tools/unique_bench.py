"""Unique gear bench for the greed-rework ledger: what each unique is worth to each build when the build is re-optimized
around it.

WV_LEDGER=rework python tools/unique_bench.py [--iters 400000] [--controls 2] [--stages early,mid,end,max]

For every standard (non Edge of Time) search winner in out/found_rework.json and every benched unique, the kernel
re-anneals from the winner with the unique locked into its slot (kernel job `ulock`: other uniques over the unique or
ancient limits come off, Temporal Breaking is forced when the time lock forbids the piece). Each winner also gets
`--controls` unlocked re-anneals with the same budget. The gain is the locked score minus the best of the original
winner and its controls, so search noise does not count as a gain; the spread of the controls is reported as noise.

Benched uniques (user ruling 2026-10-13): ability-specific uniques at every stage, as their ancient versions at end
and max; at max every unique, as its ancient version. Writes out/unique_bench_rework.json.
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("WV_LEDGER", "rework")

from model import catalog, families, kernel, stages  # noqa: E402
from model import log as mlog  # noqa: E402

FOUND = os.path.join(ROOT, "out", "found_rework.json")
RESULTS = os.path.join(ROOT, "out", "results_rework.json")
OUT = os.path.join(ROOT, "out", "unique_bench_rework.json")
P2_SCHEDULE = {"kind": "target", "a0": 0.1, "a1": 0.01, "t_init": 0.03, "eta": 0.05}
SEARCH_OPTS = {"weights": True, "repair": 4}


def ability_keys(u):
    """Ability keys a unique levels or modifies; empty for general uniques. all_* level bonuses do not count."""
    keys, special = set(), False
    for m in u["modifiers"]:
        if m["attribute"] == "the_vault:added_ability_level":
            for t in m.get("open", []):
                v = t.get("value")
                if isinstance(v, dict) and v.get("abilityKey") and not v["abilityKey"].startswith("all"):
                    keys.add(v["abilityKey"])
        if m["attribute"] == "the_vault:ability_special_modification":
            special = True
    return keys, special


SPECIAL_SKILL = {"the_vault:pestilence_wall": "Empower"}


def bench_set():
    """{regular unique id: {name, type, keys, skills, specific}} for every unique; ancients are 'ancient:' + id."""
    cat = catalog.load()
    abil = catalog.abilities()
    out = {}
    for uid, u in sorted(cat["uniques"]["uniques"].items()):
        keys, special = ability_keys(u)
        skills = set()
        for k in keys:
            if k in abil:
                skills.add(abil[k]["skill"])
            elif any(a.get("skill") == k for a in abil.values()):
                skills.add(k)
            else:
                mlog.fallback(f"unique-key-{k}", f"{uid}: ability key {k} matches no ability or skill; left unmatched")
        if special and not keys:
            if uid in SPECIAL_SKILL:
                skills.add(SPECIAL_SKILL[uid])
            else:
                mlog.fallback(f"unique-special-{uid}", f"{uid}: special modification with no ability key; no skill matched")
        out[uid] = {"name": u["name"], "type": u["type"], "keys": sorted(keys), "skills": sorted(skills),
                    "specific": bool(keys or special), "ancient_id": "ancient:" + uid if "ancient:" + uid in cat["ancients"] else None}
    return out


def wanted(stage, info):
    """The unique id to bench at this stage, or None. Ability-specific: regular early/mid, ancient end/max. Max: all."""
    if stage == "max":
        return info["ancient_id"]
    if not info["specific"]:
        return None
    return info["ancient_id"] if stage == "end" else info["_id"]


def slot_of(utype):
    return utype if utype in catalog.ARMOR else "mainhand" if utype in catalog.MAINHAND else "offhand"


def score_task(args):
    """Python re-score of kernel builds for one context (cycles and requirement status for the report)."""
    stage, mode, fid, deck, docs = args
    from model.evaluate import evaluate
    import run
    ctx = run.ctx_for(stage, mode, "all", deck)
    ex = kernel.export_for(ctx)
    fam = families.get(fid)
    out = []
    for key, doc in docs:
        r = evaluate(ex.to_build(doc, fam), ctx)
        out.append((key, {"score": r["score"], "cycle": r["cycle"], "cycle_damage": r["cycle_damage"],
                          "cycle_survival": r["cycle_survival"], "met": r["requirements"]["met"]}))
    return (stage, mode, fid), out, mlog.take()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=400000)
    ap.add_argument("--controls", type=int, default=2)
    ap.add_argument("--stages", default="early,mid,end,max")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--procs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    import run
    t0 = time.time()
    with open(FOUND, encoding="utf-8") as f:
        found = json.load(f)
    with open(RESULTS, encoding="utf-8") as f:
        res_meta = json.load(f)["meta"]
    bset = bench_set()
    for uid, info in bset.items():
        info["_id"] = uid
    best = {}
    for r in found:
        if r["variant"] != "all" or r["stage"] not in args.stages.split(","):
            continue
        k = (r["stage"], r["mode"], r["family"])
        if k not in best or r["score"] > best[k]["score"]:
            best[k] = r
    jobs, keys, status = [], [], {}
    for (s, m, f), r in sorted(best.items()):
        ctx = run.ctx_for(s, m, "all", r["deck"])
        ex = kernel.export_for(ctx)
        fam = families.get(f)
        for c in range(args.controls):
            jobs.append((ctx, fam, args.iters, zlib.crc32(f"{s}{m}{f}|ctl|{c}".encode()), P2_SCHEDULE, False, r["doc"], False,
                         -1, SEARCH_OPTS))
            keys.append((s, m, f, r["deck"], "control", c))
        for uid, info in bset.items():
            bid = wanted(s, info)
            if bid is None:
                continue
            st = status.setdefault(s, {})
            if bid not in ex.unique_idx:
                st[bid] = "blocked"
                continue
            st[bid] = "benched"
            jobs.append((ctx, fam, args.iters, zlib.crc32(f"{s}{m}{f}|{bid}".encode()), P2_SCHEDULE, False, r["doc"], False, -1,
                         SEARCH_OPTS, (slot_of(info["type"]), bid)))
            keys.append((s, m, f, r["deck"], "unique", bid))
    print(f"unique bench: {len(jobs)} re-anneals ({len(best)} builds)", flush=True)
    print("[progress] plan " + json.dumps({"tool": "unique_bench", "threads": args.threads or os.cpu_count(),
                                           "phases": [{"name": "bench", "jobs": len(jobs), "iters": args.iters}]}), flush=True)
    outs = kernel.batch_anneal(jobs, threads=args.threads)
    groups = {}
    for key, (score, doc, info) in zip(keys, outs):
        s, m, f, deck, kind, x = key
        tag = f"{kind}|{x}"
        if info.get("ulock_failed"):
            tag = f"failed|{x}|{info.get('ulock_reason', '')}"
        groups.setdefault((s, m, f, deck), []).append((tag, doc))
    scored = {}
    with mp.Pool(args.procs) as pool:
        for (s, m, f), lst, fb in pool.imap_unordered(score_task, [(s, m, f, d, docs) for (s, m, f, d), docs in groups.items()]):
            scored[(s, m, f)] = dict(lst)
            for k, v in fb.items():
                mlog.fallback(k, v["message"])
    stages_out = {}
    for (s, m, f), r in sorted(best.items()):
        sc = scored.get((s, m, f), {})
        ctl = [v for k, v in sc.items() if k.startswith("control|")]
        ref = max([r["score"]] + [v["score"] for v in ctl])
        noise = (max(v["score"] for v in ctl) - min(v["score"] for v in ctl)) if len(ctl) > 1 else None
        entry = {"base_score": r["score"], "ref_score": ref, "noise": noise, "uniques": {}}
        for k, v in sc.items():
            kind, uid = k.split("|")[:2]
            if kind == "unique":
                entry["uniques"][uid] = dict(v, gain=v["score"] - ref)
            elif kind == "failed":
                entry["uniques"][uid] = {"failed": k.split("|")[2] or "unknown"}
        stages_out.setdefault(s, {"builds": {}})["builds"][f] = entry
    for s in stages_out:
        stages_out[s]["status"] = status.get(s, {})
    unique_info = {}
    for uid, info in bset.items():
        for bid in {uid, info["ancient_id"]} - {None}:
            unique_info[bid] = {"name": ("Ancient " if bid.startswith("ancient:") else "") + info["name"], "type": info["type"],
                                "slot": slot_of(info["type"]), "skills": info["skills"], "keys": info["keys"],
                                "specific": info["specific"], "ancient": bid.startswith("ancient:")}
    meta = {"generated": time.strftime("%Y-%m-%d %H:%M"), "results_generated": res_meta["generated"], "iters": args.iters,
            "controls": args.controls, "schedule": P2_SCHEDULE, "opts": SEARCH_OPTS, "runtime_s": time.time() - t0,
            "fallbacks": mlog.summary()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "uniques": unique_info, "stages": stages_out}, f)
    print(f"wrote {OUT} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
