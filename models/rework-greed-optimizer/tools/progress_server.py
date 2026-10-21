"""Live progress page for optimizer runs: run.py, then optionally tools/unique_bench.py and report/build_report.py, all
writing to one log. The watcher only reads (the log, the wvk.exe process's CPU time, the Windows sleep log), so the
run is unaffected.

python tools/progress_server.py [LOG] [--port 8765] [--then bench,report] [--repo PATH]

LOG defaults to out/run.log; a relative path is taken from the repository root. Run the pipeline unbuffered into it:
  WV_LEDGER=rework python -u run.py --iters 360000 --p2-iters 4400000 > out/run.log 2>&1
  WV_LEDGER=rework python -u tools/unique_bench.py >> out/run.log 2>&1
  WV_LEDGER=rework python -u report/build_report.py >> out/run.log 2>&1
and open http://127.0.0.1:8765. --then lists the steps expected after run.py (default bench,report; "" for run.py
alone). --repo is the checkout whose kernel/target/release/wvk.exe is the run's kernel (default: this one).

Bars: one per phase (phase 1, phase 2, Edge of Time pass, Python rescore, unique bench, build report). Kernel phases
advance with the `[wvk] n/m jobs` lines; their expected cost is (cost per step) x jobs x iters / threads, the cost per
step being the median of the last runs in out/phase_costs.json (seeded from data/phase_costs.json). Once a kernel phase
is done, its measured/expected ratio rescales the phases still to come. At the end of a watched run the watcher
appends the run's phase costs to out/phase_costs.json.

Lost time (shown as a banner, and left out of rates and phase costs):
- sleep: the Windows Power-Troubleshooter wake events (event 1: sleep and wake time) that overlap the run, and live,
  wall clock minus QueryUnbiasedInterruptTime (which stops while the machine sleeps) between two polls;
- stall: the wvk process's CPU time against wall time while the machine is awake (below a quarter core for a minute),
  between consecutive progress lines, and the process's CPU time against its age when the watcher starts mid-batch;
- for log lines written before the watcher started: gaps between `[wvk]` lines far longer than the batch's median
  time per job (the kernel clock counts time asleep).
"""
import argparse
import codecs
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_COSTS = os.path.join(ROOT, "data", "phase_costs.json")
LIVE_COSTS = os.path.join(ROOT, "out", "phase_costs.json")
LABELS = {"phase1": "Phase 1: restarts", "phase2": "Phase 2: re-anneals", "eot": "Edge of Time pass",
          "rescore": "Python rescore", "bench": "Unique bench", "report": "Build report"}
KERNEL = ("phase1", "phase2", "eot", "bench")
DEFAULT_SECS = {"rescore": 5.0, "report": 30.0}
DEFAULT_COST = 1e-5
POLL_S = 1.0
SAMPLE_S = 2.0
SLEEP_MIN_S = 15.0
STALL_WINDOW_S = 60.0
STALL_CORES = 0.25
LOST_MIN_S = 60.0
RX = {k: re.compile(v) for k, v in {
    "plan": r"^\[progress\] plan (\{.*\})\s*$",
    "wvk": r"^\[wvk\] (\d+)/(\d+) jobs, ([\d.]+)s\s*$",
    "kdone": r"^\[kernel\] (\d+) anneals on (\d+) threads in ([\d.]+)s\s*$",
    "p1done": r"^phase 1 done in ([\d.]+)s; phase 2: (\d+) re-anneals",
    "sdone": r"^search done in ([\d.]+)s; reporting",
    "runwrote": r"^wrote .*results\w*\.json: \d+ builds in (\d+)s",
    "bench": r"^unique bench: (\d+) re-anneals",
    "benchwrote": r"^wrote .*unique_bench\w*\.json in (\d+)s",
    "reportwrote": r"^wrote .*\.html \([\d.]+ MB, \d+ builds\)",
}.items()}

_warned = set()


def warn(key, msg, sink=None):
    """Log a fallback once to stderr (and keep it for the page)."""
    if key not in _warned:
        _warned.add(key)
        print(f"[progress][FALLBACK] {msg}", file=sys.stderr, flush=True)
    if sink is not None and msg not in sink:
        sink.append(msg)


def _union(iv, kind):
    out = []
    for a, b, _ in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b), kind)
        else:
            out.append((a, b, kind))
    return out


def merge(iv):
    """Disjoint (start, end, kind) intervals: the union of the sleeps, then the stalls outside any sleep (the watcher
    sees a sleep as a CPU-idle gap too)."""
    sleeps = _union([x for x in iv if x[2] == "sleep"], "sleep")
    stalls = []
    for a, b, _ in _union([x for x in iv if x[2] != "sleep"], "stall"):
        for s, e, _ in sleeps:
            if s < b and e > a:
                if s > a:
                    stalls.append((a, s, "stall"))
                a = max(a, e)
        if b - a > 30:
            stalls.append((a, b, "stall"))
    return sorted(sleeps + stalls)


