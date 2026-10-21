"""Rework deck rules (the DeckFAST test/core-rework model, tagsim.rs simulate_rework): deck designs from DeckFAST's
menus (data/deck_menus_rework.json) and the card a build puts in each slot.

A design fixes each slot's type (row / col / surr / diag positional, deluxe, typeless, greed direction, arcane or
empty) and colour, the deck's modifiers and its top core sets. The build chooses the card in every slot from that
slot's options. Per scorable card:

    base x (1 + sum additive) x product multiplicative x (1 + sum greed)

base: positional cards count same-colour cards (row / column including itself, surrounding / diagonal peers), times
the Rook / Bishop / Pillager frequency; deluxe cards deluxe_base; typeless 1. Additive: Pure, Equilibrium, Steadfast,
Shiny, Colour, Premium, Archive, Natural, Fae, Jupiter, Nitwit, Bazaar, Talent, Temporal and the additive modifiers.
Multiplicative (self-additive inside, product across): Void and Shadow, Empyreal (best k slots), Arsenal, Aegis, Tool,
Pluto, Runic. Greed: a greed card adds the stage bonus to its target, plus the greed core's roll when 2+ greed cards
target the same card. Resource, Knack and Temporal cards fill a slot and feed the rules that read them but score 0;
Arcane and Knack cards grant ability and talent levels, which the deck multipliers never touch.

Card groups use the game's names. Positional cards are Evolution cards; under the Shiny core they are Shiny + Stat
cards instead, and every stat card gains Foil (Wold's run-level rule). Kept bit-for-bit with the kernel's deck.rs
(tools/parity_cases_deck.py).
"""
import math

T_ROW, T_COL, T_SURR, T_DIAG, T_DELUXE, T_TYPELESS = 0, 1, 2, 3, 4, 5
T_G_UP, T_G_DOWN, T_G_LEFT, T_G_RIGHT, T_ARCANE, T_DEAD = 6, 7, 8, 9, 10, 12
TYPE_CODE = {"row": T_ROW, "col": T_COL, "surr": T_SURR, "diag": T_DIAG, "deluxe": T_DELUXE, "typeless": T_TYPELESS,
             "dir_greed_up": T_G_UP, "dir_greed_down": T_G_DOWN, "dir_greed_left": T_G_LEFT,
             "dir_greed_right": T_G_RIGHT, "arcane": T_ARCANE, "dead": T_DEAD}
COLOR_CODE = {"red": 0, "green": 1, "blue": 2, "yellow": 3}
COLOR_NONE = 255
# Group bits (the kernel's deck.rs uses the same layout).
GROUP_BITS = {"Offensive": 0, "Defensive": 1, "Physical": 2, "Magical": 3, "Utility": 4, "Resource": 5, "Knack": 6,
              "Temporal": 7, "Essence": 8, "Foil": 9, "Stat": 10, "Evolution": 11, "Deluxe": 12, "Arcane": 13,
              "Greed": 14, "Shiny": 15}
N_GROUPS = 16
ALPHA = sorted(GROUP_BITS, key=str)          # dominance tie-break: alphabetically first group wins
NONSTAT = (1 << 5) | (1 << 6) | (1 << 7)     # Resource, Knack, Temporal
B_STAT, B_EVO, B_FOIL, B_SHINY = 1 << 10, 1 << 11, 1 << 9, 1 << 15
CORE_CODE = {"pure": 0, "equilibrium": 1, "steadfast": 2, "shiny": 3, "color": 4, "premium": 5, "void": 6,
             "archive": 7, "empyreal": 8, "arsenal": 9, "aegis": 10, "tool": 11, "natural": 12, "fae": 13,
             "jupiter": 14, "pluto": 15, "bazaar": 16, "talent": 17, "temporal": 18, "greed": 19}
IMP_CODE = {"global": 0, "freq": 1, "adjacency": 2, "color_mismatch": 3, "row_pos": 4, "chain": 5, "empty_slots": 6,
            "unique_groups": 7, "mirror": 8}
GREED_DIR = {T_G_UP: (-1, 0), T_G_DOWN: (1, 0), T_G_LEFT: (0, -1), T_G_RIGHT: (0, 1)}


def group_mask(groups):
    """Bit mask of the game group names this model reads (others are ignored)."""
    m = 0
    for g in groups:
        b = GROUP_BITS.get(g)
        if b is not None:
            m |= 1 << b
    return m


def is_scorable(t):
    return t <= T_TYPELESS


