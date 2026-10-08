"""Fetch the game data every model reads, from the public Wold's Vaults repos, into cache/.

Nothing from the_vault or the pack is committed to this repo. This script sparse-clones the pack repo
(config/the_vault only) and the addon repo (its data resources only) at pinned commits, so every user
models the same game version.

    python setup/fetch_sources.py                 # default pins (release 0.34.1)
    python setup/fetch_sources.py --pack-ref master --addon-ref master
    python setup/fetch_sources.py --only pack

Layout written:
    cache/pack/config/the_vault/...              vault configs, gen/1.0 rooms, palettes, pools
    cache/addon/src/generated/resources/...      addon datagen (modifiers, decks, etchings, ...)
    cache/addon/src/main/resources/data/...      addon hand-written data
    cache/SOURCES.json                           which commit each checkout is at
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "cache")

SOURCES = {
    "pack": {
        "url": "https://github.com/iwolfking/Wolds-Vaults.git",
        "ref": "c5963442e0f57f65cae6a454bfd810e9c344bed3",
        "label": "Wold's Vaults pack, release 0.34.1",
        "paths": ["config/the_vault"],
    },
    "addon": {
        "url": "https://github.com/iwolfking/Wolds-Vaults-Official-Mod.git",
        "ref": "0f0a9254b7332d6e35b96442bde7040f0f95f3d0",
        "label": "Wold's Vaults addon, release 0.34.1",
        "paths": ["src/generated/resources", "src/main/resources/data"],
    },
}


def git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[fetch][ERROR] git {' '.join(args)} failed in {cwd}:\n{r.stderr.strip()}")
    return r.stdout.strip()


def fetch(name, src, ref, force):
    dest = os.path.join(CACHE, name)
    if os.path.isdir(os.path.join(dest, ".git")):
        have = git(["rev-parse", "HEAD"], dest)
        if not force and (have == ref or have.startswith(ref)):
            print(f"[fetch] {name}: already at {have[:10]}")
            return have
        print(f"[fetch] {name}: at {have[:10]}, switching to {ref}")
    else:
        if os.path.exists(dest):
            print(f"[fetch][WARN] {dest} exists but is not a git checkout; replacing it")
            shutil.rmtree(dest)
        os.makedirs(dest)
        git(["init", "-q"], dest)
        git(["remote", "add", "origin", src["url"]], dest)
        git(["config", "core.autocrlf", "false"], dest)
    git(["sparse-checkout", "set", "--no-cone", *src["paths"]], dest)
    print(f"[fetch] {name}: fetching {ref} from {src['url']} (sparse: {', '.join(src['paths'])})")
    git(["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref], dest)
    git(["checkout", "-q", "--force", "FETCH_HEAD"], dest)
    head = git(["rev-parse", "HEAD"], dest)
    print(f"[fetch] {name}: checked out {head[:10]}")
    return head


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack-ref", default=SOURCES["pack"]["ref"], help="commit, tag or branch of the pack repo")
    ap.add_argument("--addon-ref", default=SOURCES["addon"]["ref"], help="commit, tag or branch of the addon repo")
    ap.add_argument("--only", choices=sorted(SOURCES), help="fetch just one source")
    ap.add_argument("--force", action="store_true", help="re-checkout even when already at the ref")
    a = ap.parse_args()
    if shutil.which("git") is None:
        sys.exit("[fetch][ERROR] git is not on PATH")
    os.makedirs(CACHE, exist_ok=True)
    refs = {"pack": a.pack_ref, "addon": a.addon_ref}
    record_path = os.path.join(CACHE, "SOURCES.json")
    record = {}
    if os.path.exists(record_path):
        with open(record_path, encoding="utf-8") as f:
            record = json.load(f)
    for name, src in SOURCES.items():
        if a.only and name != a.only:
            continue
        head = fetch(name, src, refs[name], a.force)
        pinned = head.startswith(src["ref"]) or src["ref"].startswith(head)
        if not pinned:
            print(f"[fetch][WARN] {name} is at {head[:10]}, not the validated pin {src['ref'][:10]}; "
                  "model accuracy figures in the docs were measured at the pin")
        record[name] = {"url": src["url"], "commit": head, "label": src["label"] if pinned else "custom ref"}
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    print(f"[fetch] wrote {os.path.relpath(record_path, ROOT)}")


if __name__ == "__main__":
    main()
