"""(a) The pinned worked example (tests/data/worked_example.json, the Smite_Base max winner) scores its pinned value in
both engines to 1e-9 relative. A failure means the model changed: re-pin with `python tests/worked_example.py --write`
only when the change is intended."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _close(a, b):
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _check():
    import worked_example as we
    ctx, fam, b, pins = we.load()
    now = we.scores(ctx, fam, b)
    bad = [(eng, f, pins[eng][f], now[eng][f]) for eng in ("python", "kernel") for f in we.FIELDS
           if not _close(pins[eng][f], now[eng][f])]
    assert not bad, f"worked example moved from its pin: {bad}"


def test_worked_example_pinned():
    if os.environ.get("WV_LEDGER") == "rework":
        _check()
        return
    out = subprocess.run([sys.executable, __file__], env=dict(os.environ, WV_LEDGER="rework"), capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    import conftest  # noqa: F401
    _check()
