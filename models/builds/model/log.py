import collections
import sys

_counts = collections.Counter()
_first = {}


def fallback(key, msg):
    """Log a fallback the first time it fires and count every occurrence."""
    _counts[key] += 1
    if key not in _first:
        _first[key] = msg
        print(f"[model][FALLBACK] {key}: {msg}", file=sys.stderr)


def fallback_n(key, msg, n):
    """Record n occurrences of a fallback counted elsewhere (the Rust kernel)."""
    if n <= 0:
        return
    fallback(key, msg)
    _counts[key] += n - 1


def summary():
    return {k: {"count": c, "message": _first[k]} for k, c in _counts.items()}


def take():
    """Counts since the last take() (worker processes report per task; the first message is kept)."""
    out = summary()
    _counts.clear()
    return out
