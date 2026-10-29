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


def test_versions_include_dotted_and_majority_columns():
    rows = [{"judge_pass": "1", "judge_v2.1x3_pass": "0", "judge_v1x3_pass": "1", "human_pass": "1", "metric": "m"}]
    assert cal._versions(rows) == ["v1", "v1x3", "v2.1x3"]
    assert cal._jp(rows[0], "v2.1x3") == "0"


def test_merge_write_keeps_labels_saved_meanwhile(tmp_path):
    import csv
    f = tmp_path / "h.csv"
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["id", "human_pass"]); w.writeheader(); w.writerows([{"id": "a", "human_pass": "1"}, {"id": "b", "human_pass": ""}])
    cal._merge_write(f, "b", {"judge_v2.1x3_pass": "0"})
    rows = list(csv.DictReader(f.open()))
    assert rows[0]["human_pass"] == "1" and rows[1]["judge_v2.1x3_pass"] == "0" and rows[0]["judge_v2.1x3_pass"] == ""


def test_holdout_appends_new_days_and_keeps_labels(tmp_path, monkeypatch):
    import csv, json, subprocess
    monkeypatch.setattr(cal, "HERE", tmp_path / "tests" / "evals")
    ev = tmp_path / "tests" / "evals"; (ev / "calibration").mkdir(parents=True)
    for day in ("20260101", "20260108"):
        live = tmp_path / "lectures" / "live" / day; live.mkdir(parents=True)
        (live / "questions.jsonl").write_text("{}\n")
    def fake_run(cmd, **kw):
        day = cmd[2].split("/")[-1]
        (ev / ".live_questions.json").write_text(json.dumps([
            {"input": f"Why do cacti store water ({day} {i})?", "actual_output": "Rain is rare.", "retrieval_context": ["rain is rare"]}
            for i in range(6)]))
    monkeypatch.setattr(cal.subprocess, "run", fake_run)
    out = cal.holdout("20260101", 4)
    rows = list(csv.DictReader(out.open())); rows[0]["human_pass"] = "1"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys()); w.writeheader(); w.writerows(rows)
    cal.holdout("20260101", 4)                     # same day again: skipped
    cal.holdout("20260108", 4)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 8 and rows[0]["human_pass"] == "1"
    assert {r["metric"] for r in rows[:4]} == {"Question relevance [GEval]", "Answerability [GEval]", "Pedagogical value [GEval]", "Faithfulness"}
