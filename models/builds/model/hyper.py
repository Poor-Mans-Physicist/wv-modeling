"""Hyper vault environment by cycle: what the chaos modifiers do to the hyperboss and to player damage (0.34.1).

Monte Carlo over whole vaults, following the addon code:
- opening dump of chaosPerKill draws from hyper_mixed, then one more dump after every boss kill
  (HyperEscalationManager.dumpChaosModifiers; a stack-capped roll is skipped and re-pulled);
- every cycle a Brutal Pillars mini (HyperCycleManager.rollBatch): obelisks = min..obeliskMax with the floor at
  obeliskMin + cycle/3, each obelisk spawns 1-3 brutal bosses, each brutal boss gets
  BrutalBossesRegistry.getRandomMobModifiers(6, true) Infernal mods and each mod draws one hyper_all_bad modifier
  (BrutalBossesObjective.addBossKillModifier; capped rolls skip without spending budget);
- one hyper_bad_timer_events draw per runner every ambientPeriodTicks (budget spent before the cap check);
- all of it shares the chaosCap budget (HyperVaultObjective.consumeChaosBudget).
At each fight the boss carries health x vaultHealthFactor (1 + sum MULTIPLY_BASE) x prod(1 + MULTIPLY_TOTAL), its
attack damage gains the vault's damage modifiers next to the escalation, and player damage is x(1 + 2 x Frenzy stacks).
Frenzy stacks are capped at 1 + (cycle - 1) / 4 (HyperModifierPolicy.stackCap).
"""
import json
import math
import os
import random
import statistics

from .log import fallback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOLS = os.path.join(ROOT, "data", "hyper_pools_0.34.1.json")
MAX_CYCLE = 80
SIM_VERSION = 1

_doc = None
_tables = {}


def load():
    global _doc
    if _doc is None:
        with open(POOLS, encoding="utf-8") as f:
            _doc = json.load(f)
    return _doc


