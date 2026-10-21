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

from model import stages, families, catalog, procs
from model.schedule import build_schedule
from model.bugs import BUG_INFO
from model.context import Context, NICHE_TALENTS
from model.search import anneal, trinket_layout
from model.evaluate import evaluate
from model import log as mlog
from model import kernel
from model import gods as godmod
from model import godfx

SCHEDULES = {
    "target": {"kind": "target", "a0": 0.25, "a1": 0.02, "t_init": 0.15, "eta": 0.05},
    "geometric": {"kind": "geometric", "t0": 0.6, "t1": 0.004},
}

OUT = os.path.join(ROOT, "out", "results_rework.json" if stages.LEDGER == "rework" else "results.json")
# Kernel build docs of every search winner, for tools/unique_bench.py (re-anneals from these builds).
FOUND_OUT = os.path.join(ROOT, "out", "found_rework.json" if stages.LEDGER == "rework" else "found.json")


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
    if isinstance(a.raw, dict) and any(k in a.raw for k in ("abilityKey", "talentKey", "effectKey", "abilityId", "hitsRolled", "flag",
                                                           "specialModificationKey")):
        out["raw"] = {k: v for k, v in a.raw.items() if k in ("abilityKey", "talentKey", "levelChange", "effectKey", "amplifier", "legendary",
                                                              "abilityId", "hitsRolled", "flag", "specialModificationKey")}
    if isinstance(a.raw, dict) and "min" in a.raw:
        out["range"] = [a.raw["min"], a.raw["max"]]
    return out


def item_json(it, ctx):
    if it.unique is not None:
        u = ctx.unique_by_id[it.unique]
        live = [(i, a) for i, a in enumerate(u["affixes"]) if i != it.udrop]
        return {
            "slot": it.slot, "type": it.type, "rarity": "ANCIENT" if u.get("ancient") else "UNIQUE", "legendary": False,
            "unique": {"id": u["id"], "name": u["name"], "powers": u["powers"], "ancient": bool(u.get("ancient")),
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
             "minGreedTier": e["minGreedTier"], "description": etching_text(e)} if e else None)


def etching_text(e):
    """The etching's in-game description with its rolled value filled in (colour tags stripped), by the Etchings
    view's filler (report/build_report.fill_etching), so a build's etching and the catalogue print the same text."""
    from report.build_report import fill_etching, _placeholders
    cat = catalog.load()["etchings"]["etchings"]
    entry = cat.get(e["id"]) or cat.get(e["attribute"])
    if not entry or not entry.get("description"):
        mlog.fallback(f"etching-text-{e['id']}", f"etching {e['id']} has no catalog description; its text is left empty")
        return None
    return fill_etching(entry["description"], _placeholders(entry, e["value"]))


SLOT_FAMILY = {"row": "evo", "col": "evo", "surr": "evo", "diag": "evo", "deluxe": "deluxe", "typeless": "typeless"}


TYPE_CHAR = {0: "R", 1: "C", 2: "S", 3: "X", 4: "D", 5: "T", 6: "^", 7: "v", 8: "<", 9: ">", 10: "a", 12: "_"}
COLOR_NAME = ["red", "green", "blue", "yellow"]
CORE_LABEL = {"pure": "Pure", "equilibrium": "Equilibrium", "steadfast": "Steadfast", "shiny": "Shiny", "color": "Colour",
              "premium": "Premium", "void": "Void", "archive": "Archive", "empyreal": "Empyreal", "arsenal": "Arsenal",
              "aegis": "Aegis", "tool": "Tool", "natural": "Natural", "fae": "Fae", "jupiter": "Jupiter", "pluto": "Pluto",
              "nitwit": "Nitwit", "bazaar": "Bazaar", "talent": "Talent", "temporal": "Temporal", "greed": "Greed"}
MULT_CORES = {"void", "empyreal", "arsenal", "aegis", "tool", "pluto"}