def is_positional(t):
    return t <= T_DIAG


def is_greed(t):
    return T_G_UP <= t <= T_G_RIGHT


class Geometry:
    """Slot peers exactly as DeckFAST builds them (src/config.py Deck) plus the rework's extra tables."""

    def __init__(self, slots):
        self.slots = [tuple(p) for p in slots]
        n = len(self.slots)
        idx = {p: i for i, p in enumerate(self.slots)}
        self.n = n
        self.row = [[j for j, q in enumerate(self.slots) if q[0] == p[0] and q != p] for p in self.slots]
        self.col = [[j for j, q in enumerate(self.slots) if q[1] == p[1] and q != p] for p in self.slots]
        self.surr = [[j for j, q in enumerate(self.slots) if q != p and max(abs(q[0] - p[0]), abs(q[1] - p[1])) <= 1]
                     for p in self.slots]
        self.diag = [[j for j, q in enumerate(self.slots)
                      if q != p and (q[0] - q[1] == p[0] - p[1] or q[0] + q[1] == p[0] + p[1])] for p in self.slots]
        self.orth = [sorted(idx[(p[0] + dr, p[1] + dc)] for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
                            if (p[0] + dr, p[1] + dc) in idx) for p in self.slots]
        self.dir = [{d: idx.get((p[0] + dv[0], p[1] + dv[1])) for d, dv in GREED_DIR.items()} for p in self.slots]
        rmax = max(p[0] for p in self.slots)
        cmin = min(p[1] for p in self.slots)
        cmax = max(p[1] for p in self.slots)
        self.rows_from_bottom = [float(rmax - p[0] + 1) for p in self.slots]
        self.mirror_self = [cmax - (p[1] - cmin) == p[1] for p in self.slots]
        self.mirror_of = [None if self.mirror_self[i] else idx.get((p[0], cmax - (p[1] - cmin)))
                          for i, p in enumerate(self.slots)]
        offs = [(-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (-1, 1), (1, -1), (1, 1)]
        self.starcross = [[idx[(p[0] + dr, p[1] + dc)] for dr, dc in offs if (p[0] + dr, p[1] + dc) in idx]
                          for p in self.slots]


def card_groups(card, slot_t, shiny):
    """Group mask of a placed card: its own groups, with positional cards turned Shiny + Stat (not Evolution) and
    every stat card made Foil under the Shiny core."""
    m = card["g"]
    if shiny:
        if is_positional(slot_t) and card.get("kind") == "evo":
            m = (m & ~B_EVO) | B_SHINY | B_STAT
        if card.get("kind") in ("evo", "typeless", "deluxe"):
            m |= B_FOIL
    return m


def placed_type(card, slot_t):
    """The type a card scores as: stat cards keep the slot's type, everything else is non-scoring."""
    k = card.get("kind")
    if k == "dead":
        return T_DEAD
    if k == "greed":
        return slot_t
    if k == "arcane":
        return T_ARCANE
    if k == "evo":
        return slot_t
    if k == "deluxe":
        return T_DELUXE
    return T_TYPELESS


def dominant(counts, most, exclude):
    best = None
    for name in ALPHA:
        b = GROUP_BITS[name]
        if (1 << b) & exclude:
            continue
        n = counts[b]
        if n == 0:
            continue
        if best is None or (n > best[1] if most else n < best[1]):
            best = (1 << b, n)
    return best[0] if best else 0


def evaluate(geo, slot_types, colors, cards, cset, implicits, greed, deluxe_base, parts=None):
    """Per-slot terms of one deck. cards: per slot a card dict {kind, g, ...}; cset: {cores: [(name, value, k,
    color)], nitwit: value, nitwit_slots: [bool], shiny: bool}; implicits: [(kind, value, groups mask, colors mask,
    ptype code)]. Returns the list of terms (0 for non-scoring slots). `parts`, when a list, receives per slot the
    breakdown the report shows: {base, adds: [(source, value)], mults: [(source, factor)], greed, greed_core,
    empyreal} (None for non-scoring slots)."""
    n = geo.n
    shiny = cset["shiny"]
    t = [placed_type(c, slot_types[i]) for i, c in enumerate(cards)]
    col = [COLOR_NONE if t[i] == T_DEAD else colors[i] for i in range(n)]
    xg = [0 if t[i] == T_DEAD else card_groups(cards[i], slot_types[i], shiny) for i in range(n)]
    gcount = [0] * N_GROUPS
    n_empty = n_nonfoil = n_deluxe = n_arcane = n_util = 0
    seen_col = set()
    row_col, col_col = {}, {}
    for i in range(n):
        if t[i] == T_DEAD:
            n_empty += 1
            continue
        m = xg[i]
        for b in range(N_GROUPS):
            if m >> b & 1:
                gcount[b] += 1
        if not m & B_FOIL:
            n_nonfoil += 1
        if t[i] == T_DELUXE:
            n_deluxe += 1
        if t[i] == T_ARCANE:
            n_arcane += 1
        if m & (1 << 4):
            n_util += 1
        if col[i] != COLOR_NONE:
            seen_col.add(col[i])
            r, c = geo.slots[i]
            row_col[(r, col[i])] = row_col.get((r, col[i]), 0) + 1
            col_col[(c, col[i])] = col_col.get((c, col[i]), 0) + 1
    n_colors = float(len(seen_col))

    add_all = void_rate = greed_core = 0.0
    jupiter = pluto = (0, 0.0)
    empyreal = None
    for (name, value, k, ccol) in cset["cores"]:
        code = CORE_CODE[name]
        if code == 5:
            add_all += value * n_deluxe
        elif code == 7:
            add_all += math.pow(1.0 + value, n_arcane) - 1.0
        elif code == 6:
            void_rate += value
        elif code == 14:
            jupiter = (dominant(gcount, True, B_STAT), value)
        elif code == 15:
            pluto = (dominant(gcount, False, 0), value)
        elif code == 19:
            greed_core += value
        elif code == 8:
            empyreal = (value, k)
    freq = [1.0] * 4
    mirror_tag, have_mirror, chain_value, have_chain, unique_value = 0.0, False, 0.0, False, 0.0
    for (kind, value, gm, cm, ptype) in implicits:
        if kind == 1:
            freq[ptype] *= max(value, 1.0)
        elif kind == 8:
            mirror_tag, have_mirror = value, True
        elif kind == 6:
            void_rate += value
        elif kind == 5:
            chain_value, have_chain = value, True
        elif kind == 7:
            unique_value = value
    chain_id = [0] * n
    chain_size = {}
    if have_chain:
        nid = 0
        for s in range(n):
            if t[s] == T_DEAD or chain_id[s]:
                continue
            nid += 1
            stack, size = [s], 0
            chain_id[s] = nid
            while stack:
                i = stack.pop()
                size += 1
                for j in geo.orth[i]:
                    if t[j] == T_DEAD or chain_id[j]:
                        continue
                    if col[i] != COLOR_NONE and col[i] == col[j]:
                        chain_id[j] = nid
                        stack.append(j)
            chain_size[nid] = size
    unique_groups = float(sum(1 for b in range(N_GROUPS) if gcount[b])) if unique_value > 0 else 0.0
    void_factor = 1.0 + void_rate * n_empty

    nsrc = [0] * n
    targets = [None] * n
    for i in range(n):
        if is_greed(t[i]):
            j = geo.dir[i][t[i]]
            if j is not None and is_scorable(t[j]):
                targets[i] = j
                nsrc[j] += 1
    boost = [1.0] * n
    for i in range(n):
        j = targets[i]
        if j is not None:
            boost[j] += greed + (greed_core if nsrc[j] >= 2 else 0.0)

    terms = [0.0] * n
    nit = cset["nitwit_slots"]
    if parts is not None:
        parts[:] = [None] * n
    IMP_NAME = {0: "deck modifier", 2: "neighbour modifier", 3: "colour mismatch", 4: "row bonus", 5: "chain",
                7: "unique groups"}
    for i in range(n):
        ti = t[i]
        if not is_scorable(ti):
            continue
        m = xg[i]
        if m & NONSTAT:
            continue
        if is_positional(ti):
            r, c = geo.slots[i]
            if ti == T_ROW:
                raw = float(row_col.get((r, col[i]), 0))
            elif ti == T_COL:
                raw = float(col_col.get((c, col[i]), 0))
            else:
                peers = geo.diag[i] if ti == T_DIAG else geo.surr[i]
                raw = float(sum(1 for q in peers if t[q] != T_DEAD and col[q] == col[i]))
            scaled = raw * freq[ti]
            base = max(scaled, 1.0) if ti == T_DIAG else scaled
        elif ti == T_DELUXE:
            base = deluxe_base
        else:
            base = 1.0

        def count(peers, bit):
            return sum(1 for q in peers if t[q] != T_DEAD and xg[q] & bit)

        add = add_all
        mult = void_factor
        rec_a, rec_m = ([], []) if parts is not None else (None, None)
        if rec_a is not None:
            if add_all:
                for (name, value, k, ccol) in cset["cores"]:
                    if CORE_CODE[name] == 5:
                        rec_a.append(("premium", value * n_deluxe))
                    elif CORE_CODE[name] == 7:
                        rec_a.append(("archive", math.pow(1.0 + value, n_arcane) - 1.0))
            if void_factor != 1.0:
                rec_m.append(("void", void_factor))
        add0, mult0 = add, mult
        for (name, value, k, ccol) in cset["cores"]:
            code = CORE_CODE[name]
            if code == 0:
                if m & (B_STAT | B_EVO):
                    add += value * n_nonfoil
            elif code == 1:
                if m & B_STAT:
                    add += value * n_colors
            elif code == 2:
                if m & B_STAT:
                    add += value
            elif code == 3:
                if m & B_FOIL:
                    add += value
            elif code == 4:
                if ccol == COLOR_NONE or col[i] == ccol:
                    add += value
            elif code == 12:
                if m & (1 << 2):
                    add += value
            elif code == 13:
                if m & (1 << 3):
                    add += value
            elif code == 16:
                if count(geo.surr[i], 1 << 5) > 0:
                    add += value
            elif code == 17:
                add += value * count(geo.surr[i], 1 << 6)
            elif code == 18:
                if count(geo.starcross[i], 1 << 7) > 0:
                    add += value
            elif code == 9:
                if m & 1:
                    mult *= 1.0 + value
            elif code == 10:
                if m & (1 << 1):
                    mult *= 1.0 + value * count(geo.orth[i], 1 << 1)
            elif code == 11:
                if m & (1 << 4):
                    mult *= 1.0 + value * max(n_util - 1, 0)
            if rec_a is not None:
                if add != add0:
                    rec_a.append((name, add - add0))
                if mult != mult0:
                    rec_m.append((name, mult / mult0))
                add0, mult0 = add, mult
        if jupiter[0] and m & jupiter[0]:
            add += jupiter[1]
            if rec_a is not None:
                rec_a.append(("jupiter", jupiter[1]))
        if pluto[0] and m & pluto[0]:
            mult *= 1.0 + pluto[1]
            if rec_a is not None:
                rec_m.append(("pluto", 1.0 + pluto[1]))
        if nit[i]:
            add += cset["nitwit"]
            if rec_a is not None:
                rec_a.append(("nitwit", cset["nitwit"]))
        add0 = add
        for (kind, value, gm, cm, ptype) in implicits:
            if kind == 0:
                if m & gm == gm and (cm == 0 or (col[i] != COLOR_NONE and cm >> col[i] & 1)):
                    add += value
            elif kind == 2:
                peers = geo.surr[i] if ptype == 1 else geo.col[i]
                add += value * sum(1 for q in peers if t[q] != T_DEAD and not is_greed(t[q]) and xg[q] & gm)
            elif kind == 3:
                add += value * sum(1 for q in geo.orth[i]
                                   if t[q] != T_DEAD and col[q] != COLOR_NONE and col[q] != col[i])
            elif kind == 4:
                add += value * geo.rows_from_bottom[i]
            elif kind == 5:
                if chain_id[i]:
                    add += chain_value * (chain_size[chain_id[i]] - 1)
            elif kind == 7:
                if m & B_STAT:
                    add += unique_value * unique_groups
            if rec_a is not None and add != add0:
                rec_a.append((IMP_NAME.get(kind, "deck modifier"), add - add0))
                add0 = add
        if have_mirror:
            k = None
            if geo.mirror_self[i]:
                k = bin(m).count("1") + 1
            elif geo.mirror_of[i] is not None:
                mi = geo.mirror_of[i]
                if t[mi] != T_DEAD and not is_greed(t[mi]) and col[mi] != COLOR_NONE and col[mi] == col[i]:
                    k = bin(m & xg[mi]).count("1") + 1
            if k is not None:
                mult *= 1.0 + mirror_tag * k
                if rec_m is not None:
                    rec_m.append(("runic", 1.0 + mirror_tag * k))
        terms[i] = base * (1.0 + add) * mult * boost[i]
        if parts is not None:
            gc = greed_core if nsrc[i] >= 2 else 0.0
            parts[i] = {"base": base, "adds": rec_a, "mults": rec_m, "greed": boost[i] - 1.0 - gc * nsrc[i],
                        "greed_core": gc * nsrc[i], "n_greed": nsrc[i], "empyreal": 1.0}

    if empyreal:
        v, k = empyreal
        idx = sorted((i for i in range(n) if terms[i] > 0.0 and xg[i] & (B_STAT | B_EVO)), key=lambda i: (-terms[i], i))
        for i in idx[:k]:
            terms[i] *= 1.0 + v
            if parts is not None and parts[i] is not None:
                parts[i]["empyreal"] = 1.0 + v
    return terms


def totals(terms, cards):
    """Per-attribute deck totals: sum of term x card value for stat cards (slot order)."""
    out = {}
    for term, c in zip(terms, cards):
        if term != 0.0 and c.get("attr"):
            out[c["attr"]] = out.get(c["attr"], 0.0) + term * c["value"]
    return out


def level_grants(cards):
    """(ability levels, talent levels) granted by Arcane and Knack cards: lists of (key, levels)."""
    ab, tal = [], []
    for c in cards:
        gr = c.get("grant")
        if gr:
            (ab if gr[0] == "ability" else tal).append((gr[1], float(gr[2])))
    return ab, tal


# ---------------------------------------------------------------------------------------------------------------
# Menus: DeckFAST designs as ledger deck choices
# ---------------------------------------------------------------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MENUS = os.path.join(ROOT, "data", "deck_menus_rework.json")
PTYPE = {"row": T_ROW, "col": T_COL, "surr": T_SURR, "diag": T_DIAG}
EMPTY = {"id": "empty", "kind": "dead", "attr": None, "value": 0.0, "g": 0, "grant": None, "groups": []}


def load_menus(path=None):
    path = path or MENUS
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pack_cards():
    """The catalog pack's card definitions (abilityKey / talentKey / levelChange / groups / icon model)."""
    from . import catalog
    p = os.path.join(catalog.load()["meta"]["pack"], "card", "modifiers.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)["values"]


def special_cards(combat_talents):
    """Non-stat card options: {kind: [card dict]}. Knack cards for combat talents (one card per talent and level;
    one plain card per distinct group set stands in for the rest as a Talent / Skull feeder), every pooled Arcane
    card, and one Temporal / Resource card per distinct group set (they differ only in groups for the deck)."""
    from . import deck as deckmod
    pools = deckmod._pools()
    vals = _pack_cards()
    out = {"knack": [], "temporal": [], "resource": [], "arcane": []}
    seen = {"knack": set(), "temporal": set(), "resource": set()}
    for pool, kind in (("knack", "knack"), ("deluxe_knack", "knack"), ("temporal", "temporal"),
                       ("deluxe_temporal", "temporal"), ("resource", "resource"), ("deluxe_resource", "resource"),
                       ("arcane", "arcane")):
        for cid in pools.get(pool, {}):
            e = vals.get(cid)
            if not e:
                continue
            g = group_mask(e.get("groups", []))
            card = {"id": cid, "kind": kind, "attr": None, "value": 0.0, "g": g, "grant": None,
                    "groups": list(e.get("groups", [])), "model": e.get("model", "")}
            roll = (e.get("pool") or [{}])[0]
            if kind == "arcane":
                card["grant"] = ("ability", roll.get("abilityKey", ""), float(roll.get("levelChange", 1)))
                out[kind].append(card)
                continue
            if kind == "knack":
                key = roll.get("talentKey", "")
                if key in combat_talents:
                    card["grant"] = ("talent", key, float(roll.get("levelChange", 1)))
                    out[kind].append(card)
                    continue
            sig = g
            if sig in seen[kind]:
                continue
            seen[kind].add(sig)
            out[kind].append(card)
    return out


def build_designs(ctx, menu):
    """Ledger designs for one stage menu: geometry, slot types and colours, per-slot card options, core sets and
    modifiers. Card values at the stage tier; cards of stages.REWORK_BANNED_CARDS stats are never offered."""
    from . import catalog, log, stages
    from . import deck as deckmod
    from .context import COMBAT_TALENTS
    tier = ctx.stage["card_tier"]
    stat = {}
    for fam in ("evo", "typeless", "deluxe"):
        rows = []
        for cid, card in sorted(ctx.card_registry[fam].items()):
            if card["attribute"] not in catalog.COMBAT_ATTRS or card["attribute"] in stages.REWORK_BANNED_CARDS:
                continue
            rows.append({"id": cid, "kind": fam, "attr": card["attribute"], "value": deckmod.card_value(card, tier),
                         "g": group_mask(card["groups"]), "grant": None, "groups": list(card["groups"])})
        stat[fam] = rows
    special = special_cards(COMBAT_TALENTS)
    feeders = special["knack"] + special["temporal"] + special["resource"]
    limits = menu.get("limits") or {}
    designs = []
    for m in menu["menu"]:
        geo = Geometry(m["slots"])
        types = [TYPE_CODE[c["t"]] for c in m["cells"]]
        colors = [COLOR_CODE.get(c["color"], 0) for c in m["cells"]]
        arcane_pos = {tuple(p) for p in m["arcane_slots"]}
        opts, seed = [], []
        for i, c in enumerate(m["cells"]):
            t = types[i]
            if t == T_DEAD:
                o = [dict(EMPTY)]
            elif is_greed(t):
                o = [{"id": c["t"], "kind": "greed", "attr": None, "value": 0.0, "g": 1 << GROUP_BITS["Greed"],
                      "grant": None, "groups": ["Greed"]}]
            elif t == T_ARCANE:
                o = [dict(x) for x in special["arcane"]] + [dict(EMPTY)]
            else:
                fam = "evo" if is_positional(t) else ("deluxe" if t == T_DELUXE else "typeless")
                o = [dict(x) for x in stat[fam]] + [dict(x) for x in feeders]
            s = 0
            sd = c.get("seed")
            if sd and sd.get("kind") in ("evo", "typeless", "deluxe"):
                s = next((k for k, x in enumerate(o) if x["id"] == sd["id"]), None)
                if s is None:
                    log.fallback("menu-seed-card", f"{m['label']}: seed card {sd['id']} not offered; first option used")
                    s = 0
            elif sd and sd.get("kind") == "battery":
                s = next((k for k, x in enumerate(o) if x["kind"] in ("knack", "temporal", "resource")
                          and x["g"] & (1 << GROUP_BITS[sd["id"]])), 0)
            opts.append(o)
            seed.append(s)
        primary_nit = (m["nitwit"].get("+".join(m["cores"])) or 0.0) > 0
        csets = []
        for cs in m["core_sets"]:
            name = "+".join(cs)
            nit = m["nitwit"].get(name, 0.0)
            if (nit > 0) != primary_nit:
                continue
            csets.append({"name": name,
                          "cores": [(n, float(v), int(k), COLOR_CODE[col] if col else COLOR_NONE)
                                    for (n, v, k, col) in m["core_spec"][name]],
                          "nitwit": float(nit), "shiny": "shiny" in cs,
                          "nitwit_slots": [nit > 0 and tuple(p) in arcane_pos for p in geo.slots]})
        imps = []
        for (kind, value, groups, cols, extra) in m["implicits"]:
            cm = 0
            for col in cols:
                cm |= 1 << COLOR_CODE[col]
            pt = PTYPE.get(extra, 0) if kind == "freq" else (1 if extra == "surrounding" else 0)
            imps.append((IMP_CODE[kind], float(value), group_mask(groups), cm, pt))
        designs.append({"label": m["label"], "deck": m["deck"], "key": m["key"], "found_for": m["found_for"],
                        "mystery": m["mystery"], "implicit_keys": m["implicit_keys"], "menu": m,
                        "geo": geo, "types": types, "colors": colors, "opts": opts, "seed": seed, "csets": csets,
                        "imps": imps, "greed": float(menu["greed"]), "deluxe_base": float(menu["deluxe_base"]),
                        "limits": {k: int(v) for k, v in limits.items()}})
    return designs


def design_cards(d, choice):
    return [d["opts"][i][o] for i, o in enumerate(choice)]


def design_terms(d, cset, choice):
    return evaluate(d["geo"], d["types"], d["colors"], design_cards(d, choice), d["csets"][cset], d["imps"],
                    d["greed"], d["deluxe_base"])


def limits_ok(d, choice):
    """Early-game card limits (each its own cap): Knack, Temporal and Deluxe cards."""
    lim = d["limits"]
    if not lim:
        return True
    n = {"knack": 0, "temporal": 0, "deluxe": 0}
    for c in design_cards(d, choice):
        if c["g"] & (1 << GROUP_BITS["Knack"]):
            n["knack"] += 1
        if c["g"] & (1 << GROUP_BITS["Temporal"]):
            n["temporal"] += 1
        if c["g"] & (1 << GROUP_BITS["Deluxe"]):
            n["deluxe"] += 1
    return all(n[k] <= v for k, v in lim.items() if k in n)
