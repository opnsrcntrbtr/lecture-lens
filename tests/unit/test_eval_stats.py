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


# --- judge benchmark scores (E3) ---
import judge_bench  # noqa: E402


def test_judge_scores_carry_kappa_and_intervals():
    labels = [1] * 36 + [0] * 36
    perfect = judge_bench.scores(labels, [0.9] * 36 + [0.1] * 36)
    assert perfect["accuracy"] == 1.0 and perfect["kappa"] == 1.0
    lo, hi = perfect["accuracy_ci95"]
    assert hi == 1.0 and lo < 0.96          # 72/72 is not proof of 100%
    assert perfect["auroc_ci95"] == (1.0, 1.0)
    chance = judge_bench.scores(labels, [0.9, 0.1] * 36)
    assert abs(chance["kappa"]) < 1e-9 and chance["accuracy"] == 0.5


def test_judge_pairs_are_balanced_and_well_formed():
    import collections
    import json
    here = Path(__file__).resolve().parents[1] / "evals"
    contexts = json.loads((here / "contexts.json").read_text())
    pairs = json.loads((here / "judge_pairs.json").read_text())["pairs"]
    supported = {"paraphrase", "derived"}
    for p in pairs:
        assert 0 <= p["ctx"] < len(contexts)
        assert p["label"] == (1 if p["kind"] in supported else 0), p
    labels = collections.Counter(p["label"] for p in pairs)
    assert labels[0] == labels[1]                                  # balanced: accuracy is not inflated by a base rate
    assert len({p["claim"] for p in pairs}) == len(pairs)          # no duplicate claims
    kinds = collections.Counter(p["kind"] for p in pairs)
    assert kinds["partial"] >= 12 and kinds["derived"] >= 12       # D6: hard pairs present


def test_sign_test_known_values():
    r = stats.sign_test([1] * 9 + [-1] + [0] * 5)                     # 9 up, 1 down, 5 ties
    assert (r["better"], r["worse"], r["ties"]) == (9, 1, 5)
    assert r["p_value"] == pytest.approx(22 / 1024)                   # same binomial as McNemar 9 vs 1
    assert stats.sign_test([0, 0])["p_value"] == 1.0
    assert stats.sign_test([0.2, -0.1, 0.3, -0.4])["p_value"] == 1.0  # 2 vs 2


# --- compare_outputs: model-free checks between two generators (S6) ---
import compare_outputs as co  # noqa: E402

NOTES_OK = "\n".join(f"## {h}\n- point" for h in co.NOTE_HEADINGS)


def outputs(gen, recall_hit, notes_text, cards=4, slow=1.0):
    ctx = ["Transcript [00:01:00]: Bees pollinate apple blossoms in spring orchards."]
    return {"generator": gen,
            "ask": [{"input": f"q{i}", "expected_output": "Bees pollinate apple blossoms",
                     "actual_output": ("Bees pollinate apple blossoms [1]." if i < recall_hit else "Wind moves seeds around [1]."),
                     "retrieval_context": ctx, "latency_s": slow} for i in range(12)],
            "notes": [{"input": f"n{i}", "actual_output": notes_text, "retrieval_context": ctx, "latency_s": slow} for i in range(4)],
            "cards": [{"input": f"c{i}", "n_cards": cards, "actual_output": ""} for i in range(4)]}


def test_compare_outputs_pairs_cases_and_reports_uncertainty():
    a = outputs("big", 12, NOTES_OK)
    b = outputs("small", 3, NOTES_OK + "\n- see [slide 4]", cards=3, slow=4.0)
    res = co.compare(a, b)
    r = res["ask:recall"]
    assert r["paired"] == 12 and r["a"]["mean"] == 1.0 and r["b"]["mean"] < 0.5
    assert r["sign_test"]["worse"] == 9 and r["sign_test"]["p_value"] < 0.01
    lo, hi = r["diff_ci95"]; assert lo <= r["diff_mean"] <= hi < 0
    assert res["notes:refs_ok"]["a"]["pass"] == 4 and res["notes:refs_ok"]["b"]["pass"] == 0   # phantom [slide 4]
    assert res["notes:sections"]["b"]["pass"] == 4
    assert res["cards:four"]["b"]["pass"] == 0 and res["cards:four"]["p_value"] == round(2 / 16, 4)
    assert res["ask:latency_s"]["diff_mean"] == 3.0
    table = co.report(res, "big", "small")
    assert "| ask:recall | 12 |" in table and "sign test" in table and "McNemar" in table


def test_compare_outputs_checks():
    assert co.refs_ok("water [slide 1] early", "[slide 1 @ 00:00:10] Raised beds")
    assert not co.refs_ok("water [deck 2]", "[slide 1 @ 00:00:10] Raised beds")
    assert co.has_sections(NOTES_OK) and not co.has_sections("## TL;DR\n- only one")
    assert co.recall("", "anything") is None
    only_a = {"ask": [{"input": "x", "actual_output": "a"}]}
    assert co.compare(only_a, {"ask": [{"input": "y", "actual_output": "b"}]}) == {}    # no shared case
