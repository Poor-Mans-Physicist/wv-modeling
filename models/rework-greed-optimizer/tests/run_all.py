"""Run every tests/test_*.py without pytest (the tests are plain pytest-style functions, so `python -m pytest tests`
runs the same set when pytest is installed).

python tests/run_all.py [-k substring]
"""
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conftest  # noqa: E402,F401

if __name__ == "__main__":
    pick = sys.argv[sys.argv.index("-k") + 1] if "-k" in sys.argv else ""
    failed = passed = 0
    for f in sorted(os.listdir(HERE)):
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        mod = importlib.import_module(f[:-3])
        for name in sorted(n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n))):
            if pick and pick not in f"{f}::{name}":
                continue
            t0 = time.time()
            try:
                getattr(mod, name)()
                passed += 1
                print(f"PASS {f}::{name} ({time.time() - t0:.1f}s)")
            except Exception:
                failed += 1
                print(f"FAIL {f}::{name} ({time.time() - t0:.1f}s)")
                traceback.print_exc()
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