def overlap(iv, a, b):
    """Seconds of the (start, end, kind) intervals inside [a, b]."""
    return sum(max(0.0, min(e, b) - max(s, a)) for s, e, _ in merge(iv)) if a is not None and b is not None else 0.0


def sleep_from_clocks(samples, min_s=SLEEP_MIN_S):
    """Sleeps between consecutive polls (wall, unbiased interrupt time): the unbiased clock stops while the machine
    sleeps, so wall minus unbiased is the time asleep. Returns (start, end, 'sleep') with start = end - asleep."""
    out = []
    for (w0, u0), (w1, u1) in zip(samples, samples[1:]):
        lost = (w1 - w0) - (u1 - u0)
        if lost > min_s:
            out.append((w1 - lost, w1, "sleep"))
    return out


def cpu_stalls(samples, window=STALL_WINDOW_S, cores=STALL_CORES):
    """Stalls from (wall, cpu seconds) samples of one kernel process while the machine is awake: runs of samples
    spanning at least `window` seconds whose CPU rate stays below `cores`. Returns (start, end, 'stall')."""
    out, run = [], None
    for (w0, c0), (w1, c1) in zip(samples, samples[1:]):
        idle = w1 > w0 and (c1 - c0) / (w1 - w0) < cores
        if idle:
            run = (run[0] if run else w0, w1)
        if (not idle or (w1, c1) == samples[-1]) and run:
            if run[1] - run[0] >= window:
                out.append((run[0], run[1], "stall"))
            run = None
    return out


def cpu_gaps(points, threads, min_s=LOST_MIN_S):
    """Lost time between consecutive progress lines of one batch from (jobs done, wall, kernel cpu seconds, jobs
    total): the CPU the kernel used in a gap, divided by its usual rate (median cores while at least `threads` jobs
    were left), is the time it actually ran; the rest of the gap was lost to sleep or a stall."""
    rates = [(c1 - c0) / (w1 - w0) for (n0, w0, c0, m), (n1, w1, c1, _) in zip(points, points[1:])
             if w1 > w0 and c1 >= c0 and m - n0 >= threads]
    if not rates:
        return []
    u = statistics.median(rates)
    if u <= 0:
        return []
    out = []
    for (n0, w0, c0, m), (n1, w1, c1, _) in zip(points, points[1:]):
        lost = (w1 - w0) - (c1 - c0) / u
        if m - n0 >= threads and c1 >= c0 and lost > min_s:
            out.append((w1 - lost, w1, "stall"))
    return out


def line_gaps(points, threads, min_s=LOST_MIN_S):
    """Lost time from kernel-clock progress lines alone (jobs done, seconds into the batch, jobs total), for lines
    written before the watcher started: a gap longer than the batch's median seconds per job times the jobs it
    finished, by more than min_s and 3x, counts the excess as lost. Returns (start, end) in batch seconds."""
    per = [(e1 - e0) / (n1 - n0) for (n0, e0, m), (n1, e1, _) in zip(points, points[1:]) if n1 > n0 and m - n0 >= threads]
    if len(per) < 3:
        return []
    r = statistics.median(per)
    out = []
    for (n0, e0, m), (n1, e1, _) in zip(points, points[1:]):
        lost = (e1 - e0) - r * (n1 - n0)
        if m - n0 >= threads and lost > min_s and lost > 2 * r * (n1 - n0):
            out.append((e1 - lost, e1))
    return out


def load_costs():
    for path in (LIVE_COSTS, SEED_COSTS):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            warn("costs-" + path, f"cannot read {path} ({e}); trying the next phase-cost file")
    warn("costs-none", "no phase-cost file; phases are weighted with a default cost per step")
    return {"runs": []}


def cost_per_step(costs, name, sink=None):
    """Thread-seconds per anneal step of a kernel phase: median over the last five recorded runs."""
    xs = []
    for r in costs.get("runs", [])[::-1]:
        p = r.get("phases", {}).get(name)
        if p and p.get("jobs") and p.get("iters") and p.get("secs"):
            xs.append((p["secs"] - p.get("asleep", 0)) * r.get("threads", 1) / (p["jobs"] * p["iters"]))
        if len(xs) == 5:
            break
    if xs:
        return statistics.median(xs)
    if name != "phase2" and name in ("eot", "bench"):
        return cost_per_step(costs, "phase2", sink)
    warn("cost-" + name, f"no recorded cost for {name}; using {DEFAULT_COST:g} thread-seconds per step", sink)
    return DEFAULT_COST