def design_json(ctx, i):
    """One menu design for the report: grid, cores per core set, modifiers, DeckFAST's own scores."""
    d = ctx.designs[i]
    m = d["menu"]
    rows = [p[0] for p in d["geo"].slots]
    cols = [p[1] for p in d["geo"].slots]
    r0, c0 = min(rows), min(cols)
    seed = [d["opts"][k][o] for k, o in enumerate(d["seed"])]
    return {
        "index": i, "label": m["label"], "deck": m["label"].split(" [")[0], "key": m["key"], "found_for": m["found_for"],
        "mystery": m["mystery"], "implicit_keys": m["implicit_keys"], "core_slots": m["core_slots"],
        "core_sets": [{"name": cs["name"], "cores": [{"core": n, "label": CORE_LABEL.get(n, n), "value": v, "k": k,
                                                      "mult": n in MULT_CORES} for (n, v, k, _c) in cs["cores"]]
                       + ([{"core": "nitwit", "label": "Nitwit", "value": cs["nitwit"], "k": 0, "mult": False}]
                          if cs["nitwit"] and "nitwit" in cs["name"].split("+") else [])} for cs in d["csets"]],
        "implicits": [list(t) for t in m["implicits"]],
        "score": m["score"], "score_by_group": m["score_by_group"],
        "rows": max(rows) - r0 + 1, "cols": max(cols) - c0 + 1,
        "cells": [{"r": p[0] - r0, "c": p[1] - c0, "t": TYPE_CHAR[d["types"][k]], "color": COLOR_NAME[d["colors"][k]],
                   "seed": seed[k]["id"], "seed_kind": seed[k]["kind"]} for k, p in enumerate(d["geo"].slots)],
    }


def design_name(ctx, i):
    """Short design name for the report: deck (with its Mystery pair) and primary cores."""
    d = ctx.designs[i]
    cores = " + ".join(CORE_LABEL.get(c, c) for c in d["menu"]["cores"]) or "no cores"
    return f"{d['menu']['label']} · {cores}"


def menus_meta(stage_list):
    """Per stage with a deck menu: its designs and DeckFAST's settings (the report's Decks view)."""
    out = {}
    for s in stage_list:
        ctx = ctx_for(s, stages.MODES[0])
        if not getattr(ctx, "designs", None):
            continue
        m = ctx.menu
        out[s] = {"greed": m["greed"], "rolls": m["rolls"], "limits": m.get("limits") or {}, "k": m.get("k"),
                  "card_tier": ctx.stage["card_tier"], "groups": {g: len(v) for g, v in m.get("groups", {}).items()},
                  "timing": m.get("timing"), "regret": ({"mean": m["regret"]["mean"], "max": m["regret"]["max"]}
                                                        if m.get("regret") else None),
                  "designs": [design_json(ctx, i) for i in range(len(ctx.designs))]}
    return out


def deck_json_menu(build, ctx):
    """A build's menu deck for the report: its design, core set, every card with its contribution and breakdown."""
    from model import deck_rework
    d = ctx.designs[build.design]
    cs = d["csets"][build.cset]
    cards = deck_rework.design_cards(d, build.dk)
    parts = []
    terms = deck_rework.evaluate(d["geo"], d["types"], d["colors"], cards, cs, d["imps"], d["greed"], d["deluxe_base"],
                                 parts=parts)
    info = design_json(ctx, build.design)
    total = {}
    cells = []
    for k, (cell, card, term) in enumerate(zip(info["cells"], cards, terms)):
        cell = dict(cell)
        cell.update({"card": card["id"], "kind": card["kind"], "attribute": card.get("attr"), "value": card["value"],
                     "term": term, "contrib": term * card["value"] if card.get("attr") else 0.0,
                     "groups": [g for g in card.get("groups", []) if g not in ("Evolution", "Stat", "Deluxe", "Greed")],
                     "grant": list(card["grant"]) if card.get("grant") else None, "parts": parts[k]})
        if card.get("attr") and term:
            total[card["attr"]] = total.get(card["attr"], 0.0) + term * card["value"]
        cells.append(cell)
    scores = getattr(build, "design_scores", None) or {}
    info.update({"menu": True, "name": info["deck"] + (" · " + " + ".join(k.title() for k in info["implicit_keys"])
                                                     + " at 75%" if info["mystery"] else ""),
                 "config": info["label"], "cset": build.cset, "cores": info["core_sets"][build.cset]["cores"],
                 "greed": d["greed"], "card_tier": ctx.stage["card_tier"], "cells": cells, "totals": total,
                 "stat_slots": sum(1 for c in cells if c["attribute"]),
                 "design_scores": [{"index": int(j), "label": design_name(ctx, int(j)), "score": v}
                                   for j, v in sorted(scores.items(), key=lambda kv: -kv[1])],
                 "limits": d["limits"]})
    return info


