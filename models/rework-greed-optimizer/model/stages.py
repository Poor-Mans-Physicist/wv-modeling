"""Progression stages and global model knobs. Every number here is an assumption shown in the report."""
import os

# Which ledger this process models: "0.34.1" (as shipped / bugs fixed) or "rework" (the greed update, bugs patched;
# catalog_rework.json, god trees instead of the greed tree). Set WV_LEDGER before importing the model.
LEDGER = os.environ.get("WV_LEDGER", "0.34.1")
# Rework deck choices per stage: "stage" = the stage deck only; "archetypes" = the stage deck plus the imported
# archetype decks (data/deck_layouts_archetypes.json). Every extra deck multiplies the search time.
DECK_CHOICES = os.environ.get("WV_DECK_CHOICES", "stage")
# Rework deck menus (DeckFAST scripts/deck_menus.py -> data/deck_menus_rework.json): "" = the fixed stage layouts;
# "1" = the default menu file; any other value = a menu file path. run.py --menus sets it for its workers.
DECK_MENUS = os.environ.get("WV_DECK_MENUS", "")

STAGES = {
    "early": {
        "label": "Early greed (tier 0-3)", "greed_tier": 3, "rarity": "OMEGA", "seals": 0, "unusual": 1,
        "unique_max": 1, "unique_seals": 0, "unique_roll_quality": 0.6, "seal_roll_quality": 0.5,
        "god_reputation": 25, "charm_prefixes": 4, "roll_quality": 0.6, "card_tier": 3, "necklace_legendary": False,
        "deck": {"layout": "The Anvil Deck|no greed",
                 "name": "The Anvil Deck, no greed cards (greed packs unlock at greed tier 4)"},
        "pouch": "Standard Trinket Pouch", "zephyr": False,
        "trinket_slots": {"red_trinket": 1, "blue_trinket": 1, "green_trinket": 1}, "trinket_fusion": False,
    },
    "mid": {
        "label": "Midgame greed (tier 4-8)", "greed_tier": 8, "rarity": "MYTHIC", "seals": 1, "unusual": 0,
        "unique_max": 3, "unique_seals": 0, "unique_roll_quality": 0.6, "seal_roll_quality": 0.5,
        "god_reputation": 50, "charm_prefixes": 4, "roll_quality": 0.6, "card_tier": 3, "necklace_legendary": False,
        "require_time_plushie": True,
        "deck": {"layout": "The Anvil Deck|custom midgame",
                 "name": "The Anvil Deck, user midgame layout (2 of each greed card; pure 5%, shiny 120%, colour 75%)"},
        "pouch": "Standard Trinket Pouch", "zephyr": True,
        "trinket_slots": {"red_trinket": 1, "blue_trinket": 0, "green_trinket": 1}, "trinket_fusion": False,
    },
    "end": {
        "label": "Endgame greed (tier 9+, modeled at 10)", "greed_tier": 10, "rarity": "MYTHIC", "seals": 2, "unusual": 0,
        "unique_max": 7, "unique_seals": 1, "unique_roll_quality": 0.8, "seal_roll_quality": 0.5,
        "god_reputation": 50, "charm_prefixes": 4, "roll_quality": 1.0, "card_tier": 5, "necklace_legendary": True,
        "require_time_plushie": True,
        "deck": {"layout": "The Rook Deck|unconstrained", "name": "The Rook Deck with greed cards"},
        "pouch": "Standard Trinket Pouch", "zephyr": True,
        "trinket_slots": {"red_trinket": 1, "blue_trinket": 0, "green_trinket": 1}, "trinket_fusion": True,
    },
    "max": {
        "label": "Hypermaxxed (greed tier 12)", "greed_tier": 12, "rarity": "MYTHIC", "seals": 3, "unusual": 0,
        "unique_max": 7, "unique_seals": 2, "unique_roll_quality": 0.8, "seal_roll_quality": 0.5,
        "god_reputation": 50, "charm_prefixes": 4, "roll_quality": 1.0, "card_tier": 5, "necklace_legendary": True,
        "require_time_plushie": True,
        "deck": {"layout": "The Mystery Deck|construction v2",
                 "name": "The Mystery Deck (Cake + Runic implicits, Greater Construction layout v2)"},
        "pouch": "Prismatic Trinket Pouch", "zephyr": True,
        "trinket_slots": {"red_trinket": 2, "blue_trinket": 1, "green_trinket": 2}, "trinket_fusion": True,
    },
}

