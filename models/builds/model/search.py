"""Simulated annealing over legal builds for one (family, stage, mode)."""
import math
import random

from . import catalog, stages
from .build import Build, Item
from .context import NICHE_TALENTS
from .evaluate import evaluate
from . import families
from .log import fallback

ARMOR = catalog.ARMOR


def item_capacity(ctx, it):
    pre, suf = catalog.affix_counts(it.type, ctx.rarity if it.type != "vault_necklace" else "OMEGA")
    cap = pre + suf
    if it.seal:
        cap -= 1
    if it.unusual:
        cap -= 1
    return pre, suf, cap


def used_groups(it, exclude=None):
    return {a.group for a in it.prefixes + it.suffixes if a is not exclude}


def learned_skills(ctx, b):
    return {ctx.abilities[sid]["skill"] for sid, n in b.abilities.items() if n > 0 and sid in ctx.abilities}


def affix_ok(ctx, b, a):
    """False for level affixes aimed at an ability or talent the build does not use."""
    if not isinstance(a.raw, dict):
        return True
    if a.attribute == "the_vault:added_ability_level":
        key = a.raw.get("abilityKey", "")
        skills = learned_skills(ctx, b) if b is not None else set()
        if key == "all_abilities" or key in skills:
            return True
        members = ctx.ability_groups.get(key.upper())
        return bool(members) and any(sk in members for sk in skills)
    if a.attribute == "the_vault:added_talent_level":
        key = a.raw.get("talentKey", "")
        return key == "all_talents" or (b is not None and b.talents.get(key, 0) > 0)
    return True


def pick_affix(ctx, b, opts, rng):
    good = [a for a in opts if affix_ok(ctx, b, a)]
    return rng.choice(good if good and rng.random() < 0.9 else opts)


def fill_item(ctx, it, rng, b=None):
    pools = ctx.pools[it.type]
    groups = catalog.implicit_groups(pools)
    for g, opts in groups.items():
        if g not in it.implicits:
            it.implicits[g] = rng.choice(opts)
    pre_n, suf_n, cap = item_capacity(ctx, it)
    while len(it.prefixes) + len(it.suffixes) > cap:
        if it.suffixes and (len(it.suffixes) >= len(it.prefixes)):
            it.suffixes.pop()
        elif it.prefixes:
            it.prefixes.pop()
    for side, n in (("PREFIX", pre_n), ("SUFFIX", suf_n)):
        lst = it.prefixes if side == "PREFIX" else it.suffixes
        while len(lst) < n and len(it.prefixes) + len(it.suffixes) < cap:
            opts = [a for a in pools[side] if a.group not in used_groups(it)]
            if not opts:
                break
            lst.append(pick_affix(ctx, b, opts, rng))


def new_item(ctx, slot, gtype, rng, b=None):
    it = Item(slot, gtype)
    fill_item(ctx, it, rng, b)
    return it


STACKING_ETCHINGS = {"etching_nova_low_mana_damage", "etching_lightning_orb_size"}


def etching_choices(ctx, b, slot):
    """Etchings legal on this item. Each item holds one; a second copy of an etching does nothing in game
    (first-found read), except the two summed/compounded ones, so duplicates are only offered for those."""
    it = b.items[slot]
    taken = {o.etching["id"] for s2, o in b.items.items() if s2 != slot and o.etching}
    return [e for e in ctx.etchings.get(it.type, []) if e["id"] not in taken or e["attribute"].split(":")[-1] in STACKING_ETCHINGS]


def trinket_feasible(ctx, lst):
    slots = ctx.stage["trinket_slots"]
    total = sum(slots.values())
    counts = {}
    for t in lst:
        c = ctx.trinkets_by_id[t]["slot"]
        counts[c] = counts.get(c, 0) + 1
    if len(set(lst)) != len(lst):
        return False
    if ctx.stage.get("trinket_fusion"):
        return len(lst) <= 2 * total and max(0, len(lst) - total) <= sum(min(n, slots.get(c, 0)) for c, n in counts.items())
    return all(n <= slots.get(c, 0) for c, n in counts.items())


