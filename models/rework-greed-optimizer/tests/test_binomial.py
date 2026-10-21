"""(b) model.binomial against brute force: the CDF and the quantile from enumerating all 2^n attempt outcomes with
exact rational weights."""
from fractions import Fraction
from itertools import product

from model import binomial


def _brute_cdf(n, p):
    pf = Fraction(p)
    pmf = [Fraction(0)] * (n + 1)
    for outcome in product((0, 1), repeat=n):
        k = sum(outcome)
        w = Fraction(1)
        for x in outcome:
            w *= pf if x else 1 - pf
        pmf[k] += w
    acc, cdf = Fraction(0), []
    for x in pmf:
        acc += x
        cdf.append(acc)
    return cdf


PS = [0.0, 0.01, 0.05, 0.1, 0.1875, 0.25, 0.3, 1 / 3, 0.5, 0.62, 0.75, 0.9, 0.99, 1.0]
QS = [0.0, 0.05, 0.1, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0]


def test_cdf_matches_enumeration():
    for n in range(0, 9):
        for p in PS:
            exact = _brute_cdf(n, p)
            for k in range(-1, n + 2):
                want = 0.0 if k < 0 else float(exact[min(k, n)])
                got = binomial.cdf(n, p, k)
                assert abs(got - want) <= 1e-12, (n, p, k, got, want)


def test_quantile_matches_enumeration():
    for n in range(0, 9):
        for p in PS:
            exact = _brute_cdf(n, p)
            for q in QS:
                qf = Fraction(q)
                ties = [k for k in range(n + 1) if abs(exact[k] - qf) <= Fraction(1, 10 ** 9)]
                want = next(k for k in range(n + 1) if exact[k] >= qf - Fraction(1, 10 ** 9))
                got = binomial.quantile(n, p, q)
                assert got == want or ties, (n, p, q, got, want)
                assert binomial.cdf(n, p, got) >= q - 1e-9
                if got > 0:
                    assert binomial.cdf(n, p, got - 1) < q - 1e-9 or ties
