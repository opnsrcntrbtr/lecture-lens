"""Successive relearning for every key item (relearning.py). Invented fixtures."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mastery_check  # noqa: E402
import relearning as rl  # noqa: E402
from test_mastery_check import make, answer  # noqa: E402

D = dt.date


def test_state_counts_spaced_criterion_sessions():
    t = D(2030, 1, 10)
    assert rl.state([(D(2030, 1, 1), True), (D(2030, 1, 3), True), (D(2030, 1, 5), True)], t, 2)["secure"]
    same_week = rl.state([(D(2030, 1, 1), True), (D(2030, 1, 2), True), (D(2030, 1, 3), True)], t, 2)
    assert same_week["sessions"] == 2 and not same_week["secure"] and same_week["due"]
    missed = rl.state([(D(2030, 1, 1), True), (D(2030, 1, 3), True), (D(2030, 1, 5), True), (D(2030, 1, 9), False)], t, 2)
    assert missed["due"] and missed["missed"] and not missed["secure"]
    waiting = rl.state([(D(2030, 1, 9), True)], t, 2)
    assert not waiting["due"] and waiting["next"] == "2030-01-11"


def test_exam_close_shortens_the_gap():
    assert rl.gap_days(D(2030, 1, 1), {"STUDY_EXAM_DATE": "2030-01-05"}) == 1
    assert rl.gap_days(D(2030, 1, 1), {"STUDY_EXAM_DATE": "2030-02-05"}) == 2
    assert rl.gap_days(D(2030, 1, 1), {}) == 2


def test_misses_from_a_mastery_check_are_due_and_a_session_reschedules_them(tmp_path, monkeypatch):
    lecs, prac = make(tmp_path)
    r = mastery_check.start(lecs, prac, per_lecture=2, seed=3)
    mastery_check.submit(r["check_id"], answer(r["items"], {0, 1, 2}), 10, lecs, prac)
    monkeypatch.setattr(rl, "budget_minutes", lambda *a, **k: 5.0)
    p = rl.plan(lecs, prac, seed=1)
    assert p["counts"]["items"] == len(r["items"]) and p["counts"]["due"] == len(r["items"]) - 3
    assert all(it["choices"][it["correct_index"]] == it["expected_answer"] for it in p["items"])
    done = rl.record(json.dumps([{"lecture_id": it["lecture_id"], "question": it["question"], "first_try_ok": i == 0}
                                 for i, it in enumerate(p["items"])]), 60, lecs, prac)
    assert done["ok"] and done["first_try_right"] == 1
    after = rl.plan(lecs, prac, seed=1)
    assert after["counts"]["due"] == 0 and "Nothing due" in after["message"]


def test_budget_caps_the_session(tmp_path, monkeypatch):
    lecs, prac = make(tmp_path)
    r = mastery_check.start(lecs, prac, per_lecture=2, seed=3)
    mastery_check.submit(r["check_id"], answer(r["items"], set()), 10, lecs, prac)
    monkeypatch.setattr(rl, "budget_minutes", lambda *a, **k: 0.0)
    p = rl.plan(lecs, prac, seed=1)
    assert len(p["items"]) == 3 and p["counts"]["due"] == len(r["items"])   # at least three when any are due