# Talents removed in the bugs-fixed ledger (user ruling 2026-10-08: Execution Strike would be patched out).
INTENDED_BAN_TALENTS = {"Execution_Strike"}
# Missing-HP execution damage is removed everywhere in the bugs-fixed ledger (user ruling 2026-10-08): the corrupted
# gear affix, the unique seal, Young Kitsune's affix, and the Ravenous Fangs etching (execute threshold).
INTENDED_BAN_ATTRS = {"the_vault:execution_damage"}
INTENDED_BAN_ETCHINGS = {"ravenous_fangs"}

VARIANTS = {
    "all": {"label": "All mechanics", "ban_attrs": set(), "ban_talents": set()},
    "no_uniques": {"label": "No uniques", "ban_attrs": set(), "ban_talents": set(), "no_uniques": True},
    # Rework only: Edge of Time / Temporal Shielding allowed (builds that bleed vault time on hit).
    "eot": {"label": "Edge of Time", "ban_attrs": set(), "ban_talents": set(), "eot": True},
}

KNOBS = {
    "kill_time_s": 60.0,
    # Survival window (user rulings 2026-10-18, execution plan 1.3): survive_hits boss attack attempts hit_interval_s
    # apart; the landed count K ~ Binomial(survive_hits, p_land) is taken at its (1 - survival_risk) quantile.
    "survive_hits": 5.0,
    "hit_interval_s": 1.0,
    "survival_risk": 0.1,
    # Attack avoidance by stable movement speed s (plan 1.2): avoid_lo at speed_lo rising linearly to avoid_hi at
    # speed_hi, clamped outside; calibrated against Magic Missiles, WaveBlast always dodged.
    "avoid_lo": 0.25,
    "avoid_hi": 0.90,
    "speed_lo": 1.5,
    "speed_hi": 3.0,
    # Ranged Build Survivability Bonus (plan 1.4): no weave, not melee, every damage source reaches >= ranged_reach
    # blocks -> attacks reach the player x ranged_bonus.
    "ranged_bonus": 0.5,
    "ranged_reach": 5.0,
    "oneshot_protection": True,
    "oneshot_heal_fraction": 0.75,
    "oneshot_extra_cycles": 1.0,
    # Castle Bastion stack uptime by stable movement speed (ruling R19): castle_uptime_lo at speed_lo falling linearly
    # to castle_uptime_hi at speed_hi, clamped outside.
    "castle_uptime_lo": 0.5,
    "castle_uptime_hi": 0.2,
    # Share of time above 80% / below 20% HP from the landed-hit sawtooth (plan 2.1); off: u_high 1, u_low uptime_low_hp.
    "hp_state_heuristic": True,
    # The Dice taken at this percentile of the kill time (plan 2.2) over the fight's scored hits; 0.5 = the mean.
    "dice_percentile": 0.9,
    # Fang geometry (plan 3, tools/fang_geometry.py): boss distance behind data/fang_hits.json (5 blocks by ruling,
    # user 2026-10-19: fang builds stand at ranged distance; was 3); Hungry Maw's pull loss.
    "fang_boss_distance": 5.0,
    "maw_pull_factor": 0.75,
    # Proposed Frozen Orb rule (user, 2026-10-15, greed-rework ledger only): its Frost Nova adds the rolled levels once
    # to the target's existing Vulnerable instead of applying (value - 1) when stronger. Set for the rework below.
    "frozen_orb_additive": False,
    # Steel Inferno (user, 2026-10-21): explosion hits on the boss land at this share of the centre bonus (the game port
    # measures distance to the nearest point of the target's hitbox); pack mobs take the disc mean of 1 - (d/R)^2.
    "steel_inferno_boss_efficiency": 0.9,
    "steel_inferno_pack_mean": 0.5,
    # Share of a stationary storm cloud's bolts and shards that hit the boss (user, 2026-10-21); a storm that tracks
    # the boss (Stormcrown) lands all of them. 1.0 = not modeled (0.34.1); the rework sets 0.75 below.
    "storm_boss_occupancy": 1.0,
    "boots_multijump_lock": True,
    "execution_cycle_cap": 5.0,
    # Melee weaving: a Better Combat swing opens a 10-tick window in which plain-hurt hits only deal raw - melee raw.
    # Casts are not synced to swings, so a plain hit lands in a window with probability min(1, window x swings/s).
    "weave_window_s": 0.5,
    # Boss hit per cycle for survival (user ruling 2026-10-08). Every hit is taken as a crit (Lethal chaos modifier;
    # rune-roster boss crit multipliers are 1.5-2.0 in vault_mobs.json). As shipped the attack-damage attribute stops
    # at 1e6 (BOSS-DMG-CAP), but per-attack multipliers (golem double hand x4, ground slam x3) sit on top; the user's
    # deliberately generous ramp covers them: the larger of the real capped crit hit and 1e6 x 2^(c - 4), never above
    # the uncapped hit, levelling off at the worst documented hit (golem double hand x4, crit x1.8 = 7.2M).
    "hyper_boss_crit_mult": 1.8,
    "hyper_attack_damage_max": 1000000.0,
    "hyper_cap_ramp_start": 4.0,
    "hyper_cap_ramp_factor": 2.0,
    "hyper_cap_plateau": 7200000.0,
    "boss_uptime_melee": 1.0,
    "pack_size": 8,
    "pack_radius": 6.0,
    "player_level": 100,
    "base_skill_points": 101,
    "uptime_low_hp": 0.0,
    # Depleted / Methodical uptime below 20% mana, used only when mana_state_heuristic is off; with it on the uptime
    # comes from the rotation's mana flow (1 when a damage ability is mana-limited, the refill time after sneaking Power
    # Dump casts otherwise).
    "uptime_low_mana": 0.3,
    "mana_state_heuristic": True,
    # Kills in the boss fight (user, 2026-10-18: one brutal add every 5 s): kill-stacking talents, Berserk, Fury per kill.
    "kills_per_s": 0.2,
    # Concentrate (user, 2026-10-18): effects stripped from nearby adds per cast; each becomes Empower with chance 1/5.
    "concentrate_effects_stripped": 4.0,
    "max_swings_per_s": 10.0,
    "rampage_refresh_regen": 15.0,
    "rampage_refresh_cdr": 0.8,
    "rampage_refresh_uptime": 0.95,
    "mitosis_multiplier": 12.0,
    "volley_bounces": 9,
    "volley_iframe_hits_per_cast": 5,
    "smite_targets_in_range": 2.0,
    "boss_shield_downtime": 0.2,
    "time_plushie_lock": True,
    "min_mana_regen": 5.0,
    "min_cooldown_reduction": 0.8,
    "hyper_chaos_modifiers": True,
    "hyper_frenzy": True,
    "hyper_minutes_per_cycle": 8.0,
    "hyper_runners": 1,
    # Greed rework situational god nodes, at conservative vault-average values (user ruling 2026-10-11).
    "god_banked_damage_taken": 200000.0,
    "god_surrounded_mobs": 2.5,
    "god_vault_minutes": 30.0,
    "god_sneaky_effects": 2.0,
    # Power Dump cap (user, 2026-10-12, reshaped 2026-10-21): the largest bonus is per_mana x sqrt(ref x max mana), and
    # a cast gets that bonus times the share of the bar it spends above its cost (rework sets ref 2000).
    "power_dump_sqrt_ref": 1000.0,
    # Rework movement rule (user, 2026-10-13): stable movement speed (before the Zephyr Charm) must reach
    # min_move_speed, scored like the mana and cooldown minimums; above it speed pays through avoidance, Castle
    # Bastion and Kinetic Impact. The kernel anneal relaxes only this requirement early (search.rs MOVE_RELAX_END).
    "min_move_speed": 1.5,
    # Rotation rule (user, 2026-10-18): the main ability (with its echoes) must deal at least this share of the build's
    # damage, woven swings counting as a secondary. The gap costs 40 x gap score with no step during the search, and the
    # end of every search drops secondaries (or weaving) until it holds (0 = free mixing).
    "main_damage_share_min": 0.7,
    # Builds whose single secondary (or swings) would take more than this share at its full cooldown rate get flagged.
    "secondary_flag_share": 0.3,
    # Woven swings are scored as damage (user, 2026-10-18); off = the 2026-10-08 rule (leech and Mana Steal only).
    "score_swings": True,
    # Secondary rule (user, 2026-10-17): players can only juggle a few casts, so a build learns at most max_secondaries
    # damage secondaries, and the end of every search drops each one worth less than secondary_min_value score.
    "max_secondaries": 3,
    # Spectral Strike rework (user, 2026-10-18): every qualifying hit spends up to maxStacksUsedPerHit stacks and adds
    # this x base (flat) ability power per stack to its base: to attack damage on swings and fang hits, to ability power
    # on Fireshot and Arcane Rail hits. The hit's own multipliers then scale it (no cross-pool double dip).
    "spectral_ap_per_stack": 0.1,
    # Power Dump rework (user, 2026-10-18): a held channel cast while sneaking drains this share of the bar per second.
    "pd_hold_drain": 0.5,
    # Power Dump sneak window (user, 2026-10-21): entering sneak takes 0.33 s and leaving it 0.33 s, with no casts in
    # between (swings continue), so a build that mixes sneaking and standing casts pays this per dump.
    "pd_sneak_window_s": 0.66,
    # Sneaking halves the speed-based avoidance (user, 2026-10-21) over the share of the fight spent crouched; block,
    # the dodge stat and Wave Blast are unaffected.
    "sneak_avoid_factor": 0.5,
    "secondary_min_value": 0.1,
    # Ultimate Shield (user, 2026-10-18): mana per absorbed point is the config's manaPerDamageScalar times this (the
    # conversion halved for now: 4.5 mana per point at t8, 0.6 at t30).
    "ultimate_shield_scalar_mult": 2.0,
    # Effect negation is mandatory (user, 2026-10-18): a build without the Carapace trinket or the Blessed etching pays
    # the requirement penalty with gap 1.
    "effect_negation_required": True,
    # Vaultarang (user ruling R29): the throw travels this many blocks out and back per cycle.
    "rang_travel_blocks": 15.0,
    # Optional Taunt nerf (user R23, off): above this amplifier Taunt gains one Vulnerable amplifier per two levels.
    "taunt_amp_half_above": 0,
    # Echo stores the hit after Rampage / Fury once (user R24, ledger rule for the rework): direct-melee and fang echoes
    # store raw x (1 + Rampage's damage increase); the replay is unchanged.
    "echo_inherits_rampage": False,
}