def recorded_secs(costs, name, p1_jobs=0):
    """Awake seconds of a phase whose size is not known yet: the latest run with the same phase-1 job count (the same
    stages and families), else the latest run scaled by the phase-1 job ratio (builds, so bench jobs and report size,
    grow with it)."""
    runs = [r for r in costs.get("runs", []) if r.get("phases", {}).get(name, {}).get("secs") is not None]
    if not runs:
        return None
    same = [r for r in runs if p1_jobs and r["phases"].get("phase1", {}).get("jobs") == p1_jobs]
    r = (same or runs)[-1]
    secs = r["phases"][name]["secs"] - r["phases"][name].get("asleep", 0)
    j = r["phases"].get("phase1", {}).get("jobs")
    return secs * p1_jobs / j if not same and p1_jobs and j else secs


def new_phase(name):
    return {"name": name, "status": "pending", "jobs": 0, "iters": 0, "threads": 0, "done": 0, "el": 0.0,
            "secs": None, "t0": None, "t1": None, "live0": False, "live1": False, "points": [], "x": None}


class Tracker:
    """Phase state from the log lines. feed() takes each line with the wall time it was read (None for lines that
    were already in the log when the watcher started; anchor() then dates them backwards from the log's mtime)."""

    def __init__(self, then, costs):
        self.then = [x for x in then if x]
        self.costs = costs
        self.warnings = []
        self.reset()

    def reset(self):
        self.plan = None
        self.order = ["phase1", "phase2", "eot", "rescore"] + self.then
        self.ph = {n: new_phase(n) for n in self.order}
        self.kqueue = ["phase1", "eot"]
        self.cur = None
        self.bench_next = False
        self.started = False
        self.lost = []

    def _start(self, name, t):
        if name not in self.ph:
            self.ph[name] = new_phase(name)
            self.order.append(name)
        p = self.ph[name]
        if p["status"] == "pending":
            p.update(status="running", t0=t, live0=t is not None)
            for q in self.order[:self.order.index(name)]:
                if self.ph[q]["status"] == "pending":
                    self.ph[q]["status"] = "skipped"
        self.started = True
        return p

    def _end(self, name, t):
        p = self._start(name, t) if self.ph.get(name, {}).get("status") != "running" else self.ph[name]
        p.update(status="done", t1=t, live1=t is not None)
        i = self.order.index(name)
        if i + 1 < len(self.order) and self.order[i + 1] == "report" and name in ("rescore", "bench"):
            self._start("report", t)

    def feed(self, line, t=None, cpu=None):
        for key, rx in RX.items():
            m = rx.match(line)
            if m:
                getattr(self, "on_" + key)(m, t, cpu)
                return key
        return None

    def on_plan(self, m, t, cpu):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            warn("plan-json", "unreadable [progress] plan line; ignored", self.warnings)
            return
        if d.get("tool") == "run":
            if self.started:
                self.reset()
            self.plan = d
            names = [p["name"] for p in d["phases"]]
            self.order = names + ["rescore"] + self.then
            self.ph = {n: new_phase(n) for n in self.order}
            self.kqueue = list(names)
        else:
            if "bench" not in self.ph:
                self.ph["bench"] = new_phase("bench")
                self.order.append("bench")
            self.bench_next = True
        for p in d["phases"]:
            self.ph[p["name"]].update(jobs=p["jobs"], iters=p["iters"], threads=d.get("threads", 0))

    def on_wvk(self, m, t, cpu):
        n, tot, e = int(m.group(1)), int(m.group(2)), float(m.group(3))
        if self.cur is None:
            if self.bench_next:
                self.cur, self.bench_next = "bench", False
            elif self.kqueue:
                self.cur = self.kqueue.pop(0)
            else:
                warn("batch-extra", "a kernel batch the watcher did not expect; its lines are ignored", self.warnings)
                return
            p = self._start(self.cur, None if t is None else t - e)
        p = self.ph[self.cur]
        p.update(done=n, el=e, jobs=tot)
        p["points"].append((n, e, t, cpu, tot))

    def on_kdone(self, m, t, cpu):
        if self.cur is None:
            return
        p = self.ph[self.cur]
        p.update(secs=float(m.group(3)), threads=int(m.group(2)), done=p["jobs"] or int(m.group(1)))
        self._end(self.cur, t)
        self.cur = None

    def on_p1done(self, m, t, cpu):
        if self.plan is None and "phase2" not in self.kqueue:
            self.kqueue.insert(0, "phase2")
        self.ph["phase2"]["jobs"] = int(m.group(2))

    def on_sdone(self, m, t, cpu):
        self._start("rescore", t)["x"] = float(m.group(1))

    def on_runwrote(self, m, t, cpu):
        p = self.ph["rescore"]
        if p["x"] is not None:
            p["secs"] = max(0.0, float(m.group(1)) - p["x"])
        self._end("rescore", t)

    def on_bench(self, m, t, cpu):
        if "bench" not in self.ph:
            self.ph["bench"] = new_phase("bench")
            self.order.append("bench")
        self.ph["bench"]["jobs"] = int(m.group(1))
        self.bench_next = True

    def on_benchwrote(self, m, t, cpu):
        if self.ph.get("bench", {}).get("status") != "done":
            self._end("bench", t)
        elif "report" in self.ph and self.ph["report"]["status"] == "pending":
            self._start("report", t)

    def on_reportwrote(self, m, t, cpu):
        self._end("report", t)

    def duration(self, p, now):
        """Wall seconds the phase has run (or ran)."""
        if p["status"] == "done":
            if p["live0"] and p["live1"]:
                return p["t1"] - p["t0"]
            if p["secs"] is not None:
                return p["secs"]
            if p["t0"] is not None and p["t1"] is not None:
                return p["t1"] - p["t0"]
            return 0.0
        if p["status"] == "running" and p["t0"] is not None:
            return max(0.0, now - p["t0"])
        return 0.0

    def anchor(self, mtime):
        """Date the phases read from the backlog, walking back from the log's last write."""
        cur = mtime
        for name in reversed(self.order):
            p = self.ph[name]
            if p["status"] in ("pending", "skipped"):
                continue
            if p["status"] == "running":
                if p["t0"] is None:
                    p["t0"] = cur - p["el"] if name in KERNEL else cur
                cur = p["t0"]
                continue
            if p["t1"] is None:
                p["t1"] = cur
            if p["t0"] is None:
                p["t0"] = p["t1"] - (p["secs"] or 0.0)
            cur = p["t0"]
        for name in KERNEL:
            p = self.ph.get(name)
            if p and p["t0"] is not None:
                p["points"] = [(n, e, w if w is not None else p["t0"] + e, c, tot) for n, e, w, c, tot in p["points"]]

    def backlog_gaps(self):
        """Lost time in batches read from the backlog (kernel clock only)."""
        out = []
        for name in KERNEL:
            p = self.ph.get(name)
            if not p or p["t0"] is None:
                continue
            pts = [(n, e, tot) for n, e, w, c, tot in p["points"] if c is None]
            for a, b in line_gaps(pts, p["threads"] or os.cpu_count()):
                out.append((p["t0"] + a, p["t0"] + b, "stall"))
        return out

    def live_gaps(self):
        out = []
        for name in KERNEL:
            p = self.ph.get(name)
            if not p:
                continue
            pts = [(n, w, c, tot) for n, e, w, c, tot in p["points"] if c is not None]
            out += cpu_gaps(pts, p["threads"] or os.cpu_count())
        return out

    def phase_name_at(self, t):
        for name in self.order:
            p = self.ph[name]
            if p["t0"] is not None and p["t0"] <= t and (p["t1"] is None or t <= p["t1"]):
                return LABELS.get(name, name)
        return None

    def estimate(self, now, lost):
        """Per-phase rows and the overall fraction and seconds left."""
        rows = []
        exp = {}
        p1 = self.ph["phase1"]["jobs"] if "phase1" in self.ph else 0
        for name in self.order:
            p = self.ph[name]
            if name in KERNEL:
                thr = p["threads"] or (self.plan or {}).get("threads") or os.cpu_count()
                if p["jobs"] and p["iters"]:
                    exp[name] = cost_per_step(self.costs, name, self.warnings) * p["jobs"] * p["iters"] / thr
                else:
                    r = recorded_secs(self.costs, name, p1)
                    if r is None:
                        warn("nojobs-" + name, f"{name}: no job count or recorded time yet; weighted as 60 s", self.warnings)
                    exp[name] = r if r is not None else 60.0
            else:
                r = recorded_secs(self.costs, name, p1)
                exp[name] = r if r is not None else DEFAULT_SECS.get(name, 30.0)
        act = {}
        for name in self.order:
            p = self.ph[name]
            d = self.duration(p, now)
            end = p["t1"] if p["status"] == "done" else now
            act[name] = max(0.0, d - overlap(lost, p["t0"], end)) if p["t0"] is not None else d
        done_k = [n for n in self.order if n in KERNEL and self.ph[n]["status"] == "done" and exp[n] > 0]
        ratio = sum(act[n] for n in done_k) / sum(exp[n] for n in done_k) if done_k else None
        total_rem, total_w = 0.0, 0.0
        for name in self.order:
            p = self.ph[name]
            e = exp[name] * (ratio if ratio and name in KERNEL else 1.0)
            a = act[name]
            if p["status"] == "skipped":
                tot, frac, rem = 0.0, 1.0, 0.0
            elif p["status"] == "done":
                tot, frac, rem = a, 1.0, 0.0
            elif p["status"] == "running" and name in KERNEL and p["jobs"]:
                frac = p["done"] / p["jobs"]
                proj = a / frac if frac > 0 else e
                tot = frac * proj + (1 - frac) * max(e, a)
                rem = max(0.0, tot - a)
                tot = a + rem
            elif p["status"] == "running":
                rem = max(0.0, e - a)
                tot = a + rem
                frac = a / tot if tot else 0.0
            else:
                tot, frac, rem = e, 0.0, e
            total_rem += rem
            total_w += tot
            rows.append({"name": name, "label": LABELS.get(name, name), "status": p["status"], "done": p["done"],
                         "jobs": p["jobs"], "iters": p["iters"], "frac": frac, "active_s": a, "total_s": tot,
                         "rem_s": rem, "lost_s": (self.duration(p, now) - a) if p["t0"] is not None else 0.0})
        for r in rows:
            r["share"] = r["total_s"] / total_w if total_w else 0.0
        frac = 1 - total_rem / total_w if total_w else 0.0
        return rows, frac, total_rem, ratio

    def finished(self):
        last = [n for n in self.order if self.ph[n]["status"] != "skipped"]
        return bool(last) and self.ph[last[-1]]["status"] == "done" and self.ph["phase1"]["status"] == "done"


