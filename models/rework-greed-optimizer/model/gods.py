"""Greed-rework god trees (addon feature/extraction-vaults; pack gods/*.json).

Rules (verified in code):
- Each god's points = pointsAtLevelOne + perLevel x (min(L, maxDefined) - 1) + pastMax x max(L - maxDefined, 0)
  + floor(reputation / reputationPerGodPoint) (GodLevels.totalPointsForLevel, GodAlignmentData.totalPoints).
- Every node costs 1; a root is always buyable, any other node needs an owned neighbour (undirected edges), so an
  owned set is legal when every owned node is connected to an owned root through owned nodes.
- An effect's rank is the number of owned placements sharing it (GodNode.ledgerKey).
- The active god is the equipped charm's god (ActiveGodResolver): its whole tree applies; other gods' stat and root
  nodes apply at 25% (GodCarryover.FOREIGN_TREE_SCALE); other gods' minors only through that god's minor transfer
  slots (one per minorTransferSlotLevels entry at or below the god's level) at full strength; foreign majors never.
- Level from XP: god_levels.json cumulativeXp, then xpPerLevelPastMax per level. The stage's god-XP pool caps the
  summed XP of the four levels. Sacrifice gates are assumed complete; reputation is the stage god_reputation for
  every god. A greed rank needs one god at its godLevelGates level (greed_ranks.json).
"""
from . import catalog, stages

GODS = ["idona", "velara", "wendarr", "tenos"]
CHARM_GOD = {"idona": "IDONA", "velara": "VELARA", "wendarr": "WENDARR", "tenos": "TENOS"}
GOD_OF_CHARM = {v: k for k, v in CHARM_GOD.items()}
FOREIGN_SCALE = 0.25
TEMPORAL_BREAKING = "wendarr_temporal_breaking"


class Tree:
    def __init__(self, god, doc, banned):
        self.god = god
        self.nodes = doc["nodes"]
        self.effects = doc["effects"]
        self.labels = doc["labels"]
        self.edges = doc["edges"]
        self.idx = {n["id"]: i for i, n in enumerate(self.nodes)}
        self.adj = [set() for _ in self.nodes]
        for a, b in doc["edges"]:
            ia, ib = self.idx[a], self.idx[b]
            self.adj[ia].add(ib)
            self.adj[ib].add(ia)
        self.type = [n["type"] for n in self.nodes]
        self.effect = [n["effect"] for n in self.nodes]
        self.usable = [n["enabled"] and n["effect"] not in banned for n in self.nodes]
        self.roots = [i for i, n in enumerate(self.nodes) if n["type"] == "root"]
        self.minor_node = {n["effect"]: i for i, n in enumerate(self.nodes) if n["type"] == "minor"}
        self.effect_type = {n["effect"]: n["type"] for n in self.nodes}

    def connected(self, owned):
        """True when every owned node reaches an owned root through owned nodes."""
        seen = {r for r in self.roots if r in owned}
        stack = list(seen)
        while stack:
            x = stack.pop()
            for y in self.adj[x]:
                if y in owned and y not in seen:
                    seen.add(y)
                    stack.append(y)
        return len(seen) == len(owned)

    def frontier(self, owned):
        out = []
        for i, ok in enumerate(self.usable):
            if not ok or i in owned:
                continue
            if self.type[i] == "root" or any(j in owned for j in self.adj[i]):
                out.append(i)
        return out

    def removable(self, owned):
        """Owned nodes whose removal keeps the rest legal (in ascending index order)."""
        return [x for x in sorted(owned) if self.connected(owned - {x})]

    def path_to(self, owned, target):
        """Shortest list of unowned nodes to buy so `target` becomes owned legally (BFS from roots / owned nodes)."""
        if target in owned or not self.usable[target]:
            return []
        from collections import deque
        start = list(owned) + [r for r in self.roots if r not in owned and self.usable[r]]
        prev = {s: None for s in start}
        q = deque(start)
        while q:
            x = q.popleft()
            if x == target:
                break
            for y in sorted(self.adj[x]):
                if y not in prev and self.usable[y]:
                    prev[y] = x
                    q.append(y)
        if target not in prev:
            return None
        path, x = [], target
        while x is not None and x not in owned:
            path.append(x)
            x = prev[x]
        return path[::-1]