BASELINE_ABILITIES = {"Vein_Miner": ("Vein_Miner_Base", 4), "Heal": ("Heal_Base", 4), "Dash": ("Dash_Base", 8)}

MODES = ("bugged", "intended")

HYPER = {
    "ref_health": 388000.0,
    "ref_damage": 1460.0,
    "stat_factor": 1.85,
    "health_percent": 50.0,
    "damage_percent": 50.0,
    "increment": 15.0,
    "innate_health": 0.5,
}


def boss_health(cycle):
    h = HYPER
    return h["ref_health"] * (1.0 + h["innate_health"] + h["health_percent"] * h["stat_factor"] ** cycle + h["increment"] * cycle)


def boss_damage(cycle):
    h = HYPER
    return h["ref_damage"] * (1.0 + h["damage_percent"] * h["stat_factor"] ** cycle + h["increment"] * cycle)


# Greed-rework stages (user ruling 2026-10-11): greed ranks Scavenger 3 / Hunter 1 / Master 3 / Legend (ranks 3, 7, 12,
# 16 of gods/greed_ranks.json), a god-XP pool to spread over the four gods, greed cards weak / strong / elite on
# re-optimized layouts (tier-3 cards), one ancient unique at endgame and any number at max. Only the bugs-fixed pass.
REWORK_STAGES = {
    "early": {"label": "Early (Scavenger 3, greed rank 3)", "greed_tier": 3, "god_xp": 40000, "greed_card": None,
              "ancient_max": 0},
    "mid": {"label": "Midgame (Hunter 1, greed rank 7)", "greed_tier": 7, "god_xp": 150000, "greed_card": "greed_weak",
            "ancient_max": 0},
    "end": {"label": "Endgame (Master 3, greed rank 12)", "greed_tier": 12, "god_xp": 800000, "greed_card": "greed_strong",
            "ancient_max": 1},
    "max": {"label": "Max (Legend, greed rank 16)", "greed_tier": 16, "god_xp": 2000000, "greed_card": "greed_elite",
            "ancient_max": 99},
}
# Layouts re-optimized in DeckFAST for the greed-update cards (2026-10-12; addends 1.1 / 1.7 / 2.0): mid moves its
# greed cards (+0.9%); end and max tie the 0.34.1 layouts, which stay optimal.
REWORK_DECK_LAYOUTS = {
    "mid": "The Anvil Deck|greed update mid (weak greed)",
    "end": "The Rook Deck|greed update end (strong greed)",
    "max": "The Mystery Deck|greed update max (elite greed, construction v2 shape)",
}
REWORK_DECK_NAMES = {
    "mid": "The Anvil Deck, re-optimized for Weak Greed cards (2 of each; pure 5%, shiny 120%, colour 75%)",
    "end": "The Rook Deck with Strong Greed cards (re-optimized; ties the 0.34.1 layout)",
    "max": "The Mystery Deck with Elite Greed cards (Cake + Runic implicits, Greater Construction v2; ties the user layout)",
}
# Gold Plating is banned (user ruling: annoying to build around). Edge of Time and Temporal Shielding bleed vault time
# per hit, so they only appear in the separate "EoT" builds.
REWORK_BANNED_EFFECTS = {"tenos_gold_plating"}
# Flat attack damage and flat ability power cards are removed (user, 2026-10-12): more of both must come from gear.
# Lucky hit chance cards are removed too (user, 2026-10-17): one card could push lucky chance far past 100%.
# Flat thorns cards are swapped for thorns % cards (user, 2026-10-17): the deck amplifies stats rather than granting
# base stats. Each keeps its card's tags and takes the matching Damage Increase card's values; the new stat multiplies
# flat thorns the way AP% multiplies AP (stats.derive).
REWORK_BANNED_CARDS = {"the_vault:attack_damage", "the_vault:ability_power", "the_vault:lucky_hit_chance",
                       "the_vault:thorns_damage_flat"}