class Win:
    """Read-only Windows queries: kernel process CPU time, unbiased interrupt time, sleep/wake events."""

    def __init__(self):
        self.ok = os.name == "nt"
        if not self.ok:
            warn("not-windows", "not on Windows: no process CPU or sleep detection; progress from the log only")
            return
        import ctypes
        from ctypes import wintypes as wt
        self.ct = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        H = ctypes.c_void_p

        class PE(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                        ("szExeFile", wt.WCHAR * 260)]
        self.PE = PE
        k.CreateToolhelp32Snapshot.restype = H
        k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k.Process32FirstW.argtypes = [H, ctypes.POINTER(PE)]
        k.Process32NextW.argtypes = [H, ctypes.POINTER(PE)]
        k.OpenProcess.restype = H
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.QueryFullProcessImageNameW.argtypes = [H, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k.GetProcessTimes.argtypes = [H] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        k.CloseHandle.argtypes = [H]
        k.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        self.k, self.wt = k, wt

    def unbiased(self):
        if not self.ok:
            return None
        v = self.ct.c_ulonglong()
        return v.value / 1e7 if self.k.QueryUnbiasedInterruptTime(self.ct.byref(v)) else None

    def kernels(self, exe):
        """wvk.exe processes whose image is `exe`: [(pid, created epoch, cpu seconds)]."""
        if not self.ok:
            return []
        ct, k = self.ct, self.k
        snap = k.CreateToolhelp32Snapshot(0x2, 0)
        if snap is None or snap == ct.c_void_p(-1).value:
            warn("snapshot", "CreateToolhelp32Snapshot failed; no kernel CPU sampling this poll")
            return []
        pids = []
        try:
            e = self.PE()
            e.dwSize = ct.sizeof(self.PE)
            ok = k.Process32FirstW(snap, ct.byref(e))
            while ok:
                if e.szExeFile.lower() == os.path.basename(exe).lower():
                    pids.append(e.th32ProcessID)
                ok = k.Process32NextW(snap, ct.byref(e))
        finally:
            k.CloseHandle(snap)
        out = []
        for pid in pids:
            h = k.OpenProcess(0x1000, False, pid)
            if not h:
                continue
            try:
                buf = ct.create_unicode_buffer(1024)
                n = self.wt.DWORD(1024)
                if not k.QueryFullProcessImageNameW(h, 0, buf, ct.byref(n)):
                    continue
                if os.path.normcase(os.path.abspath(buf.value)) != os.path.normcase(os.path.abspath(exe)):
                    continue
                c, x, kt, ut = (ct.c_ulonglong() for _ in range(4))
                if k.GetProcessTimes(h, ct.byref(c), ct.byref(x), ct.byref(kt), ct.byref(ut)):
                    out.append((pid, (c.value - 116444736000000000) / 1e7, (kt.value + ut.value) / 1e7))
            finally:
                k.CloseHandle(h)
        return out

    def sleeps(self, since):
        """(sleep, wake, 'sleep') from the System log's Power-Troubleshooter event 1, waking after `since`."""
        if not self.ok:
            return []
        q = "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and (EventID=1)]]"
        try:
            p = subprocess.run(["wevtutil", "qe", "System", "/q:" + q, "/rd:true", "/c:50", "/f:xml"],
                               capture_output=True, text=True, timeout=30, creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError) as e:
            warn("wevtutil", f"cannot read the sleep log ({e}); sleep is detected live only")
            return []
        if p.returncode != 0:
            warn("wevtutil-rc", f"wevtutil exit {p.returncode}: {p.stderr.strip()[:200]}; sleep is detected live only")
            return []
        out = []
        for a, b in re.findall(r"SleepTime'>([^<]+)<.*?WakeTime'>([^<]+)<", p.stdout):
            sa, sb = iso_epoch(a), iso_epoch(b)
            if sa and sb and sb > since:
                out.append((sa, sb, "sleep"))
        return out


def iso_epoch(s):
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z", s)
    if not m:
        return None
    t = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    return t + float(m.group(2) or 0)


class LogTail:
    """Appended lines of a log; reads UTF-8 and the UTF-16 that PowerShell 5 redirection writes."""

    def __init__(self, path):
        self.path = path
        self.reset()

    def reset(self):
        self.pos, self.dec, self.buf = 0, None, ""

    def read(self):
        """New complete lines, or None if the file is missing; 'reset' if it shrank (a new run)."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return None
        if size < self.pos:
            self.reset()
            return "reset"
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        self.pos += len(data)
        if not data:
            return []
        if self.dec is None:
            enc = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
            self.dec = codecs.getincrementaldecoder(enc)(errors="replace")
        lines = (self.buf + self.dec.decode(data)).split("\n")
        self.buf = lines.pop()
        return [x.rstrip("\r") for x in lines]


class Monitor:
    def __init__(self, log, then, exe):
        self.log, self.exe = log, exe
        self.lock = threading.Lock()
        self.tail = LogTail(log)
        self.win = Win()
        self.costs = load_costs()
        self.tr = Tracker(then, self.costs)
        self.clock = []
        self.cpu = []
        self.kpid = None
        self.age_lost = []
        self.evlog = []
        self.ev_at = 0.0
        self.last_line = None
        self.loaded = False
        self.status = "waiting for the log"

    def sample(self, now):
        u = self.win.unbiased()
        if u is not None:
            self.clock.append((now, u))
            del self.clock[:-4000]
        ks = self.win.kernels(self.exe)
        if not ks:
            self.kpid = None
            return None
        pid, created, cpu = max(ks, key=lambda x: x[1])
        if pid != self.kpid:
            self.kpid, self.cpu = pid, []
            if created < now - 120:
                self.cpu_start = (created, cpu)
        self.cpu.append((now, cpu))
        del self.cpu[:-4000]
        return cpu

    def age_check(self, now):
        """When the watcher meets a kernel already running, compare its CPU time with its age at the CPU rate the
        watcher then measures."""
        cs = getattr(self, "cpu_start", None)
        if not cs or len(self.cpu) < 2 or self.cpu[-1][0] - self.cpu[0][0] < 30:
            return
        p = self.tr.ph.get(self.tr.cur or "")
        if not p or p["jobs"] - p["done"] < (p["threads"] or os.cpu_count()):
            self.cpu_start = None
            return
        (w0, c0), (w1, c1) = self.cpu[0], self.cpu[-1]
        rate = (c1 - c0) / (w1 - w0)
        if rate > STALL_CORES:
            created, c = cs
            lost = (w0 - created) - c0 / rate
            if lost > LOST_MIN_S:
                self.age_lost = [(w0 - lost, w0, "stall")]
        self.cpu_start = None

    def step(self):
        now = time.time()
        cpu = self.sample(now)
        lines = self.tail.read()
        with self.lock:
            if lines == "reset":
                self.tr.reset()
                self.loaded = False
                lines = self.tail.read()
            if lines is None:
                self.status = f"waiting for {self.log}"
                return
            first = not self.loaded
            for line in lines:
                self.tr.feed(line, None if first else now, None if first else cpu)
            if lines:
                self.last_line = now
            if first:
                mtime = os.path.getmtime(self.log)
                self.tr.anchor(mtime)
                self.last_line = mtime
                self.loaded = True
            self.age_check(now)
            t0 = min([p["t0"] for p in self.tr.ph.values() if p["t0"] is not None] or [now])
            if now - self.ev_at > 60 or (self.clock and sleep_from_clocks(self.clock[-2:])):
                self.evlog = self.win.sleeps(t0 - 60)
                self.ev_at = now
            idle = now - (self.last_line or now)
            if self.tr.finished():
                self.status = "finished"
                self.record()
            elif self.tr.cur and self.kpid is None and idle > 120:
                self.status = f"no kernel process from {self.exe} and no output for {idle / 60:.0f} min: the run may have stopped"
            elif not self.tr.started:
                self.status = "waiting for the first progress line"
            elif not self.tr.cur and self.kpid is None and idle > 120:
                nxt = next((n for n in self.tr.order if self.tr.ph[n]["status"] in ("pending", "running")), None)
                self.status = f"no output for {idle / 60:.0f} min; waiting for {LABELS.get(nxt, nxt)}"
            else:
                self.status = "running"

    def lost(self):
        live_sleep = sleep_from_clocks(self.clock)
        stalls = cpu_stalls(self.cpu) if self.tr.cur and self.tr.cur in KERNEL else []
        return merge(self.evlog + live_sleep + stalls + self.tr.live_gaps() + self.tr.backlog_gaps() + self.age_lost)

    def record(self):
        """Append the finished run's phase costs to out/phase_costs.json once."""
        if getattr(self, "recorded", False):
            return
        self.recorded = True
        tr, lost = self.tr, self.lost()
        if tr.plan is None:
            warn("rec-noplan", "this log has no [progress] plan line (older run.py); its phase costs are not recorded")
            return
        key = f"{self.log}|{os.path.getmtime(self.log):.0f}"
        runs = self.costs.setdefault("runs", [])
        if any(r.get("key") == key for r in runs):
            return
        phases = {}
        for name in tr.order:
            p = tr.ph[name]
            if p["status"] != "done" or p["t0"] is None:
                continue
            if name in KERNEL and p["secs"] is not None:
                phases[name] = {"jobs": p["jobs"], "iters": p["iters"], "secs": round(p["secs"], 1),
                                "asleep": round(overlap(lost, p["t0"], p["t1"]), 1)}
                if not p["iters"]:
                    warn("rec-iters-" + name, f"{name} has no iters (no plan line); recorded without a cost per step")
            elif p["secs"] is not None or (p["live0"] and p["live1"]):
                secs = p["secs"] if p["secs"] is not None else p["t1"] - p["t0"]
                phases[name] = {"secs": round(secs, 1)}
        thr = next((tr.ph[n]["threads"] for n in KERNEL if n in tr.ph and tr.ph[n]["threads"]), os.cpu_count())
        runs.append({"date": time.strftime("%Y-%m-%d %H:%M"), "source": self.log, "key": key, "threads": thr,
                     "phases": phases})
        os.makedirs(os.path.dirname(LIVE_COSTS), exist_ok=True)
        tmp = LIVE_COSTS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.costs, f, indent=1)
        os.replace(tmp, LIVE_COSTS)
        print(f"[progress] recorded phase costs of this run in {LIVE_COSTS}", file=sys.stderr, flush=True)

    def snapshot(self):
        with self.lock:
            now = time.time()
            lost = self.lost()
            rows, frac, rem, ratio = self.tr.estimate(now, lost)
            t0 = min([p["t0"] for p in self.tr.ph.values() if p["t0"] is not None] or [now])
            t_end = max([p["t1"] for p in self.tr.ph.values() if p["t1"] is not None] or [now]) if self.tr.finished() else now
            events = []
            for a, b, kind in lost:
                if b < t0 - 1 or a > t_end + 1:
                    continue
                where = self.tr.phase_name_at((a + b) / 2)
                what = "Machine asleep" if kind == "sleep" else "Kernel stalled (no CPU while awake)"
                events.append({"kind": kind, "start": a, "end": b, "min": (b - a) / 60,
                               "text": f"{what} for {(b - a) / 60:.1f} min, {time.strftime('%H:%M', time.localtime(a))}"
                                       f"-{time.strftime('%H:%M', time.localtime(b))}" + (f" (during {where})" if where else "")})
            util = None
            if len(self.cpu) >= 2 and self.cpu[-1][0] - self.cpu[-2][0] > 0:
                (w0, c0), (w1, c1) = self.cpu[max(0, len(self.cpu) - 6)], self.cpu[-1]
                util = (c1 - c0) / (w1 - w0) if w1 > w0 else None
            done = self.tr.finished()
            return {"log": self.log, "now": now, "status": self.status, "started": t0,
                    "elapsed_s": t_end - t0, "frac": 1.0 if done else frac, "rem_s": 0.0 if done else rem,
                    "eta": None if done else now + rem, "speed_ratio": ratio, "phases": rows, "events": events,
                    "lost_min": sum(e["min"] for e in events), "kernel_pid": self.kpid, "kernel_cores": util,
                    "idle_s": now - self.last_line if self.last_line else None, "warnings": list(self.tr.warnings)}

    def loop(self):
        while True:
            try:
                self.step()
            except Exception as e:
                warn("step-" + type(e).__name__, f"watcher step failed ({type(e).__name__}: {e}); retrying")
            time.sleep(POLL_S)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Run progress</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e2e1dc;--bar:#2f6fb3;--done:#3b8a4a;--warn:#b35a1f;--warnbg:#fbeee4}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#9a9993;--card:#20201e;--line:#34332f;--bar:#5b9be0;--done:#5fb26e;--warn:#f0a067;--warnbg:#3a2618}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px}
