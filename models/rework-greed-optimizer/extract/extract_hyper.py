"""Extract the hyper vault's chaos-modifier pools (0.34.1) into data/hyper_pools_0.34.1.json.

Each pool entry is flattened the way the_vault's Modifiers.addModifier stores it (GroupedModifier.flatten adds every
child as its own entry), and reduced to what the hyperboss model needs:
- hp_add / hp_mult: max-health modifiers. HyperBossManager.vaultHealthFactor folds them into the boss's arm-time health
  as (1 + sum of MULTIPLY_BASE amounts) x product(1 + MULTIPLY_TOTAL amounts).
- dmg_add / dmg_mult: attack-damage modifiers. applyBossStats applies them to the live boss next to the
  MULTIPLY_BASE damage escalation.
- frenzy: mob_frenzy stacks. In hyper vaults MixinMobFrenzyModifier multiplies all player damage by 1 + 2 x stacks.
- crit / player-side stats are kept for the report only.
Stack caps mirror HyperModifierPolicy.STACK_CAPS / stackCap (Java constants, not config).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = (os.environ.get("WV_SNAPSHOT") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "cache")).replace(os.sep, "/") + "/"
OUT = os.path.join(ROOT, "data", "hyper_pools_0.34.1.json")

MOD_FILES = [SNAP + "pack/config/the_vault/vault_modifiers.json",
             SNAP + "addon/src/generated/resources/data/woldsvaults/vault_configs/vault/modifiers/wolds_builtin_modifiers.json"]
POOL_FILE = SNAP + "addon/src/generated/resources/data/woldsvaults/vault_configs/vault/modifier_pools/wolds_builtin_modifier_pools.json"
HYPER_CFG = SNAP + "pack/config/the_vault/hyper_objective.json"
POOLS = {"mixed": "woldsvaults:hyper_mixed", "all_bad": "woldsvaults:hyper_all_bad", "timer": "woldsvaults:hyper_bad_timer_events"}
# HyperModifierPolicy (addon 0f0a9254): fixed caps; frenzy and mana_leak caps depend on the cycle.
STACK_CAPS = {"the_vault:electric": 1, "the_vault:wounded": 4, "the_vault:explosive": 1, "the_vault:volcanic": 2,
              "the_vault:void_pools": 2, "the_vault:safari": 5, "the_vault:winter": 5, "the_vault:fungal": 5}
# EntityAttributeModifier.ModifierType -> AttributeModifier.Operation for the health and damage types.
OPS = {"max_health_additive_percentile": ("hp", "add"), "max_health_multiplicative_percentile": ("hp", "mult"),
       "attack_damage_additive_percentile": ("dmg", "add"), "attack_damage_multiplicative_percentile": ("dmg", "mult")}


def warn(msg):
    print(f"[hyper-extract][FALLBACK] {msg}", file=sys.stderr)


def main():
    mods = {}
    for path in MOD_FILES:
        d = json.load(open(path, encoding="utf-8"))
        for tkey, entries in d.get("modifiers", d).items():
            if isinstance(entries, dict):
                for mid, body in entries.items():
                    mods[mid] = (tkey.split("/")[-1], body.get("properties", {}), body.get("display", {}))

    def leaves(mid, n=1):
        if mid not in mods:
            yield mid, None, {}, n
            return
        t, pr, _ = mods[mid]
        if t == "grouped":
            for c, k in pr.get("children", {}).items():
                yield from leaves(c, n * k)
        else:
            yield mid, t, pr, n

    pools_raw = json.load(open(POOL_FILE, encoding="utf-8"))
    pools_raw = pools_raw.get("pools", pools_raw)
    out_pools = {}
    unresolved = set()
    for key, pid in POOLS.items():
        levels = pools_raw[pid]
        if len(levels) != 1 or len(levels[0]["entries"]) != 1:
            warn(f"{pid}: expected one level with one entry; using the last level's first entry")
        ent = levels[-1]["entries"][0]
        rows = []
        for x in ent["pool"]:
            r = {"id": x["value"], "weight": x["weight"], "hp_add": 0.0, "hp_mult": 1.0, "dmg_add": 0.0, "dmg_mult": 1.0,
                 "frenzy": 0, "crit": 0.0, "leaves": {}}
            for leaf, t, pr, n in leaves(x["value"]):
                r["leaves"][leaf] = r["leaves"].get(leaf, 0) + n
                if t is None:
                    unresolved.add(leaf)
                elif t in ("mob_attribute", "mob_attribute_settable"):
                    typ = pr.get("type", "")
                    amt = float(pr.get("amount", 0.0) or 0.0)
                    if typ in OPS:
                        stat, op = OPS[typ]
                        if op == "add":
                            r[stat + "_add"] += n * amt
                        else:
                            r[stat + "_mult"] *= (1.0 + amt) ** n
                    elif typ.startswith("crit_chance"):
                        r["crit"] += n * amt
                elif t == "mob_frenzy":
                    r["frenzy"] += n
            rows.append(r)
        out_pools[key] = {"id": pid, "rolls": [ent["min"], ent["max"]], "entries": rows}
    if unresolved:
        warn(f"pool ids with no modifier definition (VaultModifierRegistry.getOpt skips them in game): {sorted(unresolved)}")
    cfg = json.load(open(HYPER_CFG, encoding="utf-8"))
    keep = ["hyperStatFactor", "bossHealthPercent", "bossDamagePercent", "bossStatIncrement", "chaosPerKill", "chaosCap",
            "ambientPeriodTicks", "obeliskMin", "obeliskMax", "playerScaleBossHealth", "referenceBossHealth", "referenceBossDamage"]
    doc = {"source": "release 0.34.1 (pack c5963442, addon 0f0a9254)", "config": {k: cfg[k] for k in keep},
           "stack_caps": STACK_CAPS, "frenzy_id": "the_vault:frenzy", "mana_leak_id": "the_vault:mana_leak",
           "unresolved": sorted(unresolved), "pools": out_pools}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    print(f"wrote {OUT}: " + ", ".join(f"{k} {len(v['entries'])} entries" for k, v in out_pools.items()))


if __name__ == "__main__":
    main()