REWORK_THORNS_PCT = "the_vault:thorns_damage_flat_percentile"
REWORK_CARD_SWAPS = {"thorns_flat": ("thorns_percent", "damage_increase"),
                     "amp_thorns_flat": ("amp_thorns_percent", "amp_damage_increase"),
                     "deluxe_thorns_flat": ("deluxe_thorns_percent", "deluxe_damage_increase")}
# Proposed node balance (user, 2026-10-12), applied over the extracted god config:
# - Immortal: x1.25 HP and armor, x1.33 healing, +15 health, +30 armor, +8 regeneration levels, revive every 600 s.
# - Ultra Rampaging: x1.5 damage taken at max Fury (incoming = 1 + scale x sqrt(F / 3000), so scale = 0.5 / sqrt(5)).
# - Grand Archmage: x3 ability power.
# - Unstoppable Greed: 5% of item quantity + rarity (was 10%; user, 2026-10-13).
# Proposed etching changes (user, 2026-10-19), applied over the extracted etching catalog: Lucky Reset rolls on helmets
# only (one per build, since an item holds one etching and a second copy does nothing).
REWORK_ETCHING_OVERRIDES = {
    "the_vault:etching_lucky_cooldown_reset": {
        "typeGroups": ["HELMET"],
        "description": "<gray>Proccing <#6DF5A3>Lucky Hit <gray>reduces the remaining cooldown of <yellow>%svalue/ability <gray>by "
                       "<yellow>10%<gray>, at most once per ability cast"},
    # Pyramid Scheme's text says +25 but the etching rolls 5-10 (greed 9-10) and 10-15 (greed 11+) (user, 2026-10-21):
    # show the rolled value.
    "woldsvaults:pyramid_scheme": {"description": "Increases max stacking talent stacks by %dvalue"},
}
REWORK_NODE_OVERRIDES = {
    "velara_immortal": {"health_multiplier": 1.25, "armor_multiplier": 1.25, "healing_multiplier": 1.33,
                        "flat_health": 15.0, "flat_armor": 30.0, "regeneration_levels": 8, "revive_cooldown_ticks": 12000},
    "idona_ultra_rampaging": {"incoming_scale": 0.5 / 5 ** 0.5},
    "idona_grand_archmage": {"ability_damage": 3.0},
    "tenos_unstoppable_greed": {"ratio": 0.05},
    "velara_the_stonewall": {"speed_multiplier": 0.4},
    # Steadfast (user, 2026-10-17): 0.5 Armor % per Knockback Resistance % above 100% (was 1), added to the other Armor %
    # sources instead of multiplying armor on its own (model/stats.derive).
    "velara_steadfast": {"armor_per_excess": 0.5},
}
# In-game text for every node this ledger re-balances (REWORK_NODE_OVERRIDES plus Power Dump's square root). The report
# shows it in place of the pack's text with a "proposed" tag. A balance pass is not finished until this text, the method
# page and the ledger change notes match the numbers.
_G, _O, _R = "#6ef08c", "#ff7a3d", "#C23627"
REWORK_NODE_TEXT = {
    "velara_immortal": [
        {"text": "1.25x max Health and Armor, 1.33x Healing Efficiency, +15 base Health, +30 base Armor, +8 Regeneration levels", "color": _G},
        {"text": ", and you "}, {"text": "revive yourself every 600 seconds", "color": _G},
        {"text": " - but your attack damage and ability power are "}, {"text": "halved", "color": _G},
        {"text": ". Sacrifice cannot syphon through you."}],
    "idona_ultra_rampaging": [
        {"text": "Hitting, killing and being hurt build "}, {"text": "Fury", "color": _O},
        {"text": ", which bleeds away in seconds. Fury both "}, {"text": "adds to and multiplies your Rampage damage", "color": _O},
        {"text": ", so a fight that keeps moving keeps you at a far higher multiplier."},
        {"text": "\n\nBosses pay double on hit and ten times on death."},
        {"text": "\n\nWhile you hold Fury you also "}, {"text": "take up to 1.5x damage at full Fury", "color": _R}, {"text": "."}],
    "idona_grand_archmage": [
        {"text": "Turn into an archmage: "},
        {"text": "2x mana capacity, +200 flat mana, greatly increased mana regen, 3x Ability Damage and +5% CDR cap", "color": _O},
        {"text": " - but you lose the ability to swing vault weapons."}],
    "idona_power_dump": [
        {"text": "Abilities cast "}, {"text": "while sneaking", "color": _O},
        {"text": " consume your whole mana bar. Your max mana sets the largest bonus, "},
        {"text": "0.25% x sqrt(2000 x max mana)", "color": _O},
        {"text": ", and each cast multiplies its damage by "},
        {"text": "1 + that bonus x the share of your bar it spent above its cost", "color": _O},
        {"text": ". Held channels drain 50% of your bar per second and count the drain above their cost. Every Implode gets "
                 "the bonus for the mana it consumes, sneaking or not. Each cast keeps its own bonus for its own hits; casts "
                 "made standing up are unaffected."}],
    "tenos_unstoppable_greed": [
        {"text": "Adds 5%", "color": "#5ad8ff"},
        {"text": " of your Item Quantity and Rarity, summed, to your Attack Damage and Ability Power."}],
    "velara_the_stonewall": [
        {"text": "0.4x Movement Speed", "color": _G}, {"text": ", but "}, {"text": "1.5x Armor", "color": _G}, {"text": "."}],
    "velara_steadfast": [
        {"text": "Knockback Resistance above 100%", "color": _G}, {"text": " is converted to "}, {"text": "Armor %", "color": _G},
        {"text": " at 2 to 1, added to your other Armor %."}],
}
# Proposed unique balance (catalog.apply_unique_overrides): uid -> ops, each {"op": "set", "identifier", "value"} (the
# value of every open tier of that modifier), {"op": "add", "modifier"}, {"op": "replace", "identifier", "modifier"}
# (the modifier takes the old one's place, so the unique's affix order is kept) or {"op": "name", "name"} (the display
# name).
_MULTI_HIT = {"op": "add", "modifier": {
    "attribute": "the_vault:javelin_multi_hit", "kind": "PREFIX", "group": "ModJavelinMultiHit",
    "identifier": "the_vault:u_javelin_multi_hit", "tags": [], "open": [{"weight": 1, "value": {"flag": True}}]}}


