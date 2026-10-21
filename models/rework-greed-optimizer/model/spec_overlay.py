"""Hand-maintained per-spec facts that the ability configs (catalog abilities.json) do not carry.

extract/spec_table.py merges this overlay with the catalog's config keys into data/spec_table.json; model/abilities.py
and model/buffs.py read the facts from that table and model/kernel.py exports them to the kernel. Adding an ability
means a record here, its Python function (abilities.MODEL_FNS or buffs.BUFF_HANDLERS) and its kernel match arm.

One record per damage spec (abilities.MODELS order) and per buff spec:
- label: display name (abilities.Model.label for damage specs).
- scaling: what the damage scales with ("AP", "AD", "Mana", "HP", "Thorns", "AD+Block", "AD+Thorns"); None for buffs.
- tags: "single" / "aoe" (Model.tags).
- hit: the main hit's damage.HitClass (name and flags as the per-ability function builds it; double_ok includes
  normal, as HitClass does); hit_variants: the flag overrides under a condition (an etching key, "lucky_thorns" for the Lucky Thorns flag, or "rework" for the
  greed-rework ledger). Declarative: both engines still build their hit classes inline; the "rework" iframe "free"
  variant is read by abilities.free_iframes (Fireball explosions neither set nor respect i-frames there).
- leech_ok, lucky_ok: hits can leech / roll lucky hits (PlayerLeechHelper skips AOE, DOT, REFLECT, JAVELIN, CHARMED,
  EFFECT and TOTEM hits; Necromancy damage comes from the minions). "lucky_bolt": only with the Lucky Bolt etching
  (an arrow instead of an AOE hit).
- pd_hold: a held channel (HoldManaAbility) under the Power Dump rework.
- spectral: hits spend Spectral Strike stacks (True, False or "lucky_bolt").
- cast_debuffs: (procs kind, uptime rule, source label) the spec puts on the boss when the rotation casts it;
  rules: totem_interval (Totem of Hatred's mark between damage ticks), maw_slowness (Hungry Maw's 9 s Slowness),
  always (the Necromancy curse while summons live), share (Hypothermia in proportion to the cast share).
- fang_hits: which data/fang_hits.json table gives the fang hits per cast by effective radius ("wall" or "maw";
  tools/fang_geometry.py), None for every other spec.
- reach: positioning rule (execution plan section 1.4, evaluate.source_reach): projectile (projectile and targeted
  abilities: any distance), self_radius (self-centred AoE: radius x aoe_multiplier), zero (needs contact or hits
  taken), fang_line (Hungry Maw's line: 1.5 x radius x aoe_multiplier); reach_key: the config key holding that radius.
- buff: the buff kind for buff specs (None for pure damage specs).
- secondary_eligible: a build may learn it next to its main damage ability.
"""


def _hit(name, ap=False, aoe_flag=False, normal=False, lucky=False, di_baked=False, m_baked=False, iframe="plain",
         echo_ok=False, rampage=False, double_ok=False):
    return {"name": name, "ap": ap, "aoe_flag": aoe_flag, "normal": normal, "lucky": lucky, "di_baked": di_baked,
            "m_baked": m_baked, "iframe": iframe, "echo_ok": echo_ok, "rampage": rampage, "double_ok": double_ok or normal}


def _dmg(label, scaling, tags, hit, reach, leech_ok=False, lucky_ok=False, pd_hold=False, spectral=False,
         cast_debuffs=(), fang_hits=None, hit_variants=None, buff=None, secondary_eligible=True, reach_key=None):
    if reach_key is None and reach in ("self_radius", "fang_line"):
        reach_key = "radius"
    return {"label": label, "scaling": scaling, "tags": list(tags), "hit": hit, "hit_variants": hit_variants or {},
            "leech_ok": leech_ok, "lucky_ok": lucky_ok, "pd_hold": pd_hold, "spectral": spectral,
            "cast_debuffs": [list(x) for x in cast_debuffs], "fang_hits": fang_hits, "reach": reach,
            "reach_key": reach_key, "buff": buff, "secondary_eligible": secondary_eligible}


def _buff(label, buff, cast_debuffs=()):
    return {"label": label, "scaling": None, "tags": [], "hit": None, "hit_variants": {}, "leech_ok": False,
            "lucky_ok": False, "pd_hold": False, "spectral": False, "cast_debuffs": [list(x) for x in cast_debuffs],
            "fang_hits": None, "reach": None, "reach_key": None, "buff": buff, "secondary_eligible": False}


AP_AOE = _hit("AP+AOE", ap=True, aoe_flag=True)
# Rework (proposed, user 2026-10-21): Fireball and Volley explosions use DamageUtil.shotgunAttack.
REWORK_FREE = {"rework": {"iframe": "free"}}

