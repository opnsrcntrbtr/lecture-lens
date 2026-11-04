"""Known-answer tests for tests/evals/stats.py."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import stats  # noqa: E402


def test_wilson_known_values():
    # 5/10: 0.2366–0.7634 (Brown, Cai & DasGupta 2001, Wilson interval)
    assert stats.wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-4)
    # all pass: lower bound is n / (n + z^2), not 1 as the Wald interval would say
    lo, hi = stats.wilson(6, 6)
    assert lo == pytest.approx(6 / (6 + stats.Z95 ** 2)) and hi == 1.0
    lo, hi = stats.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(stats.Z95 ** 2 / (10 + stats.Z95 ** 2))
    assert stats.wilson(0, 0) is None


def test_wilson_narrows_with_more_cases():
    w = lambda k, n: stats.wilson(k, n)[1] - stats.wilson(k, n)[0]
    assert w(5, 6) > w(50, 60) > w(500, 600)


def test_mcnemar_exact():
    a = [True] * 9 + [False] * 1 + [True] * 5
    b = [False] * 9 + [True] * 1 + [True] * 5
    r = stats.mcnemar_exact(a, b)
    assert (r["a_only"], r["b_only"], r["both"], r["neither"]) == (9, 1, 5, 0)
    assert r["p_value"] == pytest.approx(22 / 1024)
    assert stats.mcnemar_exact([True, False], [True, False])["p_value"] == 1.0
    with pytest.raises(ValueError):
        stats.mcnemar_exact([True], [True, False])


def test_cohen_kappa_textbook_example():
    # 50 items: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po 0.7, pe 0.5, kappa 0.4
    r1 = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    r2 = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert stats.cohen_kappa(r1, r2) == pytest.approx(0.4)
    assert stats.cohen_kappa([1, 0, 1], [1, 0, 1]) == 1.0
    assert stats.cohen_kappa([], []) is None


def test_bootstrap_is_reproducible_and_brackets_the_mean():
    xs = [0, 1] * 20
    mean = lambda s: sum(s) / len(s)
    ci = stats.bootstrap_ci(mean, xs)
    assert ci == stats.bootstrap_ci(mean, xs)
    assert ci[0] < 0.5 < ci[1]


def test_fmt_rate():
    assert stats.fmt_rate(5, 6) == "5/6 (83%, 95% CI 44%–97%)"


# --- summarize.aggregate and compare_runs (no DeepEval needed) ---
import compare_runs  # noqa: E402
import summarize  # noqa: E402


def fake_run(outcomes):
    return {"testCases": [{"name": "test_ask[case]", "input": f"Why is the sky blue? {i}",
                           "metricsData": [{"name": "Faithfulness", "score": 0.9 if ok else 0.2,
                                            "success": ok, "threshold": 0.8}]}
                          for i, ok in enumerate(outcomes)]}


def summary(outcomes):
    _, _, _, per_case = summarize.aggregate(fake_run(outcomes))
    return {"per_case": per_case}


def test_aggregate_adds_wilson_interval():
    metrics, suites, failures, per_case = summarize.aggregate(fake_run([True] * 5 + [False]))
    m = metrics["ask:Faithfulness"]
    assert (m["pass"], m["n"]) == (5, 6)
    assert m["ci95"] == [round(x, 3) for x in stats.wilson(5, 6)]
    assert suites["ask"]["ci95"] and len(failures) == 1 and len(per_case) == 6


def test_compare_runs_pairs_cases_and_tests_the_change():
    before = summary([False] * 9 + [True] * 3)
    after = summary([True] * 9 + [True] * 3)
    r = compare_runs.compare(before, after)["ask:Faithfulness"]
    assert (r["paired"], r["fixed"], r["broke"]) == (12, 9, 0)
    assert r["p_value"] == round(2 / 2 ** 9, 4)
    assert "| ask:Faithfulness | 12 |" in compare_runs.report({"ask:Faithfulness": r})


def test_compare_runs_needs_per_case():
    with pytest.raises(ValueError):
        compare_runs.compare({"metrics": {}}, {"metrics": {}})