def _steel_inferno(radius, bonus):
    return {"op": "replace", "identifier": "the_vault:fireball_aoe_increase", "modifier": {
        "attribute": "the_vault:steel_inferno", "kind": "SUFFIX", "group": "ModSteelInferno",
        "identifier": "the_vault:u_steel_inferno", "tags": [],
        "open": [{"weight": 5, "value": {"centreBonus": {"min": bonus[0], "max": bonus[1], "step": 0.01},
                                         "radiusMultiplier": {"min": radius[0], "max": radius[1], "step": 0.01}}}]}}


_STORM_TRACKING = {"op": "add", "modifier": {
    "attribute": "the_vault:storm_tracking", "kind": "PREFIX", "group": "ModStormTracking",
    "identifier": "the_vault:u_storm_tracking", "tags": [],
    "open": [{"weight": 1, "value": {"flag": True, "abilityKey": "Storm_Arrow_Base", "speed": 10.0}}]}}
REWORK_UNIQUE_OVERRIDES = {
    # Proposed (user, 2026-10-21; replaces the 2026-10-15 multi-hit share): after a Scatter javelin splits on a block hit,
    # its children home in on the nearest enemy, so against a lone boss every child of the split hits it.
    "the_vault:warbound_helmet": [_MULTI_HIT],
    "ancient:the_vault:warbound_helmet": [_MULTI_HIT],
    # Ancient Frostguards gave fewer Stonefall levels than the regular one (+2 vs +4); ancients get regular + 1 and a
    # higher armor band (user, 2026-10-15).
    "ancient:the_vault:frostguards": [
        {"op": "set", "identifier": "the_vault:u_coldsnap", "value": {"abilityKey": "Stonefall_Cold", "levelChange": 5}},
        {"op": "set", "identifier": "the_vault:u_mod_armor", "value": {"min": 12, "max": 16, "step": 1}},
    ],
    # Steel Inferno (user, 2026-10-21): Inferno's Reach renamed; its Fireball AoE roll becomes a smaller explosion
    # (base radius x 0.5-0.6, ancient 0.55-0.65) with a centre damage bonus (+150-200%, ancient +175-225%).
    "the_vault:inferno_reach": [{"op": "name", "name": "Steel Inferno"}, _steel_inferno((0.5, 0.6), (1.5, 2.0))],
    "ancient:the_vault:inferno_reach": [{"op": "name", "name": "Ancient Steel Inferno"},
                                        _steel_inferno((0.55, 0.65), (1.75, 2.25))],
    # Stormcrown (user, 2026-10-21): the storm locks onto the mob closest to the arrow's hit and follows it at up to 10
    # blocks/s (Storm_Arrow_Base only, like its other rolls).
    "the_vault:stormcrown": [_STORM_TRACKING],
    "ancient:the_vault:stormcrown": [_STORM_TRACKING],
    # The ancient Tri-Rang rolls up to 4 chains and pierces (user, 2026-10-21: shown as Quad-Rang).
    "ancient:woldsvaults:trirang": [{"op": "name", "name": "Ancient Quad-Rang"}],
}
# Stormcrown's storm rolls leave the seal pools (user, 2026-10-21): gear seals (CORRUPTED_IMPLICIT) and unique seals.
REWORK_BANNED_SEALS = {"the_vault:storm_aoe_reduce", "the_vault:storm_cooldown", "the_vault:seal_storm_aoe_reduce",
                       "the_vault:seal_storm_cooldown"}


