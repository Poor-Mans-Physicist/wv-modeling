"""Step-by-step play of a scored build (the report's "How to play it" view). Runs on final builds only, after
evaluate; the kernel never sees it and nothing here changes a score.

From the family result it lays out one loop of the rotation on the 20-tick clock:

- Opener: toggles switched on, then the instant buffs, longest period first; each buff is recast every 1 / rate s
  (buffs_for's own cast rate, so its cooldown or duration rule is the handler's).
- Loop: with sneak-cast Power Dump mixed with standing casts, the loop is one dump period: sneak (half the window),
  the dump cast, stand up (the other half), then standing casts until the next dump. Always sneaking: one sneak at the
  start, every cast dumps the bar. Without Power Dump the loop is the longest cast interval (at least MIN_VIEW_S).
- Casts: each damage ability is due every interval s: its cast interval before the sneak windows (the rotation row's
  interval; its casts per second already carry the windows' delays, which the scheduler's own window blocking gives
  back), or 1 / casts per second without windows. A Power Dump ability's dumps are cast in their windows on top. Held channels are listed apart. One cast per tick, the earliest due first, ties in rotation
  order; a cast that comes due inside a sneak window waits for it to end and is not made up later (the duty the model
  charged). The view shows the first VIEW_S seconds of the loop.
- Bursts: a source that casts more than once per cooldown (Frozen Barrage recasts) is shown as one burst of
  round(cooldown / interval) casts per cooldown.
- Check: casts the scheduler fits into max(loop, CHECK_MIN_S) seconds against casts per second x that time; a gap above
  CHECK_TOL (and above one burst plus one cast) logs schedule-mismatch and flags the build.
"""
from . import abilities as ab
from .log import fallback

TICK = 0.05
MIN_VIEW_S = 2.0
VIEW_S = 12.0
CHECK_TOL = 0.1
CHECK_MIN_S = 60.0


def _label(spec):
    rec = ab.SPEC_TABLE.get(spec)
    return rec["label"] if rec else spec.replace("_", " ")


def build_schedule(fr, d, ctx, build):
    rows = [r for r in fr.get("rotation", []) if r.get("kind") in ("main", "secondary") and r.get("rate", 0.0) > 0]
    holds = [{"spec": r["spec"], "label": _label(r["spec"]), "share": r.get("share", 1.0), "pd": bool(r.get("pd"))}
             for r in rows if r.get("hold")]
    rows = [r for r in rows if not r.get("hold") and (r.get("casts") or 0.0) > 0]
    mode = fr.get("pd_mode")
    duty = fr.get("pd_duty", 1.0) or 1.0
    w = ctx.knobs.get("pd_sneak_window_s", 0.0)
    period = fr.get("pd_period") if mode == "mixed" else None
    flags = []
    lanes = []
    for i, r in enumerate(rows):
        every = r["interval"] if mode == "mixed" and r.get("interval") else 1.0 / r["casts"]
        cd = r.get("cd") or 0.0
        burst = 1
        if cd > 0 and every < cd * (1.0 - 1e-6) and not r.get("leftover"):
            burst = max(1, int(round(cd / every)))
            every *= burst
        lanes.append({"spec": r["spec"], "label": _label(r["spec"]), "kind": r["kind"], "pd": bool(r.get("pd")),
                      "dump": bool(r.get("pd_dumps")) if mode == "mixed" else bool(r.get("pd")) and mode == "always",
                      "every": every, "burst": burst, "rate": r["casts"], "cd": cd, "cost": r.get("cost") or 0.0,
                      "dump_mult": r.get("pd_dump_mult", 1.0), "leftover": bool(r.get("leftover")), "order": i, "times": []})
    loop = period if period else max([MIN_VIEW_S] + [x["every"] for x in lanes if x["every"] < float("inf")])
    view = min(loop, VIEW_S)
    horizon = max(loop, CHECK_MIN_S)
    sneak = []
    steps = []
    dumper = None
    if mode == "mixed" and period:
        dumper = max((x for x in lanes if x["dump"]), key=lambda x: x["dump_mult"], default=None)
        sneak = [[k * period, k * period + w] for k in range(int(view / period) + 1) if k * period < view]
        steps.append({"t": 0.0, "what": "sneak", "dt": w / 2})
        if dumper is not None:
            steps.append({"t": w / 2, "what": "dump", "spec": dumper["spec"], "label": dumper["label"], "mult": dumper["dump_mult"]})
        steps.append({"t": w, "what": "stand", "dt": w / 2})
    elif mode == "always":
        sneak = [[0.0, view]]
        steps.append({"t": 0.0, "what": "sneak_all"})
    start = w if mode == "mixed" and period else 0.0
    for x in lanes:
        x["next"] = start
    counts = {x["spec"]: 0 for x in lanes}
    first = set()
    nxt_dump = 0.0
    for k in range(int(round(horizon / TICK))):
        t = k * TICK
        if mode == "mixed" and period:
            ph = t - period * int((t + 1e-9) / period)
            if ph < w - 1e-9:
                if dumper is not None and t + 1e-9 >= nxt_dump + w / 2:
                    counts[dumper["spec"]] += dumper["burst"]
                    if t < view:
                        dumper["times"].append(t)
                    nxt_dump += period
                    dumper["next"] = max(dumper["next"], t + dumper["every"])
                continue
        due = [x for x in lanes if x["next"] <= t + 1e-9]
        if not due:
            continue
        x = min(due, key=lambda y: (y["next"], y["order"]))
        counts[x["spec"]] += x["burst"]
        if t < view:
            x["times"].append(t)
        if x["spec"] not in first:
            first.add(x["spec"])
            steps.append({"t": t, "what": "cast", "spec": x["spec"], "label": x["label"], "every": x["every"], "burst": x["burst"],
                          "dump": mode == "always" and x["pd"], "mult": x["dump_mult"] if mode == "always" and x["pd"] else 1.0})
        x["next"] = x["next"] + x["every"] if t < x["next"] + TICK + 1e-9 else t + x["every"]
    for x in lanes:
        want = x["rate"] * horizon
        got = counts[x["spec"]]
        x["achieved"] = got / horizon
        if abs(got - want) > max(1.0 + x["burst"], CHECK_TOL * want):
            flags.append(f"{x['label']}: {got} casts fit in {horizon:.0f} s where the model counts {want:.1f}")
        del x["next"]
    for f in flags:
        fallback("schedule-mismatch", f"{build.family} {build.stage_name}: {f}")
    buffs = sorted(fr.get("buff_casts", []), key=lambda b: (not b["toggle"], -(1.0 / b["rate"]) if b.get("rate") else 0.0))
    opener = []
    for b in buffs:
        if not b["toggle"] and not b.get("rate"):
            continue
        opener.append({"spec": b["spec"], "label": _label(b["spec"]), "tier": b["tier"], "toggle": b["toggle"],
                       "every": None if b["toggle"] else 1.0 / b["rate"], "dur": b.get("dur"),
                       "times": [] if b["toggle"] else [i / b["rate"] for i in range(int(view * b["rate"]) + 1) if i / b["rate"] < view]})
    weave = fr.get("weave") or {}
    swings = weave.get("swings") if weave else (fr.get("rate") if build.family.startswith("melee:") else 0.0)
    return {"mode": mode, "window_s": w, "loop_s": loop, "view_s": view, "duty": duty, "opener": opener, "steps": steps,
            "lanes": lanes, "holds": holds, "sneak": sneak, "swings": swings or 0.0, "pd_bar": fr.get("pd_bar", 0.0),
            "mana_max": d.get("mana_max", 0.0), "flags": flags}