h1{font-size:18px;margin:0 0 2px}.mut{color:var(--mut)}.small{font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0}
.row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.track{height:10px;background:var(--line);border-radius:5px;overflow:hidden;margin:6px 0 2px}
.fill{height:100%;background:var(--bar);transition:width .5s}.fill.done{background:var(--done)}
.big .track{height:16px;border-radius:8px}.big .num{font-size:28px;font-weight:600}
.ph{padding:8px 0;border-top:1px solid var(--line)}.ph:first-child{border-top:0}
.skipped{opacity:.45}.banner{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:10px 14px;margin:12px 0}
.tab{font-variant-numeric:tabular-nums}
</style></head><body><main>
<h1>Optimizer run</h1><div class="mut small" id="log"></div>
<div id="banners"></div>
<div class="card big"><div class="row"><span class="num tab" id="pct">-</span><span class="tab" id="eta"></span></div>
<div class="track"><div class="fill" id="all" style="width:0"></div></div>
<div class="row mut small"><span id="status"></span><span class="tab" id="el"></span></div></div>
<div class="card" id="phases"></div>
<div class="mut small" id="foot"></div>
</main><script>
const f=s=>{if(s==null||!isFinite(s))return"-";s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${String(m).padStart(2,"0")}m`:m?`${m}m ${String(x).padStart(2,"0")}s`:`${x}s`};
const clk=t=>new Date(t*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
const esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function tick(){let d;try{d=await (await fetch("state",{cache:"no-store"})).json()}catch(e){document.getElementById("status").textContent="watcher not reachable";return}
document.getElementById("log").textContent=d.log;
const p=Math.round(d.frac*1000)/10;document.getElementById("pct").textContent=p.toFixed(1)+"%";document.title=`${p.toFixed(0)}% run progress`;
document.getElementById("all").style.width=(d.frac*100)+"%";document.getElementById("all").className="fill"+(d.frac>=1?" done":"");
document.getElementById("eta").textContent=d.eta?`${f(d.rem_s)} left, done about ${clk(d.eta)}`:"";
document.getElementById("status").textContent=d.status+(d.idle_s>30&&d.status!="finished"?` (no new output for ${f(d.idle_s)})`:"");
document.getElementById("el").textContent=`elapsed ${f(d.elapsed_s)} since ${clk(d.started)}`;
const b=d.events.map(e=>`<div class="banner">${esc(e.text)}</div>`);
if(d.lost_min>0)b.unshift(`<div class="banner"><b>${d.lost_min.toFixed(0)} min lost</b> to sleep or stalls in this run; rates and the ETA leave it out.</div>`);
document.getElementById("banners").innerHTML=b.join("");
document.getElementById("phases").innerHTML=d.phases.map(r=>{const st=r.status=="skipped"?"skipped":r.status=="done"?`done in ${f(r.active_s)}`:r.status=="running"?`${f(r.active_s)} in, ~${f(r.rem_s)} left`:`~${f(r.total_s)}`;
const jobs=r.jobs?` ${r.done}/${r.jobs} jobs`:"";const lost=r.lost_s>60?`, ${f(r.lost_s)} lost`:"";
return `<div class="ph ${r.status}"><div class="row"><span>${esc(r.label)}<span class="mut small">${jobs}</span></span><span class="mut small tab">${st}${lost} · ${(r.share*100).toFixed(0)}% of run</span></div>
<div class="track"><div class="fill${r.status=="done"?" done":""}" style="width:${(r.status=="skipped"?0:r.frac*100)}%"></div></div></div>`}).join("");
const ft=[];if(d.speed_ratio)ft.push(`kernel speed vs recorded runs: x${(1/d.speed_ratio).toFixed(2)}`);if(d.kernel_pid)ft.push(`wvk pid ${d.kernel_pid}, ${d.kernel_cores==null?"-":d.kernel_cores.toFixed(1)} cores`);
document.getElementById("foot").innerHTML=esc(ft.join(" · "))+d.warnings.map(w=>`<div>fallback: ${esc(w)}</div>`).join("")}
tick();setInterval(tick,2000);
</script></body></html>"""


def serve(mon, port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] == "/state":
                body, ctype = json.dumps(mon.snapshot()).encode(), "application/json"
            elif self.path.split("?")[0] in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class S(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"
        daemon_threads = True

    try:
        srv = S(("127.0.0.1", port), H)
    except OSError as e:
        sys.exit(f"[progress] cannot listen on 127.0.0.1:{port} ({e}); is another watcher running? pass --port")
    srv.serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", nargs="?", default=os.path.join("out", "run.log"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--then", default="bench,report", help="steps after run.py in the same log: bench, report ('' = none)")
    ap.add_argument("--repo", default=ROOT, help="checkout whose kernel/target/release/wvk.exe the run uses")
    a = ap.parse_args()
    log = os.path.normpath(a.log if os.path.isabs(a.log) else os.path.join(ROOT, a.log))
    exe = os.path.join(a.repo, "kernel", "target", "release", "wvk.exe" if os.name == "nt" else "wvk")
    mon = Monitor(log, [x.strip() for x in a.then.split(",")], exe)
    threading.Thread(target=mon.loop, daemon=True).start()
    print(f"[progress] watching {log}; open http://127.0.0.1:{a.port}", flush=True)
    serve(mon, a.port)


if __name__ == "__main__":
    main()