def _implode_javelin_damage(lo, hi):
    return {"op": "add", "modifier": {
        "attribute": "the_vault:ability_special_modification", "kind": "SUFFIX", "group": "ModImplodingJavelinDamage",
        "identifier": "woldsvaults:u_imploding_javelin_damage", "tags": [], "open": [{"weight": 1, "value": {
            "specialModificationKey": "woldsvaults:imploding_javelin_damage", "abilityKey": "Javelin_Base",
            "min": lo, "max": hi, "step": 0.01}}]}}


# Proposed (user, 2026-10-21): Implodes cast by the Pocket Penguin's javelins deal 50-80% more damage (ancient 70-90%).
REWORK_UNIQUE_OVERRIDES["woldsvaults:pocket_penguin"] = [_implode_javelin_damage(0.5, 0.8)]
REWORK_UNIQUE_OVERRIDES["ancient:woldsvaults:pocket_penguin"] = [_implode_javelin_damage(0.7, 0.9)]

REWORK_TIME_EFFECTS = {"wendarr_edge_of_time", "wendarr_temporal_shielding"}
# Allowed only in the Edge of Time builds: the time-cost stars plus Speed Demon, whose faster vault clock players
# would not choose outside a time build (user, 2026-10-13).
REWORK_EOT_ONLY_EFFECTS = REWORK_TIME_EFFECTS | {"wendarr_speed_demon"}

if LEDGER == "rework":
    MODES = ("intended",)
    KNOBS["frozen_orb_additive"] = True
    KNOBS["echo_inherits_rampage"] = True
    KNOBS["storm_boss_occupancy"] = 0.75
    KNOBS["power_dump_sqrt_ref"] = 2000.0
    for _k, _v in REWORK_STAGES.items():
        STAGES[_k].update(_v)
        if _k in REWORK_DECK_NAMES:
            STAGES[_k]["deck"] = dict(STAGES[_k]["deck"], layout=REWORK_DECK_LAYOUTS[_k], name=REWORK_DECK_NAMES[_k])
    if DECK_CHOICES == "archetypes":
        from model import deck as _deck
        for _k in STAGES:
            _arch = _deck.archetype_choices(_k)
            if _arch:
                STAGES[_k]["deck_choices"] = [STAGES[_k]["deck"]["layout"]] + _arch