class Gods:
    """Per-context god data: trees, level curve, reputation, XP pool, rank gate, banned and time effects."""

    def __init__(self, stage, eot):
        cat = catalog.load()
        banned = set(stages.REWORK_BANNED_EFFECTS)
        if not eot:
            banned |= stages.REWORK_EOT_ONLY_EFFECTS
        self.eot = eot
        self.trees = {g: Tree(g, cat["gods"][g], banned) for g in GODS}
        lv = cat["god_levels"]
        self.cum = list(lv["cumulativeXp"])
        self.max_defined = lv["maxDefinedLevel"]
        self.past_max = lv["xpPerLevelPastMax"]
        self.p1, self.p_to, self.p_past = lv["pointsAtLevelOne"], lv["pointsPerLevelToMax"], lv["pointsPerLevelPastMax"]
        self.mts_levels = list(lv["minorTransferSlotLevels"])
        self.rep_per_point = lv["reputationPerGodPoint"]
        self.rep = stage["god_reputation"]
        self.pool = stage["god_xp"]
        ranks = cat["greed_ranks"]
        gates = {int(k): v for k, v in ranks["godLevelGates"].items()}
        self.rank_gate = max([v for k, v in gates.items() if k <= stage["greed_tier"]], default=0)
        self.max_level = self.level_for_xp(self.pool)
        from .godfx import fury_table
        self.fury_table = fury_table(self.trees["idona"].effects["idona_ultra_rampaging"])

    def xp_for_level(self, level):
        if level <= 0:
            return 0
        if level <= self.max_defined:
            return self.cum[level - 1]
        return self.cum[-1] + (level - self.max_defined) * self.past_max

    def level_for_xp(self, xp):
        level = 0
        while self.xp_for_level(level + 1) <= xp:
            level += 1
        return level

    def points(self, level):
        p = 0
        if level > 0:
            p = self.p1 + self.p_to * (min(level, self.max_defined) - 1) + self.p_past * max(level - self.max_defined, 0)
        return p + self.rep // self.rep_per_point

    def slots(self, level):
        return sum(1 for u in self.mts_levels if level >= u)

    def xp_used(self, levels):
        return sum(self.xp_for_level(levels.get(g, 0)) for g in GODS)

    def legal(self, gs, active):
        """Why `gs` (a build's god state) is illegal, or None."""
        lv = gs["levels"]
        if self.xp_used(lv) > self.pool:
            return "xp pool"
        if max((lv.get(g, 0) for g in GODS), default=0) < self.rank_gate:
            return "rank gate"
        for g in GODS:
            t, owned = self.trees[g], gs["nodes"][g]
            if len(owned) > self.points(lv.get(g, 0)):
                return f"{g} points"
            if any(not t.usable[i] for i in owned):
                return f"{g} banned node"
            if not t.connected(owned):
                return f"{g} disconnected"
            if sum(1 for i in owned if t.type[i] == "major") > 1:
                return f"{g} majors"
            m = gs["mts"][g]
            if len(m) > self.slots(lv.get(g, 0)) or len(set(m)) != len(m):
                return f"{g} transfer slots"
            for e in m:
                i = t.minor_node.get(e)
                if i is None or i not in owned:
                    return f"{g} transfer of an unowned minor"
        return None

    def ranks(self, gs, active):
        """effect -> (owned placements, scale) for every effect that applies with `active` as the charm god."""
        out = {}
        for g in GODS:
            t, owned = self.trees[g], gs["nodes"][g]
            cnt = {}
            for i in owned:
                cnt[t.effect[i]] = cnt.get(t.effect[i], 0) + 1
            mts = set(gs["mts"][g]) if g != active else set()
            for e, n in cnt.items():
                typ = t.effect_type[e]
                if g == active:
                    out[e] = (n, 1.0)
                elif typ in ("stat", "root"):
                    out[e] = (n, FOREIGN_SCALE)
                elif typ == "minor" and e in mts:
                    out[e] = (n, 1.0)
        return out

    def effect_def(self, e):
        return self.trees[e.split("_", 1)[0]].effects.get(e, {})

    def has_temporal_breaking(self, gs, active):
        return TEMPORAL_BREAKING in self.ranks(gs, active)

    def piety_units(self, gs, god):
        """Charm scaling units for `god`'s charm: floor(piety / 10), piety = 10 rep + 20 level + 10 per Pious Devotion
        (GodAlignmentData piety; MixinVaultCharmItemPiety)."""
        t = self.trees[god]
        pious = sum(1 for i in gs["nodes"][god] if t.effect[i].endswith("_pious_devotion"))
        return self.rep + 2 * gs["levels"].get(god, 0) + pious


def empty_state():
    return {"levels": {g: 0 for g in GODS}, "nodes": {g: set() for g in GODS}, "mts": {g: [] for g in GODS}}


def clone_state(gs):
    return {"levels": dict(gs["levels"]), "nodes": {g: set(v) for g, v in gs["nodes"].items()},
            "mts": {g: list(v) for g, v in gs["mts"].items()}}
