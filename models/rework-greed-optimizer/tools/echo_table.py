"""Echo pulse table: an exact tick simulation of Wold's Vaults echoing, tabulated for the model.

python tools/echo_table.py                    # writes data/echo_table.json (pulse table "v")
python tools/echo_table.py --check            # simulator vs the table at off-grid points
python tools/echo_table.py --reverb           # adds the Reverberating Echo table "v_reverb" to the file, keeping "v"
python tools/echo_table.py --reverb --check   # reverb simulator vs its table

Game rules simulated (addon EchoingEffectInstance, EchoingPotionEffect, LivingEntityEvents.echoingHit):
- An eligible hit makes an echo on the target with chance p (luck-affected): damage S, decay 1, duration 10N ticks,
  N = floor(1 + effect duration). The target holds one Echoing effect; a weaker echo with a longer duration waits in
  the hidden slot (its duration keeps counting down), a stronger one takes the slot (the old one moves to the hidden
  slot, decayed once, dropping the old hidden chain), an equal one extends the duration keeping the pulse phase.
- Every tick with duration % 10 == 1 the effect pulses: it hurts the target for its damage through shotgunAttack (no
  i-frames, never lucky). A pulse re-procs with chance q = luck(sqrt(chance)): a new echo of damage pulse x decay,
  decay x 0.95 - 0.05, duration 10N.

The table holds V(a, q, N): pulse damage per second in units of S, for one target hit by originals arriving as a
Poisson stream of rate a per second, all of damage S. Echo DPS = V x (the pipeline value of a pulse of S).

Reverberating Echo (etching woldsvaults:reverberation; EchoingPotionEffect skips the pulse damage, LivingEntityEvents
.echoingHit): pulses deal nothing. Instead every echo proc that finds an Echoing effect on the target first
reverberates it: the effect's damage becomes damage x decay (and is dealt at once through shotgunAttack), its decay
steps to decay x 0.95 - 0.05, and EchoingEffectInstance.reverberate re-applies it 10 ticks shorter (or pops the hidden
echo when 11 ticks or fewer remain); the dealt hit is itself an echoing hit, so it re-procs with q and, on a proc,
reverberates the effect again before adding its own echo (damage x the effect's decay). Then the triggering proc adds
its echo as usual. V_reverb(a, q, N) is the reverberated damage per second in units of S, simulated event by event
(nothing happens between procs except the durations running down)."""
import argparse
import json
import math
import multiprocessing as mp
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A_GRID = [10 ** (-2 + k / 8) for k in range(33)]
Q_GRID = [k / 20 for k in range(21)]
N_MAX = 8


