"""Run the build search for every family x stage x mode and write out/results.json."""
import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from model import stages, families, catalog
from model.bugs import BUG_INFO
from model.context import Context, NICHE_TALENTS
from model.search import anneal, trinket_layout
from model.evaluate import evaluate
from model import log as mlog
from model import kernel

SCHEDULES = {
    "target": {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05},
    "geometric": {"kind": "geometric", "t0": 0.6, "t1": 0.004},
}

OUT = os.path.join(ROOT, "out", "results.json")


def classify(r):
    ratio = r["pack_dps"] / max(r["dps_raw"], 1e-9)
    if ratio >= 3.0:
        return "crowd clear", ratio
    if ratio <= 1.6:
        return "single target", ratio
    return "hybrid", ratio


def niche_flags(build, ctx, r):
    flags = list(r["family_result"].get("flags", []))
    for tid, n in build.talents.items():
        if n > 0 and tid in NICHE_TALENTS:
            flags.append(f"niche: {tid} {NICHE_TALENTS[tid]}")
    for it in build.items.values():
        e = it.etching
        if e and e.get("minGreedTier", 0) >= 7:
            flags.append(f"rare: {e['name']} etching (greed tier {e['minGreedTier']}+, ~1-2% of trader offers)")
    ex = r.get("execution")
    if ex and ex["capped"]:
        flags.append(f"gimmick: %-missing-HP execution would add {ex['gain']:.1f} cycles; capped at +{ctx.knobs['execution_cycle_cap']:.0f} "
                     "(regular hyper mobs still need real damage)")
    if build.trinkets and "the_vault:the_dice" in build.trinkets:
        flags.append("variance: The Dice (x0.01-x3.0 per hit, mean x1.5)")
    return flags


def affix_json(a, slot, legendary=False):
    out = {"attribute": a.attribute, "value": a.value, "kind": a.kind, "label": a.label}
    if isinstance(a.raw, dict) and ("abilityKey" in a.raw or "talentKey" in a.raw or "effectKey" in a.raw):
        out["raw"] = {k: v for k, v in a.raw.items() if k in ("abilityKey", "talentKey", "levelChange", "effectKey", "amplifier", "legendary")}
    if isinstance(a.raw, dict) and "min" in a.raw:
        out["range"] = [a.raw["min"], a.raw["max"]]
    return out


def item_json(it, ctx):
    if it.unique is not None:
        u = ctx.unique_by_id[it.unique]
        live = [(i, a) for i, a in enumerate(u["affixes"]) if i != it.udrop]
        return {
            "slot": it.slot, "type": it.type, "rarity": "UNIQUE", "legendary": False,
            "unique": {"id": u["id"], "name": u["name"], "powers": u["powers"],
                       "dropped": affix_json(u["affixes"][it.udrop], it.slot) if it.udrop is not None else None},
            "implicits": [affix_json(a, it.slot) for i, a in live if a.kind == "IMPLICIT"],
            "prefixes": [affix_json(a, it.slot) for i, a in live if a.kind == "PREFIX"],
            "suffixes": [affix_json(a, it.slot) for i, a in live if a.kind == "SUFFIX"],
            "seal": affix_json(it.seal, it.slot) if it.seal else None, "unusual": None, "etching": etching_json(it),
        }
    rar = "OMEGA" if it.type == "vault_necklace" else ctx.rarity
    return {
        "slot": it.slot, "type": it.type, "rarity": rar,
        "legendary": it.type == "vault_necklace" and bool(ctx.stage.get("necklace_legendary")),
        "implicits": [affix_json(a, it.slot) for a in it.implicits.values()],
        "prefixes": [affix_json(a, it.slot) for a in it.prefixes],
        "suffixes": [affix_json(a, it.slot) for a in it.suffixes],
        "seal": affix_json(it.seal, it.slot) if it.seal else None,
        "unusual": affix_json(it.unusual, it.slot) if it.unusual else None,
        "etching": etching_json(it),
    }


def etching_json(it):
    e = it.etching
    return ({"id": e["id"], "name": e["name"], "attribute": e["attribute"], "value": e["value"],
             "minGreedTier": e["minGreedTier"]} if e else None)


SLOT_FAMILY = {"row": "evo", "col": "evo", "surr": "evo", "diag": "evo", "deluxe": "deluxe", "typeless": "typeless"}


