"""(e) The JSON contract: kernel.py's name lists equal the kernel's (`contract` op), the kernel's knobs are exported on
both ledgers, and the kernel refuses an export whose lists drift or that carries a field it does not know."""
import json
import os
import subprocess
import sys

from model import kernel

KNOB_SNIPPET = ("import json, os, sys; sys.path.insert(0, os.getcwd()); from model.context import Context; "
                "from model import kernel; print(json.dumps(sorted(kernel.export_for(Context('max', 'intended')).doc['knobs'])))")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _norm(v):
    if isinstance(v, dict):
        return sorted((k, _norm(x)) for k, x in v.items())
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def test_lists_match_kernel():
    rs = kernel.kernel_contract()
    py = kernel.contract_lists()
    for key in py:
        if key == "knobs":
            continue
        a, b = _norm(py[key]), _norm(rs[key])
        if key in ("etch_queries", "cast_debuff_classes"):
            a, b = sorted(map(json.dumps, a)), sorted(map(json.dumps, b))
        assert a == b, f"{key}: model/kernel.py {py[key]} vs kernel {rs[key]}"


def test_kernel_knobs_exported_on_both_ledgers():
    need = set(kernel.kernel_contract()["knobs"])
    for led in ("rework", "0.34.1"):
        out = subprocess.run([sys.executable, "-c", KNOB_SNIPPET], cwd=ROOT, env=dict(os.environ, WV_LEDGER=led),
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr[-2000:]
        have = set(json.loads(out.stdout.strip().splitlines()[-1]))
        assert need <= have, f"{led}: knobs the kernel reads but the export lacks: {sorted(need - have)}"


def test_buff_specs_all_dispatched():
    from model import buffs as bf
    rs = set(kernel.kernel_contract()["buff_specs"])
    assert set(bf.BUFF_SPECS) <= rs, f"buff specs without a kernel buffs.rs arm: {sorted(set(bf.BUFF_SPECS) - rs)}"


def test_kernel_rejects_drift_and_unknown_fields():
    from model.context import Context
    from model import families
    ex = kernel.export_for(Context("max", "intended"))
    fam = families.get("ability:Smite_Base")
    good = json.loads(ex._bytes)
    fdoc = ex.family_doc(fam)

    def run(doc):
        data = json.dumps({"ctx": doc, "family": fdoc, "cmd": {"op": "eval", "builds": []}}).encode()
        return subprocess.run([kernel.EXE], input=data, capture_output=True)
    assert run(good).returncode == 0
    bad = json.loads(ex._bytes)
    bad["contract"]["flag_keys"] = bad["contract"]["flag_keys"][::-1]
    out = run(bad)
    assert out.returncode != 0 and b"JSON contract" in out.stderr, out.stderr[-500:]
    bad = json.loads(ex._bytes)
    bad["affixes"][0]["surprise"] = 1
    out = run(bad)
    assert out.returncode != 0 and b"unknown field" in out.stderr, out.stderr[-500:]
    bad = json.loads(ex._bytes)
    del bad["clouds"]
    out = run(bad)
    assert out.returncode != 0 and b"missing field" in out.stderr, out.stderr[-500:]