def trinket_layout(ctx, lst):
    """One legal way to put the trinkets in the pouch slots (trinket_feasible must hold). With fusion each slot holds one
    trinket or a fused pair, and a pair must include the slot's colour; a combat trinket alone in a slot of another colour
    is fused with a non-combat trinket of that colour (filler)."""
    slots = dict(ctx.stage["trinket_slots"])
    color = {t: ctx.trinkets_by_id[t]["slot"] for t in lst}
    out = []
    if not ctx.stage.get("trinket_fusion"):
        return [{"slot": color[t], "trinkets": [t], "filler": False} for t in lst]
    slot_list = [c for c, n in sorted(slots.items()) for _ in range(n)]
    held = [[] for _ in slot_list]

    def legal(i):
        h = held[i]
        return len(h) < 2 or any(color[t] == slot_list[i] for t in h)

    def place(k):
        if k == len(lst):
            return True
        t = lst[k]
        order = sorted(range(len(slot_list)), key=lambda i: (len(held[i]), slot_list[i] != color[t]))
        for i in order:
            if len(held[i]) < 2:
                held[i].append(t)
                if legal(i) and place(k + 1):
                    return True
                held[i].pop()
        return False

    if not place(0):
        raise RuntimeError(f"no legal trinket layout for {lst} in {slots}")
    for i, h in enumerate(held):
        if h:
            out.append({"slot": slot_list[i], "trinkets": list(h), "filler": len(h) == 1 and color[h[0]] != slot_list[i]})
    return out


def count_seals(b):
    return sum(1 for it in b.items.values() if it.seal)


def count_uniques(b):
    return sum(1 for it in b.items.values() if it.unique is not None)


def count_unique_seals(b):
    return sum(1 for it in b.items.values() if it.unique is not None and it.seal)


UNIQUE_SLOTS = ARMOR + ["mainhand", "offhand"]


def equip_unique(ctx, b, slot, u, rng):
    """Swap the whole piece for unique u. The normal piece's seal, etching and unusual are parked on the item and come
    back when the unique is taken off. Uniques can be sealed and etched (WV allows etching uniques)."""
    it = b.items[slot]
    if it.type != u["type"]:
        it = new_item(ctx, slot, u["type"], rng, b)
        b.items[slot] = it
    if it.unique is None:
        it.shadow = (it.seal, it.etching, it.unusual)
    it.unique, it.uaffixes, it.udrop = u["id"], u["affixes"], None
    it.seal = it.etching = it.unusual = None


def unequip_unique(ctx, b, slot, rng):
    """Back to the parked normal piece; a parked seal, etching or unusual that is no longer legal is dropped."""
    it = b.items[slot]
    seal, etching, unusual = it.shadow
    it.unique = it.uaffixes = it.udrop = it.shadow = None
    if seal is not None and count_seals(b) < ctx.stage["seals"]:
        it.seal = seal
    if unusual is not None and count_unusual(b) < ctx.stage["unusual"]:
        it.unusual = unusual
    if etching is not None and any(e["id"] == etching["id"] for e in etching_choices(ctx, b, slot)):
        it.etching = etching
    fill_item(ctx, it, rng, b)


def unique_seal_legal(ctx, b):
    return count_unique_seals(b) <= ctx.stage["unique_seals"] and count_seals(b) <= ctx.stage["seals"]


def count_unusual(b):
    return sum(1 for it in b.items.values() if it.unusual)


def talent_points(ctx, b):
    return sum(ctx.talent_cost(t, n) for t, n in b.talents.items())


def talent_legal(ctx, b):
    """Skill gates: points spent on other talents, required talents, either-of groups, lock-outs."""
    owned = {t for t, n in b.talents.items() if n > 0}
    spent = talent_points(ctx, b)
    for t in sorted(owned):
        g = ctx.gates.get(t)
        if not g:
            continue
        if g["spent"] and spent - ctx.talent_cost(t, b.talents[t]) < g["spent"]:
            return False
        if any(x not in owned for x in g["deps"]):
            return False
        if any(not (owned & set(grp)) for grp in g["either"]):
            return False
        if any(x in owned for x in g["locked"]):
            return False
    return True


def points_ok(ctx, b):
    return b.skill_points_spent(ctx) <= b.skill_points_total(ctx)