SPEC_OVERLAY = {
    "Fireball_Base": _dmg("Fireball", "AP", ["aoe"], AP_AOE, "projectile", hit_variants=REWORK_FREE),
    "Fireball_Volley": _dmg("Fireball Volley", "AP", ["aoe"], AP_AOE, "projectile", hit_variants=REWORK_FREE),
    "Fireball_Fireshot": _dmg("Fireshot", "AP", ["single"], _hit("AP+FIRESHOT", ap=True, echo_ok=True), "projectile",
                              leech_ok=True, spectral=True),
    "Nova_Base": _dmg("Nova", "AP", ["aoe"], AP_AOE, "self_radius"),
    "Nova_Dot": _dmg("Poison Nova (DoT)", "AP", ["aoe"], _hit("DOT", ap=True, aoe_flag=True, iframe="shotgun"),
                     "self_radius"),
    "Smite_Base": _dmg("Smite", "AP", ["single"], _hit("AP+SMITE_BASE", ap=True, iframe="shotgun", echo_ok=True),
                       "self_radius", leech_ok=True),
    "Smite_Archon": _dmg("Smite (Archon)", "AP", ["single"], _hit("AP+SMITE", ap=True, iframe="shotgun"),
                         "self_radius", leech_ok=True),
    "Smite_Blast_Wave": _dmg("Smite (Blast Wave)", "AP", ["aoe"], _hit("AP+AOE+SMITE", ap=True, aoe_flag=True),
                             "self_radius"),
    "Arcane_Base": _dmg("Arcane Beam", "AP", ["single"], _hit("AP+EFFECT", ap=True), "projectile", pd_hold=True),
    "Arcane_Rail": _dmg("Arcane Rail", "AP", ["single"], _hit("AP+ARCANE_RAIL", ap=True, echo_ok=True), "projectile",
                        leech_ok=True, spectral=True),
    "Arcane_Prism": _dmg("Arcane Prism", "AP", ["aoe"], _hit("AP+EFFECT+AOE", ap=True, aoe_flag=True), "self_radius"),
    "Chain_Lightning_Base": _dmg("Chain Lightning", "AP", ["aoe"], AP_AOE, "projectile"),
    "Chain_Lightning_Orbs": _dmg("Lightning Orbs", "AP", ["aoe"], AP_AOE, "projectile"),
    "Chain_Lightning_Charged_Bolts": _dmg("Charged Bolts", "AP", ["aoe"],
                                          _hit("AP+AOE", ap=True, aoe_flag=True, iframe="shotgun"), "projectile"),
    "Storm_Arrow_Base": _dmg("Storm Arrow", "AP", ["single"], _hit("AP+SMITE", ap=True, iframe="shotgun"), "projectile",
                             leech_ok=True),
    "Ice_Bolt_Base": _dmg("Ice Bolt", "AD", ["single"],
                          _hit("AOE", aoe_flag=True, di_baked=True, m_baked=True, iframe="shotgun"), "projectile",
                          leech_ok="lucky_bolt", spectral="lucky_bolt",
                          hit_variants={"etching_ice_bolt_lucky": {"name": "arrow (Lucky Bolt)", "aoe_flag": False,
                                                                   "m_baked": False}}),
    "Shard_Blizzard": _dmg("Shard Blizzard", "AD", ["aoe"], _hit("AOE", aoe_flag=True, di_baked=True, m_baked=True),
                           "projectile", cast_debuffs=[("HYPOTHERMIA", "share", "Shard Blizzard")]),
    "Javelin_Base": _dmg("Javelin", "AD", ["single"], _hit("JAVELIN", iframe="javelin"), "projectile"),
    "Javelin_Piercing": _dmg("Piercing Javelin", "AD", ["aoe"], _hit("JAVELIN", iframe="javelin"), "projectile"),
    "Javelin_Scatter": _dmg("Scatter Javelin", "AD", ["aoe"], _hit("JAVELIN", iframe="javelin"), "projectile"),
    "Earthquake_Base": _dmg("Earthquake", "AD", ["aoe"], AP_AOE, "self_radius"),
    "Earthquake_Singularity": _dmg("Earthquake (Singularity)", "AD", ["aoe"], AP_AOE, "self_radius"),
    "Earthquake_Tremor": _dmg("Earthquake (Tremor)", "AD", ["aoe"], AP_AOE, "self_radius"),
    "Grenade_Base": _dmg("Grenade", "AD", ["aoe"], AP_AOE, "projectile"),
    "Grenade_Sticky": _dmg("Sticky Grenade", "AD", ["aoe"], AP_AOE, "projectile"),
    "Toxic_Grenade": _dmg("Toxic Grenade (DoT)", "AP", ["aoe"], _hit("DOT", ap=True, aoe_flag=True, iframe="shotgun"),
                          "projectile"),
    "Necromancy_Base": _dmg("Necromancy (Warriors)", "AP", ["single"], _hit("AP+NECRO", ap=True), "projectile",
                            cast_debuffs=[("CURSE", "always", "Necromancy")]),
    "Necromancy_Archer": _dmg("Necromancy (Archers)", "AP", ["single"], _hit("AP+NECRO", ap=True), "projectile",
                              cast_debuffs=[("CURSE", "always", "Necromancy")]),
    "Dash_Damage": _dmg("Dash Damage", "AD", ["aoe"], _hit("AOE", aoe_flag=True, m_baked=True), "zero"),
    "Fangs_Base": _dmg("Wall of Fangs", "AD", ["aoe"], _hit("FANG", normal=True, lucky=True, iframe="reset", rampage=True),
                       "self_radius", leech_ok=True, lucky_ok=True, spectral=True, fang_hits="wall"),
    "Fangs_Maw": _dmg("Hungry Maw", "AD", ["single"], _hit("FANG", normal=True, lucky=True, iframe="reset", rampage=True),
                      "fang_line", leech_ok=True, lucky_ok=True, spectral=True, fang_hits="maw",
                      cast_debuffs=[("SLOWNESS", "maw_slowness", "Fangs Maw")]),
    "Mana_Shield_Implode": _dmg("Implode", "Mana", ["aoe"], _hit("AOE", aoe_flag=True), "self_radius"),
    "Implode_Life_Tap": _dmg("Life Tap", "HP", ["aoe"], _hit("AOE", aoe_flag=True), "self_radius"),
    "Shield_Bash": _dmg("Shield Bash", "AD+Block", ["aoe"], _hit("AOE", aoe_flag=True, m_baked=True), "zero"),
    "Shield_Bash_Earthshatter": _dmg("Shield Bash (Earthshatter)", "AD+Block", ["aoe"],
                                     _hit("AOE", aoe_flag=True, m_baked=True), "zero"),
    "Shield_Bash_Battering_Ram": _dmg("Shield Bash (Battering Ram)", "AD+Thorns", ["aoe"],
                                      _hit("AOE", aoe_flag=True, m_baked=True), "zero"),
    "Totem_Mob_Damage": _dmg("Totem (Mob Damage)", "AP", ["aoe"], _hit("AP+TOTEM", ap=True, echo_ok=True), "self_radius",
                             cast_debuffs=[("MARK", "totem_interval", "Totem of Hatred")], reach_key="totemEffectRadius",
                             hit_variants={"etching_totem_mob_damage_ad": {"name": "TOTEM", "ap": False}}),
    "Shell_Porcupine": _dmg("Thorns (Shell Porcupine)", "Thorns", ["single"],
                            _hit("THORNS", echo_ok=True, double_ok=True), "zero",
                            hit_variants={"lucky_thorns": {"lucky": True}}, buff="thorns_toggle",
                            secondary_eligible=False),
    "Taunt_Base": _buff("Taunt", "boss_vulnerable"),
    "Totem_Player_Damage": _buff("Totem (Player Damage)", "registry_add"),
    "Rampage_Base": _buff("Rampage", "rampage"),
    "Rampage_Berserker": _buff("Rampage (Berserker)", "rampage"),
    "Rampage_Bloodlust": _buff("Rampage (Bloodlust)", "rampage"),
    "Rampage_Instinct": _buff("Rampage (Instinct)", "rampage"),
    "Battle_Cry_Base": _buff("Battle Cry", "battle_cry_ad"),
    "Battle_Cry_Spectral_Strike": _buff("Battle Cry (Spectral Strike)", "spectral_stacks"),
    "Battle_Cry_Lucky_Strike": _buff("Battle Cry (Lucky Strike)", "battle_cry_lucky"),
    "Colossus_Base": _buff("Colossus", "resistance"),
    "Concentrate_Base": _buff("Concentrate", "empower"),
    "Totem_Mana_Regen": _buff("Totem (Mana Regen)", "mana_regen"),
    "Nova_Slow": _buff("Frost Nova", "boss_hypothermia", cast_debuffs=[("HYPOTHERMIA", "share", "Nova Slow")]),
    "Necromancy_Golem": _dmg("Necromancy (Golem)", "AP", ["single"], _hit("AP+NECRO", ap=True), "projectile",
                             cast_debuffs=[("CURSE", "always", "Necromancy")]),
    "Empower_Base": _buff("Empower", "move_speed"),
    "Empower_Slowness_Aura": _buff("Empower (Slowness Aura)", "boss_slowness"),
    "Sneaky_Getaway": _buff("Sneaky Getaway", "dodge"),
    "Totem_Base": _buff("Totem (Rejuvenation)", "heal"),
    "Mana_Shield_Base": _buff("Mana Shield", "absorb_pool"),
    "UltimateShield_Base": _buff("Ultimate Shield", "absorb_mana"),
}

# Better Combat combo multipliers per melee weapon (pack weapon_attributes).
WEAPON_COMBOS = {
    "sword": [1.0, 1.0, 1.25],
    "axe": [1.0, 1.0],
    "battlestaff": [0.8, 1.0, 1.2, 1.4, 0.8, 0.8],
    "trident": [1.0],
}
