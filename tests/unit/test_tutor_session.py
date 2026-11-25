"""Guided tutor v2 (tutor_session.py): the answer stays hidden, the ladder persists, the exit check is
graded in code. Invented fixtures; the model is off (templates)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["STUDY_TUTOR_LLM"] = "0"
import mastery_check  # noqa: E402
import tutor_session as ts  # noqa: E402
from test_mastery_check import make, answer  # noqa: E402


def mastery_miss(tmp_path):
    lecs, prac = make(tmp_path)
    r = mastery_check.start(lecs, prac, per_lecture=2, seed=3)
    mastery_check.submit(r["check_id"], answer(r["items"], set()), 10, lecs, prac)
    return lecs, prac, r


def test_key_hidden_until_two_attempts_or_asked(tmp_path):
    lecs, prac, r = mastery_miss(tmp_path)
    it = r["items"][0]
    s = ts.start(item_ref=f"mastery:{r['check_id']}:{it['item_id']}", lectures_dir=lecs, practice_dir=prac)
    assert s["ok"] and "answer" not in s and s["can_reveal"] is False
    raw = json.dumps(s)
    assert it["expected_answer"] not in raw
    blocked = ts.reveal(s["id"], lectures_dir=lecs, practice_dir=prac)
    assert blocked["ok"] is False and "answer" not in blocked
    a1 = ts.attempt(s["id"], "no idea really", lecs, prac)
    assert a1["ok"] and not a1["solved"] and "answer" not in a1 and "missing" not in json.dumps(a1["last"])
    ts.attempt(s["id"], "still unsure", lecs, prac)
    shown = ts.reveal(s["id"], lectures_dir=lecs, practice_dir=prac)
    assert shown["revealed"] == "earned" and shown["answer"] == it["expected_answer"]


def test_just_tell_me_is_logged_and_ladder_persists_across_chats(tmp_path):
    lecs, prac, r = mastery_miss(tmp_path)
    ref = f"mastery:{r['check_id']}:{r['items'][1]['item_id']}"
    s = ts.start(item_ref=ref, lectures_dir=lecs, practice_dir=prac)
    h1 = ts.hint(s["id"], lecs, prac)
    h2 = ts.hint(s["id"], lecs, prac)
    assert [h["rung"] for h in h2["hints"]] == [1, 2] and h2["hints"][1]["how"] == "pointer"
    again = ts.start(item_ref=ref, lectures_dir=lecs, practice_dir=prac)   # a new chat
    assert again["rung"] == 2 and again["note"] and again["next_hint"] == "one worked step"
    told = ts.reveal(again["id"], just_tell_me=True, lectures_dir=lecs, practice_dir=prac)
    assert told["revealed"] == "asked" and told["answer"]
    assert h1["ok"]


def test_correct_attempt_opens_the_answer_and_exit_check_is_graded_in_code(tmp_path):
    lecs, prac, r = mastery_miss(tmp_path)
    it = r["items"][2]
    s = ts.start(item_ref=f"mastery:{r['check_id']}:{it['item_id']}", lectures_dir=lecs, practice_dir=prac)
    ok = ts.attempt(s["id"], it["expected_answer"], lecs, prac)
    assert ok["solved"] and ok["revealed"] == "earned"
    e = ts.exit_check(s["id"], seed=1, lectures_dir=lecs, practice_dir=prac)
    assert e["exit"] and "correct_index" not in e["exit"] and e["exit"]["question"] != it["question"]
    sess = json.loads((prac / "tutor" / "sessions" / f"{s['id']}.json").read_text())
    done = ts.answer_exit(s["id"], sess["exit"]["correct_index"], lecs, prac)
    assert done["exit"]["correct"] is True
    st = ts.stats(lecs, prac)
    assert st["episodes"] == 1 and st["exit_correct_pct"] == 100 and st["solved_pct"] == 100


def test_leak_guard_and_graded_quiz_refusal(tmp_path):
    key = "Mulch keeps soil moist and cool so roots do not dry out"
    assert ts.leaks("Mulch keeps the soil moist and cool for the roots", key)
    assert not ts.leaks("What does covering the ground do to water loss?", key)
    assert ts.looks_like_graded_quiz("Question 3 of 10 (1 point)\nWhich is true?")
    assert ts.looks_like_graded_quiz("Which is right?\nA) one\nB) two\nC) three\nD) four")
    assert not ts.looks_like_graded_quiz("Why does mulch help young plants?")
    lecs, prac = make(tmp_path)
    r = ts.start("Question 2 of 5 (2 points) Which soil is best?", lectures_dir=lecs, practice_dir=prac)
    assert not r["ok"] and "graded quiz" in r["error"]


def test_open_question_uses_the_lectures_and_never_returns_the_key(tmp_path):
    lecs, prac = make(tmp_path)
    s = ts.start("What does mulch do for the soil?", lectures_dir=lecs, practice_dir=prac)
    assert s["ok"] and s["source"] == "open" and "answer" not in s
    sess = json.loads((prac / "tutor" / "sessions" / f"{s['id']}.json").read_text())
    assert sess["key"] and sess["key"] not in json.dumps(ts.hint(s["id"], lecs, prac))


def test_learner_brief_uses_exact_per_lecture_counts(tmp_path):
    import learner
    lecs, prac, r = mastery_miss(tmp_path)
    b = learner.brief("lec-a", lectures_dir=lecs, practice_dir=prac)
    assert b.startswith("Mastery checks: 0 of 2 right on this lecture.") and "Recently missed" in b
    s = ts.start(item_ref=f"mastery:{r['check_id']}:{r['items'][0]['item_id']}", lectures_dir=lecs, practice_dir=prac)
    assert s["brief"].startswith("Mastery checks:")
    assert learner.brief("nope", lectures_dir=lecs, practice_dir=prac) == "No study record for this lecture yet."