def initial_build(ctx, family, rng):
    b = Build(ctx.stage_name, ctx.stage, ctx.mode, family.id)
    if family.main_spec:
        b.abilities[family.main_spec] = ctx.ability_max(family.main_spec)
    apply_baseline(ctx, b, family)
    for s in ARMOR:
        b.items[s] = new_item(ctx, s, s, rng, b)
    b.items["mainhand"] = new_item(ctx, "mainhand", rng.choice(ctx.mainhands(family)), rng, b)
    b.items["offhand"] = new_item(ctx, "offhand", rng.choice(ctx.offhand_types), rng, b)
    b.items["necklace"] = new_item(ctx, "necklace", "vault_necklace", rng, b)
    b.prestige = ctx.prestige
    slots = dict(ctx.stage["trinket_slots"])
    for color, n in slots.items():
        opts = [t["id"] for t in ctx.trinkets if t["slot"] == color]
        rng.shuffle(opts)
        b.trinkets.extend(opts[:n])
    god = rng.choice(list(ctx.charms))
    b.charm = {"god": god, "mods": pick_charm_mods(ctx, god, rng)}
    b.deck = [rng.choice(list(opts)) for opts in ctx.deck_options]
    while len(b.greed) < ctx.greed_budget:
        fr = ctx.greed_frontier(b.greed)
        if not fr:
            break
        b.greed.add(rng.choice(fr))
    return b


def baseline_specs(ctx, family):
    """Skills every build carries with points (movement and utility): {spec: minimum learned tier}."""
    out = {}
    main_skill = ctx.abilities[family.main_spec]["skill"] if family.main_spec else None
    for skill, (spec, n) in stages.BASELINE_ABILITIES.items():
        if skill == main_skill:
            continue
        if spec in ctx.abilities:
            out[spec] = min(n, ctx.ability_max(spec))
    return out


def apply_baseline(ctx, b, family):
    for spec, n in baseline_specs(ctx, family).items():
        b.abilities[spec] = max(b.abilities.get(spec, 0), n)


def pick_charm_mods(ctx, god, rng):
    mods = list(ctx.charms[god])
    rng.shuffle(mods)
    out, groups = [], set()
    for m in mods:
        if m["group"] in groups:
            continue
        out.append(m)
        groups.add(m["group"])
        if len(out) >= ctx.stage["charm_prefixes"]:
            break
    return out


