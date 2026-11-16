"""Judge card (EP-1): invented sheets; the always-pass judge is caught by its TNR."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import judge_card as jc  # noqa: E402


def sheet(tmp_path, rows, name="labels.csv"):
    f = tmp_path / name
    keys = sorted({k for r in rows for k in r})
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    return f


def test_always_pass_judge_has_high_agreement_and_fails_on_tnr(tmp_path):
    rows = [{"id": f"r{i}", "metric": "Relevance", "human_pass": "1" if i % 5 else "0", "judge_pass": "1"} for i in range(80)]
    c = jc.build([sheet(tmp_path, rows)])[0]
    assert c["raw_agreement"] == 0.8 and c["tpr"] == 1.0 and c["tnr"] == 0.0
    assert c["status"] == "unreliable" and "low_tnr" in c["flags"] and c["kappa"] == 0.0


def test_few_labels_or_invalid_verdicts_are_exploratory(tmp_path):
    small = [{"id": f"s{i}", "metric": "M", "human_pass": str(i % 2), "judge_pass": str(i % 2)} for i in range(20)]
    assert jc.build([sheet(tmp_path, small)])[0]["status"] == "exploratory"
    bad = [{"id": f"b{i}", "metric": "M", "human_pass": str(i % 2), "judge_pass": "" if i < 10 else str(i % 2)} for i in range(80)]
    c = jc.build([sheet(tmp_path, bad, "b.csv")])[0]
    assert c["valid_rate"] == 0.875 and c["status"] == "exploratory"   # unparseable is missing, never a fail
    good = [{"id": f"g{i}", "metric": "M", "human_pass": str(i % 2), "judge_pass": str(i % 2)} for i in range(80)]
    c = jc.build([sheet(tmp_path, good, "g.csv")])[0]
    assert c["status"] == "ok" and c["tpr_ci"][0] > 0.9 and c["kappa"] == 1.0


def test_versions_not_run_on_a_sheet_are_not_invalid_and_splits_are_stable(tmp_path):
    a = sheet(tmp_path, [{"id": f"a{i}", "metric": "M", "human_pass": "1", "judge_pass": "1"} for i in range(5)], "a.csv")
    b = sheet(tmp_path, [{"id": f"b{i}", "metric": "M", "human_pass": "1", "judge_pass": "1", "judge_v2_pass": "1"}
                         for i in range(5)], "b.csv")
    card = {c["rubric_version"]: c for c in jc.build([a, b])}
    assert card["v2"]["valid_rate"] == 1.0 and card["v2"]["n_labels"] == 5 and card["v1"]["n_labels"] == 10
    assert jc.split("row-1") == jc.split("row-1") and {jc.split(f"x{i}") for i in range(200)} == {"fewshot", "dev", "test"}
