"""Progression stages and global model knobs. Every number here is an assumption shown in the report."""

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
}

KNOBS = {
    "kill_time_s": 60.0,
    "survive_hits": 5.0,
    "hit_interval_s": 1.0,
    "oneshot_protection": True,
    "oneshot_heal_fraction": 0.75,
    "oneshot_extra_cycles": 1.0,
    "castle_bastion_uptime": 0.5,
    "boots_multijump_lock": True,
    "execution_cycle_cap": 5.0,
    # Melee weaving: a Better Combat swing opens a 10-tick window in which plain-hurt hits only deal raw - melee raw.
    # Casts are not synced to swings, so a plain hit lands in a window with probability min(1, window x swings/s).
    "weave_window_s": 0.5,
    "boss_uptime_melee": 1.0,
    "pack_size": 8,
    "pack_radius": 6.0,
    "player_level": 100,
    "base_skill_points": 101,
    "uptime_low_hp": 0.0,
    "uptime_low_mana": 0.3,
    "uptime_kill_stacks": 0.5,
    "uptime_target_debuffed": 0.75,
    "concentrate_empower_amp": 20,
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