def mutate(ctx, b, family, rng):
    """Return a mutated copy, or None if the move was illegal."""
    n = b.clone()
    move = rng.choices(
        ["affix", "implicit", "weapon", "offhand", "seal", "unusual", "etching", "trinket", "charm", "deck", "deck",
         "talent", "talent", "ability", "greed", "greed", "unique", "weave"],
        k=1)[0]
    if move == "weave":
        if family.kind != "ability":
            return None
        n.weave = not n.weave
        return n
    if move in ("affix", "implicit", "unusual", "etching"):
        slot = rng.choice([s for s in n.items if move != "etching" or s != "necklace"])
        if n.items[slot].unique is not None and move != "etching":
            return None
    if move == "unique":
        slot = rng.choice(UNIQUE_SLOTS)
        it = n.items[slot]
        opts = [u for u in ctx.unique_choices(slot, family) if u["id"] != it.unique]
        if it.unique is not None and (not opts or rng.random() < 0.5):
            unequip_unique(ctx, n, slot, rng)
        else:
            if not opts or (it.unique is None and count_uniques(n) >= ctx.stage["unique_max"]):
                return None
            equip_unique(ctx, n, slot, rng.choice(opts), rng)
        if not unique_seal_legal(ctx, n):
            return None
        return n
    if move == "affix":
        it = n.items[slot]
        pools = ctx.pools[it.type]
        sides = [("PREFIX", it.prefixes), ("SUFFIX", it.suffixes)]
        side, lst = rng.choice(sides)
        if not lst:
            fill_item(ctx, it, rng, n)
            return n
        i = rng.randrange(len(lst))
        opts = [a for a in pools[side] if a.group not in used_groups(it, exclude=lst[i])]
        if not opts:
            return None
        lst[i] = pick_affix(ctx, n, opts, rng)
    elif move == "implicit":
        it = n.items[slot]
        groups = catalog.implicit_groups(ctx.pools[it.type])
        if not groups:
            return None
        g = rng.choice(list(groups))
        it.implicits[g] = rng.choice(groups[g])
    elif move == "weapon":
        n.items["mainhand"] = new_item(ctx, "mainhand", rng.choice(ctx.mainhands(family)), rng, n)
    elif move == "offhand":
        n.items["offhand"] = new_item(ctx, "offhand", rng.choice(ctx.offhand_types), rng, n)
    elif move == "seal":
        slot = rng.choice(list(n.items))
        it = n.items[slot]
        if it.unique is not None:
            u = ctx.unique_by_id[it.unique]
            if it.seal is not None and rng.random() < 0.4:
                it.seal, it.udrop = None, None
            else:
                if not ctx.unique_seals:
                    return None
                it.seal = rng.choice(ctx.unique_seals)
                it.udrop = rng.choice(u["explicit"]) if u["explicit"] else None
            if not unique_seal_legal(ctx, n):
                return None
            return n
        if it.seal is not None and rng.random() < 0.4:
            it.seal = None
            fill_item(ctx, it, rng, n)
        else:
            pool = ctx.pools[it.type]["CORRUPTED_IMPLICIT"]
            if not pool:
                return None
            if it.seal is None and count_seals(n) >= ctx.stage["seals"]:
                return None
            it.seal = rng.choice(pool)
            fill_item(ctx, it, rng, n)
    elif move == "unusual":
        it = n.items[slot]
        pool = ctx.pools[it.type]["UNUSUAL_PREFIX"] + ctx.pools[it.type]["UNUSUAL_SUFFIX"]
        if it.unusual is not None and rng.random() < 0.4:
            it.unusual = None
            fill_item(ctx, it, rng, n)
        else:
            if not pool:
                return None
            if it.unusual is None and count_unusual(n) >= ctx.stage["unusual"]:
                return None
            it.unusual = rng.choice(pool)
            fill_item(ctx, it, rng, n)
    elif move == "etching":
        it = n.items[slot]
        opts = etching_choices(ctx, n, slot)
        if not opts or rng.random() < 0.2:
            it.etching = None
        else:
            it.etching = rng.choice(opts)
    elif move == "trinket":
        opts = [t["id"] for t in ctx.trinkets if t["id"] not in n.trinkets]
        r = rng.random()
        if r < 0.3 and opts:
            n.trinkets.append(rng.choice(opts))
        elif r < 0.45 and n.trinkets:
            n.trinkets.pop(rng.randrange(len(n.trinkets)))
        elif n.trinkets and opts:
            n.trinkets[rng.randrange(len(n.trinkets))] = rng.choice(opts)
        else:
            return None
        if not trinket_feasible(ctx, n.trinkets):
            return None
    elif move == "charm":
        god = rng.choice(list(ctx.charms)) if rng.random() < 0.3 else n.charm["god"]
        n.charm = {"god": god, "mods": pick_charm_mods(ctx, god, rng)}
    elif move == "deck":
        if not n.deck:
            return None
        i = rng.randrange(len(n.deck))
        if rng.random() < 0.3 and len(n.deck) > 1:
            j = rng.randrange(len(n.deck))
            a, c = n.deck[i], n.deck[j]
            if a == c or c not in ctx.deck_options[i] or a not in ctx.deck_options[j]:
                return None
            n.deck[i], n.deck[j] = c, a
        else:
            n.deck[i] = rng.choice(list(ctx.deck_options[i]))
    elif move == "talent":
        tid = rng.choice(list(ctx.talents))
        cur = n.talents.get(tid, 0)
        mx = ctx.talent_max(tid)
        new = max(0, min(mx, cur + rng.choice([-1, 1, 1, mx])))
        if new == cur:
            return None
        n.talents[tid] = new
        if not talent_legal(ctx, n):
            return None
        if not points_ok(ctx, n):
            for _ in range(6):
                cands = [t for t, v in n.talents.items() if v > 0 and t != tid]
                base = baseline_specs(ctx, family)
                cands += [s for s, v in n.abilities.items() if v > base.get(s, 0) and s != family.main_spec]
                if not cands:
                    break
                victim = rng.choice(cands)
                if victim in n.talents:
                    n.talents[victim] -= 1
                else:
                    n.abilities[victim] -= 1
                if points_ok(ctx, n):
                    break
            if not points_ok(ctx, n) or not talent_legal(ctx, n):
                return None
    elif move == "ability":
        choices = list(families.BUFF_SPECS)
        if family.main_spec:
            choices.append(family.main_spec)
        sid = rng.choice(choices)
        if sid not in ctx.abilities:
            return None
        if ctx.abilities[sid]["skill"] in stages.BASELINE_ABILITIES:
            return None
        skill = ctx.abilities[sid]["skill"]
        for other in list(n.abilities):
            if other != sid and ctx.abilities[other]["skill"] == skill:
                if other == family.main_spec:
                    return None
                n.abilities[other] = 0
        cur = n.abilities.get(sid, 0)
        mx = ctx.ability_max(sid)
        new = max(0, min(mx, cur + rng.choice([-1, 1, mx, -mx])))
        if sid == family.main_spec:
            new = max(1, new)
        n.abilities[sid] = new
        if not points_ok(ctx, n):
            return None
    elif move == "greed":
        leaves = ctx.greed_leaves(n.greed)
        if leaves and (len(n.greed) >= ctx.greed_budget or rng.random() < 0.5):
            n.greed.discard(rng.choice(leaves))
        fr = ctx.greed_frontier(n.greed)
        while len(n.greed) < ctx.greed_budget and fr:
            n.greed.add(rng.choice(fr))
            fr = ctx.greed_frontier(n.greed)
        if not points_ok(ctx, n):
            return None
    return n