def deck_json(build, ctx):
    if getattr(ctx, "designs", None) and getattr(build, "dk", None):
        return deck_json_menu(build, ctx)
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
        "charm": charm_json(build, ctx),
        "deck": deck_json(build, ctx),
        "talents": talents, "abilities": abil, "greed": sorted(build.greed),
        "prestige": [{"id": p["id"], "name": p.get("name", p["id"]), "type": p.get("type")} for p in build.prestige
                     if p.get("type") in ("gear_attribute_power", "masterful_power", "berserk_power")],
        "skill_points": {"spent": build.skill_points_spent(ctx), "total": build.skill_points_total(ctx)},
        "requirements": {k: v for k, v in r["requirements"].items() if k != "penalty"},
        "offhand_locked": ctx.time_lock,
        "ability_levels": [[k, v] for k, v in st.ability_levels],
        "healing": r["healing"], "survival": r["survival"], "attack_stream": r.get("attack_stream"),
        "dice": fr.get("dice"), "weave": fr.get("weave"),
        "rotation": [{"spec": x["spec"], "kind": x.get("kind"), "tier": x["tier"], "share": x["share"], "rate": x.get("rate", 0.0),
                      "dps": x["dps"], "pmult": x["pmult"], "mana": x["mana"], "leftover": x["leftover"], "pd": x.get("pd", False),
                      "reach": x.get("reach"), "debuffs": x.get("debuffs", []), "pd_dumps": x.get("pd_dumps", 0.0),
                      "pd_dump_mult": x.get("pd_dump_mult", 1.0), "cd": x.get("cd"), "cost": x.get("cost"),
                      "casts": x.get("casts"), "hold": x.get("hold", False),
                      "interval": x.get("interval")} for x in fr.get("rotation", [])],
        "pd_mode": fr.get("pd_mode"), "pd_period": fr.get("pd_period"), "pd_duty": fr.get("pd_duty", 1.0),
        "sneak_share": fr.get("sneak_share", 0.0),
        "schedule": build_schedule(fr, d, ctx, build),
        "pd_nova": "Starforge_Nova" in getattr(build, "pd", set()),
        "main_share": fr.get("main_share"), "echo_dps": fr.get("echo_dps", 0.0), "echo_main": fr.get("echo_main", 0.0),
        "secondary_share": fr.get("secondary_share"), "secondary_share_unconstrained": fr.get("secondary_share_unconstrained"),
        "secondary_full": fr.get("secondary_full", []), "u_low_mana": fr.get("u_low_mana"),
        "debuffed_uptime": fr.get("debuffed_uptime"), "ranged": d.get("ranged"), "pd_bar": fr.get("pd_bar"),
        "debuffs": [list(x) for x in fr.get("debuffs", [])],
        "boss_chill": procs.boss_chill(fr.get("debuffs", [])),
        "unique_powers": [{"slot": s, "name": ctx.unique_by_id[it.unique]["name"], "powers": ctx.unique_by_id[it.unique]["powers"]}
                          for s, it in build.items.items() if it.unique is not None],
        "talent_levels": [[k, v] for k, v in st.talent_levels],
        "variant": "eot" if getattr(ctx, "variant", "all") == "eot" else "normal",
        "gods": gods_json(build, ctx, st),
    }


TIME_EFFECTS = set(stages.REWORK_TIME_EFFECTS)


def charm_json(build, ctx):
    """Charm mods as worn: rework charms scale with the charm god's piety units (value = base x (units + 1))."""
    mult = 1.0
    if ctx.gods is not None and build.gods is not None and build.charm["god"]:
        mult = ctx.gods.piety_units(build.gods, godmod.GOD_OF_CHARM[build.charm["god"]]) + 1.0
    return {"god": build.charm["god"], "mods": [{"attribute": m["attribute"], "value": m["value"] * mult} for m in build.charm["mods"]]}