class Echo:
    __slots__ = ("damage", "dur", "decay", "src", "hidden")

    def __init__(self, damage, dur, decay, src):
        self.damage, self.dur, self.decay, self.src, self.hidden = damage, dur, decay, src, None

    def copy_decayed(self):
        return Echo(self.damage, self.dur, self.decay * 0.95 - 0.05, self.src)

    def tick_down(self):
        if self.hidden is not None:
            self.hidden.tick_down()
        self.dur -= 1

    def remove(self):
        h = self.hidden
        if h is not None:
            self.damage, self.dur, self.decay, self.src = h.damage, h.dur, h.decay, h.src
            self.hidden = h.hidden

    def update(self, o):
        if o.damage > self.damage:
            if self.dur < o.dur:
                self.hidden = self.copy_decayed()
            self.damage, self.decay, self.dur, self.src = o.damage, o.decay, o.dur, o.src
            return True
        if o.dur > self.dur:
            if self.damage == o.damage:
                self.dur = (o.dur // 10) * 10 + min((o.dur - 1) % 10, (self.dur - 1) % 10) + 1
                self.decay = o.decay
                return True
            if self.hidden is None:
                self.hidden = o.copy_decayed()
            else:
                self.hidden.update(o)
        return False


def simulate(sources, p, q, N, seconds, seed=1):
    """sources: [(hits per second, stored damage S, periodic?)]. Returns (pulses/s, pulse damage/s by source id)."""
    rng = random.Random(seed)
    end = int(seconds * 20)
    nxt = [(rng.random() / r if per else rng.expovariate(r)) * 20 if r > 0 else math.inf for r, _, per in sources]
    top = None
    pulses = 0
    value = [0.0] * len(sources)
    t = 0
    while t < end:
        if top is None:
            t = max(t, int(math.ceil(min(nxt))))
            if t >= end:
                break
        for j, (r, S, per) in enumerate(sources):
            while nxt[j] <= t:
                nxt[j] += 20.0 / r if per else rng.expovariate(r) * 20
                if rng.random() < p:
                    e = Echo(S, 10 * N, 1.0, j)
                    if top is None:
                        top = e
                    else:
                        top.update(e)
        if top is not None:
            if top.dur % 10 == 1:
                pulses += 1
                value[top.src] += top.damage
                if rng.random() < q:
                    nd = top.damage * top.decay
                    if nd > 1.0:
                        top.update(Echo(nd, 10 * N, top.decay * 0.95 - 0.05, top.src))
            top.tick_down()
            if top.dur == 0:
                top.remove()
            if top.dur <= 0:
                top = None
        t += 1
    return pulses / seconds, [v / seconds for v in value]


class Reverb:
    """Event simulation of Reverberating Echo on one target (a single stream of originals of damage S)."""

    def __init__(self, q, N, rng):
        self.q, self.N, self.rng = q, N, rng
        self.top = None
        self.value = 0.0

    def advance(self, k):
        """k ticks of EchoingEffectInstance.tick (pulses deal nothing): the chain's durations run down; at 0 the top
        takes the hidden echo, and an expired top leaves the target."""
        while k > 0 and self.top is not None:
            step = min(k, max(1, self.top.dur))
            e = self.top
            while e is not None:
                e.dur -= step
                e = e.hidden
            k -= step
            if self.top.dur == 0:
                self.top.remove()
            if self.top.dur <= 0:
                self.top = None

    def add(self, e):
        if self.top is None:
            self.top = e
        else:
            self.top.update(e)

    def reverberate(self):
        t = self.top
        dmg = t.damage * t.decay
        t.damage = dmg
        t.decay = t.decay * 0.95 - 0.05
        if t.dur > 11:
            rv = Echo(t.damage, t.dur - 10, t.decay, t.src)
            t.remove()
            t.update(rv)
        else:
            t.remove()
        if dmg > 1.0:
            self.value += dmg
            if self.rng.random() < self.q:
                nd = dmg * t.decay
                ndecay = t.decay * 0.95 - 0.05
                if nd > 1.0:
                    self.reverberate()
                    self.add(Echo(nd, 10 * self.N, ndecay, 0))

    def proc(self, S):
        if self.top is not None:
            self.reverberate()
        self.add(Echo(S, 10 * self.N, 1.0, 0))


def simulate_reverb(a, q, N, procs_n, seed):
    """Reverberated damage per second in units of S for originals arriving at a per second (each already an echo
    proc), averaged over procs_n procs."""
    rng = random.Random(seed)
    sim = Reverb(q, N, rng)
    S = 1e9
    now = 0
    x = 0.0
    for _ in range(procs_n):
        x += rng.expovariate(a) * 20.0
        tick = int(math.ceil(x))
        sim.advance(tick - now)
        now = tick
        sim.proc(S)
    return sim.value / S / (x / 20.0)


def cell_reverb(args):
    a, q, N, seed = args
    return simulate_reverb(a, q, N, REVERB_PROCS, seed)


REVERB_PROCS = 60000


def build_reverb():
    jobs = [(a, q, N, 5000 * N + 50 * i + j) for N in range(1, N_MAX + 1) for i, a in enumerate(A_GRID) for j, q in enumerate(Q_GRID)]
    with mp.Pool(min(12, os.cpu_count())) as pool:
        vals = pool.map(cell_reverb, jobs, chunksize=16)
    na, nq = len(A_GRID), len(Q_GRID)
    table = [[[vals[(N - 1) * na * nq + i * nq + j] for j in range(nq)] for i in range(na)] for N in range(1, N_MAX + 1)]
    path = os.path.join(ROOT, "data", "echo_table.json")
    with open(path, encoding="utf-8") as f:
        out = json.load(f)
    out["v_reverb"] = monotone(table)
    out["doc_reverb"] = ("v_reverb[N-1][a][q]: Reverberating Echo damage per second in units of S (tools/echo_table.py "
                         f"--reverb, {REVERB_PROCS} procs per cell); V ~ a^2 below the grid (a reverberation needs a "
                         "live echo when the next proc lands)")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print("wrote v_reverb into data/echo_table.json")


def check_reverb():
    import sys
    sys.path.insert(0, ROOT)
    from model import procs
    rng = random.Random(9)
    worst = 0.0
    for _ in range(40):
        N = rng.randint(1, 4)
        a = 10 ** rng.uniform(-1.5, 1.5)
        q = rng.random() ** 0.7
        sim = simulate_reverb(a, q, N, 4 * REVERB_PROCS, rng.randrange(1 << 30))
        tab = procs.echo_value(a, q, N, reverb=True)
        err = (tab - sim) / sim if sim > 0 else 0.0
        worst = max(worst, abs(err))
        print(f"N={N} a={a:7.3f} q={q:.3f}: sim {sim:.4f} table {tab:.4f} ({err:+.2%})")
    print(f"worst {worst:.2%}")


def isolated(q, N, chains, seed):
    """Pulse damage of one original echo with no other originals (units of S), averaged over `chains` echoes."""
    rng = random.Random(seed)
    total = 0.0
    for _ in range(chains):
        top = Echo(1e9, 10 * N, 1.0, 0)
        while top is not None:
            if top.dur % 10 == 1:
                total += top.damage / 1e9
                if rng.random() < q:
                    nd = top.damage * top.decay
                    if nd > 1.0:
                        top.update(Echo(nd, 10 * N, top.decay * 0.95 - 0.05, 0))
            top.tick_down()
            if top.dur == 0:
                top.remove()
            if top.dur <= 0:
                top = None
    return total / chains


def cell(args):
    a, q, N, seed = args
    seconds = max(40000.0, 60000.0 / a)
    _, v = simulate([(a, 1e9, False)], 1.0, q, N, seconds, seed)
    return v[0] / 1e9


def monotone(table):
    """V is non-decreasing in the arrival rate (more originals never remove a pulse); a running max along a removes
    simulation noise there, so procs.echo's nested weights are never negative."""
    for vn in table:
        for i in range(1, len(vn)):
            vn[i] = [max(x, y) for x, y in zip(vn[i], vn[i - 1])]
    return table


def build():
    jobs = [(a, q, N, 1000 * N + 50 * i + j) for N in range(1, N_MAX + 1) for i, a in enumerate(A_GRID) for j, q in enumerate(Q_GRID)]
    with mp.Pool(min(8, os.cpu_count())) as pool:
        vals = pool.map(cell, jobs, chunksize=8)
        iso = pool.starmap(isolated, [(q, N, 200000, 7 * N + j) for N in range(1, N_MAX + 1) for j, q in enumerate(Q_GRID)])
    na, nq = len(A_GRID), len(Q_GRID)
    table = [[[vals[(N - 1) * na * nq + i * nq + j] for j in range(nq)] for i in range(na)] for N in range(1, N_MAX + 1)]
    single = [[iso[(N - 1) * nq + j] for j in range(nq)] for N in range(1, N_MAX + 1)]
    out = {"a": A_GRID, "q": Q_GRID, "n_max": N_MAX, "v": monotone(table), "isolated": single,
           "doc": "V[N-1][a][q]: echo pulse damage per second in units of S (tools/echo_table.py); isolated[N-1][q]: "
                  "pulse damage of one lone echo chain, so V ~ a x isolated below the grid"}
    with open(os.path.join(ROOT, "data", "echo_table.json"), "w", encoding="utf-8") as f:
        json.dump(out, f)
    print("wrote data/echo_table.json")


def check():
    import sys
    sys.path.insert(0, ROOT)
    from model import procs
    rng = random.Random(5)
    worst = 0.0
    for _ in range(40):
        N = rng.randint(1, 4)
        a = 10 ** rng.uniform(-1.5, 1.5)
        q = rng.random() ** 0.7
        _, v = simulate([(a, 1e9, False)], 1.0, q, N, max(40000.0, 60000.0 / a), rng.randrange(1 << 30))
        sim = v[0] / 1e9
        tab = procs.echo_value(a, q, N)
        err = (tab - sim) / sim
        worst = max(worst, abs(err))
        print(f"N={N} a={a:7.3f} q={q:.3f}: sim {sim:.4f} table {tab:.4f} ({err:+.2%})")
    print(f"worst {worst:.2%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--monotone", action="store_true", help="re-apply monotone() to the existing table")
    ap.add_argument("--reverb", action="store_true", help="the Reverberating Echo table (v_reverb)")
    a = ap.parse_args()
    if a.reverb:
        check_reverb() if a.check else build_reverb()
    elif a.monotone:
        path = os.path.join(ROOT, "data", "echo_table.json")
        t = json.load(open(path, encoding="utf-8"))
        t["v"] = monotone(t["v"])
        json.dump(t, open(path, "w", encoding="utf-8"))
    else:
        check() if a.check else build()