def _score(ctx, b):
    try:
        return evaluate(b, ctx)["score"]
    except Exception as e:
        fallback("eval-error", f"{b.family}: {type(e).__name__}: {e}")
        return -1e9


def polish(ctx, b, family, rounds=2):
    """Coordinate descent: each affix, implicit, etching, deck card, trinket and charm slot set to its best option."""
    best_s = _score(ctx, b)
    for _ in range(rounds):
        improved = False

        def consider(cand):
            nonlocal b, best_s, improved
            s2 = _score(ctx, cand)
            if s2 > best_s + 1e-9:
                b, best_s, improved = cand, s2, True

        for slot in UNIQUE_SLOTS:
            cur_u = b.items[slot].unique
            for u in [None] + ctx.unique_choices(slot, family):
                if (u and u["id"]) == cur_u:
                    continue
                c = b.clone()
                if u is None:
                    unequip_unique(ctx, c, slot, random.Random(0))
                elif cur_u is None and count_uniques(c) >= ctx.stage["unique_max"]:
                    continue
                else:
                    equip_unique(ctx, c, slot, u, random.Random(0))
                if unique_seal_legal(ctx, c):
                    consider(c)
        for slot in list(b.items):
            if b.items[slot].unique is not None:
                u = ctx.unique_by_id[b.items[slot].unique]
                for s in [None] + ctx.unique_seals:
                    for drop in ([None] if s is None or not u["explicit"] else u["explicit"]):
                        if s is b.items[slot].seal and drop == b.items[slot].udrop:
                            continue
                        c = b.clone()
                        c.items[slot].seal, c.items[slot].udrop = s, drop
                        if unique_seal_legal(ctx, c):
                            consider(c)
                for e in [None] + etching_choices(ctx, b, slot):
                    if (b.items[slot].etching or {}).get("id") == (e or {}).get("id"):
                        continue
                    c = b.clone()
                    c.items[slot].etching = e
                    consider(c)
                continue
            pools = ctx.pools[b.items[slot].type]
            for side, key in (("prefixes", "PREFIX"), ("suffixes", "SUFFIX")):
                for i in range(len(getattr(b.items[slot], side))):
                    for a in pools[key]:
                        cur = getattr(b.items[slot], side)[i]
                        if a is cur or a.group in used_groups(b.items[slot], exclude=cur):
                            continue
                        c = b.clone()
                        getattr(c.items[slot], side)[i] = a
                        consider(c)
            for g, opts in catalog.implicit_groups(pools).items():
                for a in opts:
                    if b.items[slot].implicits.get(g) is a:
                        continue
                    c = b.clone()
                    c.items[slot].implicits[g] = a
                    consider(c)
            if slot != "necklace":
                for e in [None] + etching_choices(ctx, b, slot):
                    if (b.items[slot].etching or {}).get("id") == (e or {}).get("id"):
                        continue
                    c = b.clone()
                    c.items[slot].etching = e
                    consider(c)
        for i, opts in enumerate(ctx.deck_options):
            for cid in opts:
                if b.deck[i] == cid:
                    continue
                c = b.clone()
                c.deck[i] = cid
                consider(c)
        for i in range(len(b.trinkets)):
            for t in ctx.trinkets:
                if t["id"] in b.trinkets:
                    continue
                c = b.clone()
                c.trinkets[i] = t["id"]
                if trinket_feasible(ctx, c.trinkets):
                    consider(c)
        god = b.charm["god"]
        for i in range(len(b.charm["mods"])):
            for m in ctx.charms[god]:
                groups = {x["group"] for j, x in enumerate(b.charm["mods"]) if j != i}
                if m in b.charm["mods"] or m["group"] in groups:
                    continue
                c = b.clone()
                c.charm["mods"][i] = m
                consider(c)
        if not improved:
            break
    return b, best_s