def deck_json(build, ctx):
    lay = ctx.deck_layout
    stat = {tuple(s["pos"]): (i, s) for i, s in enumerate(ctx.deck_slots)}
    cells = []
    total = {}
    for r, line in enumerate(lay["grid"]):
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            cell = {"r": r, "c": c, "t": ch}
            if (r, c) in stat:
                i, s = stat[(r, c)]
                cid = build.deck[i]
                attr, contrib, val, mult = ctx.deck_options[i][cid]
                card = ctx.card_registry[SLOT_FAMILY[s["kind"]]][cid]
                cell.update({"card": cid, "attribute": attr, "value": val, "mult": mult, "contrib": contrib,
                             "kind": s["kind"], "base": s["base"], "boost": s["boost"], "mirror": s["mirror"],
                             "core": s["common"], "groups": [g for g in card["groups"] if g not in ("Evolution", "Stat", "Deluxe")]})
                total[attr] = total.get(attr, 0.0) + contrib
            cells.append(cell)
    name = ctx.stage["deck"]["name"] if ctx.deck_layout_key == ctx.stage["deck"]["layout"] else f"{lay['deck']} ({lay['config']})"
    return {"name": name, "deck": lay["deck"], "key": lay["key"], "config": lay["config"],
            "ndm": lay["ndm_deckfast"], "cores": lay["cores"], "implicits": lay["implicits"], "core_slots": lay["core_slots"],
            "card_tier": ctx.stage["card_tier"], "cells": cells, "totals": total,
            "stat_slots": len(ctx.deck_slots), "rows": len(lay["grid"]), "cols": max(len(g) for g in lay["grid"])}


def bug_attribution(b, ctx, r):
    """Each bug switched to intended on its own: how much of the build's result it carries."""
    out = []
    for bid, info in BUG_INFO.items():
        c = b.clone()
        c.bug_overrides = {bid: "intended"}
        try:
            r2 = evaluate(c, ctx)
        except Exception as e:
            mlog.fallback("bug-attr-" + bid, f"{type(e).__name__}: {e}")
            continue
        dc = r["cycle"] - r2["cycle"]
        ratio = r["dps_no_hp_scaling"] / max(r2["dps_no_hp_scaling"], 1e-9)
        pratio = r["pack_dps"] / max(r2["pack_dps"], 1e-9)
        if abs(dc) >= 0.05 or abs(math.log(max(ratio, 1e-9))) >= 0.05 or abs(math.log(max(pratio, 1e-9))) >= 0.1:
            out.append({"id": bid, "cycle_delta": dc, "dps_ratio": ratio, "pack_ratio": pratio, **info})
    out.sort(key=lambda x: -abs(x["cycle_delta"]) - 0.01 * abs(math.log(max(x["dps_ratio"], 1e-9))))
    return out


def etching_attribution(b, ctx, r):
    out = []
    for slot, it in b.items.items():
        if not it.etching:
            continue
        c = b.clone()
        c.items[slot].etching = None
        r2 = evaluate(c, ctx)
        out.append({"slot": slot, "name": it.etching["name"], "id": it.etching["id"],
                    "cycle_delta": r["cycle"] - r2["cycle"],
                    "dps_ratio": r["dps_no_hp_scaling"] / max(r2["dps_no_hp_scaling"], 1e-9),
                    "pack_ratio": r["pack_dps"] / max(r2["pack_dps"], 1e-9),
                    "survival_delta": r["cycle_survival"] - r2["cycle_survival"]})
    out.sort(key=lambda x: -abs(math.log(max(x["dps_ratio"], 1e-9))) - abs(x["cycle_delta"]))
    return out


