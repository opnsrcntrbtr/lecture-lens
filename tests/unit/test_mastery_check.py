"""Mastery check (mastery_check.py): cumulative, interleaved, 90% bar, retries start with misses. Invented fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

STUDY_DIR = Path(__file__).resolve().parents[2]
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import mastery_check as mastery  # noqa: E402

TOPICS = {
    "lec-a": ("Soil basics", "2030-01-01", [("What does mulch do?", "Keeps soil moist and cool"),
                                             ("What is compost?", "Decomposed organic matter for soil"),
                                             ("What is loam?", "Soil mixing sand silt and clay")]),
    "lec-b": ("Watering", "2030-01-08", [("When to water seedlings?", "Early morning before the heat"),
                                          ("What is drip irrigation?", "Water delivered slowly at the roots"),
                                          ("Why avoid overwatering?", "Roots rot without enough oxygen")]),
    "lec-c": ("Pests", "2030-01-15", [("What do ladybirds eat?", "Aphids that damage leaves"),
                                       ("What is companion planting?", "Growing plants that protect each other"),
                                       ("What is a slug trap?", "A shallow dish that lures slugs at night")]),
}


def make(root: Path, ids=("lec-a", "lec-b", "lec-c")) -> tuple[Path, Path]:
    lecs, prac = root / "lectures", root / "practice"
    prac.mkdir(parents=True, exist_ok=True)
    for lid in ids:
        title, day, cards = TOPICS[lid]
        f = lecs / f"{day}_{lid}"
        f.mkdir(parents=True, exist_ok=True)
        (f / "manifest.json").write_text(json.dumps({"id": lid, "title": title, "start": f"{day}T10:00:00+00:00"}))
        (f / "cards.tsv").write_text("".join(f"{q}\t{a}\t[00:01:00]\n" for q, a in cards))
        (f / "notes.md").write_text(f"# {title}\n\n" + "".join(f"- **{a.split()[0]}** {a}\n" for _, a in cards))
    return lecs, prac


def answer(items, right: set[int], conf="sure"):
    out = []
    for i, it in enumerate(items):
        ok = i in right
        if it["kind"] == "mcq":
            ci = it["correct_index"] if ok else (it["correct_index"] + 1) % len(it["choices"])
            out.append({"item_id": it["item_id"], "answer": it["choices"][ci], "choice_index": ci, "confidence": conf})
        else:
            out.append({"item_id": it["item_id"], "answer": it["expected_answer"] if ok else "no idea",
                        "choice_index": None, "confidence": conf})
    return json.dumps(out)


def test_first_attempt_covers_every_lecture_interleaved(tmp_path):
    lecs, prac = make(tmp_path)
    assert mastery.status(lecs, prac)["state"] == "not_taken"
    r = mastery.start(lecs, prac, per_lecture=2, seed=7)
    assert r["ok"] and r["attempt"] == 1 and r["retry"] is False and r["bar"] == 90
    ids = [it["lecture_id"] for it in r["items"]]
    assert set(ids) == {"lec-a", "lec-b", "lec-c"} and len(ids) == 6
    assert all(a != b for a, b in zip(ids, ids[1:])), ids   # never the same lecture twice in a row


def test_below_bar_then_retry_starts_with_misses(tmp_path):
    lecs, prac = make(tmp_path)
    r = mastery.start(lecs, prac, per_lecture=2, seed=3)
    n = r["item_count"]
    res = mastery.submit(r["check_id"], answer(r["items"], set(range(n - 2))), 60, lecs, prac)
    assert res["ok"] and res["passed"] is False and res["score_pct"] < 90
    assert res["graded_items"][0]["explanation"].startswith("Expected answer:")
    assert "more right answer" in res["next"]
    st = mastery.status(lecs, prac)
    assert st["state"] == "retry" and "2 you missed" in st["message"] and "due from" in st["message"]
    rl = mastery.relearned(r["check_id"], 2, 3, lecs, prac)
    assert rl["ok"] and rl["next_try"] and mastery.status(lecs, prac)["due_at"] == rl["next_try"]
    assert not mastery.relearned("mastery-nope", 1, 1, lecs, prac)["ok"]
    missed = {(g["lecture_id"]) for g in res["graded_items"] if not g["correct"]}
    r2 = mastery.start(lecs, prac, per_lecture=2, seed=4)
    assert r2["retry"] is True and r2["attempt"] == 2 and r2["retry_count"] >= 1
    assert {it["lecture_id"] for it in r2["items"] if it["retry"]} <= missed
    assert {it["lecture_id"] for it in r2["items"]} == {"lec-a", "lec-b", "lec-c"}   # still cumulative


def test_pass_then_new_lecture_makes_it_due(tmp_path):
    lecs, prac = make(tmp_path, ("lec-a", "lec-b"))
    r = mastery.start(lecs, prac, seed=1)
    res = mastery.submit(r["check_id"], answer(r["items"], set(range(r["item_count"]))), 30, lecs, prac)
    assert res["passed"] is True and res["score_pct"] == 100
    assert mastery.status(lecs, prac)["state"] == "passed"
    assert mastery.submit(r["check_id"], "[]", 0, lecs, prac)["ok"] is False   # no double submit
    make(tmp_path, ("lec-c",))
    st = mastery.status(lecs, prac)
    assert st["state"] == "due_new" and st["new_lectures"] == ["Pests"]


def test_calibration_flags_overconfidence_and_underconfidence():
    sure_wrong = [{"confidence": "sure", "correct": i < 1} for i in range(4)]
    c = mastery.calibration(sure_wrong)
    assert c["overconfident"] == 3 and c["verdict"].startswith("You were sure more often")
    assert c["line"] == "Sure 4 (1 right)"
    doubt_right = [{"confidence": "guess", "correct": True} for _ in range(3)]
    assert mastery.calibration(doubt_right)["verdict"].startswith("You know more")


def test_interleave_never_repeats_while_another_lecture_has_items():
    out = mastery.interleave({"a": [1, 2, 3], "b": [4], "c": [5, 6]})
    labels = ["a" if x <= 3 else "b" if x == 4 else "c" for x in out]
    assert all(x != y for x, y in zip(labels, labels[1:]))


def test_bar_from_environment():
    assert mastery.bar({"STUDY_MASTERY_BAR": "80"}) == 80
    assert mastery.bar({"STUDY_MASTERY_BAR": "x"}) == 90
    assert mastery.bar({"STUDY_MASTERY_BAR": "20"}) == 50


def test_no_lectures(tmp_path):
    assert mastery.start(tmp_path / "none", tmp_path / "p")["ok"] is False
    assert mastery.status(tmp_path / "none", tmp_path / "p")["state"] == "no_lectures"


def test_questions_are_the_lectures_own_with_full_answers_and_reasons(tmp_path):
    lecs, prac = make(tmp_path, ("lec-a", "lec-b"))
    f = next(lecs.glob("*_lec-a"))
    (f / "practice_quiz.json").write_text(json.dumps({"questions": [
        {"q": "Which soil holds water best for most vegetables?", "options": ["Pure sand", "Loam with compost", "Gravel", "Chalk"],
         "answer": 1, "why": "Loam drains yet holds moisture, and compost adds structure."}]}))
    r = mastery.start(lecs, prac, per_lecture=4, seed=2)
    qs = {it["question"]: it for it in r["items"]}
    quiz = qs["Which soil holds water best for most vegetables?"]
    assert quiz["choices"] == sorted(quiz["choices"], key=["Pure sand", "Loam with compost", "Gravel", "Chalk"].index) or len(quiz["choices"]) == 4
    assert quiz["expected_answer"] == "Loam with compost"
    card = qs.get("What does mulch do?") or next(it for it in r["items"] if it["question"].endswith("?") and it is not quiz)
    assert all("[00:" not in c for c in card["choices"]) and len(card["choices"]) == 4
    assert card["choices"][card["correct_index"]] == card["expected_answer"]
    backs = {a for _, _, cards in TOPICS.values() for _, a in cards}
    assert set(card["choices"]) <= backs   # wrong options are other cards' answers, not stubs
    ans = [{"item_id": it["item_id"], "answer": it["choices"][it["correct_index"]], "choice_index": it["correct_index"],
            "confidence": "sure"} for it in r["items"]]
    res = mastery.submit(r["check_id"], json.dumps(ans), 10, lecs, prac)
    g = next(x for x in res["graded_items"] if x["question"] == quiz["question"])
    assert g["correct"] and "Why: Loam drains yet holds moisture" in g["explanation"]


def test_optional_reasons_are_kept_and_feed_the_learner_brief(tmp_path):
    import learner
    lecs, prac = make(tmp_path, ("lec-a", "lec-b"))
    r = mastery.start(lecs, prac, seed=5)
    it = r["items"][0]
    wrong = (it["correct_index"] + 1) % len(it["choices"])
    ans = [{"item_id": it["item_id"], "answer": it["choices"][wrong], "choice_index": wrong, "confidence": "sure",
            "why": "I thought it was about drainage, mail me at a@example.com"}]
    res = mastery.submit(r["check_id"], json.dumps(ans), 5, lecs, prac)
    rec = mastery.attempts(prac / "mastery_check")[-1]
    assert rec["reasons"][0]["why"].startswith("I thought it was about drainage") and "@" not in rec["reasons"][0]["why"]
    assert "On a recent miss they reasoned" in learner.brief(it["lecture_id"], lectures_dir=lecs, practice_dir=prac)
    assert res["ok"]


def test_source_markers_are_stripped_from_options():
    assert mastery._clean("The service tracks repeat contacts [7]. See [00:01:02] and [slide 3] [2, 5]") == "The service tracks repeat contacts. See and"