def anneal(ctx, family, iters, seed, t0=0.6, t1=0.004):
    rng = random.Random(seed)
    cur = initial_build(ctx, family, rng)
    cur, cur_s = polish(ctx, cur, family, rounds=1)
    best, best_s = cur, cur_s
    for i in range(iters):
        T = t0 * (t1 / t0) ** (i / max(1, iters - 1))
        cand = mutate(ctx, cur, family, rng)
        if cand is None:
            continue
        try:
            s = evaluate(cand, ctx)["score"]
        except Exception as e:
            fallback("eval-error", f"{family.id}: {type(e).__name__}: {e}")
            continue
        if s >= cur_s or rng.random() < math.exp((s - cur_s) / T):
            cur, cur_s = cand, s
            if s > best_s:
                best, best_s = cand, s
    best, best_s = polish(ctx, best, family, rounds=3)
    best, best_s = strip_idle_etchings(ctx, best, best_s)
    return strip_idle_points(ctx, best, best_s, family)


def strip_idle_points(ctx, b, score, family):
    """Refund talent and support-ability points that change nothing (spare points the search parked)."""
    base = baseline_specs(ctx, family)
    for _ in range(2):
        changed = False
        for tid in sorted(t for t, n in b.talents.items() if n > 0):
            c = b.clone()
            c.talents[tid] = 0
            if not talent_legal(ctx, c):
                continue
            s2 = _score(ctx, c)
            if s2 >= score - 1e-9 and _same_output(ctx, b, c):
                b, score, changed = c, max(score, s2), True
        for sid in sorted(a for a, n in b.abilities.items() if n > 0 and a != family.main_spec and a not in base):
            c = b.clone()
            c.abilities[sid] = 0
            s2 = _score(ctx, c)
            if s2 >= score - 1e-9 and _same_output(ctx, b, c):
                b, score, changed = c, max(score, s2), True
        if not changed:
            break
    return b, score


def strip_idle_etchings(ctx, b, score):
    """Remove etchings that change nothing, so the report only shows ones that matter."""
    for slot in list(b.items):
        if not b.items[slot].etching:
            continue
        c = b.clone()
        c.items[slot].etching = None
        s2 = _score(ctx, c)
        if s2 >= score - 1e-9 and _same_output(ctx, b, c):
            b, score = c, max(score, s2)
    return b, score


def _same_output(ctx, a, c):
    ra, rc = evaluate(a, ctx), evaluate(c, ctx)
    return abs(ra["dps_no_hp_scaling"] - rc["dps_no_hp_scaling"]) <= 1e-6 * max(1.0, ra["dps_no_hp_scaling"]) and         abs(ra["pack_dps"] - rc["pack_dps"]) <= 1e-6 * max(1.0, ra["pack_dps"]) and abs(ra["ehp"] - rc["ehp"]) <= 1e-6 * max(1.0, ra["ehp"])