def serialize(build, ctx, r):
    st = r["stats"]
    d = r["derived"]
    fam = families.get(build.family)
    talents = []
    for tid, n in sorted(build.talents.items()):
        if n > 0:
            eff = int(st.attr.get("x:talent_tier:" + tid, n))
            talents.append({"id": tid, "learned": n, "effective": eff, "cost": ctx.talent_cost(tid, n)})
    abil = []
    for sid, n in sorted(build.abilities.items()):
        if n > 0:
            eff, bonus = families.effective_tier(ctx, st, sid, n)
            abil.append({"id": sid, "skill": ctx.abilities[sid]["skill"], "learned": n, "effective": eff, "bonus": bonus,
                         "cost": ctx.ability_cost(sid, n), "max": ctx.ability_max(sid),
                         "role": "primary" if sid == fam.main_spec else
                         "baseline" if ctx.abilities[sid]["skill"] in stages.BASELINE_ABILITIES else "support"})
    sources = {}
    for attr, lst in st.sources.items():
        sources[attr] = [[s, v if isinstance(v, str) else round(v, 6)] for s, v in lst]
    fr = r["family_result"]
    cls, ratio = classify(r)
    return {
        "family": build.family, "family_label": fam.label, "kind": fam.kind, "stage": build.stage_name,
        "mode": build.mode, "main_spec": fam.main_spec,
        "scaling": getattr(getattr(fam, "model", None), "scaling", "AD (melee)"),
        "score": r["score"], "cycle": r["cycle"], "cycle_damage": r["cycle_damage"], "cycle_survival": r["cycle_survival"],
        "dps": r["dps_no_hp_scaling"], "dps_raw": r["dps_raw"], "dps_matched": r["dps_vs_matched_boss"],
        "dps_lo": fr.get("dps_lo"), "dps_hi": fr.get("dps_hi"),
        "pack_dps": r["pack_dps"], "targets": fr.get("targets"), "ehp": r["ehp"], "damage_taken_mult": r["damage_taken_mult"],
        "hp_scaling": r["hp_scaling"], "ttk": r["ttk_at_cycle"], "execution": r["execution"],
        "class": cls, "pack_ratio": ratio,
        "hit": fr.get("hit"), "rate": fr.get("rate"), "steps": fr.get("steps"), "notes": fr.get("notes"),
        "mana_supply": fr.get("mana_supply"), "mana_demand": fr.get("mana_demand"), "source": fr.get("source"),
        "mana_sources": fr.get("mana_sources", []), "mana_drains": fr.get("mana_drains", []),
        "flags": niche_flags(build, ctx, r),
        "derived": dict(d), "sources": sources,
        "items": {k: item_json(v, ctx) for k, v in build.items.items()},
        "trinkets": [{"id": t, "name": ctx.trinkets_by_id[t]["name"], "text": ctx.trinkets_by_id[t]["text"],
                      "slot": ctx.trinkets_by_id[t]["slot"]} for t in build.trinkets],
        "trinket_layout": trinket_layout(ctx, build.trinkets), "pouch": ctx.stage.get("pouch"), "zephyr": bool(ctx.stage.get("zephyr")),
        "charm": {"god": build.charm["god"], "mods": [{"attribute": m["attribute"], "value": m["value"]} for m in build.charm["mods"]]},
        "deck": deck_json(build, ctx),
        "talents": talents, "abilities": abil, "greed": sorted(build.greed),
        "prestige": [{"id": p["id"], "name": p.get("name", p["id"]), "type": p.get("type")} for p in build.prestige
                     if p.get("type") in ("gear_attribute_power", "masterful_power", "berserk_power")],
        "skill_points": {"spent": build.skill_points_spent(ctx), "total": build.skill_points_total(ctx)},
        "requirements": {k: v for k, v in r["requirements"].items() if k != "penalty"},
        "offhand_locked": ctx.time_lock,
        "ability_levels": [[k, v] for k, v in st.ability_levels],
        "healing": r["healing"], "survival": r["survival"], "weave": fr.get("weave"),
        "unique_powers": [{"slot": s, "name": ctx.unique_by_id[it.unique]["name"], "powers": ctx.unique_by_id[it.unique]["powers"]}
                          for s, it in build.items.items() if it.unique is not None],
        "talent_levels": [[k, v] for k, v in st.talent_levels],
    }


_ctx_cache = {}


def ctx_for(stage, mode, variant="all", deck=None):
    deck = deck or stages.STAGES[stage]["deck"]["layout"]
    if (stage, mode, variant, deck) not in _ctx_cache:
        _ctx_cache[(stage, mode, variant, deck)] = Context(stage, mode, variant, deck)
    return _ctx_cache[(stage, mode, variant, deck)]


def deck_choices(stage):
    st = stages.STAGES[stage]
    return st.get("deck_choices") or [st["deck"]["layout"]]


def seeds_for(stage, mode, fid, restarts):
    return [1000 * k + zlib.crc32(f"{stage}{mode}{fid}".encode()) % 997 for k in range(restarts)]


