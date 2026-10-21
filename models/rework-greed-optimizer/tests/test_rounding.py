"""(f) Rounding audit: every floor / ceil / round / truncation in model/*.py and kernel/src/*.rs is listed in
model/rounding.py (and every entry still matches a line)."""
import io
import os
import re
import tokenize

from model import rounding

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS = re.compile(r"\.floor\(\)|\.ceil\(\)|\.round\(\)|\.trunc\(\)|\bpy_round\(|\bas\s+(i64|i32|isize|usize|u8|u16|u32|u64)\b")
FLOATY = re.compile(r"f64|1e-9|\d\.\d|\.floor\(\)|\.ceil\(\)|\.round\(\)")


def py_hits(path):
    """Lines with math.floor / math.ceil / round( / int( / trunc( / `//` as code tokens (not comments or strings)."""
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out = set()
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        prev = toks[i - 1] if i else None
        if t.type == tokenize.OP and t.string == "//":
            out.add(t.start[0])
        elif t.type == tokenize.NAME and t.string in ("floor", "ceil") and prev is not None and prev.string == ".":
            out.add(t.start[0])
        elif (t.type == tokenize.NAME and t.string in ("round", "int", "trunc") and nxt is not None and nxt.string == "("
              and (prev is None or prev.string not in ("def", "."))):
            out.add(t.start[0])
    return [(n, lines[n - 1].strip()) for n in sorted(out)]


def rs_hits(path):
    """Lines with .floor() / .ceil() / .round() / .trunc() / py_round, or an integer cast on a line with float arithmetic
    (string literals and // comments stripped)."""
    out = []
    for n, line in enumerate(open(path, encoding="utf-8").read().splitlines(), 1):
        code = re.sub(r'"(\\.|[^"\\])*"', '""', line).split("//")[0]
        for m in RS.finditer(code):
            if m.group(1) and not FLOATY.search(code):
                continue
            out.append((n, line.strip()))
            break
    return out


def hits():
    out = []
    for d, fn, ext in (("model", py_hits, ".py"), ("kernel/src", rs_hits, ".rs")):
        for f in sorted(os.listdir(os.path.join(ROOT, d))):
            if f.endswith(ext):
                out += [(f"{d}/{f}", n, line) for n, line in fn(os.path.join(ROOT, d, f))]
    return out


def test_every_rounding_is_listed():
    found = hits()
    unlisted = [f"{f}:{n}: {line}" for f, n, line in found
                if not any(e[0] == f and e[1] in line for e in rounding.ENTRIES)]
    assert not unlisted, "rounding not listed in model/rounding.py:\n" + "\n".join(unlisted)


def test_every_entry_matches():
    found = hits()
    stale = [e for e in rounding.ENTRIES if not any(f == e[0] and e[1] in line for f, _, line in found)]
    assert not stale, f"model/rounding.py entries that match no line: {stale}"


def test_entry_classes():
    classes = {rounding.TOL, rounding.EXACT, rounding.CONTINUOUS, rounding.DATA, rounding.HELPER, rounding.NONE}
    assert all(e[2] in classes for e in rounding.ENTRIES)


def test_no_untolerated_stat_rounding():
    gaps = [e for e in rounding.ENTRIES if e[2] == rounding.NONE]
    assert not gaps, f"stat-derived roundings without a tolerance: {gaps}"
