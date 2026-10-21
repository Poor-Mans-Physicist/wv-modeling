"""Per-stage lookup tables: everything a build in that stage may legally pick."""
from . import catalog, stages, deck
from .log import fallback
from . import gods as godmod

COMBAT_TALENTS = {
    "Intelligence", "Strength", "Sorcery", "Prime_Amplification", "Lingering_Fumes", "Berserking", "Depleted",
    "Fanged_Strike", "Fatal_Strike", "Execution_Strike", "Arcane_Strike", "Cleave", "Executioner", "Hexbreaker",
    "Battle_Trance", "Lucky_Momentum", "Frenzy", "Blood_Chakra", "Arcana", "Stack_Master", "Ethereal",
    "Quickening", "Mind_Meld", "Lightning_Damage", "Mana_Steal", "Life_Steal", "Lunge", "Medic", "Methodical",
    "Blood_Rush", "Speed", "Stoneskin", "Last_Stand", "Blizzard",
}
NICHE_TALENTS = {
    "Berserking": "needs you below 20% HP: modeled at the share of the fight below 20% HP from the landed-hit heuristic (evaluate.hp_state)",
    "Depleted": "needs you below 20% mana (uptime from the rotation's mana flow)",
    "Blood_Chakra": "stacks on kills (one add killed every 5 s in the boss fight)",
    "Methodical": "healing bonus only below 20% mana (uptime from the rotation's mana flow)",
    "Blood_Rush": "stacks on kills (one add killed every 5 s in the boss fight)",
    "Last_Stand": "halves damage taken only below 20% HP (the HP-state heuristic's low-HP uptime)",
    "Blizzard": "casts a free Frost Nova on Hypothermic targets; only its Frozen Orb Vulnerable counts on the boss",
}


def talent_gates(talents):
    """Skill gates of the modeled talents (pack skill_gates.json + addon put-by-key): points spent before learning,
    required talents, either-of groups and lock-outs. A gate naming a talent outside the model can't be met."""
    raw = catalog.load()["talent_gates"]
    out = {}
    for t in sorted(talents):
        g = raw.get(t)
        if not g:
            continue
        out[t] = g
        missing = [x for x in g["deps"] if x not in talents] + [x for grp in g["either"] if not set(grp) & set(talents) for x in grp[:1]]
        if missing:
            fallback(f"gate-unmet-{t}", f"talent {t} needs {missing}, which the model does not offer; {t} can't be learned")
    return out


def legendary(a):
    """Legendary necklace roll: the ability-level affix moves +2 tier indices, i.e. +2 -> +4 (VaultGearLegendaryHelper)."""
    if a.attribute != "the_vault:added_ability_level" or not isinstance(a.raw, dict):
        return a
    raw = dict(a.raw, levelChange=4, legendary=True)
    return catalog.Affix(a.attribute, a.group, a.ident, 4.0, raw, a.kind)