class _Pool:
    def __init__(self, p):
        self.entries = p["entries"]
        self.cum = []
        acc = 0.0
        for e in self.entries:
            acc += e["weight"]
            self.cum.append(acc)
        self.total = acc
        self.rolls = p["rolls"]

    def draw(self, rng):
        x = rng.random() * self.total
        lo, hi = 0, len(self.cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.cum[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return self.entries[lo]


def _infernal_mod_count(rng):
    """BrutalBossesRegistry.getRandomMobModifiers(6, true): each of 6 slots fails on a coin flip, at most 4 times."""
    fails = n = 0
    for _ in range(6):
        if rng.random() < 0.5 and fails < 4:
            fails += 1
            continue
        n += 1
    return n


def simulate(vaults=4000, cycles=MAX_CYCLE, minutes_per_cycle=8.0, runners=1, seed=20261007):
    """Per fight cycle: lists over vaults of (chaos count, health factor, damage add, damage mult, frenzy stacks)."""
    doc = load()
    cfg = doc["config"]
    caps = doc["stack_caps"]
    pools = {k: _Pool(v) for k, v in doc["pools"].items()}
    unresolved = set(doc["unresolved"])
    per_kill, cap = int(cfg["chaosPerKill"]), int(cfg["chaosCap"])
    ambient_per_cycle = minutes_per_cycle * 60 * 20 / cfg["ambientPeriodTicks"]
    rng = random.Random(seed)
    out = [[] for _ in range(cycles + 1)]
    for _ in range(vaults):
        st = {"chaos": 0, "hp_add": 0.0, "hp_mult": 1.0, "dmg_add": 0.0, "dmg_mult": 1.0, "counts": {}}

        def capped(e, cycle):
            mid = e["id"]
            if mid == doc["frenzy_id"]:
                c = 1 + max(0, cycle - 1) // 4
            elif mid == doc["mana_leak_id"]:
                c = cycle // 3
            else:
                c = caps.get(mid)
            return c is not None and st["counts"].get(mid, 0) >= c

        def add(e):
            st["hp_add"] += e["hp_add"]
            st["hp_mult"] *= e["hp_mult"]
            st["dmg_add"] += e["dmg_add"]
            st["dmg_mult"] *= e["dmg_mult"]
            st["counts"][e["id"]] = st["counts"].get(e["id"], 0) + 1
            if e["frenzy"]:
                st["counts"]["#frenzy"] = st["counts"].get("#frenzy", 0) + e["frenzy"]

        def budget(n):
            g = max(0, min(n, cap - st["chaos"]))
            st["chaos"] += g
            return g

        def dump(cycle):
            granted = budget(per_kill)
            added = tries = 0
            while added < granted and tries < 10 * granted:
                tries += 1
                e = pools["mixed"].draw(rng)
                if e["id"] in unresolved or capped(e, cycle):
                    continue
                add(e)
                added += 1

        dump(0)
        ambient_acc = 0.0
        for cycle in range(cycles + 1):
            floor_ = min(cfg["obeliskMax"], cfg["obeliskMin"] + cycle // 3 + max(0, runners - 1) // 2)
            obelisks = rng.randint(floor_, cfg["obeliskMax"])
            for _ in range(obelisks):
                for _ in range(rng.randint(1, 3)):
                    for _ in range(_infernal_mod_count(rng)):
                        e = pools["all_bad"].draw(rng)
                        if e["id"] in unresolved or capped(e, cycle):
                            continue
                        if budget(1) <= 0:
                            continue
                        add(e)
            ambient_acc += ambient_per_cycle * runners
            while ambient_acc >= 1.0:
                ambient_acc -= 1.0
                if budget(1) <= 0:
                    continue
                e = pools["timer"].draw(rng)
                if e["id"] in unresolved or capped(e, cycle):
                    continue
                add(e)
            frenzy = st["counts"].get("#frenzy", 0)
            out[cycle].append((st["chaos"], (1.0 + st["hp_add"]) * st["hp_mult"], st["dmg_add"], st["dmg_mult"], frenzy))
            dump(cycle + 1)
    return out


def tables(knobs):
    """Per integer cycle 0..MAX_CYCLE: log-average boss health factor, damage add / mult and player damage multiplier,
    plus the summary rows the report shows. Cached per knob setting."""
    key = (knobs.get("hyper_chaos_modifiers", True), knobs.get("hyper_frenzy", True), knobs.get("hyper_minutes_per_cycle", 8.0),
           knobs.get("hyper_runners", 1))
    if key in _tables:
        return _tables[key]
    chaos_on, frenzy_on, minutes, runners = key
    cache = None
    try:
        import hashlib
        h = hashlib.sha1((open(POOLS, "rb").read() if os.path.exists(POOLS) else b"") + repr((key, SIM_VERSION)).encode()).hexdigest()[:16]
        cache = os.path.join(ROOT, "out", f"hyper_tables_{h}.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                _tables[key] = json.load(f)
            return _tables[key]
    except OSError as e:
        fallback("hyper-cache", f"hyper table cache unavailable ({e}); simulating in memory")
        cache = None
    rows = []
    if not chaos_on:
        for c in range(MAX_CYCLE + 1):
            rows.append({"cycle": c, "chaos": 0, "hp_factor": 1.0, "hp_factor_p10": 1.0, "hp_factor_p90": 1.0, "hp_factor_mean": 1.0,
                         "dmg_add": 0.0, "dmg_mult": 1.0, "frenzy_mult": 1.0, "frenzy_mean": 1.0, "p_frenzy": 0.0})
    else:
        try:
            sims = simulate(minutes_per_cycle=minutes, runners=runners)
        except FileNotFoundError as e:
            fallback("hyper-pools-missing", f"{e}; run extract/extract_hyper.py. Hyperboss modelled without chaos modifiers")
            _tables[key] = tables({**knobs, "hyper_chaos_modifiers": False})
            return _tables[key]
        for c, vs in enumerate(sims):
            hp = sorted(v[1] for v in vs)
            fr = [1.0 + 2.0 * v[4] for v in vs] if frenzy_on else [1.0] * len(vs)
            rows.append({
                "cycle": c, "chaos": statistics.mean(v[0] for v in vs),
                "hp_factor": math.exp(statistics.mean(math.log(x) for x in hp)),
                "hp_factor_p10": hp[len(hp) // 10], "hp_factor_p90": hp[(9 * len(hp)) // 10],
                "hp_factor_mean": statistics.mean(hp),
                "dmg_add": statistics.mean(v[2] for v in vs), "dmg_mult": math.exp(statistics.mean(math.log(v[3]) for v in vs)),
                "frenzy_mult": math.exp(statistics.mean(math.log(x) for x in fr)), "frenzy_mean": statistics.mean(fr),
                "p_frenzy": sum(v[4] > 0 for v in vs) / len(vs) if frenzy_on else 0.0,
            })
    _tables[key] = rows
    if cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            tmp = cache + f".{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            os.replace(tmp, cache)
        except OSError as e:
            fallback("hyper-cache", f"could not write {cache}: {e}")
    return rows


C_LO, C_HI = -12.0, 80.0
GRID_STEP = 1.0 / 32.0
_grids = {}


def _interp(rows, c, key, log):
    if c <= 0:
        v = rows[0][key]
        return v
    if c >= MAX_CYCLE:
        return rows[MAX_CYCLE][key]
    i = int(math.floor(c))
    t = c - i
    a, b = rows[i][key], rows[i + 1][key]
    if log:
        return math.exp((1.0 - t) * math.log(a) + t * math.log(b))
    return (1.0 - t) * a + t * b


def grid(knobs, hyper_cfg):
    """Boss health H, boss damage per hit D and player-damage multiplier M on c = C_LO + k * GRID_STEP.

    Fractional cycles interpolate the per-cycle chaos tables (log-linear for factors); below cycle 0 the cycle-0 state
    applies. Shared verbatim with the Rust kernel through the export, so both solve on identical numbers."""
    key = (tuple(sorted((k, v) for k, v in knobs.items() if k.startswith("hyper_"))), tuple(sorted(hyper_cfg.items())))
    if key in _grids:
        return _grids[key]
    rows = tables(knobs)
    h = hyper_cfg
    n = int(round((C_HI - C_LO) / GRID_STEP)) + 1
    H, D, M = [], [], []
    for k in range(n):
        c = C_LO + k * GRID_STEP
        e = h["stat_factor"] ** c
        H.append(h["ref_health"] * (1.0 + h["innate_health"] + h["health_percent"] * e + h["increment"] * c)
                 * _interp(rows, c, "hp_factor", True))
        D.append(h["ref_damage"] * (1.0 + h["damage_percent"] * e + h["increment"] * c + _interp(rows, c, "dmg_add", False))
                 * _interp(rows, c, "dmg_mult", True))
        M.append(_interp(rows, c, "frenzy_mult", True))
    g = {"c0": C_LO, "step": GRID_STEP, "H": H, "D": D, "M": M}
    _grids[key] = g
    return g


def at(g, arr, c):
    """Grid value at cycle c (linear between grid points)."""
    x = (min(max(c, C_LO), C_HI) - g["c0"]) / g["step"]
    k = min(int(x), len(g[arr]) - 2)
    t = x - k
    return (1.0 - t) * g[arr][k] + t * g[arr][k + 1]


def _last_true(n, feas):
    """Largest k with feas(k), assuming feas is true then false (binary search); -1 if none."""
    if not feas(0):
        return -1
    if feas(n - 1):
        return n - 1
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if feas(mid):
            lo = mid
        else:
            hi = mid
    return lo


def solve_damage(g, dps0, slope, kill_time):
    """Damage cycle: largest c where the boss dies within kill_time x M(c) (Frenzy scales every hit, so it stretches
    the effective kill window and leaves the missing-HP execution term unchanged)."""
    if dps0 <= 0:
        return C_LO
    s = 2.0 * slope
    H, M = g["H"], g["M"]

    def ttk(hp):
        if s * hp / dps0 < 1e-6:
            return hp / dps0
        return math.log1p(s * hp / dps0) / s

    def feas(k):
        return H[k] <= 0 or ttk(H[k]) <= kill_time * M[k]

    n = len(H)
    k = _last_true(n, feas)
    if k < 0:
        return C_LO
    if k == n - 1:
        return C_HI
    if H[k] <= 0:
        return g["c0"] + k * g["step"]
    a = math.log(kill_time * M[k]) - math.log(ttk(H[k]))
    b = math.log(kill_time * M[k + 1]) - math.log(ttk(H[k + 1]))
    return g["c0"] + (k + a / (a - b)) * g["step"] if a > b else g["c0"] + k * g["step"]


def solve_survival(g, cap):
    """Survival cycle: largest c where the boss's damage per hit D(c) stays at or below `cap`."""
    D = g["D"]

    def feas(k):
        return D[k] <= 0 or D[k] <= cap

    n = len(D)
    k = _last_true(n, feas)
    if k < 0:
        return C_LO
    if k == n - 1:
        return C_HI
    if D[k] <= 0:
        return g["c0"] + k * g["step"]
    a = math.log(cap) - math.log(D[k])
    b = math.log(cap) - math.log(D[k + 1])
    return g["c0"] + (k + a / (a - b)) * g["step"] if a > b else g["c0"] + k * g["step"]
