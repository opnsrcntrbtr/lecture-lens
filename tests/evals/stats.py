"""Small-sample statistics for eval results. Standard library only, no model,
so it is unit-tested in tests/unit and usable from any eval script.

Our suites are small (6 smoke cases, 72 judge pairs), so a pass rate means
little without its uncertainty, and two runs must be compared case by case.

- wilson: Wilson (1927) score interval for a binomial proportion; it keeps
  sensible coverage at small n and at 0% or 100%, where the normal
  ("Wald") interval collapses (Brown, Cai & DasGupta 2001).
- mcnemar_exact: McNemar (1947) test on paired pass/fail outcomes, exact
  binomial form; recommended for comparing two classifiers on the same cases
  (Dietterich 1998).
- cohen_kappa: agreement beyond chance between two raters (Cohen 1960).
- bootstrap_ci: percentile bootstrap interval for any statistic
  (Efron & Tibshirani 1993), seeded so reports are reproducible.
- sign_test: exact two-sided sign test on paired differences (Dixon & Mood
  1946); ties are dropped. Assumes nothing about the size of differences.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """95% Wilson score interval (low, high) for successes/n; None when n == 0."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def mcnemar_exact(a_pass: Sequence[bool], b_pass: Sequence[bool]) -> dict:
    """Paired comparison of run A and run B on the same cases.

    Only discordant cases count: b = A passed and B failed, c = A failed and B
    passed. Two-sided exact p = min(1, 2 * P(X <= min(b, c))), X ~ Bin(b + c, 1/2).
    """
    if len(a_pass) != len(b_pass):
        raise ValueError("runs must be scored on the same cases")
    b = sum(1 for x, y in zip(a_pass, b_pass) if x and not y)
    c = sum(1 for x, y in zip(a_pass, b_pass) if y and not x)
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)
    return {"a_only": b, "b_only": c, "both": sum(1 for x, y in zip(a_pass, b_pass) if x and y),
            "neither": sum(1 for x, y in zip(a_pass, b_pass) if not x and not y), "p_value": p}


def cohen_kappa(r1: Sequence, r2: Sequence) -> float | None:
    """Cohen's kappa for two raters over the same items (any hashable labels)."""
    if len(r1) != len(r2) or not r1:
        return None
    n = len(r1)
    po = sum(a == b for a, b in zip(r1, r2)) / n
    labels = set(r1) | set(r2)
    pe = sum((list(r1).count(k) / n) * (list(r2).count(k) / n) for k in labels)
    if pe == 1:
        return 1.0 if po == 1 else None
    return (po - pe) / (1 - pe)


def bootstrap_ci(stat: Callable[[list], float | None], items: Sequence, n_boot: int = 2000,
                 alpha: float = 0.05, seed: int = 0) -> tuple[float, float] | None:
    """Percentile bootstrap interval of stat(resample). Resamples where stat is
    undefined (returns None) are skipped; None if too few remain."""
    rng = random.Random(seed)
    items = list(items)
    vals = []
    for _ in range(n_boot):
        v = stat([items[rng.randrange(len(items))] for _ in items])
        if v is not None:
            vals.append(v)
    if len(vals) < n_boot // 2:
        return None
    vals.sort()
    lo = vals[int(alpha / 2 * (len(vals) - 1))]
    hi = vals[int((1 - alpha / 2) * (len(vals) - 1))]
    return lo, hi


def sign_test(diffs: Sequence[float]) -> dict:
    """Exact two-sided sign test on paired differences (after minus before); zeros are ties
    and are dropped. p = min(1, 2 * P(X <= min(pos, neg))), X ~ Bin(pos + neg, 1/2)."""
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(pos, neg) + 1)) / 2 ** n)
    return {"better": pos, "worse": neg, "ties": len(diffs) - n, "p_value": p}


def fmt_rate(successes: int, n: int) -> str:
    """'5/6 (83%, 95% CI 44%–97%)' for reports."""
    ci = wilson(successes, n)
    if ci is None:
        return "0/0"
    return f"{successes}/{n} ({successes / n:.0%}, 95% CI {ci[0]:.0%}–{ci[1]:.0%})"