def gods_json(build, ctx, st):
    """Rework: the build's god state and which node effects apply (with their carryover scale)."""
    if ctx.gods is None or build.gods is None:
        return None
    G = ctx.gods
    gs = build.gods
    active = godmod.GOD_OF_CHARM.get(build.charm["god"])
    ranks = G.ranks(gs, active)
    per = {}
    for g in godmod.GODS:
        t = G.trees[g]
        lv = gs["levels"][g]
        per[g] = {"level": lv, "xp": G.xp_for_level(lv), "points": G.points(lv), "spent": len(gs["nodes"][g]),
                  "slots": G.slots(lv), "nodes": sorted(t.nodes[i]["id"] for i in gs["nodes"][g]), "mts": list(gs["mts"][g])}
    return {"active": active, "pool": G.pool, "xp_used": G.xp_used(gs["levels"]), "rank_gate": G.rank_gate, "rep": G.rep,
            "piety_units": G.piety_units(gs, active) if active else 0, "gods": per,
            "effects": {e: [n, s] for e, (n, s) in sorted(ranks.items())},
            "not_counted": {e: godfx.NOT_COUNTED[e] for e in st.god_effects if e in godfx.NOT_COUNTED},
            "time_nodes": sorted(e for e in ranks if e in TIME_EFFECTS)}


_ctx_cache = {}


def ctx_for(stage, mode, variant="all", deck=None):
    deck = deck or stages.STAGES[stage]["deck"]["layout"]
    if (stage, mode, variant, deck) not in _ctx_cache:
        _ctx_cache[(stage, mode, variant, deck)] = Context(stage, mode, variant, deck)
    return _ctx_cache[(stage, mode, variant, deck)]


def design_candidates(ctx, fid, n=4):
    """Rework deck menus: the designs a family may use, best first by DeckFAST's score for that build (its group's
    and the generalist's when DeckFAST has no score for it). Empty without menus."""
    designs = getattr(ctx, "designs", None)
    if not designs:
        return []

    def score(i):
        m = designs[i]["menu"]
        return m["score_by_build"].get(fid, m["score_by_group"].get("all", 0.0))

    return sorted(range(len(designs)), key=lambda i: -score(i))[:n]


def deck_choices(stage):
    st = stages.STAGES[stage]
    return st.get("deck_choices") or [st["deck"]["layout"]]


def eot_reachable(ctx):
    """Edge of Time pass: can any time node be bought with the stage's whole god-XP pool in Wendarr? Builds without
    one are discarded, so stages where none is reachable (early) skip the pass."""
    G = ctx.gods
    t = G.trees["wendarr"]
    pts = G.points(G.max_level)
    paths = [t.path_to(set(), i) for i, e in enumerate(t.effect) if e in TIME_EFFECTS]
    return any(pth is not None and len(pth) <= pts for pth in paths)


def seeds_for(stage, mode, fid, restarts):
    return [1000 * k + zlib.crc32(f"{stage}{mode}{fid}".encode()) % 997 for k in range(restarts)]


# Phase 2 of the rework pipeline: a cooler re-anneal from each of the best phase-1 builds (smoke bench 2026-10-12).
P2_SCHEDULE = {"kind": "target", "a0": 0.1, "a1": 0.01, "t_init": 0.03, "eta": 0.05}


