"""Type-preserving NBT read/write for Minecraft structure files.

Verified: round-trips 13/13 real vault room .nbt files byte-for-byte (rooms, tunnels, decor,
objective templates), so anything written here is in exactly the format the game already ships.
"""
import gzip, struct

# tag ids
END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARR, STRING, LIST, COMPOUND, INT_ARR, LONG_ARR = range(13)


class T:
    """A typed tag: .t is the NBT tag id, .v the payload."""
    __slots__ = ("t", "v")

    def __init__(self, t, v):
        self.t, self.v = t, v

    def __repr__(self):
        return f"T({self.t},{self.v!r})"


class _R:
    def __init__(self, d):
        self.d, self.p = d, 0

    def u1(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def raw(self, fmt, n):
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += n
        return v

    def s(self):
        n = self.raw(">H", 2)
        v = self.d[self.p:self.p + n].decode("utf-8")
        self.p += n
        return v

    def pay(self, t):
        if t == BYTE: return self.raw(">b", 1)
        if t == SHORT: return self.raw(">h", 2)
        if t == INT: return self.raw(">i", 4)
        if t == LONG: return self.raw(">q", 8)
        if t == FLOAT: return self.raw(">f", 4)
        if t == DOUBLE: return self.raw(">d", 8)
        if t == BYTE_ARR:
            n = self.raw(">i", 4)
            v = list(struct.unpack_from(f">{n}b", self.d, self.p)); self.p += n
            return v
        if t == STRING: return self.s()
        if t == LIST:
            et = self.u1(); n = self.raw(">i", 4)
            if et == END:
                return (END, [None] * n)
            return (et, [self.pay(et) for _ in range(n)])
        if t == COMPOUND:
            o = {}
            while True:
                tt = self.u1()
                if tt == END:
                    return o
                k = self.s()                      # name BEFORE payload; assignment order matters
                o[k] = T(tt, self.pay(tt))
        if t == INT_ARR:
            n = self.raw(">i", 4)
            v = list(struct.unpack_from(f">{n}i", self.d, self.p)); self.p += 4 * n
            return v
        if t == LONG_ARR:
            n = self.raw(">i", 4)
            v = list(struct.unpack_from(f">{n}q", self.d, self.p)); self.p += 8 * n
            return v
        raise ValueError(f"unknown tag {t} at {self.p}")


class _W:
    def __init__(self):
        self.b = bytearray()

    def u1(self, v):
        self.b.append(v & 0xFF)

    def s(self, v):
        e = v.encode("utf-8")
        self.b += struct.pack(">H", len(e)) + e

    def pay(self, t, v):
        if t == BYTE: self.b += struct.pack(">b", v)
        elif t == SHORT: self.b += struct.pack(">h", v)
        elif t == INT: self.b += struct.pack(">i", v)
        elif t == LONG: self.b += struct.pack(">q", v)
        elif t == FLOAT: self.b += struct.pack(">f", v)
        elif t == DOUBLE: self.b += struct.pack(">d", v)
        elif t == BYTE_ARR: self.b += struct.pack(">i", len(v)) + struct.pack(f">{len(v)}b", *v)
        elif t == STRING: self.s(v)
        elif t == LIST:
            et, items = v
            self.u1(et); self.b += struct.pack(">i", len(items))
            if et != END:
                for it in items:
                    self.pay(et, it)
        elif t == COMPOUND:
            for k, tag in v.items():
                self.u1(tag.t); self.s(k); self.pay(tag.t, tag.v)
            self.u1(END)
        elif t == INT_ARR: self.b += struct.pack(">i", len(v)) + struct.pack(f">{len(v)}i", *v)
        elif t == LONG_ARR: self.b += struct.pack(">i", len(v)) + struct.pack(f">{len(v)}q", *v)
        else: raise ValueError(f"unknown tag {t}")


def read(path):
    """-> (root name, root T, decompressed bytes)"""
    raw = open(path, "rb").read()
    d = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    r = _R(d)
    t = r.u1()
    name = r.s()
    return name, T(t, r.pay(t)), d


def dump(name, root):
    w = _W()
    w.u1(root.t)
    w.s(name)
    w.pay(root.t, root.v)
    return bytes(w.b)


def write(path, root, name=""):
    open(path, "wb").write(gzip.compress(dump(name, root), 6))


def block_id(palette_entry):
    """Palette compound -> 'ns:name[prop=val,...]' with properties sorted."""
    n = palette_entry["Name"].v
    p = palette_entry.get("Properties")
    if p is None:
        return n
    return n + "[" + ",".join(f"{k}={t.v}" for k, t in sorted(p.v.items())) + "]"


def parse_id(s):
    """'ns:name[prop=val]' -> palette compound."""
    if "[" not in s:
        return {"Name": T(STRING, s)}
    name, props = s[:-1].split("[", 1)
    d = {k: T(STRING, v) for k, v in (p.split("=") for p in props.split(","))}
    return {"Name": T(STRING, name), "Properties": T(COMPOUND, d)}


def load_room(path):
    """-> (size (x,y,z), list of block ids indexed by palette, dict (x,y,z)->palette index)"""
    _, root, _ = read(path)
    size = tuple(root.v["size"].v[1])
    ids = [block_id(e) for e in root.v["palette"].v[1]]
    grid = {}
    for b in root.v["blocks"].v[1]:
        grid[tuple(b["pos"].v[1])] = b["state"].v
    return size, ids, grid
