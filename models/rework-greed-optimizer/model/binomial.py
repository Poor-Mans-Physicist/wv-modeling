"""Binomial quantile for the survival window (execution plan 1.3); kernel/src/survival.rs binom_quantile mirrors the
arithmetic term by term."""

QUANTILE_TOL = 1e-9


def pmf_terms(n, p):
    """P(K = k) for k = 0..n, K ~ Binomial(n, p), by the ratio recurrence from (1 - p)^n (repeated products, so the
    kernel reproduces every bit). p = 1 puts all mass on n."""
    if p >= 1.0:
        return [0.0] * n + [1.0]
    q = 1.0 - p
    t = 1.0
    for _ in range(n):
        t *= q
    out = [t]
    r = p / q
    for k in range(n):
        t = t * (n - k) / (k + 1) * r
        out.append(t)
    return out


def quantile(n, p, q):
    """Smallest k with P(K <= k) >= q (within QUANTILE_TOL, the rounding rule), K ~ Binomial(n, p); n when the
    cumulative sum never gets there (rounding)."""
    if p <= 0.0:
        return 0
    c = 0.0
    for k, t in enumerate(pmf_terms(n, p)):
        c += t
        if c >= q - QUANTILE_TOL:
            return k
    return n


def cdf(n, p, k):
    """P(K <= k) for K ~ Binomial(n, p), the same terms quantile walks, summed in k order."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    c = 0.0
    for j, t in enumerate(pmf_terms(n, p)):
        if j > k:
            break
        c += t
    return c
