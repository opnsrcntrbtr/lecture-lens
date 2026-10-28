import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import calibration as cal


def test_kappa_and_false_pass():
    pairs = [(1, 1), (1, 0), (0, 0), (0, 0), (1, 1), (0, 1)]
    s = cal.summary(pairs)
    assert s["agreement"] == round(4 / 6, 3)
    assert s["false_pass_rate"] == round(1 / 3, 3)
    assert -1 <= s["kappa"] <= 1
    assert cal.kappa([(1, 1), (0, 0)]) == 1.0


def test_balanced_sample_covers_metrics_and_verdicts():
    rows = [{"id": f"{m}{v}{i}", "metric": m, "judge_pass": v} for m in "ab" for v in (0, 1) for i in range(5)]
    pick = cal.balanced(rows, 8)
    assert {(r["metric"], r["judge_pass"]) for r in pick} == {("a", 0), ("a", 1), ("b", 0), ("b", 1)}


def test_report_ignores_unlabelled():
    rows = [{"metric": "m", "judge_pass": "1", "human_pass": "1"}, {"metric": "m", "judge_pass": "1", "human_pass": ""}]
    assert cal.report(rows)["labelled"] == 1


def test_report_splits_human_and_panel_labels():
    rows = [{"metric": "m", "judge_pass": "1", "human_pass": "1", "label_source": "human"},
            {"metric": "m", "judge_pass": "1", "human_pass": "0", "label_source": "panel"},
            {"metric": "m", "judge_pass": "0", "human_pass": "0", "label_source": "panel-split"}]
    r = cal.report(rows)
    assert r["by_source"]["human"]["n"] == 1 and r["by_source"]["panel"]["n"] == 2
    assert r["by_source"]["panel"]["false_pass_rate"] == 1.0