def strata_plan(fid, restarts):
    """Rework: kernel stratum per restart (-1 = free). Half the restarts are locked, in blocks of eight: four to
    Velara + Immortal, two to Idona + Ultra Rampaging Fury, two to Idona + Grand Archmage (ability families; melee
    families give those two to Velara + Immortal and Idona + Fury). Immortal is worth up to a cycle and the free
    anneal rarely buys it back once sold, so it gets its own restarts."""
    if stages.LEDGER != "rework":
        return [-1] * restarts
    block = [0, 0, 0, 0, 1, 1] + ([2, 2] if fid.startswith("ability") else [0, 1])
    lock = block * (restarts // 16) + block[:(restarts % 16) // 2]
    return (lock + [-1] * restarts)[:restarts]


def task(args):
    """Python engine: anneal, then report. Rust engine: report only (the kernel batch already searched)."""
    stage, mode, fid, iters, restarts, engine, found = args[:7]
    which = args[7] if len(args) > 7 else "main"
    found = {k: v for k, v in found.items() if (k[0] == "eot") == (which == "eot")}
    fam = families.get(fid)
    cands = []
    if engine == "rust":
        for (variant, deck), (s, doc, digest) in sorted(found.items()):
            c = ctx_for(stage, mode, variant, deck)
            ex = kernel.export_for(c)
            if ex.digest != digest:
                raise RuntimeError(f"{stage} {mode}: worker export differs from the batch export; index tables would not match")
            bb = ex.to_build(doc, fam)
            if c.gods is not None:
                from model.search import rework_legal
                if not rework_legal(c, bb):
                    mlog.fallback("illegal-build", f"{stage} {mode} {fid} {variant}: the kernel returned a build that breaks the "
                                  "god-tree / time-lock / ancient rules; it is still reported")
            rr = evaluate(bb, c)
            if abs(rr["score"] - s) > 1e-7 * max(1.0, abs(rr["score"])):
                if os.environ.get("WV_DUMP_MISMATCH"):
                    with open(os.environ["WV_DUMP_MISMATCH"], "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"stage": stage, "mode": mode, "fid": fid, "variant": variant, "deck": deck,
                                             "kernel": s, "python": rr["score"], "doc": doc}) + "\n")
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
    if not cands:
        return [], mlog.take()
    top = max(cands, key=lambda x: x[0])
    if which == "eot":
        rr = top[5]
        if not any(e in TIME_EFFECTS for e in rr["stats"].god_effects):
            return [], mlog.take()
        r = evaluate(top[3], top[4], record=True)
        sb = serialize(top[3], top[4], r)
        sb["family_label"] += " (Edge of Time)"
        sb["etching_effects"] = etching_attribution(top[3], top[4], r)
        sb["bug_effects"] = []
        return [sb], mlog.take()
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


def secondary_table(builds, top=10):
    """Per stage, the secondaries (and woven swings) by the share they would take at their full cooldown rate
    (secondary_share_unconstrained parts): builds using each, mean and max share, and how many builds flag it."""
    flag = stages.KNOBS["secondary_flag_share"]
    out = {}
    for b in builds:
        if b.get("variant") == "eot" or b.get("kind") != "ability":
            continue
        for x in b.get("secondary_full") or []:
            rec = out.setdefault(b["stage"], {}).setdefault(x["spec"], {"spec": x["spec"], "label": x["label"], "builds": 0,
                                                                         "sum": 0.0, "max": 0.0, "hungry": 0})
            rec["builds"] += 1
            rec["sum"] += x["share"]
            rec["max"] = max(rec["max"], x["share"])
            rec["hungry"] += x["share"] > flag
    table = {}
    for stage in stages.STAGES:
        rows = [dict(r, mean=r["sum"] / r["builds"]) for r in out.get(stage, {}).values()]
        for r in rows:
            r.pop("sum")
        rows.sort(key=lambda r: (-r["mean"], r["spec"]))
        table[stage] = rows[:top]
    return table


def print_secondary_table(table):
    for stage, rows in table.items():
        if not rows:
            continue
        print(f"secondaries at {stage} by uncapped share (builds, mean, max, flagged > "
              f"{stages.KNOBS['secondary_flag_share']:.0%}):")
        for r in rows:
            print(f"  {r['label']:32s} {r['builds']:4d}  {r['mean']:6.1%}  {r['max']:6.1%}  {r['hungry']:4d}")


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
                    "hit_fixed": hyper.at(g, "DF", c), "hit_shipped": hyper.at(g, "DS", c),
                    "frenzy": r["frenzy_mult"], "p_frenzy": r["p_frenzy"], "crit_chance": hyper.boss_crit_chance(c)})
    return out


