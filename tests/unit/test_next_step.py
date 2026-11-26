"""Today's next step (next_step.py). Invented fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery_check  # noqa: E402
import next_step  # noqa: E402
import relearning  # noqa: E402
from test_mastery_check import make, answer  # noqa: E402


def test_first_step_is_the_mastery_check(tmp_path):
    lecs, prac = make(tmp_path)
    r = next_step.steps(lecs, prac)
    assert r["top"]["kind"] == "mastery" and r["top"]["title"] == "Take the mastery check"


def test_misses_lead_to_relearning_then_the_tutor(tmp_path, monkeypatch):
    lecs, prac = make(tmp_path)
    monkeypatch.setattr(relearning, "budget_minutes", lambda *a, **k: 5.0)
    c = mastery_check.start(lecs, prac, per_lecture=2, seed=3)
    mastery_check.submit(c["check_id"], answer(c["items"], {0, 1, 2}), 10, lecs, prac)
    r = next_step.steps(lecs, prac)
    kinds = [s["kind"] for s in r["steps"]]
    assert kinds[0] == "relearn" and "tutor" in kinds
    tutor = next(s for s in r["steps"] if s["kind"] == "tutor")
    assert tutor["ref"].startswith(f"mastery:{c['check_id']}:")
    assert "mastery" not in kinds          # the retry waits for its two-day spacing
