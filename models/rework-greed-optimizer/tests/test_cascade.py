"""The Arcane Cascade solver (evaluate.cascade_solve) on synthetic rate curves: a linear piece lands on the root's grid
cell in three computes from f(0), a flat curve in two, and a curve that jumps across the diagonal is reported as having
no fixed point instead of converging onto the jump."""
from model import evaluate as ev


def _solve(fn):
    calls = []

    def f(x):
        calls.append(x)
        return fn(x)
    y0 = f(0.0)
    L, ok = ev.cascade_solve(f, 0.0, y0, y0, 1)
    return L, ok, len(calls)


def test_linear_piece_three_computes():
    for a, b in ((0.25, 0.012), (1.7, 0.14), (3.0, 0.6), (2.0, -0.3)):
        L, ok, n = _solve(lambda x: a + b * x)
        root = a / (1.0 - b)
        assert ok and n == 3 and abs(L - root) <= 2 ** -20 * root, (a, b, L, root, n)
        assert L == ev.cascade_grid(L)


def test_flat_curve_two_computes():
    L, ok, n = _solve(lambda x: 5.0)
    assert ok and n == 2 and L == 5.0


def test_jump_has_no_fixed_point():
    L, ok, n = _solve(lambda x: 10.95 + 0.43 * x if x < 12.9 else 10.0)
    assert not ok and 10.0 <= L <= 16.6 and n <= ev.CASCADE_COMPUTES


def test_grid_ignores_rounding_noise_on_round_numbers():
    for x in (5.0, 4.0, 10.0, 0.25, 1.0 / 3.0):
        assert ev.cascade_grid(x) == ev.cascade_grid(x * (1 + 2e-16)) == ev.cascade_grid(x * (1 - 2e-16))
