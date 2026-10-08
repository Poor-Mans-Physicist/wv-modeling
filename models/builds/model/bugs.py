"""Plain-language summaries of the 0.34.1 bugs the model can switch (full registry: MECHANICS §6.8).

Each entry: short title, what happens in game, and why it changes the numbers. The report shows these on a
build when switching that one bug to its intended behaviour moves the build's result noticeably.
"""

BUG_INFO = {
    "ICEBOLT-DI2": {
        "title": "Ice Bolt applies damage increase twice",
        "what": "The bolt bakes attack damage x (1 + damage increase) x the multiplier list into its own damage, then the hurt "
                "event (flagged AoE) multiplies by (1 + damage increase) again.",
        "why": "Every point of damage increase counts squared on Ice Bolt, so DI-heavy decks scale it far past other skills.",
        "where": "AbstractIceBoltAbility:73-82, IceBoltEntity:199-203",
    },
    "ECHO-FLAGLOSS": {
        "title": "Echoes of ability hits lose the ability-power flag",
        "what": "An echo replays the stored hit with only the ECHOING and UNLUCKY flags, so an echo of Fireshot, base Smite, "
                "Arcane Rail or an AP Totem is treated as a normal attack.",
        "why": "The replay picks up damage increase, the multiplier list and execution damage, none of which the original "
               "ability-power hit gets.",
        "where": "addon LivingEntityEvents:556-573, EchoingPotionEffect:30-36",
    },
    "EXEC-GEAR": {
        "title": "Execution gear scales the whole hit by the boss penalty",
        "what": "Against bosses the addon computes (hit + missing HP x execution) x 0.25, so the 0.25 boss penalty applies to "
                "the entire hit, not only the execution part.",
        "why": "With execution gear a fresh boss takes a quarter of the normal hit; a heavily damaged one takes much more.",
        "where": "addon LivingEntityEvents:395-426",
    },
    "ORDER-FLATADD": {
        "title": "Flat-damage handlers race damage increase",
        "what": "Execution and other flat adds sit at the same event priority as damage increase; Forge's handler order is "
                "decided at launch, so the add lands before or after the multiplier depending on the session.",
        "why": "The model averages both orders; fixing the order removes the spread.",
        "where": "MECHANICS §2.2 (same-priority NORMAL handlers)",
    },
    "FANG-RESIDUAL": {
        "title": "Fanged Strike fangs re-apply on-hit effects",
        "what": "Proc fangs still re-run Conditional Damage, Damage On Hit, Vulnerable and The Dice, and can start on-hit AoE "
                "or chains.",
        "why": "Each fang is multiplied by effects the parent hit already paid for.",
        "where": "addon FangedStrikeLuckyHitTalent:41, CustomFangEntity:128-133",
    },
    "VULN-OFF1": {
        "title": "Vulnerable is one level short",
        "what": "Vulnerable's bonus is amplifier x 10%, so Vulnerable I gives +0% and etching levels clamp at 8.",
        "why": "This one makes builds weaker: fixing it adds +10% to every Vulnerable source.",
        "where": "VulnerableEffect:61",
    },
    "VOLLEY-BOUNCE-EXPLODE": {
        "title": "Fireball Volley explodes on every bounce",
        "what": "WV's Mitosis mixin calls explode() on every bounce whether or not the Mitosis etching is present.",
        "why": "A Volley lands up to ~10 explosions per cast instead of one; a single target is still i-frame limited.",
        "where": "addon MixinVaultFireball:49-86",
    },
    "ICEBOLT-T12-HOLE": {
        "title": "Ice Bolt tier 12 has no attack-damage scaling",
        "what": "Tier 12 of Ice_Bolt_Base omits percentAttackDamageDealt and the splinter keys, so the reader defaults them to 0.",
        "why": "An Ice Bolt sitting at exactly effective tier 12 deals only its flat damage.",
        "where": "abilities.json Ice_Bolt_Base tier 12",
    },
    "MANA-CAP": {
        "title": "Max mana stops at 4096",
        "what": "the_vault registers generic.mana_max as a RangedAttribute with a maximum of 4096, so any mana past that is "
                "clamped away; nothing in the pack raises the cap.",
        "why": "Mana-scaling skills (Implode, Mind Meld's cooldown reduction, the mana pool over a fight) stop growing at 4096.",
        "where": "ModAttributes:57",
    },
    "OFFHAND-STAFF": {
        "title": "Battlestaff keeps the offhand's stats",
        "what": "The rule that drops offhand stats under a two-handed battlestaff is gated off when Better Combat is installed.",
        "why": "A battlestaff build also gets a full shield, focus or wand worth of affixes.",
        "where": "MixinAttributeSnapshotCalculatorNoBC (Better Combat gate)",
    },
}
