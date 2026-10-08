# Playbook: change or extend a model

Use this whenever a model's behaviour changes: a new mechanic, a corrected formula, a new knob, a game
update. The rule everywhere is the same: **a mechanic must trace to a source** (a config file, game code,
or an in-game test), and the model's gate must still pass afterwards.

## Before writing code

1. Find the model's spec: `libs/vaultsim/SPEC.md`, `models/routerunner/MODEL.md`,
   `models/builds/MECHANICS_0.34.1.md`, `models/decks/MODELING_CHOICES.md`.
2. Establish the mechanic from a primary source. Configs are in `cache/`. Game code: the addon is open
   source (github.com/iwolfking/Wolds-Vaults-Official-Mod); the_vault and vhapi are closed, so decompile
   the jar from your own install (CFR works) and cite `Class.method` and line. Never infer a mechanic from
   tooltip text or second-hand numbers alone; several past "bugs" were exactly that.
3. If it can't be confirmed, mark it INFERRED or OPEN in the spec and tell the user, rather than shipping
   it as fact.

## Per model

| Model | Change | Then run |
|---|---|---|
| vaultsim | `libs/vaultsim/src/`, update `SPEC.md` | `python setup/check.py routerunner`; compare chest counts to real vault data if counts move |
| routerunner | `libs/lane/src/` or `weights/` | `python setup/check.py routerunner` (moves the panel numbers; if that is intended, say by how much and why) |
| builds | Python `model/` first, then mirror in `libs/buildkernel/` | `python models/builds/tools/kernel_parity.py` (0 mismatches), `kernel_speed.py --verify` |
| decks | `libs/ndm/src/` and `src/`, update `MODELING_CHOICES.md` | `uv run python scripts/parity_2_0.py` (Part A exact) |
| roomlab | `room.py`, `lint.py`, `build.py` | `python setup/check.py roomlab` |

## Fallbacks

When code falls back on a default (unknown block, missing config key, unknown chest type), it prints a
`[component][FALLBACK]` or `[component][WARN]` line. Keep that convention so a user can see when an
answer rests on a fallback.

## Game version updates

```
python setup/fetch_sources.py --pack-ref <commit> --addon-ref <commit>
python setup/check.py
```

The gates were calibrated at release 0.34.1, so expect `routerunner` numbers to move when room pools
change. A moved number is not automatically a bug; report what changed in the data.
