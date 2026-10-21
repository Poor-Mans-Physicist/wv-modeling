"""Every custom god-node handler in the rework catalog is either applied (godfx.compute, CUSTOM_ADDS, the modules in
COUNTED_ELSEWHERE, the piety handler) or listed in NOT_COUNTED with a reason, and an unknown one logs a fallback."""
import json
import os

from model import godfx, log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _custom():
    with open(os.path.join(ROOT, "data", "catalog_rework.json"), encoding="utf-8") as fh:
        cat = json.load(fh)
    return {e: d for g in cat["gods"].values() for e, d in g["effects"].items() if d.get("handler") != "gear_attribute_scaled"}


def _knobs():
    from model import stages
    return dict(stages.KNOBS, god_banked_damage_taken=1e6, god_surrounded_mobs=1.0, god_vault_minutes=30.0)


def test_every_custom_handler_is_accounted_for():
    defs = _custom()
    log.take()
    godfx.compute({e: (1, 1.0) for e in defs}, defs, _knobs())
    unknown = [k for k in log.take() if k.startswith("godfx-unknown-")]
    assert not unknown, f"custom god effects with no formula and no NOT_COUNTED reason: {unknown}"


def test_unknown_handler_logs_a_fallback():
    defs = dict(_custom(), made_up_effect={"handler": "made_up_effect"})
    log.take()
    godfx.compute({"made_up_effect": (1, 1.0)}, defs, _knobs())
    assert "godfx-unknown-made_up_effect" in log.take()
