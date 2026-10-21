"""(c) hyper.solve_damage is non-decreasing in dps and hyper.solve_survival in the cap, on both ledgers' grids and with
and without the boss damage cap; (d) procs.echo_value is non-decreasing in the arrival rate a for every table row."""
import math

from model import hyper, procs, stages


def _grid():
    from model.context import Context
    from model.evaluate import boss_grid
    return boss_grid(Context("max", stages.MODES[0]))


def _logspace(lo, hi, n):
    return [10 ** (lo + (hi - lo) * i / (n - 1)) for i in range(n)]


def test_solve_damage_monotone_in_dps():
    g = _grid()
    T = stages.KNOBS["kill_time_s"]
    for slope in (0.0, 1e-6, 1e-3):
        prev = -math.inf
        for dps in _logspace(3, 13, 400):
            c = hyper.solve_damage(g, dps, slope * dps, T)
            assert c >= prev - 1e-12, (slope, dps, c, prev)
            prev = c


def test_solve_survival_monotone_in_cap():
    g = _grid()
    for dmg_cap in (False, True):
        prev = -math.inf
        for cap in _logspace(0, 14, 400):
            c = hyper.solve_survival(g, cap, dmg_cap)
            assert c >= prev - 1e-12, (dmg_cap, cap, c, prev)
            prev = c


def test_echo_value_monotone_in_rate():
    t = procs.echo_table()
    for n in range(1, t["n_max"] + 1):
        for q in [i / 20 for i in range(21)]:
            prev = 0.0
            for a in _logspace(-4, 3, 300):
                v = procs.echo_value(a, q, n)
                assert v >= prev - 1e-12 * max(1.0, prev), (n, q, a, v, prev)
                prev = v