def task(args):
    """Python engine: anneal, then report. Rust engine: report only (the kernel batch already searched)."""
    stage, mode, fid, iters, restarts, engine, found = args
    fam = families.get(fid)
    cands = []
    if engine == "rust":
        for (variant, deck), (s, doc, digest) in sorted(found.items()):
            c = ctx_for(stage, mode, variant, deck)
            ex = kernel.export_for(c)
            if ex.digest != digest:
                raise RuntimeError(f"{stage} {mode}: worker export differs from the batch export; index tables would not match")
            bb = ex.to_build(doc, fam)
            rr = evaluate(bb, c)
            if abs(rr["score"] - s) > 1e-7 * max(1.0, abs(rr["score"])):
                mlog.fallback("kernel-parity", f"{stage} {mode} {fid}: kernel scored {s:.6f}, Python {rr['score']:.6f}; the Python score is used")
            cands.append((rr["score"], variant, deck, bb, c, rr))
    else:
        for deck in deck_choices(stage):
            c = ctx_for(stage, mode, "all", deck)
            best = None
            for seed in seeds_for(stage, mode, fid, restarts):
                bb, s = anneal(c, fam, iters, seed=seed)
                if best is None or s > best[0]:
                    best = (s, bb)
            cands.append((best[0], "all", deck, best[1], c, evaluate(best[1], c)))
    top = max(cands, key=lambda x: x[0])
    main = max((x for x in cands if x[1] == "all"), key=lambda x: x[0])
    if top[1] != "all" and top[0] > main[0] + 1e-9:
        mlog.fallback("no-unique-better", f"{stage} {mode} {fid}: the no-uniques search found a better build than the "
                      "main search (search noise; that build is legal in both and is reported)")
    _, _, deck, b, ctx, _ = top
    nu = [x for x in cands if x[1] == "no_uniques"]
    rn = max(nu, key=lambda x: x[0])[5] if nu else None
    r = evaluate(b, ctx, record=True)
    sb = serialize(b, ctx, r)
    if rn is not None:
        sb["without_uniques"] = {"cycle": rn["cycle"], "cycle_damage": rn["cycle_damage"], "cycle_survival": rn["cycle_survival"]}
    if getattr(b, "weave", False):
        nb = b.clone()
        nb.weave = False
        rw = evaluate(nb, ctx)
        sb["without_weave"] = {"cycle": rw["cycle"], "cycle_damage": rw["cycle_damage"], "cycle_survival": rw["cycle_survival"],
                               "healing": rw["healing"]["per_s"], "mana_supply": rw["family_result"]["mana_supply"]}
    sb["etching_effects"] = etching_attribution(b, ctx, r)
    if mode == "bugged":
        sb["bug_effects"] = bug_attribution(b, ctx, r)
        ictx = ctx_for(stage, "intended", ctx.variant, ctx.deck_layout_key)
        ib = b.clone()
        ib.mode = "intended"
        ib.talents = {t: n for t, n in ib.talents.items() if t in ictx.talents}
        ir = evaluate(ib, ictx)
        sb["intended_check"] = {"cycle": ir["cycle"], "dps": ir["dps_no_hp_scaling"], "ehp": ir["ehp"]}
    else:
        sb["bug_effects"] = []
    return [sb], mlog.take()


