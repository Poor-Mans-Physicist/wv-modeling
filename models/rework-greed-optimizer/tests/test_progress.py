"""tools/progress_server.py: log parsing, phase estimates and lost-time detection (sleep and stalls)."""
import progress_server as ps

LOG_2026_10_18 = """[wvk] 268/2688 jobs, 28.5s
[wvk] 536/2688 jobs, 56.7s
[wvk] 2688/2688 jobs, 347.8s
[kernel] 2688 anneals on 24 threads in 347.9s
phase 1 done in 348.3s; phase 2: 672 re-anneals
[wvk] 67/672 jobs, 111.8s
[wvk] 672/672 jobs, 1151.4s
[kernel] 672 anneals on 24 threads in 1151.4s
Edge of Time pass skips early: no time node is affordable with that stage's god XP
[wvk] 50/504 jobs, 81.8s
[wvk] 100/504 jobs, 563.7s
[wvk] 150/504 jobs, 646.3s
[wvk] 200/504 jobs, 718.4s
[wvk] 250/504 jobs, 793.3s
[wvk] 300/504 jobs, 882.5s
[wvk] 350/504 jobs, 970.4s
[wvk] 400/504 jobs, 1052.7s
[wvk] 450/504 jobs, 1124.0s
[wvk] 500/504 jobs, 1202.6s
[wvk] 504/504 jobs, 1215.6s
[kernel] 504 anneals on 24 threads in 1215.6s
search done in 2715.2s; reporting
50/336 reported, 2716s
wrote C:\\x\\out\\results_rework.json: 293 builds in 2717s""".splitlines()

COSTS = {"runs": [{"threads": 24, "phases": {"phase1": {"jobs": 100, "iters": 1000, "secs": 10.0},
                                              "phase2": {"jobs": 10, "iters": 10000, "secs": 20.0, "asleep": 10.0},
                                              "rescore": {"secs": 2.0}}}]}


def tracker(lines, then=()):
    tr = ps.Tracker(list(then), COSTS)
    for x in lines:
        tr.feed(x)
    return tr


def test_old_log_without_plan():
    tr = tracker(LOG_2026_10_18)
    assert [tr.ph[n]["status"] for n in ("phase1", "phase2", "eot", "rescore")] == ["done"] * 4
    assert (tr.ph["phase1"]["jobs"], tr.ph["phase2"]["jobs"], tr.ph["eot"]["jobs"]) == (2688, 672, 504)
    assert tr.ph["eot"]["secs"] == 1215.6 and abs(tr.ph["rescore"]["secs"] - 1.8) < 1e-9
    assert tr.finished()


def test_line_gaps_find_the_2026_10_18_sleep():
    """The Edge of Time batch of that run spans the machine's 425 s sleep (event log: 06:52:57-07:00:02 UTC); the
    kernel clock counts it, so the 50->100 gap is ~400 s longer than the batch's pace."""
    tr = tracker(LOG_2026_10_18)
    tr.anchor(1760771467.0)
    gaps = tr.backlog_gaps()
    assert len(gaps) == 1
    a, b, kind = gaps[0]
    assert kind == "stall" and 380 < b - a < 420
    p = tr.ph["eot"]
    assert abs((b - p["t0"]) - 563.7) < 1e-6
    assert tr.phase_name_at((a + b) / 2) == ps.LABELS["eot"]


def test_plan_line_orders_batches_and_bench():
    lines = ['[progress] plan {"tool": "run", "ledger": "rework", "threads": 24, "phases": ['
             '{"name": "phase1", "jobs": 100, "iters": 1000}, {"name": "eot", "jobs": 8, "iters": 500}]}',
             "[wvk] 100/100 jobs, 9.0s", "[kernel] 100 anneals on 24 threads in 9.0s",
             "[wvk] 4/8 jobs, 1.0s"]
    tr = tracker(lines, then=("bench", "report"))
    assert tr.ph["phase1"]["status"] == "done" and tr.ph["eot"]["status"] == "running"
    assert "phase2" not in tr.ph
    tr.feed("[wvk] 8/8 jobs, 2.0s")
    tr.feed("[kernel] 8 anneals on 24 threads in 2.0s")
    tr.feed("search done in 12.0s; reporting")
    tr.feed("wrote C:\\x\\out\\results_rework.json: 5 builds in 13s")
    tr.feed("unique bench: 30 re-anneals (5 builds)")
    tr.feed('[progress] plan {"tool": "unique_bench", "threads": 24, "phases": [{"name": "bench", "jobs": 30, "iters": 400}]}')
    tr.feed("[wvk] 30/30 jobs, 3.0s")
    tr.feed("[kernel] 30 anneals on 24 threads in 3.0s")
    assert tr.ph["bench"]["status"] == "done" and tr.ph["bench"]["iters"] == 400
    assert tr.ph["report"]["status"] == "running" and not tr.finished()
    tr.feed("wrote C:\\x\\build_explorer_rework.html (3.10 MB, 5 builds)")
    assert tr.finished()


