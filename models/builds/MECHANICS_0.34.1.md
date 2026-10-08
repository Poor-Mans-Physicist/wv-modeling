# Combat mechanics reference (release 0.34.1)

Recon pass for the build-modeling system: what has to be catalogued, where the mechanics live,
and where the numbers live. Scope: level 100, post-Herald, combat and defense stats only.

## 0. Pinned sources

| Layer | Version | Where to read it |
|---|---|---|
| Pack | 0.34.1 = pack commit `c5963442` | `cache/pack/` (`python setup/fetch_sources.py`) |
| Addon (woldsvaults) | 0.34.1 = addon commit `0f0a9254` | `cache/addon/` (`generated/` holds the datagen'd config overlays) |
| the_vault | 3.21.6 | closed source; decompile your own copy of the jar to check a citation |
| vhapi | pack runs 5.8.1 | closed source; same as above |

Class and line citations below (`Foo.java:120`) refer to a CFR decompile of those jars. They are
pointers for checking a mechanic yourself, not something this repo ships. Better Combat client/server
config lives in the pack's `configureddefaults/config/bettercombat/`.

**Config layering.** the_vault configs = pack `config/the_vault/**` + addon jar
`data/woldsvaults/vault_configs/**` (and `data/vhapi/vault_configs/**`), merged by vhapi at load.
For `gear/gear_modifiers` (`CustomVaultGearLoader`) overlay groups are **appended** to the
same-named group of the pack file unless the overlay path contains `overwrite` (whole file),
`replace` (group) or `remove`. Example: the addon's `chestplate_mythic.json` adds 11 extra
CORRUPTED_IMPLICIT entries (phoenix, added_ability_level, jester lucky hit, execution damage,
burning hit, …). Other file types have their own loader rules (see §5/§6). A config extractor
therefore has to implement the vhapi merge, not just read the pack JSON.

## 1. How a player's stats are assembled

Two parallel channels. Every stat the model tracks lives in exactly one of them, and putting a
stat in the wrong channel is the classic silent no-op (memory: Idona/Velara/Wendarr nodes).

**A. AttributeSnapshot (VH gear attributes).** `AttributeSnapshotCalculator.computeSnapshot`
(the_vault `snapshot/AttributeSnapshotCalculator.java`) sums, in order:

1. talents (`GearAttributeSkill`, unlocked only)
2. expertises
3. prestige powers (skipped when prestige is disabled)
4. greed tree (`GearAttributeGreedNode`, skipped in Royale)
5. curios: trinkets (`GearAttributeTrinket`), charms (`GearAttributeCharm`), every curio with
   `AttributeGearData` — **card decks are curios with `CardDeckGearData`**, so deck output lands here;
   god/vault charms are scaled by `1 + VAULT_CHARM_EFFECTIVENESS`
6. equipped armor + hands (skips broken gear, wrong slot, gear above the player's level; the addon
   drops offhand stats while a battlestaff is in the main hand)
7. `VaultGearAttributeModifier.applySnapshotModifiers` (vault `player_attribute` modifiers — out of scope)

Values merge per attribute (`floatSum`, `intSum`, `asList`, `anyTrue`).

**B. Vanilla attributes.** `VaultGearHelper#getModifiers` turns these gear attributes into
`AttributeModifier`s, so they never get read from the snapshot: attack damage (flat), attack
speed (flat, and % = MULTIPLY_BASE), armor (flat, and % = MULTIPLY_BASE), toughness, KB resist,
health (flat, and % = MULTIPLY_BASE), reach/range, mana regen % (MULTIPLY_BASE), mana max (flat,
and %), movement speed. Base player values: 20 HP, mana 100 and regen 1.0/s (`mana.json`). Potion
effects (Empower, Strength, …) join this channel as MULTIPLY_TOTAL/ADDITION modifiers.

**Attack damage, end to end (verified 2026-10-05).** `ATTACK_DAMAGE` is a vanilla attribute. Every
source below reaches it through `VaultGearHelper.getModifiers` (ADDITION) or a potion effect:

| Source | How it reaches the attribute | L100 values |
|---|---|---|
| Player base | vanilla | 1.0 |
| Mainhand weapon | `VaultSwordItem/VaultAxeItem…getAttributeModifiers(slot)` → only if intended for the slot and not broken. Offhand weapons give nothing | implicit / suffix (OMEGA → MYTHIC): sword 90-100 / 20-25 → 100-125 / 25-35; axe 109-132 / 51-57 → 125-150 / 75-100; trident 116-155 / 49-55 → 130-180 / 55-80; rang 60.5-65 / 40-55 → 75-90 / 40-60; battlestaff 60-70 / 20-25 → **50-60** / 35-50 (mythic implicit is *lower* than omega, a likely config slip); crafted suffix on non-mythic; uniques 45-132 implicit, some 49-70 prefixes |
| Deck | `CardDeckItem.getAttributeModifiers` → `getModifiers(deck data)` (deck can't be swapped inside a vault) | AD card 5 at t5 × scaler × cores × greed (`card/modifiers.json`, 4 AD entries) |
| Talents | `GearAttributeTalent.onAddModifiers` | Might 5→22 (learnable), over-level higher (`talents.json`) |
| Greed tree | `GearAttributeGreedNode.onUnlock → onAddModifiers` | one +10 AD node (`greed_nodes.json`) |
| Prestige | `GearAttributePrestigePower` (same bridge) | none with AD in 0.34.1 |
| Empower effect (addon `EmpowerEffect`, from Concentrate/Empower) | +0.10 per amp level, **MULTIPLY_TOTAL** | uncapped via Concentrate |
| Weakness on the player | `ModEffects` WeaknessEffect | negative |

No armor, jewelry, offhand, trinket, charm or unusual affix rolls flat AD. **No "% attack damage"
stat exists**: the percentage lever for melee is `DAMAGE_INCREASE`, applied in the hurt chain (§2.2),
not on the attribute. So:

`AD = (1 + weapon + deck + Might + greed) × Π(1 + 0.1·(amp+1))_Empower`

Per swing: `AD × (0.2 + 0.8·s²)` (attack strength s from attack speed and swing timing) × Better
Combat combo `damage_multiplier` (sword 1/1/1.25; battlestaff 0.8/1/1.2/1.4/0.8/0.8) × 1.5 on a
vanilla crit, then the hurt chain. Battle Cry adds `AD × perStack × stacks` flat at HIGH.

**Crit vs lucky hit:** lucky hit is suppressed by `CritHelper.getCrit`, which only checks
*jump-crit physics* (falling, not on ground). A crit forced by the gear stat
`VANILLA_CRITICAL_HIT_CHANCE` (`forceVanillaCrit`, ×1.5) on the ground does **not** block lucky hits.
Grounded play gets both.

Side note: `VaultGearHelper.getModifiers(UUID, Stream)` (the talent/greed/prestige bridge) uses
`createOrReplaceAttributeValue`, so two instances of the same attribute from one skill collapse to the last one.

**Derived stats.** `iskallia/vault/util/calc/*Helper.java` turns snapshot sums into effective
stats. Each one calls `CommonEvents.PLAYER_STAT.invoke(PlayerStat.X, …)`, which is the hook that
archetypes, ability effects and talents use to change a stat. One helper is one formula, so they
are the right unit for the model:

| Stat | Formula (3.21.6 + addon mixins) | Cap |
|---|---|---|
| Ability power | `AP = ΣAP_flat × (1+ΣAP_percent) × (1+ΣAP_percentile) × PLAYER_STAT(ABILITY_POWER_MULTIPLIER)` — two separate % buckets, sequentially multiplicative | none |
| Cooldown reduction | `cdr = Σcdr × (1+Σcdr_percentile)`, then PLAYER_STAT; ability CD = per-ability flat adj → `−base×cdr` → per-ability % adj | 0.8 + COOLDOWN_REDUCTION_CAP, hard 0.95 |
| AoE | `range × clamp(1+ΣAoE (+PLAYER_STAT, + Colossus), 0, 1+cap)` with per-ability flat/% adjustments either side | cap 0.8 in base; **10.0 via `attribute_cap_overrides.json`** (vhapi) |
| Lucky hit chance | `(Σflat) × (1 + Σpercentile + Σjester_percentile)`, then PLAYER_STAT, then ×1.15^luck / ×0.7^unluck (addon) | 0.562 base; **1.0 via override** |
| Resistance | Σresist + Resistance-effect (amp+1)% + aura + LowHealthResistance talents + Colossus, then PLAYER_STAT | 0.5 + RESISTANCE_CAP (+Colossus etching cap), hard 0.95 |
| Block chance | 0.05 if holding a vanilla shield + Σblock, then PLAYER_STAT, × luck | 0.6 + BLOCK_CAP, hard 0.95 |
| Fatal strike | chance and damage are plain sums + PLAYER_STAT | — |
| Leech | Σleech + PLAYER_STAT | — |
| Double hit | Σchance + PLAYER_STAT; multiplier 2.0 + axe specialisation | — |
| Mana cost | per-ability flat then % adjustments; Ethereal talent rolls cost to 0 | — |

`PLAYER_STAT` writers found (base + addon): Totem Player Damage (AP mult), Ward archetype (block),
Mind Meld (CDR, addon stat-conversion talent), Rampage/Rampage Instinct + etchings (lucky hit),
Rampage Chain (chain), Rampage Leech + Vampire archetype (leech), Barbarian archetype (rage,
healing), Empower (speed), Shell Porcupine (thorns flat). Full map: grep `PLAYER_STAT.of(PlayerStat.`.

## 2. Outgoing damage pipeline

### 2.1 Two damage families

Flags in `iskallia/vault/event/ActiveFlags` tag every hurt event, and handlers branch on them:

- **Melee / "normal attack"** — `WoldEventHelper.isNormalAttack()` = none of AoE, totem, charmed,
  DoT, reflect, effect, AP, thorns, smite, glacial shatter or necro-minion flags is set. Base is the
  vanilla `ATTACK_DAMAGE` attribute × attack-strength scale (+ vanilla crit ×1.5 on jump crits; the
  `VANILLA_CRITICAL_HIT_CHANCE` gear attribute forces it).
- **Ability power (`IS_AP_ATTACKING`)** — set by ~25 sources (Nova, Chain Lightning, Smite, Storm,
  Fireball, Blizzard, Earthquake, Arcane, Necromancy, Mana Barrier, Riftblade, Blast Wave, Charged
  Bolts, grenades, the trident, Percent Burn, …). AP damage **skips `DAMAGE_INCREASE`** and the
  normal-attack multiplier registry, and uses the separate AP multiplier list instead. Mob-type
  damage (undead/champion/…) still applies.
- Abilities that scale off `ATTACK_DAMAGE` (e.g. Wall of Fangs: `ATTACK_DAMAGE × mult + base`)
  are the bridge between the two families. Per-ability classification: §3.

### 2.2 LivingHurtEvent order (Forge priority; same-priority = registration order)

| Prio | Handler | Effect |
|---|---|---|
| HIGHEST | `GlacialShatterEffect.on` | shatter |
| HIGH | addon `echoingHit` | stores/refreshes an echo of the current amount |
| HIGH | `triggerEffectCloudsPassive`, `BonkAbility.on` | |
| NORMAL | `EntityEvents.entityDealCrit` | **mob** crit (CRIT_CHANCE/CRIT_MULTIPLIER attrs) × (1 − player CRITICAL_HIT_TAKEN_REDUCTION) on the multiplier part |
| NORMAL | `PlayerDamageHelper.onPlayerDamage` | × the **damage-multiplier registry**: `(1 + Σ ADDITIVE_MULTIPLY) × Π STACKING_MULTIPLY`, separate lists for normal and AP hits. Writers: Rage, Relentless Strike, Third Attack, Berserk (0.05/stack, max 20), Low Health/Low Mana Damage talents, Berserker/Commander/Ward archetypes (dead), Totem. Rampage is *not* a writer in 3.21.6 (direct ×(1+x) at LOW) |
| NORMAL | `GearAttributeEvents.increaseDamageDealt` (+ addon overwrite) | × (1 + ΣDAMAGE_INCREASE [not on AP] + Σ mob-type bonuses) × **Purist (multiplicative in WV)** |
| NORMAL | `doFatalStrikeAttack` | on proc × (1 + fatal strike damage) |
| NORMAL | `triggerAoEAttack` (ON_HIT_AOE, 60%), `triggerChainAttack` (ON_HIT_CHAIN, 0.5 falloff per jump, range 5×AoE; chaining-penalty etching; addon falloff stat) | secondary hurt events |
| NORMAL | addon `cleavingDamage` (axe spec), `reavingDamage` (%max HP once per mob), `executionDamage` (missing HP × attr; ×0.25 vs champions/elites/bosses, ×0.01 vs the Vessel), `thornsScalingDamage` (+thorns × %), `apScalingDamage` (+AP × %), `bonusDebuffDamage` (AP hits vs debuffed mobs) | flat adds |
| NORMAL | talents: Conditional Damage, Damage/Effect/Cast/Sweeping On Hit; Rage/Relentless helpers; burning hit, hexing | |
| LOW | `RampageAbility.onDirectMeleeDamage`, `EntityReflectTracker` | |
| LOWEST | `VulnerableEffect.on` | target-side × |
| LOWEST | `LuckyHitTalent.doLuckyHit` | one roll, and on success **every** unlocked lucky-hit talent fires. Needs a non-crit, full-charge hit; Ice Bolt can proc it when `allowLuckyHit()`. Lucky thorns need the LUCKY_THORNS attribute |
| LOWEST | `ArcaneNovaOnHitHelper`, `PlayerThirdAttackDamageHelper`, `DiceTrinket`, `TauntAbility`, `GreedMobDamageHandler`, Treasure Hunter archetype | |

**Same-priority order is not fixed by any code: traced through Forge 40.3.11 / EventBus 5.0.3**
(decompiled from the CurseForge install's `fmlcore`, `javafmllanguage`, `eventbus-5.0.3` and the
`forge-…-universal` jar):

1. EventBus keeps one `ArrayList` of listeners per priority and fires each list in **registration
   order** (`ListenerList.ListenerListInst.priorities`). Nothing else sorts within a priority.
2. `@EventBusSubscriber` classes are registered by `AutomaticEventSubscriber.inject`, called from
   `FMLModContainer.constructMod`.
3. The CONSTRUCT stage is a `ParallelTransition` (`net.minecraftforge.fml.core.ModStateProvider`):
   `ModList.futureVisitor` runs every mod's construct as an independent `CompletableFuture.runAsync`
   on the ForkJoin loading pool, with no dependency chaining. **the_vault and woldsvaults therefore
   register their listeners concurrently**, and their relative order inside a priority is a thread race.
4. Within one class, `EventBus.registerClass` iterates `clazz.getMethods()`. The JVM spec leaves that
   order unspecified. HotSpot sorts by method-name symbol address, which isn't guaranteed stable
   across launches.

So the devs never chose an order: putting all of these at NORMAL means "unspecified". Order-sensitive
pairs at NORMAL:
- **Flat adds vs multipliers** (cross-mod race): addon execution, AP-scaling, thorns-scaling, cleaving
  and reaving vs base ×(1+DI)·Purist, the multiplier registry and fatal strike. Execution damage scaled
  or not by (1+DI) is a several-× swing.
- **Samplers vs multipliers** (mostly inside the_vault): `triggerAoEAttack` hurts nearby mobs for
  0.6 × the *current* amount and `triggerChainAttack` for 0.5^k × current. AoE secondary hits run
  `increaseDamageDealt` again (it only exits for `IS_CHAINING_ATTACKING`; the multiplier registry does
  skip AoE), so AoE sampling after DI means DI applies twice on secondary targets. Chain hits skip DI on
  the secondary hit, so for them the order only decides whether the primary's DI is inherited. Same
  question for sweeping-on-hit.
- Multipliers among themselves commute; HIGH (echo store) and LOWEST (lucky hit) are fixed by priority.

**Model treatment:** for the 0.34.1 baseline, compute both orders and report the spread (min/max). For
the greed rework this is worth fixing for real: give the order-sensitive addon handlers explicit
priorities (e.g. flat adds at HIGH so DI scales them, AoE/chain samplers handled deliberately), so the
model can encode the intended order. An in-game debug hit (`HyperBossDamageInstrumentation`) would only
show one launch's outcome, so it's optional.

### 2.3 After the hurt chain

Vanilla `actuallyHurt` → armor via the mixin on `CombatRules.getDamageAfterAbsorb`:
**`dmg × 1600/(1600 + armor²)`** (`StatUtils.getArmorMultiplier`; toughness ignored; applies to mobs
too, so mob armor scaling matters for player DPS) → `getDamageAfterMagicAbsorb` + VH resistance
mixin (`× (1 − resistance)`, players and eternals only) → absorption → `LivingDamageEvent`.

### 2.4 Invulnerability frames (verified against the Forge 40.3.11 `LivingEntity` and Better Combat 1.6.2)

Vanilla `hurt`: LivingAttackEvent (raw amount, fires even for hits that get clipped) → shield → **if
`invulnerableTime > 10`**: a hit with `amount ≤ lastHurt` is dropped entirely (no LivingHurtEvent, so
no procs), a larger one deals only `amount − lastHurt` (raw), and `lastHurt = amount`; **else**
`lastHurt = amount`, `invulnerableTime = 20`, full hit. `lastHurt` is the **raw pre-event amount**, so
clipping compares raw numbers and the multipliers then scale whatever got through. Mobs tick
invulnerability down by 1 per tick, so the clip window is ~10 ticks after a full hit.

| Source class | Behaviour |
|---|---|
| Melee (Better Combat, `allow_fast_attacks` default true) | BC zeroes `invulnerableTime` on every swing target before `player.attack`: **never clipped**, and it opens a new window with lastHurt = raw melee |
| `DamageUtil.shotgunAttack` users (Ice Bolt, Charged Bolts, Smite, Blizzard storm, Glacial Shatter, Poison override, VH DoTs, addon echo procs and Reverberation) | save/zero/restore: **always land in full and don't disturb the window** |
| Wall of Fangs / proc fangs (addon) | zero before the hit: land in full, then **become the new baseline** (clip what follows) |
| Javelin | its own hit can be clipped; it zeroes invulnerability *after* hitting |
| Plain `hurt` (Nova, Implode, Arcane, Rail, Prism, Chain Lightning, Earthquake (not landmine), Shield Bash, Riftblade, Mana Barrier, Stonefall, Percent Burn, Flexible Smite/Implode, Rang, Trident) | within 10 ticks of a full hit they deal `max(0, raw − lastHurt)`. Small sources (burn ticks) next to melee are usually dropped completely, procs included |
| On-hit AoE / chain / Cleave spill | never re-hit the primary target; on secondary mobs they're plain hurts under those mobs' windows |

Model rule: per target, track `lastHurt` and window; each event goes `raw → clip → hurt chain`. For
single-target DPS, melee + shotgun sources stack freely; plain-hurt abilities only add their excess
over the last melee/fang raw value.

**Hit cadence (verified 2026-10-07, BC 1.6.2 decompile + pack `configureddefaults/config/bettercombat/server.json5`).**
- Melee is client-paced. Swing period P = k + U ticks, with D = 20/AS, k = max(1, ceil(0.75·D)) and
  U = max(1, round(0.25·max(D, attack_interval_cap = 2))). The 0.25 is weapon upswing 0.5 × upswing_multiplier 0.5
  (`MinecraftClientInject:260-296`, `PlayerAttackHelper:48`).
  - Resulting swings/s: AS 4 → 4, AS 5–7.5 → 5, AS 7.5–15 → 6.67, AS > 15 → 10 (the hard cap).
  - Holding and clicking use the same gate. Combo only picks the damage multiplier.
  - No VH/WV code caps or rescales ATTACK_SPEED.
- Smite: one bolt every `intervalTicks` (Archon: 2 ticks at every tier, so 10 bolts/s). Each bolt hits one
  uniformly random living entity whose box meets a 0.8×AoE sphere at the player's feet (`AbstractSmiteAbility:264-325`).
  The boss gets 10/n bolts/s with n candidates. Echoing Smite / Strike Delay etchings add one delayed copy per bolt.
  The model takes n = 2 (knob `smite_targets_in_range`).
- **Hyperboss** (`VaultBossEntity` in a hyper vault): the rune shield (40 runes, cooldown 400 / duration 100,
  `vault_rune_boss.json:138-146`) rejects every non-bypass hit for 100 of every 500 ticks. That is −20% boss DPS
  (knob `boss_shield_downtime`). Resistance III (−60%) applies while fight adds live; not modeled.
- **Greed Vessel** (`TheVesselEntity:301-328`):
  - It dodges a player hit before `super.hurt` (no events, no procs): 25% chance, at most once per 20 ticks,
    and only while not attacking.
  - It sits dormant for 410 ticks per phase. The dormant boss still absorbs Smite bolts.
  - This likely explains why in-game Smite and fast melee look slower on the Vessel than the raw cadence.

## 3. Incoming damage / survivability pipeline (player as target)

`LivingAttackEvent` (cancels the hit outright): Shell Quill / thorns reflect (HIGHEST/HIGH) →
**block** (addon overwrite: block chance roll, works without a shield, Safer Spaces stacks) →
fire/fall/kinetic immunity, damage-immunity trinket, Ice Armour, Shell, Taunt Charm, Ghost Walk.
`LivingHurtEvent`: addon **dodge** (`DODGE_PERCENT` + Sneaky Getaway etching, ×luck, cap 0.95) →
mob crit vs crit mitigation → lightning capped at 10% max HP in vaults → Immortality.
Then armor `1600/(1600+a²)` → resistance (cap 0.5 + caps) → absorption → `LivingDamageEvent`:
Castle Bastion, Mana Shield, Ultimate Shield (addon), Low Health Resistance.

Sustain: leech (`PlayerLeechHelper`, on LivingDamageEvent), on-kill heal (+healing effectiveness),
soul leech (flat on melee kill), health/mana-leech lucky-hit talents, regen effects,
`PlayerRecoveryHelper` (healing effectiveness reads the **snapshot**, not `HEALING_MAX`).

So EHP for the model = HP / ((1−block)(1−dodge)(armor mult)(1−resist)), plus crit mitigation and
sustain. Note that block and dodge are rolled per hit.

## 4. Proc and loop mechanics the model must represent

Status in 0.34.1: the
fang/echo double-dip fix (addon `3561585d`) **is in the release**. The fix adds a
`IS_PROC_FANG_ATTACKING` flag that skips DI, fatal strike and the multiplier registry for proc fangs.

- Echoing (`ECHOING_CHANCE`/`ECHOING_DAMAGE`, addon) re-hits the stored amount.
- Fanged Strike lucky-hit talent spawns fangs from the event amount.
- Concentrate converts mob effects into uncapped Empower stacks (+10% ATTACK_DAMAGE per amp,
  MULTIPLY_TOTAL), and that feeds melee and Wall of Fangs.
- Damage lucky hits multiply on top of each other: Fatal Strike ×(1+DI) (Greater Fatal Strike ×5 is hidden in 0.34.1, so it can't be learned)
  × Executing Strike.

The model needs an expected-value treatment for the chance-based ones (lucky hit, fatal strike,
double hit, echo, chain/AoE counts), and an explicit loop-gain check for echo×fang-style feedback.

**Echo eligibility (verified 2026-10-07, addon `LivingEntityEvents:545-667`).** Echo skips projectile
sources, proc fangs, DOT/LEECHING/AOE/REFLECT/CHARMED/EFFECT hits, and AP hits unless they also carry
FIRESHOT, ARCANE_RAIL, TOTEM or SMITE_BASE. So Fireshot, base Smite (and its etching bolts), Arcane Rail and
AP Totem can echo; Fireball, Volley, Smite Archon/Thunderstorm, Nova, Frost Nova, Arcane and Prism cannot.
Replays carry only ECHOING + UNLUCKY, so an AP echo gains DI, the multiplier list and execution (ECHO-FLAGLOSS),
but never lucky hits.

## 5. Gear / equipment

Roots: **P** = `release_0.34.1\pack\config\the_vault\`, **A** = addon `generated\resources\data\woldsvaults\vault_configs\`,
**B** = `the_vault_3216_full\iskallia\vault\`, **J** = addon `main\java\xyz\iwolfking\woldsvaults\`.
Extractor prototypes that print every tier open at L100 per file/group: scratchpad `dump.py`, `dump2.py`
(session scratch; to be rewritten as the real extractor).

### 5.1 Slot model (the clone player's inventory)

| Slot | Count | Fits | Notes |
|---|---|---|---|
| Head/chest/legs/feet | 1 each | armor types | |
| Mainhand | 1 | sword, axe, battlestaff (=SWORD), rang (=SWORD), trident (=AXE), bow (=AXE) | Better Combat weapon_attributes in `kubejs/data/bettercombat/weapon_attributes/` (combo `damage_multiplier`s, e.g. battlestaff 0.8/1/1.2/1.4/0.8/0.8, sword 1/1/1.25; battlestaff two_handed) |
| Offhand | 1 | shield, focus, wand, plushie (=FOCUS), loot sack (=FOCUS) | A mainhand weapon in the offhand contributes **nothing** to the snapshot (base intended-slot check). `MixinAttributeSnapshotCalculatorBC` (which would add it) is **not listed in `woldsvaults.mixins.json`**, so it never loads. `…NoBC` (drops offhand stats under a battlestaff) is listed but `@Restriction`-gated by `NoBetterCombatTester`, so it's off with Better Combat installed. Net in 0.34.1: **battlestaff + shield/focus/wand offhand stats both count** |
| Curios | necklace, ring, charm, deck, belt, back, head, trinket_pouch: size 1 each (`defaultconfigs/curios-server.toml`) | | |
| Trinket colour slots | 0 base, opened by the equipped pouch (`P/trinket_pouch.json` `TRINKET_POUCH_CONFIGS.*.SLOT_ENTRIES`); Prismatic = 2 red + 2 blue + 2 green | trinkets | **no duplicates** (author-confirmed; the enforcing code wasn't located) |
| Charm | 1 | god charm (`VaultCharmItem`) **or** affinity charm | |
| Deck | 1 | card deck | |

Gear above the player's level is ignored by the snapshot.

### 5.2 Rolling at L100

- Affix count (`B/gear/VaultGearRarity`; MYTHIC added by `J/mixins/.../enum_extension/MixinVaultRarityEnum`):
  OMEGA 6 armor / 5 weapon-shield-wand-focus; MYTHIC 7 / 6. Split count/2 each side, odd remainder random.
  Necklace: 1 suffix.
- Implicits: one roll per `group` (e.g. armor value + one `BaseBonusPool` pick, weighted by tier weight).
- Explicits: group picked **uniformly** among rollable groups, tier weighted among tiers open at the
  level (`minLevel ≤ L ≤ maxLevel`, maxLevel −1 = open-ended). The top tier is not guaranteed.
- Above-L100 tiers only via tier jumps: legendary at identification +2 (`P/gear/gear_common.json`
  `legendaryModifierChance` 0.02 + expertise), imbuement +1 once (failure corrupts, `P/imbuement_altar.json`).
  Sword AD suffix: L100 25-35 (mythic), mythic L102 55-75. The model needs a "roll quality" knob:
  typical / best-at-L100 / jumped.
- Mythic → `<type>_mythic.json` (`MixinVaultGearTierConfig`). Mythic files exist for all armor, every
  weapon, shield, focus, wand, plushie, loot sack, magnet, map, plus addon unique/necklace mythic.
  Mythic explicits are level-independent (single minLevel-0 tier + 101/102 tiers). Mythic `CRAFTED_*`
  groups are empty (no crafted affixes on mythics).
- Crafting (`P/gear/gear_modification.json`): crafted prefix/suffix on non-mythics; addon
  `add_unusual_modifier` adds one `UNUSUAL_PREFIX/SUFFIX` (addon-only groups, cross-class stats such as
  leech, DI, CDR, AP% on weapons), max one per item.

**Numbers:** `P/gear_modifiers/<type>[_mythic].json` → `modifierGroup.<GROUP>[] = {attribute, group,
identifier, tags, tiers:[{minLevel, maxLevel, weight, value:{min,max,step} | object}]}`, with addon
groups appended (§0).

- **Legendary (verified 2026-10-07):** on identifying vault-generated loot gear there is a 2% chance (+1/2% from
  Fortuitous Finesse) to move one random prefix/suffix to the highest open tier **+2 tier indices**
  (`GearRollHelper:146-148`, `VaultGearLegendaryHelper:100-126`). On the necklace (always SCRAPPY, one ability-level
  suffix) that turns +2 into +4. No crafting route exists for necklaces. The optimizer follows the author's ruling
  that late-game necklaces are legendary (end/max stages). Aspect of Mastery (greed 12) doubles positive ability
  levels on top, so +4 shows as +8 at the max stage.
- **Roll quality (author ruling 2026-10-07):** 60% of the roll range at early/mid, 100% at end/max, snapped to the
  config `step` (0.01 for percent stats, i.e. whole percents in game).

### 5.3 Combat affixes (top open tier at L100, OMEGA → MYTHIC; representative, full data in the files)

| Type | Implicit | Key prefixes | Key suffixes |
|---|---|---|---|
| Helmet | armor 28-30→31-40; one of crit-mitigation / AP 45-50→50-80 / lucky 5-6→6-8% | armor, health 6→6-10, resist 13-16→14-18%, DI 29-35→32-42%, AP% 16-20→18-25%, mana% | CDR 18-20→20-22%, AoE 17-20→20-25%, mana regen, healing |
| Chest | armor + one of thorns / AP / mana / block 9-12→10-15% (mythic adds soul leech) | as helmet + dodge 6-8→6-10%, +1–2 to a named ability | CDR, mana regen, healing |
| Legs/boots | armor + bonus pool | as helmet | CDR, mana regen |
| Sword | AD 90-100→100-125, AS −2.29→−2.24 | lucky 9-10→10-12%, chain 5-6→6-7, champion dmg | AD 20-25→25-35, AP 17-20, echoing 15-18→17-20%, sweep, AS% 21-25→24-30% |
| Axe | AD 109-132→125-150 | double hit 30-40%, lucky 16-18→15-24%, reaving 25-28%, on-hit AoE 3→4-6 | AD 51-57→75-100, echoing 20-24→23-28% |
| Battlestaff | AD 60-70 + AP 42-46→60-90 or lucky + mana regen or resist 19-20% | block 18-24%, AP% 18-21→20-25% | CDR 26-28→28-30%, AoE, echoing, AD |
| Trident | AD 116-155→130-180 | wind-up, channeling | AD 49-55→55-80 |
| Rang | AD 60-65→75-90 | piercing, returning dmg 56-65%, ricochet | AD, echoing |
| Shield | block 26-30→28-35% or thorns | health, thorns, resist | lucky |
| Focus | mana 91-100→100-150 | +ability levels | CDR 26-28→27-31%, mana regen, lucky |
| Wand | AP 46-50→50-90 | AP%, +ability levels | CDR 26-28→28-34%, AoE, effect duration |
| Plushie | effect implicits | crit-mitigation, AP, DI, resist, chain | AS%, mana regen, lucky |
| Necklace | — | — | +1/+2 to one of 29 abilities |

Addon-defined attributes (`J/init/ModGearAttributes.java`, 81 incl. non-combat): echoing chance/damage,
reaving, execution, hexing, burning hit, chaining damage, dodge, soul leech, piercing, returning damage,
ricochet, trident_*, AP scaling, thorns scaling, effect-cloud chance, second judgement, javelin implode,
breaching, unique_effect. Base registry: 164 attributes in `B/init/ModGearAttributes.java`.

Gear-granted potion effects (`the_vault:effect` values): almost all utility. Combat-relevant ones:
`minecraft:luck` I and saturation as CORRUPTED_IMPLICITs on every type, `resistance` I and `regeneration`
I on uniques, `unluck` on a unique prefix (mob crits are cancelled against a target with Unluck —
`MixinEntityEvents.cancelCritWhenUnlucky`).

### 5.4 Seals (= Vorpal / CORRUPTED_IMPLICIT outcomes)

- Vorpal Focus at the Vault Sealer (`B/gear/modification/operation/CorruptGearModification.java`).
  The item needs every affix slot filled. 1/10 adds a new slot holding the seal; 9/10 replaces a random
  explicit. The item is then corrupted/locked → **one per item**, any combat gear.
- Cost: Vorpal foci 5 (OMEGA) / **20 (MYTHIC)** (`MixinVorpalSealCost`), 10 for uniques, + gold L×6, painite L×10.
  The source of Vorpal foci was not traced yet (rarity flag pending).
- Pool: +4 to an ability class (offensive/utility/powerup/ultimate) or +2 all abilities, relentless
  strike 8-16%, third attack 0.4-1.2, on-hit AoE +1, CD skip 5-20%, arcane nova on hit, frost nova on
  damage, ability-specific AoE/CD changes, lucky thorns, kinetic immunity. Addon adds phoenix, jester
  lucky% 6-15%, broodmother web, crit mitigation 21-50%, execution 7-10%, burning hit 16-20%,
  javelin scatter, talent levels (focus/wand/shield/necklace).
- **Numbers:** `modifierGroup.CORRUPTED_IMPLICIT` per gear file (pack 29 + addon 11 appended).

### 5.5 Etchings

- One per item, only vanilla equipment slots are read (`EtchingHelper.getEtchings`). The player
  must have greed tier ≥ `minGreedTier`. Bought from the Greed Trader / etching vendor.
- Gear groups `P/gear/etchings.json` `groups`: Offensive = sword/axe/chest, Defensive = armor + shield,
  Utility = focus/wand/shield/helmet/boots (staff/rang → SWORD, trident → AXE, plushie → FOCUS).
- **Numbers:** `ETCHINGS.<id>.attributes[0].tiers[{minGreedTier, maxGreedTier, weight, value}]`, so
  values scale with **greed tier**, which makes greed tier a model axis. Addon `wolds_etchings.json` merges via `putAll`.
- Ability etchings: Frozen Barrage (Ice Bolt recasts 7-10×), Piercing Bolt, Lucky Bolt, Glacial Damage,
  Nova Recast, Nova low-mana +80-120%, Smite Echo 70-120%, Earthquake recast, Lightning repeat, Ball
  Lightning triple/size/kill-CD, Arcane/Rail pierce, Scatter pierce, Lucky Vortex/Frost Nova/Vulnerable,
  Rampage lucky +7-12%, Totem AD/AP, Colossus Titan (resist cap), Chaining Penalty, Poison mana.
- High greed tier: Lucky Reset g12, Pyramid Scheme g9 (sword), Ravenous Fangs g8, Blessed g7,
  Sneaky Getaway Ninja g7, Ingenium g6 (+3 all talents). Tier-4 group: Reverberation, Colossus Titan,
  AD/AP Totem, Landmine, Prism Split.
- **Stacking (verified 2026-10-07):** etchings do **not** stack.
  - ~60 base call sites and all 15 addon etchings read `findFirst` or `hasEtching`. "First" means slot order
    MAINHAND → OFFHAND → FEET → LEGS → CHEST → HEAD.
  - The exceptions are Nova Low-Mana Damage (`forEach`, compounds ∏(1+mᵢ), NOVA-LOWMANA-STACK) and Lightning Orb
    Size (1 + Σv).
  - Ingenium and Pyramid Scheme are snapshot-summed but fit one item only.
  - The optimizer allows one copy of each etching, except those two.
- **Mitosis** (Volley, addon `MixinVaultFireball:49-86`):
  - On every bounce it spawns N one-shot BASE children (N = 1 / 2 / 2–4 by greed tier). Children never split again.
  - Every Volley explosion, parent and children, runs at 0.5× AP (`MixinAbstractFireballAbility:38-40`).
  - The mixin calls `explode()` on **every bounce even without the etching** (VOLLEY-BOUNCE-EXPLODE).
  - Against one boss, plain-hurt i-frames limit a cast to about 5 explosions.
  - The pack gain uses the author's in-game estimate of 10–15× per cast (knob `mitosis_multiplier` 12).

### 5.6 Trinkets and charms

- Trinkets: `P/trinket.json` `TRINKETS.<id>.config`, addon `A/trinkets/wolds_trinkets.json` overrides by `put`.
  Red (combat): Clover +25% lucky, Cufflinks +50% CDR, Spellbook +50% AP (AP_PERCENTILE), Crystal Ball
  +100% mana, Giant's Heart +25% HP, Phylactery +50% mana regen, Vibrating Stone 10% echo/+25% echo dmg,
  Healing Salve, The Dice ×0.01–3 per hit (LOWEST), Immortal Seal. Blue: Swift Amulet 15% dodge,
  Stone of Jordan +1 all abilities. Greed trinkets carry 2 effects.
- God charm: `P/gear_modifiers/vault_charm.json` `godModifiers.<GOD>`, prefixes 1/2/3/4 by rarity, value ×
  (godReputation+1) (`B/gear/GodCharmRollHelper`) × (1+vault_charm_effectiveness). The reputation range
  at late game is still open.

### 5.7 Uniques, sets, enchants

- Uniques: `library\UNIQUE_GEAR_CATALOG.md` (62 uniques). Only the imbue +1 tier jump applies to them.
  Unique-only attributes (javelin implode, dripping lava, second judgement, phoenix…) need hand-coded mechanics.
- `P/sets.json` and `P/etching.json` are **legacy, loaded by nothing** in 3.21.6. Ignore them.
- Combat enchants cost command blocks (`P/gear_enchantment.json`), so they are unobtainable. Ignore them.

### 5.8 Decks

- One deck. Per card (`B/core/card/CardDeck.getSnapshotAttributes`): **roll(tier) × scaler frequency ×
  core multiplier × greed multiplier**; a failed condition gives 0.
- Cores additive in WV: `1+Σ(v−1)` × Π multiplicative cores (`J/mixins/.../MixinCardDeck.getModifierValue`).
- **Greed in 0.34.1 = the 3.21.6 bug** (`max(1, Σ raw multipliers)`). The additive fix `d8d69080` is
  not in the release (verified with merge-base). Model it as a switch, since the rework ships the fix.
- Numbers: shapes `P/card/decks.json` `values.<deck>.layout[].value` (O slot, A arcane, X none) +
  `socketCount`, addon decks (wold 24 O + 3 A, 4 sockets; snake/crate/wall/fairy) `A/card/decks/wolds_decks.json`;
  cards `P/card/modifiers.json` `values.<id>.{type:"gear", attribute, pool:[{min,max,tier}], groups}`
  (card families matter: positional slots of an EVO deck take only **Evolution** cards, pool `scaling`:
  t5 AD 3, AP 3, DI 1.3%, AP% 1.25%, HP% 1.25%, armor% 1%, resist 1%, CDR 1%, lucky 0.5%, AS 1.2%, AoE 1%, mana 10, regen 8%;
  t3 is roughly 0.6× of that. The typeless "Stat" cards (pool `default`: AD 5, DI 2.5%, armor% 5% at t5) fit only
  typeless T slots at 1×. Deluxe cards (pool `deluxe_stat`) are fixed, e.g. AD 8, DI 3.5%, armor% 5%. The first
  optimizer build wrongly priced every slot with the typeless values, about 5× too high); `scalers.json`, `conditions.json`; cores
  `P/card/deck_modifiers.json` + `A/card/deck_mods/new_cores.json`; implicits `A/implicit_deck_modifiers/wolds_implicits.json`.
- DeckFAST already solves layout, positional types, cores (incl. Construction/Arcane placement),
  implicits, foil/shiny and greed (additive = post-fix rule). It does **not** do per-attribute
  values, mixed-stat decks, conditions, or the conversion to player stats; it optimizes one scalar
  (NDM). Plan: reuse its kernel for "multiplier per slot", then layer the attribute assignment on top.
  Its scaler rules have not been cross-checked against `scalers.json`.
- **Deck model (2026-10-07).** The optimizer uses fixed EVO layouts that DeckFAST computed under 0.34.1 rules
  (driver `wv-combat-model/extract/deckfast_run_layouts.py`). It only anneals which stat card sits in each slot.
  - `wv-combat-model/model/deck.py` ports the tagged kernel's slot formula and reproduces DeckFAST's NDM exactly.
    It is base × greed boost × mirror × (1 + Σ cores + implicit addends). The implicit is the only card-dependent
    term: the Anvil's +100% applies to Defensive cards only.
  - **The additive Archive (wv `aa5e7b39`) is in 0.34.1** (merge-base checked). So the July panel NDMs (Rook 7085,
    Mystery 10120) were never reachable in the release; that Rook layout scores 4332.
  - Re-run layouts and NDMs:
    | Stage | Deck | NDM | Cores / implicits |
    |---|---|---|---|
    | early | Anvil, no greed | 875 | — |
    | mid | Anvil | 3299 | — |
    | end | Rook | 4352 | color, foil, pure |
    | max | Mystery, Greater Construction layout | 9037 | Cake + Runic, 1 core left after Construction |
  - DeckFAST adds +1 core (expertise) to socketCount.

## 6. Point-spend systems (abilities, talents, expertises, prestige, archetypes, greed tree)

Roots as in §5 (`P`, `A` = addon `src\`, `B`).

### 6.1 Point economy (the clone player's budgets)

| Pool | Amount at late game | Spent on | Source |
|---|---|---|---|
| Skill points (**shared**) | 100 (+1/level to L100, none above) + 9 (3 greed nodes × 3) + 1 quest ≈ **110**; addon Skill Orb +1 each (no source found) | abilities **and** talents | `B/skill/PlayerVaultStats.addVaultExp` |
| Expertise points | 20 (+1 per 5 levels) | expertises: none combat-relevant | |
| Knowledge points | Knowledge Stars, shared with research | prestige powers | |
| Greed nodes | `greedTier × 3` (`B/greed/GreedTree.getMaxUnlockableNodes`), +1 tier per Greed Trial | greed tree (113 paid nodes) | |
| Archetype points | Archetype Stars | one archetype | see 6.5 |

Maxing everything would cost about 285 points for abilities (one spec each) and 332 for the 64
visible talents, against a budget of about 110. That scarcity is what keeps the enumerator honest.
Level cap is `100 + 25×greedTier`, but levels above 100 grant no points.

**Tiers.** `maxLearnableTier` (point-bought) vs `tiers[]` length (30 for most abilities, 12–31 for
talents). Effective tier = learned + bonus. Bonus comes from gear `the_vault:ability_level` /
`talent_level` (target: an id, an AbilityType group via `abilities_group.json`, `all_abilities`,
`all_talents`), clamped to the tier list. The **Masterful** prestige doubles positive ability-level
gear. Bonus sources: necklace +1/+2, chest/focus/wand prefixes, Vorpal +4 class / +2 all, Stone of
Jordan +1 all, Ingenium etching +3 all talents, Potent Elixir. Abilities can therefore run t8 → t30.
Addon bug: `abilities/group/wolds_abilities.json` lists `Ultimate_Shield`, but the id is
`UltimateShield_Base`, so it gets no ULTIMATE-group bonus.

**Specs:** one active spec per ability (`SpecializedSkill`); the learned tier carries over on switch.

**Gates** (`P/skill_gates.json` + 3 addon overlays under `A/generated/.../skill/gates/`, vhapi replaces per id):
talent_points_spent thresholds 5/15/25/40; exclusive pairs Berserking↔Last Stand, Ethereal↔Quickening,
Potent↔Healthy Elixir; **Arcane / Execution / Fanged Strike: max one** (40 spent + Mana Steal /
Fatal Strike / Life Steal); Stack Master needs 40 spent + a stack talent; Battle Trance←Lunge←Strength;
Blazing←Momentum Engine; Mind Meld←Intelligence. Only talents with a GUI style can be learned
(addon `talent_gui/overwrite/wolds_talents.json`, 64 entries). Hidden examples: Critical_Strike, Fatal_Strike
Chance/Damage, Thorns_*, *_II variants, Axe/Sword Specialisation.

**Numbers:** `P/abilities.json` `tree.skills[i].specializations[j].{maxLearnableTier, unlockLevel, tiers[k].{learnPointCost, <stat keys>}}`;
`P/talents.json` `tree.skills[i].{maxLearnableTier, tiers[k].*}`.

### 6.2 Abilities (34 base + addon; 30 tiers each; max learnable 8 at 1 pt/tier unless noted)

| Ability (specs) | Scales on (t1→t8) | Family |
|---|---|---|
| Fireball / Volley / Fireshot | AP × 0.4→1.8 / 0.25→1.3 / 0.2→0.9, radius | AP+AoE |
| Nova / Slow / Dot | AP 0.6→1.3; Dot 0.5→1.9 | AP (Dot = poison-nova flag, AP-like) |
| Smite / Archon / Blast Wave / Thunderstorm | AP min–max per bolt (Base 1.1–5.5 @t8), interval, mana/s + per bolt | AP+smite |
| Arcane / Rail / Prism | AP 0.05→0.4 per tick (10 mana/s); Rail 2.4–2.9; Prism 0.39 | AP |
| Chain Lightning / Orbs / Charged Bolts | AP ranges | AP+AoE |
| Storm Arrow / Blizzard | AP min–max / %AD | AP |
| Stonefall Snow | AP 0.2→1.6 | AP+AoE |
| Toxic Grenade | AP 0.2→1.6 + poison | AP |
| Necromancy (3) | AP 0.14→0.23 per minion shot, cap 1→8 minions | AP+minion |
| Javelin / Piercing / Scatter | %AD 0.86→2.12 | javelin flag, **gets DI** |
| Earthquake (Base/Singularity/Tremor; Landmine cost 999 = disabled) | %AD 0.5→1.4 × shocks | **AD-scaled under the AP flag → no DI** |
| Grenade / Sticky | %AD 0.75→2.5 | AD-scaled, AP+AoE flag |
| Ice Bolt / Blast / Shard Blizzard | damagePerBolt + %AD, splinter | arrow source = normal pipeline; can lucky-hit; Frozen Barrage etching recasts 7–10× |
| Dash Damage | %AD 0.25→2.0 per dash | AoE |
| Implode / Life Tap | %mana 0.4→1.1 / dmg per HP | AoE |
| Shield Bash (4) | block-chance scalar, thorns scalar, Retribution per stack | AoE / thorns |
| Taunt Charm | charmed mob does %AD | charmed |
| Totem (Player Dmg / Mana Regen / Mob Dmg) | +player dmg 0.2→0.9 (non-AP registry); Mob: % max HP per interval | |
| Rampage (Base/Bloodlust/Berserker/Instinct; Leech/Chain cost 999) | dmgIncrease 0.25→1.0 (Berserker 8.0 @t30), toggle mana/s | direct ×(1+x) on full-charge non-crit melee (3.21.6) |
| Battle Cry (3), max 4 | stacks of AD / AP / lucky | |
| Execute, max 4 (costs 4,1,1,1) | **non-functional**: `doAction` always fails (§6.2.1) | excluded |
| **Addon** Fangs (Wall / Maw) | `ATTACK_DAMAGE × mult + base` (0.25×+4 → 0.6×+24; 2.5× @t30), waves, execute threshold | full melee pipeline incl. DI and lucky |
| **Addon** Concentrate / Expunge | uncapped Empower amp (§4) | |
| **Addon** Colossus / Sneaky Getaway (2 pts/tier, max 2) | +20→30% resist (+cap via etching), AoE / dodge | |
| **Addon** Ultimate Shield (max 8) | absorbs 12.5→100% of damage at 4→2.25 mana per point | LivingDamageEvent LOWEST |
| Defensive/utility | Empower, Heal, Mana Shield/Barrier, Shell (thorns specs), Ghost Walk, Taunt, Dash, Summon Eternal | |

`MeteorStormAbility` is registered but has no config entry, so it's unused.

#### 6.2.1 Exact formulas (read 2026-10-05; tiers T1 / max learnable / T30)

Notation: **M** = multiplier registry (non-AP; the AP list is always empty, so ×1). **E** = the
`increaseDamageDealt` stage (×(1+DI if not AP + mob-type)·Purist). "Baked" = the formula multiplies
inside the ability before the hurt event.

- **Javelin** (`AbstractJavelinAbility:60-66`): `AD × (pct + JavelinDamage talent 0.1→0.8)`. Base pct
  0.86/2.12/8.94; Piercing 0.34/0.79/2.22 with pierce 2/9/53; Scatter 0.20/0.44/1.21 × 3/5/11
  javelins, which split **only on block hits**. Flag IS_JAVELIN only → E and M apply, lucky hit is
  **blocked**, i-frames are reset per hit. Etchings: Scatter Pierce (+2 javelins × v), Piercing
  Ricochet (copy × v **per pierce, compounding** D·v^k; the code multiplies by v, the tooltip says
  "reduce"). Addon Imploding Javelin (Base only): casts the player's Implode spec at the target,
  `currentMana × percentManaDealt × distance falloff`, no mana cost.
- **Ice Bolt** (`AbstractIceBoltAbility:74-83`): `damagePerBolt + AD×(1+DI)×pct×M` (4/13/35 flat,
  pct 0.15/0.50/1.60, mana 2/3.4/7.8, CD 20 ticks). The hit runs inside IS_AOE, and E still applies →
  **DI is applied twice** on the AD part. With the **Lucky Bolt** etching: M is not baked in and the
  hit isn't AoE-wrapped, so M applies once (at the hurt stage) but **DI is still doubled** (baked
  DI + E). Lucky hit is allowed on paper (crit and attack-scale gates bypassed), on-hit AoE/chain can
  proc, and the Glacial Shatter trigger stops working (LUCKYBOLT-SHATTER, §6.8). Splinters: only vs already-Chilled targets, chance
  0.10/0.45/0.75, 4 bolts × 0.5, never hit the primary target, never lucky. Piercing Bolt: hits v+1
  entities. **Frozen Barrage**: v = 4–10 extra full `onAction`s at +1…+v ticks; each **pays full mana**,
  rolls cast failure, recomputes damage and rolls lucky hit separately; recasts don't recast or touch CD.
- **Ice Bolt Blast deals 0 damage in 0.34.1**: the addon `MixinIceBoltChunkAbility` @Overwrites
  `doAction` without setting damage (Chilled + Glacial Shatter only). This makes the **Glacial Damage
  etching dead**.
- **Shard Blizzard** (separate class, Frozen Barrage does not apply): per shard
  `damagePerShard + AD×(1+DI)×pct×M` (1/2/7.5, pct 0.04/0.08/0.256), IS_AOE → **DI twice** again; one
  shard per interval+1 ticks (8/5/5) over 120/180/268 ticks (× effect duration). Blizzard Damage
  etching adds AP × v (AP+AoE flags).
- **Execute (ability) does nothing**: `ExecuteAbility.doAction` always returns `fail()`. Its
  description says "Deals a percentage of target's total hitpoints in damage on your next hit"
  (`damageHealthPercentage` 0.25/0.5/0.75/1.0 for T1–4, CD 1200 → 400 at T12). Nothing reads
  `damageHealthPercentage`, there's no "next hit" listener, and it's absent from the ability GUI. It
  was already a stub in the_vault 3.21.5, so upstream VH disabled it and WV never restored it.
  **Excluded.** Intended version, if revived: next hit + `pct × target max HP`, which would need a
  boss multiplier like the ones below.
- **Every other execute source (all functional):**

  | Source | Effect | Target multiplier |
  |---|---|---|
  | `execution_damage` gear attr (addon `executionDamage`, NORMAL, normal melee only, not projectile/proc fang) | + missing HP × attr (seal 7–10%) | champions/infernal/elites/bosses ×0.25 *of the whole amount*, the Vessel ×0.01 |
  | Execution Strike talent T1–3 (`execution_lucky_hit`, on lucky hit) | + missing HP × 0.15/0.16/0.17 | `MissingHealthDamageHelper`: Vessel/Artifact 0.1, special 0.25, champion/infernal 0.5, Gaia 0.75 |
  | Execution Strike **T4–T29** (over-level only) | type switches to `damage_lucky_hit`: × (1 + 0.18 → 0.43) | none. Over-levelling replaces missing-HP damage with a small flat multiplier, a likely config slip |
  | Executioner talent (`low_target_health_damage`) | × (1 + 0.2/0.4/0.6, T31 1.9) vs targets < 50% HP | none |
  | Wall of Fangs `executeThreshold` (addon `CustomFangEntity`) | target ≤ threshold% HP → hurt for `Float.MAX_VALUE` (instant kill); Ravenous Fangs etching doubles the threshold | Vessel and VaultBoss are exempt (heart fragment instead). The same threshold is also reused as the heart-fragment drop chance |

  **Bug (BUG-EXEC-GEAR):** the gear-attribute handler computes `(amount + missingHP×attr) × 0.25`
  for champions/infernals/elites/bosses (×0.01 for the Vessel). The penalty hits the *whole* hit,
  base damage included. Any non-zero execution roll therefore cuts melee damage vs a full-HP boss to
  25% (Vessel 1%). Reaving (`MaxHealthDamageHelper.applyScaledMaxHealthDamageBonus`) and the
  talent (`MissingHealthDamageHelper`) scale only the bonus. Intended: `amount + missingHP × attr × mult`.
  The two helper tables also disagree (Vessel 0.1 vs 0.01, champion 0.5 vs 0.25).
- **Totem Mob Damage** (`TotemMobDamageTileEntity:121-143`): `(AP [+ AD×M with the AD etching]) ×
  0.10/0.10/0.54` per mob per interval (110/40/40 ticks), radius 17/24/24, duration 850/1200/2300.
  Without the etching: AP+TOTEM flags (no DI). With the AD etching: TOTEM only → E and M apply on top,
  so **M applies twice** to the AD part.
- **Totem Player Damage**: `+0.20/0.90/3.10` ADDITIVE_MULTIPLY into M while in radius (6.5/24/46). It
  reaches every formula with M baked in plus all unflagged/javelin hits. AP Totem etching: AP multiplier
  ×(1 + that value).
- **Shield Bash** (`ShieldBashAbility:182-202`): `(AD×(1 + block×scalar) + AD×etching v) × M`, scalar
  Base/Battering Ram 0.6/2.0/6.4, Earthshatter 0.4/1.24/3.88; IS_AOE → E applies, lucky blocked; cone
  55° (Earthshatter = front half-plane), radius 3 × AoE; CD refund 20 ticks per hit (max 80%). Battering
  Ram adds `(AD×thornsMult + thornsFlat) × 0.5/0.85/1.95`; `ThornsHelper.getThornsDamageMultiplier`
  does `x += PLAYER_STAT(x)`, so **gear thorns damage counts twice** when no listener changes it.
  Retribution: `thornsFlat × 0.01/0.08/0.30 × stacks` (stacks from hits taken during a 200-tick charge:
  champion 6, tank 3, assassin/guardian 2, horde/illager 1), one AoE burst, radius 3/10/32.
- **The Dice**: every player-sourced hurt, no flag checks, LOWEST: × U[0.01, 3.0), mean **1.505**. It
  applies to AP, AoE, totem and splinter hits too.

Not determined: vanilla i-frame interaction for non-shotgun repeat hits (Shard Blizzard every 6 ticks,
Shield Bash right after a melee hit); the addon Hypothermia modification values.

### 6.3 Talents (visible, combat)

All numbers are in `P/talents.json` `tiers[k].<key>`. Gear-attribute talents enter the snapshot
like gear, so they sit *inside* the % buckets.

| Type | Talents (t1 → max) |
|---|---|
| `gear_attribute` | Intelligence AP 8→32, Might AD 5→22, Speed, Lingering Fumes (effect duration), Medic (healing), Lunge (range), Champion Mastery (+50% vs champions), Stack Master (+10 stack cap) |
| `player_stat` | Assassin Mastery stun, Dungeon Mastery +25% resist vs dungeon mobs |
| `purist` | +10→40% per scrappy armor piece, **multiplicative** in WV |
| high-HP (≥80%) | Sorcery mana regen, Prime Amplification AoE 0.05→0.4, Stoneskin |
| low-HP / low-mana | Berserking +100% <20% HP ↔ Last Stand −50% taken; Depleted +25→100% <20% mana (additive registry, non-AP); Methodical healing |
| target-conditional (each its own ×) | Executioner ×1.2→1.6 vs <50% HP; Daze ×1.25→2 vs stunned; **Hexbreaker ×1.2→1.8, AP hits only**, vs debuffed |
| stack-on-hit (addon) | Battle Trance +3% DI/stack (4→10), Lucky Momentum +1% lucky/stack (2→8), Frenzy +2.5% AS, Blazing |
| stack-on-kill | Arcana mana regen, Blood Rush healing, **Blood Chakra +3% AP_percentile/stack (4→10)** |
| lucky-hit (all fire per proc) | Fatal Strike ×(2.5→3.5), Execution Strike, Fanged Strike (fang at 5→10% of hit), Arcane Strike (CDs −1→3%), Mana Steal, Life Steal, Cleave 45→75% sweep |
| on-hit casts | Blizzard (frost nova), Frostbite (glacial shatter), Nucleus (Nova on stunned kill), Toxic Reaction (Nova Dot) |
| other | Voltaic Impact, Lightning Damage/Stun, Blight, Ethereal (free cast), Quickening (CD refund), **Mind Meld +1% CDR per 50 max mana**, Momentum Engine, Potent/Healthy Elixir |

"t1 → max" means max *learnable*. Over-level tiers go much further: Fatal Strike is learnable to
t3 (DI 2.5 = ×3.5), but its list runs to t29 (DI 8.0 = ×9) through `talent_level` gear (Ingenium +3,
Vorpal talent levels). The talent side of the build therefore also has an over-level axis.

### 6.4 Expertises
None of the 25 is combat-relevant (Trinketer only saves trinket uses). Excluded.

### 6.5 Prestige powers and archetypes

Prestige (`P/prestige_powers.json` `tiers[0].{learnKnowledgeCost, requiredGreedTier, <value>}`; no
exclusions; gated by greed tier and knowledge points). Combat-relevant powers:

| Power | Effect | Greed tier |
|---|---|---|
| Shielded | absorption = max HP on entry | 2 |
| Kinetic / Fall immunity | | 2 / 1 |
| HealthIncrease | +30 HP | 8 |
| ChampionsDamage | +0.5 vs champions | 9 |
| BarrierOfResilience | resist cap +0.05 | 9 |
| ShieldOfLastingGuard | block cap +0.1 | 10 |
| BerserkPower | +5%/kill ×20, non-AP registry | 11 |
| OriginalWard | absorption refill after 60 s | 11 |
| WeaverOfTime | CDR cap +0.05 | 12 |
| **Masterful** | doubles +ability-level gear | 12 |

**Archetypes: probably dead in 0.34.1 (needs confirmation).** `B/config/ArchetypesConfig` reads
`archetypes.json` as `{DEFAULT, BERSERKER, COMMANDER, TREASURE_HUNTER, WARD, BARBARIAN, VAMPIRE}`. The
pack file (from the U33 merge `1989683f`) has a different format, a recommended-build guide
(`{"archetypes":[{id:"fireball", steps:[…], gearGoals}]}`). No loader for that format exists in the
base mod, vhapi or the addon, so Gson leaves every archetype field null. The build-guide entries are
still useful as reference builds.

### 6.6 Greed tree

`P/greed/greed_nodes.json` `tree.skills[] = {id, parent|parents, tier, type, entries[{attribute, value}]}`.
113 binary nodes, one budget unit each, each needs its parent. `greed_gear_attribute` nodes use the
**same snapshot path as talents**. `greed_tree.json` is layout only; `greed_progression.json` branch ids
don't match the nodes (dead). The addon adds no nodes. There are no ability-upgrade nodes in 0.34.1.

- Offense: +6% DI / +4% AP% nodes (×9 and similar), +10 AD / +5 AP, lucky +1–2%, double hit +1%, +1 chain,
  stun +5%, AoE +5%, mob-type +10%, keystone relentless_strike 0.08.
- Defense: health% +10/+25, +10 HP, armor% +2/+4, +10 armor, resist +4/+8%, crit mitigation +20%, healing,
  keystone phoenix.
- Utility: CDR +5/+10%, mana regen +25/50%, max mana +5%, effect duration +10%, keystone CD skip 0.1.
- Totals if *all* nodes were taken: DI 0.58, AP% 0.39, CDR 0.25, health% 0.65, resist 0.28, lucky 0.08.
  The budget limits real builds (tier 10 = 30 nodes).

## 6.7 Reference decks named in the brief

All exist in 0.34.1 config: `anvil` (`P/card/decks.json`), Rook = id `wall` (addon `wolds_decks.json`,
display name "The Rook Deck", implicit `rook_deck`), `cake` + `runic` + `mystery` with cores
`cake_deck` / `runic_deck` / `construction` (`A/card/deck_mods/new_cores.json`). DeckFAST already has
these layouts in `decks/wolds_decks.json` and `structural_layouts.json`.

## 6.8 Bug registry (bugged vs intended switch)

The model takes `mode = "bugged" | "intended"` globally, plus per-id overrides. **bugged** = 0.34.1 as
shipped. **intended** = the proposed fix in the right-hand column. `DECIDE` = intent is unclear and
needs a design call; until then intended = bugged. Severity is the rough multiplier effect where it
can be estimated. Confidence: V = verified in code, I = inferred.

Notation: E = DI / mob-type / Purist stage, M = multiplier registry, FS = fatal strike.

**Damage double-dips and skipped stages**

| id | where | bugged (0.34.1) | intended | severity | conf |
|---|---|---|---|---|---|
| ICEBOLT-DI2 | `AbstractIceBoltAbility:73-82`, `IceBoltEntity:199-203` | bakes AD×(1+DI)×M, then E applies DI again (IS_AOE). The Lucky Bolt variant still doubles DI and applies M once | DI once, M once | ×(1+DI) on AD part | V |
| BLIZZARD-DI2 | `VaultBlizzardShard:252-283` | same as Ice Bolt | DI once | ×(1+DI) | V |
| ADTOTEM-M2 | `TotemMobDamageTileEntity:121-140` | (AP + AD×M)×pct under IS_TOTEM only: M twice on AD, and the AP part gets DI + M | AD×M once; AP part stays AP-family | ×M; ×(1+DI)·M on AP | V |
| SHATTER-EXPL-M2 | `GlacialShatterEffect:176-186` | explosion etching bakes AD×pct×M, then M again | M once | ×M | V |
| TRIDENT-DI2 | addon `MixinThrownTrident:122-130`, `VaultTridentItem:343-382`, `LivingEntityEvents:758-770` | thrown/riptide bake (1+DI+mob), then E again; channelling-riptide melee runs under IS_CHAINING, so no DI at all | DI once | ×(1+DI+mob) | V |
| THORNS-X2 | `ThornsHelper.getThornsDamageMultiplier` | `x += PLAYER_STAT(x)` → gear thorns damage counted twice (reflect, Shell Quill, Battering Ram, Retribution) | x + talent bonus | ×2 thorns mult | V |
| EXEC-GEAR | addon `LivingEntityEvents:417-425` | `(amount + missingHP×exec) × 0.25` vs champions/elites/bosses (×0.01 Vessel): the penalty hits the whole hit | `amount + missingHP×exec×k` | ×0.25 / ×0.01 of all melee vs bosses | V |
| CLEAVE-REDIP | `SweepingLuckyHitTalent:84` | copies the amount at LOWEST (after every NORMAL stage); children re-run E, FS, Effect/Daze, Vulnerable, Dice | children take the copy only | ×(1+DI)·Purist·FS per child | V |
| FANG-RESIDUAL | addon `FangedStrikeLuckyHitTalent:41`, `CustomFangEntity:128-133` | proc fangs still re-apply Conditional Damage, Damage On Hit (Daze), Vulnerable, Dice, and can trigger on-hit AoE/chain | skip all of these | ×(1+effect)·Vul·Dice | V |
| CHAIN-REDIP | addon `MixinGearAttributeEvents:220-247`, `WoldEventHelper.isNormalAttack` | chain children skip E but **re-apply M** and re-roll FS. `isNormalAttack` lacks IS_CHAINING, so execution, AP-scaling, thorns-scaling, cleave and reave apply on **every chain child** (EXEC-GEAR's ×0.25 too) | children skip M, FS and flat adds | ×M + flat adds per child | V |
| FS-UNGATED | `GearAttributeEvents:807-823` | **Latent in 0.34.1**: the gear `fatal_strike_chance/damage` attributes come only from the hidden talents `Fatal_Strike_Chance/Damage`, which can't be learned, and nothing else rolls them. If a source is ever added, gear fatal strike (separate from the Fatal Strike lucky-hit talent) rolls on every hit: AoE/chain/sweep children (re-roll on top of an inherited proc), thorns, shatter, totem, Smite, DoT ticks, echoes | primary hits only (DECIDE: abilities?) | ×(1+FSD) on copies | V |
| ECHO-FLAGLOSS | addon `LivingEntityEvents:556-573`, `EchoingPotionEffect:30-34` | echo replays carry only ECHOING+UNLUCKY: echoes of AP sources (Smite base, Fireshot, Rail, AP Totem), Glacial Shatter, thorns and chain children **gain DI, M and flat adds** and can trigger on-hit AoE. A shatter echo replays %max-HP as melee | echo keeps the source's flags | ×(1+DI)·M + adds | V |
| VULN-OFF1 | `VulnerableEffect:61` | bonus = amp×0.1, so Vulnerable I = +0%; etching levels clamp to 8 (levels 8–12 identical) | (amp+1)×0.1 (DECIDE) | −10% per level | I |
| THORNS-STAGES | `GearAttributeEvents:933-973` | the thorns reflect gets E, M, FS and **double hit**; Shell Quill quills skip M (inconsistent) | DECIDE (at minimum no double hit) | ×M·DH | V (intent unknown) |
| SHATTER-PROCS | `GlacialShatterEffect:167-173` | the %max-HP shatter hit gets E, M, FS, and triggers on-hit AoE (DI again) and chain (M again) | exclude GLACIAL from samplers; DECIDE on E/M | large | V |
| APHIT-PROCS | e.g. `VaultFireball:438` (fire shards) | IS_AP-only single-target hits can trigger on-hit AoE, chain, sweeping on-hit, lucky hit (stale swing scale) and double hit | add IS_EFFECT/IS_AOE | low | V |
| CHARM-PROCS | `TauntCharmAbility:267-271` | charmed-mob hits can trigger on-hit AoE and chain | exclude CHARMED | low | V |

**Same-priority races** (unspecified order, §2.2): ORDER-FLATADD (flat adds before/after E/M),
ORDER-AOE (AoE samples before/after E: DI once or twice on secondaries), ORDER-CHAIN (sample
before/after M), ORDER-LOWEST (Vulnerable/Dice vs the Cleave copy). bugged = report min and max;
intended = flat adds before multipliers, samplers take the pre-E amount (DECIDE).

**Things that silently do nothing or fizzle**

| id | where | bugged | intended | conf |
|---|---|---|---|---|
| EXECUTE-ABILITY | `ExecuteAbility:49-51` | always fails (stub since 3.21.5); not in the GUI | next hit + pct × target max HP with a boss multiplier (DECIDE: revive or remove) | V |
| CHUNK-ZERO | addon `MixinIceBoltChunkAbility:53` | Ice Bolt Blast deals 0 damage; kills Glacial Damage, Lucky Bolt, Piercing Bolt and Effect etchings on Blast (Frozen Barrage still works) | DECIDE (CC rework, or restore damage) | V |
| LUCKYBOLT-SHATTER | `IceBoltArrowAbility:129`, `IceBoltEntity:172-203` | Lucky Bolt removes IS_AOE, and the Glacial Shatter trigger requires IS_AOE → **no shatter with Lucky Bolt** | shatter still triggers | V |
| LUCKYBOLT-LUCKY | same | author reports lucky hits don't fire. On **Blast** that's explained (CHUNK-ZERO). On **Base**, no static cause found: every `doLuckyHit` gate passes for a lucky bolt (bytecode-checked). Possible runtime causes: a leaked flag (THORNS-FLAGLEAK), or a test on Blast | lucky bolt can lucky-hit | open |
| ARCNOVA-FIZZLE | `ArcaneNovaOnHitHelper:114-134` | the counter counts AoE/chain/echo/thorns hits; if the threshold lands on a hit already under IS_AOE/IS_EFFECT, nested `runIfNotSet` **skips the body** → nova deals 0, counter still resets | count primaries only; push flags unconditionally | V |
| CASTONHIT-FIZZLE | `CastOnHitTalent:63-120` | casts triggered inside an IS_AP hit get 0 damage (nested `runIfNotSet`); inside IS_CHAINING they skip E | exclude those flags | I |
| THORNS-FLAGLEAK | `GearAttributeEvents:933-956`, `ShellQuillAbility:132-146` | IS_THORNS_REFLECTING is pushed with no pop (cleared next entity tick); Shell Quill relies on the leak to avoid a double reflect | pop properly and remove the duplicate call (a naive fix doubles reflects) | V |
| SCALE-LEAK | `IceBoltEntity:175-177` | Lucky Bolt sets lastAttackScale = 1 and leaves it, so later non-melee hits pass full-swing gates | restore the previous value | V |
| CHAIN-DROPS | addon chain lambda overwrite | drops the CHAINING_PENALTY etching (dead), EffectOnHit on chained mobs, and the pet filter (pets can be chained) | restore | V |
| SWEEP-DEAD | `MixinPlayerEntity:202-215` + Better Combat `allow_sweeping=false` | `sweeping_hit_chance/damage` (sword, rang, uniques) and Sword Specialisation's sweep do nothing | DECIDE (remove the affixes or re-implement) | I (high) |
| SHATTER-MIXIN-UNLISTED | addon `fixes/MixinGlacialShatterEffect` (not in mixins.json) | the addon's 25%/150% max-HP shatter never loads; base shatter runs | DECIDE | V |
| OFFHAND-STAFF | `MixinAttributeSnapshotCalculatorNoBC` (BC-gated off) | battlestaff + shield/focus/wand offhand stats both count | offhand stats dropped under a battlestaff | V |
| EXECSTRIKE-T4 | `talents.json` Execution_Strike | over-level tiers 4–29 switch type to `damage_lucky_hit` ×1.18–1.43 | stay `execution_lucky_hit` 0.18→0.43 | V |
| STAFF-MYTHIC-AD | `battlestaff_mythic.json` IMPLICIT | mythic AD implicit 50-60 < omega 60-70 | ≥ omega | V |
| ULTSHIELD-GROUP | addon `abilities/group/wolds_abilities.json` | lists `Ultimate_Shield`, the id is `UltimateShield_Base` → no ULTIMATE-group level bonus | fix the id | V |
| GREED-RAWSUM | `CardDeck.getGreedMultiplier` (3.21.6) | `max(1, Σ raw multipliers)` | `1 + Σ(roll − 1)` (fixed on the greed branch, `d8d69080`) | V |
| EXEC-TABLES | addon execution vs `MissingHealthDamageHelper` | two different boss multiplier tables (Vessel 0.01 vs 0.1, champion 0.25 vs 0.5) | one table (DECIDE values) | V |

**Found while building the optimizer (2026-10-05)**

| id | where | bugged | intended | conf |
|---|---|---|---|---|
| ICEBOLT-T12-HOLE | `abilities.json` Ice_Bolt_Base tier 12 | the tier omits `percentAttackDamageDealt` and the splinter keys, so the reader defaults them to 0: Ice Bolt at *exactly* effective tier 12 deals flat damage only. It's the only key hole in the whole ability and talent tree | copy the tier-11/13 values | V |
| DELUXE-CARD-IDS | `card/modifiers.json` pool `deluxe_knack` | lists `deluxe_blood_chakra` / `deluxe_hex_breaker`; the defined ids are `blood_chakra_deluxe` / `hex_breaker_deluxe`, so those rolls produce nothing | fix the ids | V |
| CARDS-NO-POOL | `card/modifiers.json` | flat `health` / `amp_health` / `deluxe_health`, `mind_meld` knacks and 37 spec-level ability cards exist but are in no booster pool (unobtainable); `wild_pack` has no source | DECIDE | V |
| MYTHIC-OVERLAY-ORDER | vhapi `CustomVaultGearLoader` vs addon `MixinModConfigs` | whether the addon's `*_mythic` gear overlays (extra seals: phoenix, execution, talent levels…) merge at all depends on TAIL-inject order. If vhapi runs first, mythic gear lacks the addon seals and mythic necklaces are empty | — (needs an in-game look at a mythic chestplate's seal list) | open |
| FANGS-WAVES | addon `EvokerFangsAbility:47-63` | `waveCount`/`waveDelay` are dead (wave loop commented out): one wave only | DECIDE | V |
| STORM-BLIZZARD-ZERO | `StormArrowBlizzardAbility:87-110` | Storm Arrow Blizzard deals no direct damage (also not learnable: no GUI style) | DECIDE | V |
| VOLLEY-BOUNCE-EXPLODE | addon `MixinVaultFireball:85` | the Mitosis mixin calls `explode()` on every Volley bounce whether or not the etching is present (~10 explosions per cast) | explode per bounce only with Mitosis (DECIDE) | V |
| NOVA-LOWMANA-STACK | `NovaAbility:68-75` | Nova Low-Mana Damage is read with `forEach` and compounds: n copies give ∏(1+mᵢ), up to ~64× below 50% mana with 6 copies | first copy only, like every other etching | V |
| WOLDETCH-EARLYFALSE | addon `WoldEtchingHelper:17-19` | `hasEtching` returns false as soon as it meets vault gear in an unintended slot (e.g. a sword in the offhand), disabling Reverberation, Ravenous Fangs, Greedball and Mitosis's 0.5× penalty | skip the item instead of returning | V |

**Execution Strike × lucky cap × attack speed (design exploit, not a code bug).** Execution Strike
T1–3 adds `0.15 × missing HP × 0.25` (boss reduction) on every lucky hit, *after* the multipliers.
With lucky hit at the cap and a fast swing rate, missing HP grows compounding, so kill time is
~logarithmic in boss HP. The gear `execution_damage` stat does the same at NORMAL priority (and,
depending on the handler race, *before* the DI × registry stack). Since 2026-10-07 the optimizer caps
execution at +5 hyper cycles over the same build without it (author ruling: regular hyper mobs still need
real damage, so execution builds such as Hungry Maw cannot actually clear high cycles in vault time). The
earlier "no execution" variant is retired.

**Trinket fusion (capacity, not a bug).** The Trinket Fusion Forge merges two different trinkets into one
item that sits in the first effect's colour, and all effects count. That effectively doubles trinket
capacity once Prismatic Glue is available. The no-duplicates rule lives in addon
`MixinTrinketItem.canEquip`.

**Design-level (not bugs, modeled as-is):** Concentrate's uncapped Empower amplifier; Wall of Fangs
instant-kill threshold; The Dice applying to every hit type.

**Checked and correct:** Earthquake/Grenade/Chaos Cube/Riftblade (DI once, M once — they do *not* miss
DI); Dash Damage, Shield Bash (M baked, IS_AOE skips M, DI once); Javelin; non-proc fangs; Smite family;
all pure-AP abilities; Reave; Rampage (LOW, direct melee only); double hit and low-target-health damage
(LivingDamageEvent, after the samplers); proc-fang fix for E/M/FS/flat adds/echo.

## 6.9 Obtainability pass (2026-10-05): excluded from the optimizer

- **Unlearnable abilities** (no GUI style or cost 999): Smite_Thunderstorm, Taunt_Charm,
  Storm_Arrow_Blizzard, Mana_Shield_Retribution, Rampage_Leech/Chain, Earthquake_Landmine (and so the
  Landmine Spawn etching), Execute, Summon_Eternal.
- **Unlearnable talents** (no GUI style): Javelin_Conduct/Frugal/Throw_Power/Damage (so Javelin never
  gets the talent bonus), Fatal_Strike_Chance/Damage, Fatal_Strike_II, Critical_Strike, Thorns_*,
  *_II variants, Axe/Sword Specialisation. The talent GUI merge is `putAll`, not a replace.
- **Affixes that never roll at L100** (weight 0): mob-type damage prefixes (illagers/spiders/undead/nether)
  on every weapon, sword sweeping chance (non-mythic), a plushie implicit and two plushie_mythic clouds.
- **Unusual affixes** exist only in OMEGA tables, so mythics can't get one (cost: Eccentric Focus, one
  per item, needs a free slot).
- **Cards:** see CARDS-NO-POOL / DELUXE-CARD-IDS. No flat-health cards from packs.
- **Decks with no source:** expanded, gdungeon/ldungeon/odungeon, black, lost, cactus. Deck-implicit cores
  (cake_deck, runic_deck, rook_deck) come only as a deck's implicit or as the mystery deck's two random implicits.
- **Greed cards** only from the greed trader, from greed tier 4. Early-greed decks have none.
- **Prismatic pouch** (2 red + 2 blue + 2 green) is a greed-tier-11 prestige recipe.
- **Etchings:** all 66 are in the greed trader's uniform `random_etching` pool (minGreedTier ≤ player tier),
  so any one etching is ~1-2% of offers. Also from hyper crates and omega trader cores.
- **Uniques:** crystal_double_blade has no pool. The Sweetheart is craft-only. 7 unique affixes never roll
  (Butcher's Axe rampage level, Vitalis block, Frostguards/Plague Steppers movement, Frostwarden ice-bolt
  level, Echoflare dash level, Pocket Penguin/Chroma Brew mana).
- **Prestige hidden** (combat-relevant): RegenerationPower, CatDodge, TheCarapace.

## 7. What the catalogue must contain (checklist for the next phase)

Config-read (numbers):
1. Gear affix tables per type × {OMEGA, MYTHIC} × group, with the vhapi merge applied, filtered to
   combat attributes, keeping tier lists (L100 open tiers + 101/102 jump tiers + weights).
2. Corrupted implicits (seals), unusual affixes, uniques (`unique_gear` pack + addon, jar wins).
3. Etchings with greed-tier-indexed values and gear-group restrictions.
4. Trinkets (by colour), pouches (slot counts), god charm prefixes.
5. Card modifiers (attribute, tier pools, deluxe), scalers, conditions, cores, deck layouts, implicits.
6. Abilities: per spec, per tier (1–30) stat keys + learn costs + max learnable; ability groups.
7. Talents: per tier keys + costs + max learnable + visibility; skill gates.
8. Prestige powers (value, knowledge cost, greed gate); greed nodes (graph + entries).
9. Caps (`AttributeLimitHelper` constants + `attribute_cap_overrides.json`), base player stats (`mana.json`).

Hand-coded (mechanics, roughly 60–80 rules):
- Stat assembly (snapshot vs vanilla channel per attribute) and the derived-stat formulas in §1.
- The hurt-event pipeline in §2.2 as an ordered list of stages with flag filters (normal / AP / AoE / DoT / proc-fang).
- Per-ability damage formula (which inputs, which flag family, hits per cast, cast rate from CD/mana).
- Per-talent and per-etching proc rules (lucky-hit fan-out, recasts, echo, chain/AoE fan-out).
- Defense chain (§3) and sustain.

Model axes: greed tier (node budget, etching values, prestige gates), gear roll quality
(typical / best L100 / tier-jumped), OMEGA vs MYTHIC, scenario (single-target boss vs pack clear),
and the greed-card stacking rule (0.34.1 bug vs fixed).

Automating relationships: mostly feasible. Every gear/talent/greed/deck stat is an attribute id,
so "what affects what" for those reduces to "attribute X is read by formula Y". A grep-generated
attribute→reader index (`getAttributeValue(ModGearAttributes.X`) gives the reader list
automatically. Only ability formulas, proc talents and etchings need hand-written rules.

## 8. Decisions and answers (author, 2026-10-05)

1. Archetypes do not work in 0.34.1. **Excluded.**
2. Progression stages by greed tier: **early 0–3, mid 4–8, endgame 9+**. Node budget is 3/tier, so
   ≤9 / 12–24 / 27+ nodes; etching values and prestige gates follow the tier.
3. Same-priority ordering: traced in code (§2.2). Unspecified by design, modeled as min/max bounds.
4. Dual-wield: offhand weapons don't apply their stats. Confirmed in code: the BC snapshot mixin
   isn't registered, and the base snapshot ignores items not intended for the offhand. Side effect:
   the "no offhand with battlestaff" rule is also inactive with Better Combat, so battlestaff +
   shield/focus/wand is a legal stat combo in 0.34.1 (model it as legal; flag it for design).
5. **Duplicate trinkets are not allowed.**
6. Seals: Vorpal foci are easy to get. Budget: **1 good seal in midgame, 2–3 in endgame.** None in early.
7. God reputation is maxed (50 in all gods) by midgame, so the god charm scale is fixed from mid onward.
8. Ability formulas for Javelin, Ice Bolt (+ Frozen Barrage), Execute, Totem, Shield Bash and the
   Dice: done, §6.2.1.

Still open: Skill Orb source (assume 0); DeckFAST scaler rules vs `scalers.json` not cross-checked.

## 9. Out of scope / noted only

Vault modifiers (`player_attribute`, mob modifiers), mob stat scaling (`library\MOB_STAT_SCALING.md`
describes greed-test-19+; in 0.34.1 the owner's greed rank still scaled mobs), consumables/elixirs,
eternals/companions, PvP. Luck/unluck effects matter (gear CORRUPTED_IMPLICITs roll `minecraft:luck`
I): luck multiplies lucky hit, block, dodge, burn and hexing chances by 1.15^n.


## 10. Audit round 2026-10-07 (author questions)

- **Executioner works.** `LowTargetHealthDamageTalent` registers a static listener on `CommonEvents.ENTITY_DAMAGE` at
  LOWEST: ×(1 + Σ damageIncrease) on every hit (ability power included) once the target is at or below 50% HP.
  Over a kill it is worth 1 / ((1 − θ) + θ / (1 + x)), ×1.23 at rank 3 (+60%). It stacks multiplicatively on top of
  the additive DI pool, which is why the optimizer takes it in ~80% of builds.
- **Ingenium** (wand etching, `added_talent_level` all_talents): +2 at greed tier 6–9, +3 at 10+, in 0.34.1 and on
  wv-development origin/master `13a67325` (2026-10-04). No +1 version exists in either.
- **Time-acceleration plushie:** plushie IMPLICIT group `modImmunity` includes `the_vault:mod_immunity_time`
  (effect_immunity, minLevel 80). The same group holds the plush Luck effect, so the lock also gives up plushie Luck.
- **Baseline (author ruling):** every build learns Vein Miner 4, Heal 4, Dash 8 (16 points) and must reach 5 mana/s and
  80% CDR. Because Heal is in every build, low-HP damage bonuses (Berserking) are modeled at 0% uptime.
- **End and max deck:** author's Mystery layout (cake + runic, pure, Greater Construction ×5), NDM 10936.88, reproduced
  exactly by `model/deck.py` (`data/deck_layouts_manual.json`).

## 11. Healing, thorns and uniques (2026-10-07)

Author rulings: survive 5 boss hits 1 s apart (tank or out-heal); never one-shot by a regular hit; with one-shot
protection (knob, on) a build that heals 75% of max HP between hits may go one cycle past its one-shot cycle (max-HP loss
ignored; greed update will disqualify one-shots instead). Heal is cast only with spare mana. One-shots are judged on the
unblocked hit. Chaos healing-effectiveness debuffs (-43% at cap) are deferred with the other chaos player effects.
Unique rolls: top bracket at 60% early/mid, 80% late/max. Unique limits: early 1, mid 3, late/max any; sealed uniques
late 1, max 2. Uniques are not etched (author ruling; note: WV's MixinEtchingApplicationTableTileEntity does allow it).

- **Healing effectiveness** (`PlayerRecoveryHelper`): heal x max(0, 1 + HE + Methodical), no cap. Bloodthirst (any
  on-kill-heal modifier on the snapshot) zeroes every LivingHealEvent heal. Healing Salve is +150% in the pack config.
- **Leech** (`PlayerLeechHelper`, LivingDamageEvent): vs VaultBoss the damage/max-HP cap is skipped, so each qualifying
  damage instance heals max HP x leech. AOE/DOT/REFLECT/JAVELIN/CHARMED/EFFECT/TOTEM hits never leech; needs last melee
  scale >= 1. Gear leech exists only as an addon unusual (early stage). Life Steal (lucky hit) heals max HP x 1-3%;
  Life Steal II is hidden in 0.34.1.
- **Heal ability**: flat HP (Heal_Base t4 5 HP / 170 t / 16 mana). Regeneration is vanilla (0.4-3.3 HP/s), not modeled.
- **One-shot protection** is the Second Chance mod: damage >= HP and HP >= 13.5 -> left at 1 HP; addon shrinks max HP
  x0.827 per proc in vaults. The hyperboss melee cadence is ~1.5 s (model uses 1 s per the author).
- **Thorns** (`GearAttributeEvents.thornsReflectDamage`, LivingAttackEvent HIGH): every hit reflects (no chance roll,
  before block/dodge): attack damage x 2 x thorns_damage (ThornsHelper doubles it; the Thorns talent is hidden, no gear
  rolls it) + flat thorns. Shell Porcupine multiplies flat thorns by (1 + p) on every read (p 4.5 at t8, 15.5 at t30).
  Reflects take DI, registry, Vulnerable, double hit, echo, lucky hits only with lucky_thorns; never execution/Rampage.
  Thorns cards on the Mystery deck reach ~5800 flat at max, so a Porcupine build reaches cycle ~8 without execution.
- **Uniques**: registry put-by-key (addon wins), modifiers resolved against merged unique.json (pack first); missing
  identifiers are skipped in game (Butcher's Axe rampage level, Frostwarden ice bolt level, Echoflare dash level, ...).
  Seals draw from unique.json CORRUPTED_IMPLICIT (9/10 replace an explicit). Powers that matter vs the boss: Castle
  Bastion (x0.5 damage taken standing still), frost nova vulnerability (Vulnerable level-1 via Nova Slow), Everflame
  (Fireball +p recast), Safer Spaces (worse block), Aurora Scissors AP scaling (AP flat x (1 + AP%)), Grass Sword thorns
  scaling, lucky thorns. Chainlash, Zeus, Fork, phoenix, hexing, clouds and similar do nothing to a lone boss.