def boss_table(last=30):
    """Hyperboss stats per cycle as the model scores them (chaos modifiers and Frenzy per the hyper_* knobs)."""
    from model import hyper
    g = hyper.grid(stages.KNOBS, stages.HYPER)
    rows = hyper.tables(stages.KNOBS)
    out = []
    for c in range(0, last + 1):
        r = rows[c]
        out.append({"cycle": c, "health_base": stages.boss_health(c), "damage_base": stages.boss_damage(c),
                    "chaos": r["chaos"], "hp_factor": r["hp_factor"], "hp_factor_p10": r["hp_factor_p10"],
                    "hp_factor_p90": r["hp_factor_p90"], "health": hyper.at(g, "H", c), "damage": hyper.at(g, "D", c),
                    "frenzy": r["frenzy_mult"], "p_frenzy": r["p_frenzy"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=320000)
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--no-unique-pass", action="store_true", help="skip the comparison search with uniques banned")
    ap.add_argument("--engine", choices=("rust", "python"), default="rust",
                    help="rust: libs/buildkernel/ (legal-only moves, schedule below); python: model/search.py (geometric schedule)")
    ap.add_argument("--schedule", choices=sorted(SCHEDULES), default="target")
    ap.add_argument("--procs", type=int, default=os.cpu_count(), help="processes for the reporting pass (and the Python engine)")
    ap.add_argument("--threads", type=int, default=0, help="kernel threads (0 = all logical cores)")
    ap.add_argument("--families", default="")
    ap.add_argument("--stages", default="early,mid,end,max")
    args = ap.parse_args()
    fams = [f.id for f in families.all_families()]
    if args.families:
        fams = [f for f in fams if any(x in f for x in args.families.split(","))]
    if args.engine == "rust" and not os.path.exists(kernel.EXE):
        sys.exit(f"Rust kernel not built: {kernel.EXE}. Run `cargo build --release` in libs/buildkernel/ or pass --engine python.")
    combos = [(s, m, f) for s in args.stages.split(",") for m in stages.MODES for f in fams
              if ctx_for(s, m).mainhands(families.get(f))]
    skipped = [(s, m, f) for s in args.stages.split(",") for m in stages.MODES for f in fams
               if not ctx_for(s, m).mainhands(families.get(f))]
    for s, m, f in skipped:
        mlog.fallback(f"no-mainhand-{s}-{m}-{f}", f"{f} has no legal mainhand at {s} {m} (battlestaff with OFFHAND-STAFF "
                      "fixed loses the time plushie); no build")
    t0 = time.time()
    found = {c: {} for c in combos}
    if args.engine == "rust":
        jobs, keys = [], []
        variants = ["all"] if args.no_unique_pass else ["all", "no_uniques"]
        for v in variants:
            for s, m, f in combos:
                for deck in deck_choices(s):
                    for seed in seeds_for(s, m, f, args.restarts):
                        jobs.append((ctx_for(s, m, v, deck), families.get(f), args.iters, seed, SCHEDULES[args.schedule], False))
                        keys.append((v, deck, s, m, f))
        for (v, deck, s, m, f), (score, doc, _) in zip(keys, kernel.batch_anneal(jobs, threads=args.threads)):
            tgt = found[(s, m, f)]
            if (v, deck) not in tgt or score > tgt[(v, deck)][0]:
                tgt[(v, deck)] = (score, doc, kernel.export_for(ctx_for(s, m, v, deck)).digest)
        print(f"search done in {time.time() - t0:.1f}s; reporting", flush=True)
    tasks = [(s, m, f, args.iters, args.restarts, args.engine, found[(s, m, f)]) for s, m, f in combos]
    builds = []
    fallbacks = {}
    with mp.Pool(args.procs) as pool:
        for i, (res, fb) in enumerate(pool.imap_unordered(task, tasks, chunksize=4)):
            builds.extend(res)
            for k, v in fb.items():
                if k in fallbacks:
                    fallbacks[k]["count"] += v["count"]
                else:
                    fallbacks[k] = dict(v)
            if (i + 1) % 50 == 0:
                print(f"{i + 1}/{len(tasks)} reported, {time.time() - t0:.0f}s", flush=True)
    if len(builds) != len(tasks):
        raise RuntimeError(f"reported {len(builds)} builds for {len(tasks)} tasks; a worker result was lost")
    for k, v in mlog.summary().items():
        if k.startswith("kernel") or k not in fallbacks:
            fallbacks.setdefault(k, {"count": 0, "message": v["message"]})["count"] += v["count"]
    catalog.load()
    meta = {
        "release": "0.34.1", "generated": time.strftime("%Y-%m-%d %H:%M"), "iters": args.iters, "restarts": args.restarts,
        "engine": args.engine, "schedule": SCHEDULES[args.schedule] if args.engine == "rust" else SCHEDULES["geometric"],
        "stages": {k: {kk: vv for kk, vv in v.items()} for k, v in stages.STAGES.items()},
        "knobs": stages.KNOBS, "hyper": stages.HYPER, "bugs": BUG_INFO,
        "boss_table": boss_table(),
        "fallbacks": fallbacks, "runtime_s": time.time() - t0,
        "invalid": [{"stage": s, "mode": m, "family": f,
                     "reason": "A battlestaff drops the offhand's stats once OFFHAND-STAFF is fixed, including the time "
                               "plushie's time-acceleration immunity that hyper vaults require."} for s, m, f in skipped],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "builds": builds}, f, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(f"wrote {OUT}: {len(builds)} builds in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