def test_estimate_weights_and_speed_ratio():
    lines = ['[progress] plan {"tool": "run", "ledger": "rework", "threads": 24, "phases": ['
             '{"name": "phase1", "jobs": 100, "iters": 1000}, {"name": "phase2", "jobs": 10, "iters": 10000}]}']
    tr = tracker(lines)
    rows, frac, rem, ratio = tr.estimate(1000.0, [])
    by = {r["name"]: r for r in rows}
    assert abs(by["phase1"]["total_s"] - 10.0) < 1e-9 and abs(by["phase2"]["total_s"] - 10.0) < 1e-9
    assert ratio is None and frac == 0.0 and abs(rem - 22.0) < 1e-9
    tr.feed("[wvk] 100/100 jobs, 20.0s", t=1020.0)
    tr.feed("[kernel] 100 anneals on 24 threads in 20.0s", t=1020.0)
    rows, frac, rem, ratio = tr.estimate(1020.0, [])
    by = {r["name"]: r for r in rows}
    assert abs(ratio - 2.0) < 1e-9 and abs(by["phase2"]["total_s"] - 20.0) < 1e-9
    assert abs(frac - 20.0 / 42.0) < 1e-9


def test_lost_time_is_left_out_of_phase_time():
    tr = tracker(['[progress] plan {"tool": "run", "ledger": "rework", "threads": 24, "phases": ['
                  '{"name": "phase1", "jobs": 100, "iters": 1000}]}'])
    tr.feed("[wvk] 10/100 jobs, 100.0s", t=1100.0)
    rows, *_ = tr.estimate(1100.0, [(1010.0, 1090.0, "sleep")])
    r = rows[0]
    assert abs(r["active_s"] - 20.0) < 1e-9 and abs(r["lost_s"] - 80.0) < 1e-9


def test_sleep_from_clocks():
    samples = [(0.0, 50.0), (1.0, 51.0), (2.0, 52.0), (1202.0, 53.0), (1203.0, 54.0)]
    out = ps.sleep_from_clocks(samples)
    assert out == [(3.0, 1202.0, "sleep")]


def test_cpu_stalls_and_gaps():
    samples = [(float(t), 24.0 * min(t, 100)) for t in range(0, 200, 2)] + [(200.0, 2400.0 + 48.0)]
    st = ps.cpu_stalls(samples)
    assert len(st) == 1 and st[0][0] == 100.0 and st[0][2] == "stall"
    pts = [(0, 0.0, 0.0, 1000), (100, 10.0, 240.0, 1000), (200, 20.0, 480.0, 1000), (300, 330.0, 720.0, 1000),
           (400, 340.0, 960.0, 1000)]
    gaps = ps.cpu_gaps(pts, 24)
    assert len(gaps) == 1 and abs((gaps[0][1] - gaps[0][0]) - 300.0) < 1e-9


def test_merge_keeps_sleep_over_stall():
    m = ps.merge([(100.0, 400.0, "stall"), (150.0, 350.0, "sleep"), (500.0, 520.0, "sleep")])
    assert m == [(100.0, 150.0, "stall"), (150.0, 350.0, "sleep"), (350.0, 400.0, "stall"), (500.0, 520.0, "sleep")]
    assert ps.overlap(m, 0.0, 1000.0) == 320.0


def test_log_tail_reads_utf16_and_partial_lines(tmp_path=None):
    import os
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "run.log")
    with open(path, "wb") as f:
        f.write("[wvk] 1/2 jobs, 1.0s\r\n[wvk] 2/".encode("utf-16"))
    t = ps.LogTail(path)
    assert t.read() == ["[wvk] 1/2 jobs, 1.0s"]
    with open(path, "ab") as f:
        f.write("2 jobs, 2.0s\r\n".encode("utf-16-le"))
    assert t.read() == ["[wvk] 2/2 jobs, 2.0s"]


def test_recorded_secs_prefers_same_size_then_scales():
    costs = {"runs": [{"phases": {"phase1": {"jobs": 2688}, "bench": {"secs": 900.0, "asleep": 100.0}}},
                      {"phases": {"phase1": {"jobs": 704}, "bench": {"secs": 60.0}}},
                      {"phases": {"phase1": {"jobs": 2688}, "rescore": {"secs": 2.0}}}]}
    assert ps.recorded_secs(costs, "bench", 2688) == 800.0
    assert ps.recorded_secs(costs, "bench", 704) == 60.0
    assert ps.recorded_secs(costs, "bench", 1408) == 120.0
    assert ps.recorded_secs(costs, "report", 704) is None
