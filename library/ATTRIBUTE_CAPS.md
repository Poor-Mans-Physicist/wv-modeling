# Attribute Cap Overrides (lucky hit / AoE) — who reads them and what the live caps are

**Verified 2026-08-13** by extracting `vhapi-5.8.0.jar` from the live instance
and CFR-decompiling the two classes below.
Nothing else in the pack reads this file — the_vault 3.21.6, woldsvaults, and
vaultintegrations 1.0.16 all have zero references (checked by stream-decompressed grep across
the instance's jars; note that grepping a jar *without* decompressing gives false negatives —
entries are DEFLATE-compressed).

## The config

`config/the_vault/attribute_cap_overrides.json` (pack-side):

```json
{
  "luckyHitCap": 10.0,
  "aoeCap": 10.0,
  "enableOverrides": true
}
```

## The reader — vhapi 5.8.0

- `xyz.iwolfking.vhapi.api.config.AttributeCapConfig` — extends `iskallia.vault.config.Config`,
  `getName()` = `"attribute_cap_overrides"`. vhapi's own defaults (used on reset): `luckyHitCap
  0.562`, `aoeCap 0.8`, `enableOverrides false` — i.e. overrides are opt-in and this pack opts in.
- `xyz.iwolfking.vhapi.mixin.custom.MixinLuckyHitHelper` — despite the name it targets
  `iskallia.vault.util.calc.AttributeLimitHelper` (remap=false) with **two cancellable HEAD
  injects**:
  - `getLuckyHitChanceLimit(LivingEntity)` → returns `luckyHitCap` when `enableOverrides`
  - `getAreaOfEffectLimit(LivingEntity)` → returns `aoeCap` when `enableOverrides`

## What this means in this pack

| Cap | Base mod (3.21.5 decompile) | Live in Wold's Vaults |
|---|---|---|
| Lucky hit chance | hard `0.562` (`AttributeLimitHelper.java:70`) | **10.0 (1000%)** |
| Area of effect | vhapi default implies `0.8` (base value not independently re-verified) | **10.0** |

- Player lucky-hit chance is clamped at 1000%, not the base 56.2%. Raised from 1.0 for the
  greed rework: Idona's Overcrit node reads the chance accumulated *above* 100%, which the old
  cap made unreachable. The raise applies pack-wide, to every lucky-hit source. Raw attribute
  sums beyond the cap are still computed — only the limit helper clamps.
- Anyone adding another mixin to `AttributeLimitHelper` must account for vhapi's **cancellable
  HEAD** injects (mixin priority/ordering), or the second mixin may never run when overrides
  are enabled.

## Cross-references

- Base cap constants live in `iskallia.vault.util.calc.AttributeLimitHelper` (the_vault); the
  resistance cap (0.5, with 0.95 absolute) is a separate mechanism in `ResistanceHelper`, not
  covered by this override file.
