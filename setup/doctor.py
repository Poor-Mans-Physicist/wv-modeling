"""Check that this machine can run every model, and say exactly what is missing.

    python setup/doctor.py            # report only
    python setup/doctor.py --build    # also cargo-build the Rust crates that are not built yet

Exit code 0 when everything needed for the core models is present; 1 otherwise. The roomlab texture
preview is optional and only reported.
"""
import argparse
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = ".exe" if os.name == "nt" else ""
CRATES = {
    "libs/vaultsim": ["wv_vault_grid", "wv-modifier-panel"],
    "libs/lane": ["lane_cli"],
    "libs/buildkernel": ["wvk"],
}
PY_MODULES = {"numpy": "routerunner sim", "scipy": "routerunner sim", "PIL": "roomlab (pip package: pillow)"}

problems = []


def ok(msg):
    print(f"  ok    {msg}")


def bad(msg, fix):
    print(f"  MISSING {msg}\n          fix: {fix}")
    problems.append(msg)


def note(msg):
    print(f"  note  {msg}")


def version(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()[0]
    except (OSError, IndexError):
        return None


def check_tools():
    print("Tools")
    for tool, fix in [("git", "install git"), ("cargo", "install Rust from https://rustup.rs (stable)"),
                      ("uv", "install uv from https://docs.astral.sh/uv/ (only needed for models/decks)")]:
        v = version([tool, "--version"]) if shutil.which(tool) else None
        if v:
            ok(v)
        elif tool == "uv":
            note(f"uv not found; {fix}")
        else:
            bad(tool, fix)
    if sys.version_info < (3, 9):
        bad(f"python {sys.version.split()[0]} (need 3.9+)", "install Python 3.9 or newer")
    else:
        ok(f"python {sys.version.split()[0]}")
    for mod, use in PY_MODULES.items():
        if importlib.util.find_spec(mod):
            ok(f"python module {mod}")
        else:
            bad(f"python module {mod} ({use})", "pip install numpy scipy pillow   (or: uv sync, then prefix commands with uv run)")


def check_sources():
    print("Game data (cache/)")
    rec = os.path.join(ROOT, "cache", "SOURCES.json")
    if not os.path.exists(rec):
        bad("cache/ is empty", "python setup/fetch_sources.py")
        return
    with open(rec, encoding="utf-8") as f:
        src = json.load(f)
    for name in ("pack", "addon"):
        if name in src:
            ok(f"{name}: {src[name]['commit'][:10]} ({src[name]['label']})")
        else:
            bad(f"{name} checkout", f"python setup/fetch_sources.py --only {name}")
    gen = os.path.join(ROOT, "cache", "pack", "config", "the_vault", "gen", "1.0", "structures")
    if not os.path.isdir(gen):
        bad("cache/pack/config/the_vault/gen/1.0/structures", "python setup/fetch_sources.py --force")


def check_builds(build):
    print("Rust builds")
    for crate, bins in CRATES.items():
        missing = [b for b in bins if not os.path.exists(os.path.join(ROOT, crate, "target", "release", b + EXE))]
        if missing and build and shutil.which("cargo"):
            print(f"  ...   cargo build --release in {crate}")
            r = subprocess.run(["cargo", "build", "--release"], cwd=os.path.join(ROOT, crate))
            if r.returncode != 0:
                print(f"  [doctor][ERROR] cargo build failed in {crate}")
            missing = [b for b in bins if not os.path.exists(os.path.join(ROOT, crate, "target", "release", b + EXE))]
        if missing:
            bad(f"{crate}: {', '.join(missing)} not built", f"cd {crate} && cargo build --release   (or doctor.py --build)")
        else:
            ok(f"{crate} built")
    venv = os.path.join(ROOT, "models", "decks", ".venv")
    if os.path.isdir(venv):
        ok("models/decks environment exists")
    else:
        note("models/decks not set up yet: cd models/decks && uv sync   (compiles libs/ndm on first run)")


def find_instance():
    env = os.environ.get("WV_INSTANCE")
    if env:
        return env, "WV_INSTANCE"
    home = os.path.expanduser("~")
    pats = [os.path.join(home, "curseforge", "minecraft", "Instances", "*Wold*"),
            os.path.join(home, "AppData", "Roaming", "PrismLauncher", "instances", "*Wold*", "minecraft"),
            os.path.join(home, ".local", "share", "PrismLauncher", "instances", "*Wold*", "minecraft")]
    for p in pats:
        hits = sorted(h for h in glob.glob(p) if os.path.isdir(os.path.join(h, "mods")))
        if hits:
            return hits[-1], "auto-detected"
    return None, None


def find_client_jar():
    env = os.environ.get("MC_CLIENT_JAR")
    if env:
        return env
    home = os.path.expanduser("~")
    for p in [os.path.join(home, "curseforge", "minecraft", "Install", "versions", "1.18.2", "1.18.2.jar"),
              os.path.join(home, "AppData", "Roaming", ".minecraft", "versions", "1.18.2", "1.18.2.jar"),
              os.path.join(home, ".minecraft", "versions", "1.18.2", "1.18.2.jar")]:
        if os.path.isfile(p):
            return p
    return None


def check_roomlab():
    print("Roomlab texture preview (optional)")
    inst, how = find_instance()
    if inst and os.path.isdir(os.path.join(inst, "mods")):
        ok(f"instance ({how}): {inst}")
        if how != "WV_INSTANCE":
            note(f'set WV_INSTANCE="{inst}" before running roomlab/build.py')
    else:
        note("no Wold's Vaults instance found; set WV_INSTANCE to the folder holding mods/ "
             "(only build.py's textured preview needs it; room generation and lint do not)")
    jar = find_client_jar()
    if jar:
        ok(f"Minecraft 1.18.2 client jar: {jar}")
        if not os.environ.get("MC_CLIENT_JAR"):
            note(f'set MC_CLIENT_JAR="{jar}" for vanilla block textures')
    else:
        note("no 1.18.2 client jar found; set MC_CLIENT_JAR (vanilla blocks render untextured without it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="cargo build --release any crate that is not built")
    a = ap.parse_args()
    check_tools()
    check_sources()
    check_builds(a.build)
    check_roomlab()
    if problems:
        print(f"\n{len(problems)} problem(s). Fix them in the order listed, then re-run doctor.py.")
        sys.exit(1)
    print("\nReady. Run `python setup/check.py` to verify every model against its reference numbers.")


if __name__ == "__main__":
    main()