class Context:
    def __init__(self, stage_name, mode, variant="all", deck_layout=None):
        catalog.load()
        self.stage_name = stage_name
        self.stage = stages.STAGES[stage_name]
        self.mode = mode
        self.variant = variant
        ban = stages.VARIANTS[variant]
        self.knobs = dict(stages.KNOBS)
        if mode == "intended":
            # User ruling 2026-10-09: in the bugs-fixed ledger a one-shot is death; protection is as-shipped only.
            self.knobs["oneshot_protection"] = False
        self.greed_tier = self.stage["greed_tier"]
        self.rarity = self.stage["rarity"]
        self.q = self.stage["roll_quality"]
        self.pools = {}
        exec_ban = mode == "intended" and stages.LEDGER != "rework"
        ban_attrs = set(ban["ban_attrs"]) | (stages.INTENDED_BAN_ATTRS if exec_ban else set())
        ban_etch = stages.INTENDED_BAN_ETCHINGS if mode == "intended" else set()
        types = catalog.ARMOR + catalog.MAINHAND + catalog.OFFHAND + ["vault_necklace"]
        for t in types:
            rar = self.rarity if t != "vault_necklace" else "OMEGA"
            pools = catalog.gear_pools(t, rar, self.q, self.stage["seal_roll_quality"])
            if ban_attrs:
                pools = {g: [a for a in lst if a.attribute not in ban_attrs] for g, lst in pools.items()}
            self.pools[t] = pools
        self.time_lock = bool(self.stage.get("require_time_plushie") and stages.KNOBS.get("time_plushie_lock"))
        self.rework = stages.LEDGER == "rework"
        # Rework: the lock is per build (Temporal Breaking in the active Wendarr tree or a Wendarr transfer slot grants
        # the immunity), so every offhand stays offered and search.time_lock_ok enforces the plushie otherwise.
        self.time_lock_dynamic = self.rework and self.time_lock
        self.offhand_types = list(catalog.OFFHAND)
        if self.time_lock:
            ti = catalog.time_immunity_affix("plushie", self.rarity, self.q)
            if ti is None:
                fallback("time-plushie", "time-acceleration immunity implicit not found on plushies; lock skipped")
                self.time_lock = False
            else:
                if not self.time_lock_dynamic:
                    self.offhand_types = ["plushie"]
                imp = [a for a in self.pools["plushie"]["IMPLICIT"] if (a.group or a.ident) != ti.group]
                self.pools["plushie"]["IMPLICIT"] = imp + [ti]
        # Multi-jump rule: normal boots with the Multi Jump implicit, or the Prismatic Feather trinket. With the feather the
        # boots' implicit group is free and unique boots are allowed (search.multijump_ok).
        self.boots_lock = bool(stages.KNOBS.get("boots_multijump_lock"))
        self.boots_mj = None
        if self.boots_lock:
            mj = catalog.implicit_affix("boots", self.rarity, catalog.MULTIJUMP)
            if mj is None:
                fallback("boots-multijump", "Multi Jump implicit not found on boots; lock skipped")
                self.boots_lock = False
            else:
                self.boots_mj = mj
                imp = [a for a in self.pools["boots"]["IMPLICIT"] if a.ident != mj.ident]
                self.pools["boots"]["IMPLICIT"] = imp + [mj]
        if self.stage.get("necklace_legendary"):
            self.pools["vault_necklace"]["SUFFIX"] = [legendary(a) for a in self.pools["vault_necklace"]["SUFFIX"]]
        self.etchings = {t: [e for e in catalog.etching_options(t, self.greed_tier, self.q)
                             if e["id"].split(":")[-1] not in ban_etch]
                         for t in types if t in catalog.ETCH_TYPE}
        self.trinkets = [t for t in catalog.trinket_options()
                         if t["slot"] in self.stage["trinket_slots"]
                         and (self.stage.get("trinket_fusion") or self.stage["trinket_slots"][t["slot"]] > 0)]
        self.trinkets_by_id = {t["id"]: t for t in catalog.trinket_options()}
        self.deck_layout_key = deck_layout or self.stage["deck"]["layout"]
        # Rework: base values; the build scales them by (piety units + 1) of the charm's god (build.assemble).
        self.charms = catalog.charm_options(0 if self.rework else self.stage["god_reputation"], self.q)
        self.unique_by_id, self.unique_seals = catalog.unique_options(self.stage["unique_roll_quality"], self.stage["seal_roll_quality"])
        if ban_attrs:
            self.unique_seals = [a for a in self.unique_seals if a.attribute not in ban_attrs]
            for u in self.unique_by_id.values():
                keep = [a for a in u["affixes"] if a.attribute not in ban_attrs]
                if len(keep) != len(u["affixes"]):
                    u["affixes"] = keep
                    u["explicit"] = [i for i, a in enumerate(keep) if a.kind in ("PREFIX", "SUFFIX")]
                    u["powers"] = [p for p in u["powers"] if p["attribute"] not in ban_attrs]
        self.unique_slots = {}
        self.ancient_max = self.stage.get("ancient_max", 0)
        for u in ([] if ban.get("no_uniques") else self.unique_by_id.values()):
            if u["ancient"] and self.ancient_max <= 0:
                continue
            t = u["type"]
            slot = t if t in catalog.ARMOR else "mainhand" if t in catalog.MAINHAND else "offhand"
            if slot == "offhand" and (t not in self.offhand_types or (self.time_lock and not self.time_lock_dynamic)):
                continue
            self.unique_slots.setdefault(slot, []).append(u)
        self.init_deck()
        all_talents = catalog.visible_talents()
        banned = set(ban["ban_talents"]) | (stages.INTENDED_BAN_TALENTS if exec_ban else set())
        self.talents = {k: v for k, v in all_talents.items() if k in COMBAT_TALENTS and k not in banned}
        self.gates = talent_gates(self.talents)
        missing = COMBAT_TALENTS - set(self.talents) - banned
        if missing:
            fallback("talents-missing", f"combat talents not visible/learnable, skipped: {sorted(missing)}")
        self.abilities = catalog.abilities()
        self.ability_groups = catalog.ability_groups()
        self.greed_nodes = [] if self.rework else catalog.greed_nodes()
        self.greed_by_id = {n["id"]: n for n in self.greed_nodes}
        self.greed_budget = 0 if self.rework else 3 * self.greed_tier
        self.gods = godmod.Gods(self.stage, eot=bool(ban.get("eot"))) if self.rework else None
        self.prestige = catalog.prestige_powers(self.greed_tier)

    def init_deck(self):
        """Fixed precomputed layout for the stage; per stat slot, the obtainable cards and their contribution. With
        rework deck menus on, the stage's DeckFAST designs instead (init_menu_deck)."""
        self.designs = []
        if self.rework and stages.DECK_MENUS and self.init_menu_deck():
            return
        lay = deck.load_layouts()[self.deck_layout_key]
        self.deck_layout = lay
        cv = dict(lay.get("core_values") or {})
        if self.stage.get("greed_card"):
            # Rework greed cards: each adds roll - 1 to its slot's multiplier (MixinCardDeck.getGreedMultiplier).
            cv["greed"] = catalog.load()["greed_cards"][self.stage["greed_card"]] - 1.0
        self.deck_slots, self.deck_meta = deck.score_layout(lay["grid"], lay["cores"], lay["implicits"],
                                                            core_values=cv or None)
        reg = deck.card_registry()
        tier = self.stage["card_tier"]
        self.card_registry = reg
        self.deck_options = []
        for slot in self.deck_slots:
            fam = deck.SLOT_FAMILY[slot["kind"]]
            opts = {}
            for cid, card in reg[fam].items():
                if card["attribute"] not in catalog.COMBAT_ATTRS:
                    continue
                if self.rework and card["attribute"] in stages.REWORK_BANNED_CARDS:
                    continue
                mult = deck.slot_mult(slot, card["groups"])
                val = deck.card_value(card, tier)
                opts[cid] = (card["attribute"], val * mult, val, mult)
            self.deck_options.append(opts)

    def init_menu_deck(self):
        """Rework deck menus: the stage's designs (model/deck_rework.py). False (logged) when the menu file has no
        entry for this stage, which keeps the fixed layout."""
        from . import deck_rework
        menus = deck_rework.load_menus(None if stages.DECK_MENUS == "1" else stages.DECK_MENUS)
        menu = menus.get(self.stage_name)
        if not menu:
            fallback(f"deck-menu-missing-{self.stage_name}", f"no deck menu for stage {self.stage_name}; fixed layout used")
            return False
        self.card_registry = deck.card_registry()
        self.deck_layout = None
        self.deck_slots, self.deck_meta, self.deck_options = [], {}, []
        self.menu = menu
        self.designs = deck_rework.build_designs(self, menu)
        return True

    def staff_banned(self):
        """A battlestaff drops the offhand's stats (Better Combat empties the offhand under a two-handed weapon in every
        mode), including the time plushie's time-acceleration immunity that hyper vaults need, so battlestaffs are
        illegal while that lock is on."""
        return self.time_lock and not self.time_lock_dynamic

    def mainhands(self, family):
        return [t for t in family.mainhands if not (t == "battlestaff" and self.staff_banned())]

    def unique_choices(self, slot, family):
        """Uniques that may go in this slot for this family (mainhand: the family's weapon types)."""
        opts = self.unique_slots.get(slot, [])
        if slot == "mainhand":
            opts = [u for u in opts if u["type"] in self.mainhands(family)]
        return opts

    def talent_cost(self, tid, tier):
        t = self.talents.get(tid)
        if not t or tier <= 0:
            return 0
        return sum(x.get("learnPointCost", 0) for x in t.get("tiers", [t])[:tier])

    def talent_max(self, tid):
        t = self.talents[tid]
        return t.get("maxLearnableTier", len(t.get("tiers", [t])))

    def ability_cost(self, sid, tier):
        a = self.abilities.get(sid)
        if not a or tier <= 0:
            return 0
        return sum(x.get("learnPointCost", 0) for x in a.get("tiers", [a])[:tier])

    def ability_max(self, sid):
        a = self.abilities[sid]
        return a.get("maxLearnableTier", len(a.get("tiers", [a])))

    def family_hits_normally(self, family):
        return family.startswith("melee:") or family.startswith("ranged:")

    def greed_parents(self, nid):
        n = self.greed_by_id[nid]
        ps = n.get("parents") or ([n["parent"]] if n.get("parent") else [])
        return ps

    def greed_free(self, nid):
        n = self.greed_by_id[nid]
        return n.get("type") == "greed_root" or n.get("present", False)

    def greed_frontier(self, owned):
        out = []
        for n in self.greed_nodes:
            nid = n["id"]
            if nid in owned or self.greed_free(nid):
                continue
            ps = self.greed_parents(nid)
            if any(p in owned or self.greed_free(p) for p in ps):
                out.append(nid)
        return out

    def greed_leaves(self, owned):
        out = []
        for nid in owned:
            if not any(nid in self.greed_parents(o) for o in owned if o != nid):
                out.append(nid)
        return out
