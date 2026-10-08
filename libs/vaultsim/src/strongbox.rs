use rand::Rng;

use crate::assemble::ChestSpot;

/// Confirmed level-bracketed strongbox upgrade probabilities (see MECHANICS_NOTES.md "Strongbox").
/// Wooden chests never upgrade at any level - no `wooden_strongbox` block even exists. Gilded
/// and ornate share the same bracket shape; living stays flat once it starts.
pub fn strongbox_chance(chest_type: &str, vault_level: u32) -> f32 {
    match chest_type {
        "gilded_chest" | "ornate_chest" => {
            if vault_level >= 100 {
                1.0 / 8.0
            } else if vault_level >= 80 {
                1.0 / 12.0
            } else if vault_level >= 50 {
                1.0 / 20.0
            } else {
                0.0
            }
        }
        "living_chest" => {
            if vault_level >= 50 {
                1.0 / 20.0
            } else {
                0.0
            }
        }
        _ => 0.0,
    }
}

/// Applies the upgrade roll to a batch of newly-resolved chests, in place - a single weighted
/// roll at the exact same slot as the plain chest, never an independent extra spawn (see
/// MECHANICS_NOTES.md). Call this on baseline POI chests right after `assemble()`, and again on
/// each `decorator_add_pass` call's own returned chests (both route through the same real
/// placeholder pipeline) - never on `decorator_cascade_pass` output, which can't produce a
/// strongbox at all (cascade copies a block-entity directly, bypassing the roll pipeline).
pub fn apply_strongbox_rolls(chests: &mut [ChestSpot], vault_level: u32, rng: &mut impl Rng) {
    for chest in chests {
        let chance = strongbox_chance(chest.chest_type, vault_level);
        if chance > 0.0 && rng.r#gen::<f32>() < chance {
            chest.is_strongbox = true;
        }
    }
}