def print_plan(args, combos, keys, n1):
    """One `[progress] plan {json}` line for tools/progress_server.py: kernel job count and steps per job of each
    search batch, in the order the batches run. Printing it does not change the search."""
    per_group = {}
    for v, deck, s, m, f, _ in keys:
        per_group[(v, deck, s, m, f)] = per_group.get((v, deck, s, m, f), 0) + 1
    phases = [{"name": "phase1", "jobs": n1, "iters": args.iters}]
    if args.p2_top > 0:
        phases.append({"name": "phase2", "jobs": sum(min(args.p2_top, n) for n in per_group.values()),
                       "iters": args.p2_iters})
    if stages.LEDGER == "rework" and args.eot_restarts > 0:
        no_time = {s for s, m, f in combos if not eot_reachable(ctx_for(s, m, "eot"))}
        n = sum(args.eot_restarts * len(deck_choices(s)) for s, m, f in combos if s not in no_time)
        if n:
            phases.append({"name": "eot", "jobs": n,
                           "iters": args.eot_iters or (args.p2_iters if args.p2_top else args.iters // 2)})
    plan = {"tool": "run", "ledger": stages.LEDGER, "threads": args.threads or os.cpu_count(), "phases": phases}
    print("[progress] plan " + json.dumps(plan), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=None, help="phase-1 steps per restart (default 320000; rework 90000)")
    ap.add_argument("--restarts", type=int, default=None, help="phase-1 restarts (default 8; rework 16)")
    ap.add_argument("--p2-top", type=int, default=None, help="phase 2: re-anneal the best N phase-1 builds (default 0; rework 4)")
    ap.add_argument("--p2-iters", type=int, default=1100000, help="phase 2: steps per re-anneal (cooler schedule)")
    ap.add_argument("--opts", default=None, help='kernel search opts as JSON (rework default {"weights": true, "repair": 4})')
    ap.add_argument("--unique-pass", action="store_true", help="also run the comparison search with uniques banned (off by "
                    "default since 2026-10-12: uniques are an integral part of builds)")
    ap.add_argument("--engine", choices=("rust", "python"), default="rust",
                    help="rust: kernel/ (legal-only moves, schedule below); python: model/search.py (geometric schedule)")
    ap.add_argument("--schedule", choices=sorted(SCHEDULES), default="target")
    ap.add_argument("--procs", type=int, default=os.cpu_count(), help="processes for the reporting pass (and the Python engine)")
    ap.add_argument("--threads", type=int, default=0, help="kernel threads (0 = all logical cores)")
    ap.add_argument("--families", default="")
    ap.add_argument("--stages", default="early,mid,end,max")
    ap.add_argument("--eot-restarts", type=int, default=4, help="rework: Edge of Time pass restarts per family (0 = off)")
    ap.add_argument("--eot-iters", type=int, default=0, help="rework: iterations per Edge of Time restart (0 = iters / 2)")
    ap.add_argument("--menus", nargs="?", const="1", default=None,
                    help="rework: deck designs from DeckFAST's menus (data/deck_menus_rework.json, or a path) instead of "
                         "the fixed stage layouts")
    ap.add_argument("--merge", action="store_true", help="re-search only --stages x --families and replace those builds "
                    "(and their search winners) in the existing results; the rest of the file is kept")
    args = ap.parse_args()
    rw = stages.LEDGER == "rework"
    if args.menus:
        if not rw:
            sys.exit("--menus needs WV_LEDGER=rework")
        os.environ["WV_DECK_MENUS"] = args.menus
        stages.DECK_MENUS = args.menus
    if args.iters is None:
        args.iters = 90000 if rw else 320000
    if args.restarts is None:
        args.restarts = 16 if rw else 8
    if args.p2_top is None:
        args.p2_top = 4 if rw else 0
    args.opts = json.loads(args.opts) if args.opts is not None else ({"weights": True, "repair": 4} if rw else None)
    fams = [f.id for f in families.all_families()]
    if args.families:
        fams = [f for f in fams if any(x in f for x in args.families.split(","))]
    if args.engine == "rust" and not os.path.exists(kernel.EXE):
        sys.exit(f"Rust kernel not built: {kernel.EXE}. Run `cargo build --release` in kernel/ or pass --engine python.")
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
        variants = ["all", "no_uniques"] if args.unique_pass else ["all"]
        for v in variants:
            for s, m, f in combos:
                for deck in deck_choices(s):
                    cands = design_candidates(ctx_for(s, m, v, deck), f)
                    for r, (seed, st) in enumerate(zip(seeds_for(s, m, f, args.restarts), strata_plan(f, args.restarts))):
                        dj = {"design": cands[r % len(cands)], "designs": cands} if cands else None
                        jobs.append((ctx_for(s, m, v, deck), families.get(f), args.iters, seed, SCHEDULES[args.schedule], False,
                                     None, False, st, args.opts, None, dj))
                        keys.append((v, deck, s, m, f, st))
        print_plan(args, combos, keys, len(jobs))
        dropped = {}
        phase1 = {}
        for (v, deck, s, m, f, st), (score, doc, info) in zip(keys, kernel.batch_anneal(jobs, threads=args.threads)):
            if info.get("lock_dropped"):
                dropped[s] = dropped.get(s, 0) + 1
            phase1.setdefault((v, deck, s, m, f), []).append((score, doc, st))
        design_scores = {}
        for key, lst in phase1.items():
            ds = {}
            for score, doc, _st in lst:
                d = int(doc.get("design", 0))
                ds[d] = max(ds.get(d, -1e9), score)
            design_scores[key] = ds
        if args.p2_top > 0:
            jobs2, keys2 = [], []
            for (v, deck, s, m, f), lst in phase1.items():
                lst.sort(key=lambda x: -x[0])
                cands = design_candidates(ctx_for(s, m, v, deck), f)
                for i, (score, doc, st) in enumerate(lst[:args.p2_top]):
                    dj = {"design": int(doc.get("design", 0)), "designs": cands, "design_lock": True} if cands else None
                    jobs2.append((ctx_for(s, m, v, deck), families.get(f), args.p2_iters,
                                  zlib.crc32(f"{s}{m}{f}{v}{deck}|p2|{i}".encode()), P2_SCHEDULE, False, doc, False, st, args.opts,
                                  None, dj))
                    keys2.append((v, deck, s, m, f))
            print(f"phase 1 done in {time.time() - t0:.1f}s; phase 2: {len(jobs2)} re-anneals", flush=True)
            for key, (score, doc, info) in zip(keys2, kernel.batch_anneal(jobs2, threads=args.threads)):
                phase1[key].append((score, doc, -9))
                d = int(doc.get("design", 0))
                design_scores[key][d] = max(design_scores[key].get(d, -1e9), score)
        for (v, deck, s, m, f), lst in phase1.items():
            score, doc, _ = max(lst, key=lambda x: x[0])
            if design_scores[(v, deck, s, m, f)] and getattr(ctx_for(s, m, v, deck), "designs", None):
                doc = dict(doc, design_scores={str(k): v_ for k, v_ in design_scores[(v, deck, s, m, f)].items()})
            found[(s, m, f)][(v, deck)] = (score, doc, kernel.export_for(ctx_for(s, m, v, deck)).digest)
        for s_, n in sorted(dropped.items()):
            mlog.fallback(f"stratum-lock-dropped-{s_}", f"{n} locked restarts at {s_} could not reach their major (god XP too "
                          "low); they ran unlocked")
        if stages.LEDGER == "rework" and args.eot_restarts > 0:
            ejobs, ekeys = [], []
            no_time = sorted({s for s, m, f in combos if not eot_reachable(ctx_for(s, m, "eot"))})
            if no_time:
                print(f"Edge of Time pass skips {', '.join(no_time)}: no time node is affordable with that stage's god XP", flush=True)
            for s, m, f in combos:
                if s in no_time:
                    continue
                for deck in deck_choices(s):
                    base = found[(s, m, f)].get(("all", deck))
                    if base is None:
                        continue
                    ca, ce = ctx_for(s, m, "all", deck), ctx_for(s, m, "eot", deck)
                    da, de = dict(kernel.export_for(ca).doc), dict(kernel.export_for(ce).doc)
                    da.pop("godtree"), de.pop("godtree")
                    if da != de:
                        raise RuntimeError(f"{s} {m}: the Edge of Time context exports different tables; builds cannot carry over")
                    cands = design_candidates(ce, f)
                    for seed in seeds_for(s, m, f + "|eot", args.eot_restarts):
                        dj = {"design": int(base[1].get("design", 0)), "designs": cands, "design_lock": True} if cands else None
                        ejobs.append((ce, families.get(f), args.eot_iters or (args.p2_iters if args.p2_top else args.iters // 2),
                                      seed, SCHEDULES[args.schedule], False, base[1], True, -1, args.opts, None, dj))
                        ekeys.append((deck, s, m, f))
            for (deck, s, m, f), (score, doc, _) in zip(ekeys, kernel.batch_anneal(ejobs, threads=args.threads)):
                tgt = found[(s, m, f)]
                if ("eot", deck) not in tgt or score > tgt[("eot", deck)][0]:
                    tgt[("eot", deck)] = (score, doc, kernel.export_for(ctx_for(s, m, "eot", deck)).digest)
        print(f"search done in {time.time() - t0:.1f}s; reporting", flush=True)
        rows = [{"stage": s, "mode": m, "family": f, "variant": v, "deck": deck, "score": sc, "doc": doc}
                for (s, m, f), d in found.items() for (v, deck), (sc, doc, _) in d.items()]
        if args.merge and os.path.exists(FOUND_OUT):
            with open(FOUND_OUT, encoding="utf-8") as fh:
                prev = {(x["stage"], x["mode"], x["family"], x["variant"], x["deck"]): x for x in json.load(fh)}
            for x in rows:
                k = (x["stage"], x["mode"], x["family"], x["variant"], x["deck"])
                if k in prev and prev[k]["score"] > x["score"]:
                    x.update(prev[k])
                prev[k] = x
            rows = list(prev.values())
        with open(FOUND_OUT, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    tasks = [(s, m, f, args.iters, args.restarts, args.engine, found[(s, m, f)]) for s, m, f in combos]
    if stages.LEDGER == "rework" and args.engine == "rust" and args.eot_restarts > 0:
        tasks += [(s, m, f, args.iters, args.restarts, args.engine, found[(s, m, f)], "eot") for s, m, f in combos]
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
    n_main = sum(1 for t in tasks if len(t) == 7)
    if sum(1 for b in builds if b.get("variant") != "eot") != n_main:
        raise RuntimeError(f"reported {len(builds)} builds for {n_main} main tasks; a worker result was lost")
    for k, v in mlog.summary().items():
        if k.startswith("kernel") or k not in fallbacks:
            fallbacks.setdefault(k, {"count": 0, "message": v["message"]})["count"] += v["count"]
    catalog.load()
    meta = {
        "release": catalog.load()["meta"]["release"], "ledger": stages.LEDGER,
        "generated": time.strftime("%Y-%m-%d %H:%M"), "iters": args.iters, "restarts": args.restarts, "p2_top": args.p2_top, "p2_iters": args.p2_iters, "opts": args.opts,
        "engine": args.engine, "schedule": SCHEDULES[args.schedule] if args.engine == "rust" else SCHEDULES["geometric"],
        "stages": {k: {kk: vv for kk, vv in v.items()} for k, v in stages.STAGES.items()},
        "knobs": stages.KNOBS, "hyper": stages.HYPER, "bugs": BUG_INFO,
        "boss_table": boss_table(),
        "fallbacks": fallbacks, "runtime_s": time.time() - t0,
        "deck_menus": menus_meta(args.stages.split(",")) if stages.DECK_MENUS else None,
        "invalid": [{"stage": s, "mode": m, "family": f,
                     "reason": "A battlestaff drops the offhand's stats once OFFHAND-STAFF is fixed, including the time "
                               "plushie's time-acceleration immunity that hyper vaults require."} for s, m, f in skipped],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if args.merge:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        done = {(s, m, f) for s, m, f in combos}
        kept = [b for b in old["builds"] if (b["stage"], b["mode"], b["family"]) not in done]
        prev = {(b["stage"], b["mode"], b["family"], b.get("variant")): b for b in old["builds"]}
        better = 0
        for i, b in enumerate(builds):
            o = prev.get((b["stage"], b["mode"], b["family"], b.get("variant")))
            if o is not None and o["score"] > b["score"]:
                builds[i] = o
            else:
                better += 1
        print(f"merge: kept {len(kept)} builds; {better} of {len(builds)} re-searched builds improved and replace the old ones")
        old["meta"].setdefault("merged", []).append({"generated": meta["generated"], "stages": args.stages,
                                                     "families": args.families, "iters": args.iters,
                                                     "restarts": args.restarts, "p2_iters": args.p2_iters})
        for k, v in fallbacks.items():
            old["meta"]["fallbacks"].setdefault(k, {"count": 0, "message": v.get("message", "")})["count"] += v["count"]
        meta, builds = old["meta"], kept + builds
    meta["secondary_table"] = secondary_table(builds)
    print_secondary_table(meta["secondary_table"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "builds": builds}, f, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(f"wrote {OUT}: {len(builds)} builds in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
