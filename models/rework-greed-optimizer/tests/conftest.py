"""Test setup: repository root and tests/ on sys.path; the rework ledger unless WV_LEDGER says otherwise (the model reads
it at import, so this runs before any test module imports model)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("WV_LEDGER", "rework")
