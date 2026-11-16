"""Study aids v2: quiz linter (LS-2), attempt-first gate (LS-1), prequestions (LS-4).
Invented fixtures; the model is a stub."""
import datetime as dt
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import practice  # noqa: E402
import prequestions as pq  # noqa: E402
import quiz  # noqa: E402

GOOD = {"stem": "A garden team sees raised beds drying by noon. What should they change first?",
        "options": ["Water raised beds more often", "Add sand to the beds", "Move beds into shade", "Stop mulching"],
        "key": 0, "explanation": "Raised beds drain faster.", "line_ids": ["x/t/1"]}


def item(**kw):
    return {**GOOD, **kw}


@pytest.mark.parametrize("change, flag", [
    ({"options": ["Water raised beds more often in the morning, before leaves warm up, so they dry by evening",
                  "Add sand", "Move beds", "Stop mulching"]}, "key_longest"),
    ({"options": ["Water raised beds more often", "Always add sand to beds", "Move beds into shade", "Stop mulching"]},
     "absolute_terms"),
    ({"stem": "Raised beds need ____ watering."}, "fill_in_blank"),
    ({"options": ["True", "False", "Move beds into shade", "Stop mulching"]}, "true_false"),
    ({"options": ["Water raised beds more often", "Add sand to the beds", "Move beds into shade", "All of the above"]},
     "all_none_above"),
    ({"stem": "Which choice explains drying raised beds by noon?",
      "options": ["Raised beds dry because drying beds drain", "Sand content high", "Shade is missing", "Mulch is thin"]},
     "stem_words_only_in_key"),
    ({"options": ["A", "A", "B", "C"]}, "not_four_distinct_options"),
    ({"key": 7}, "key_out_of_range"),
])
def test_each_lint_rule_has_a_failing_fixture(change, flag):
    r = quiz.lint(item(**change))
    assert not r["passed"] and flag in r["flags"]


def test_a_clean_item_and_uniform_absolute_framing_pass():
    assert quiz.lint(GOOD) == {"passed": True, "flags": []}
    never = item(stem="Which never-rule fits a bank bot?", options=["Never ask for an OTP", "Never greet users",
                 "Never answer in Hindi", "Never show the balance"])
    assert "absolute_terms" not in quiz.lint(never)["flags"]


def test_generation_regenerates_lint_failures_and_requires_cited_lines():
    lines = [{"line_id": f"x/t/{i}", "t": "10:00:00", "text": t} for i, t in enumerate(
        ["Raised beds drain faster.", "So water raised beds more often.", "Mulch keeps soil moist."])]
    bad = {"stem": "Raised beds need ____ water.", "options": ["a", "b", "c", "d"], "key": 0, "line_ids": ["L1"]}
    nolines = {**GOOD, "stem": "Unsupported?", "line_ids": ["L99"]}
    replies = iter([json.dumps({"items": [bad, nolines, {**GOOD, "line_ids": ["L1", "L2"], "level": "apply"}]}),
                    json.dumps({"items": [{**GOOD, "stem": "Why mulch?", "options": ["Keeps soil moist", "Adds sand",
                                "Shades beds", "Stops worms"], "line_ids": ["L3"], "level": "understand"}]})])
    items = quiz.generate("Garden", "lec", lines, lambda p: next(replies), n=2)
    assert [i["stem"] for i in items] == [GOOD["stem"], "Why mulch?"]
    assert items[0]["line_ids"] == ["x/t/0", "x/t/1"] and items[0]["requested_level"] == "apply"


def test_leak_and_key_checks_parse_constrained_replies():
    it = item(item_id="i1", line_ids=["x/t/1"])
    assert quiz.leak_check(it, lambda p: '{"choice": 0, "confidence": "high"}')
    assert not quiz.leak_check(it, lambda p: '{"choice": 0, "confidence": "low"}')
    lines = {"x/t/1": {"text": "Raised beds drain faster so water them more often."}}
    assert quiz.key_check(it, lines, lambda p: '{"supported": true}')
    assert not quiz.key_check(item(line_ids=[]), lines, lambda p: '{"supported": true}')


def test_shuffle_keeps_the_key_trackable():
    opts, pos, order = quiz.shuffled(GOOD, random.Random(3))
    assert opts[pos] == GOOD["options"][GOOD["key"]] and sorted(order) == [0, 1, 2, 3]


# ------------------------------------------------------------------ LS-1
def test_reveal_needs_an_attempt_or_an_explicit_skip(tmp_path):
    s = practice.Store(tmp_path)
    st = practice.start_recap(s, "lec", "Garden", "# Recap\n- Mulch keeps soil moist through dry weeks\n- Raised beds drain faster and need water\n")
    assert st["reveal_enabled"] is False and "Write what you remember" in st["prompt"]
    assert practice.reveal_recap(s, st["session_id"])["ok"] is False
    assert practice.attempt(s, st["session_id"], "mulch soil")["reveal_enabled"] is False      # under 15 characters
    assert practice.attempt(s, st["session_id"], "mulch keeps soil moist in dry weeks")["reveal_enabled"] is True
    r = practice.reveal_recap(s, st["session_id"])
    assert r["ok"] and [p["missed"] for p in r["points"]] == [False, True] and r["recalled_share"] == 0.5
    st2 = practice.start_recap(s, "lec", "Garden", "- Mulch keeps soil moist through dry weeks\n")
    assert practice.reveal_recap(s, st2["session_id"], skip=True)["ok"]
    rows = s.attempts()
    assert rows[-1]["skipped_attempt"] is True and rows[-2]["skipped_attempt"] is False


def test_quiz_session_confidence_before_key_hints_and_closed_book_progress(tmp_path):
    s = practice.Store(tmp_path)
    items = [{**GOOD, "item_id": f"i{n}", "stem": f"{GOOD['stem']} ({n})", "lint": {"passed": True, "flags": []}}
             for n in range(6)]
    v = practice.start_quiz(s, "lec", items, n=2, seed=1)
    assert v["of"] == 2 and "key" not in v
    assert practice.answer(s, v["session_id"], 0, "")["ok"] is False                           # confidence first
    h = practice.hint(s, v["session_id"]); h = practice.hint(s, v["session_id"])
    assert h["hint_level"] == 2 and h["removed"] is not None
    sess = s.load(v["session_id"])
    key = sess["items"][0]["key_pos"]
    r = practice.answer(s, v["session_id"], key, "sure")
    assert r["correct"] and r["key"] == key
    assert practice.advance(s, v["session_id"])["index"] == 1
    assert practice.advance(s, v["session_id"])["ok"] is False                                 # not answered yet
    practice.skip_item(s, v["session_id"])
    cb = practice.closed_book_items(s, items, "lec", seed=2)
    assert cb["closed_book"] and practice.hint(s, cb["session_id"])["ok"] is False
    sess = s.load(cb["session_id"])
    practice.answer(s, cb["session_id"], (sess["items"][0]["key_pos"] + 1) % 4, "sure")
    p = practice.progress(s)
    assert p["closed_book_items"] == 1 and p["closed_book_accuracy"] == 0.0 and p["sure_and_wrong"] == 1
    assert p["skip_rate"] == 0.5                                                               # 1 of 2 assisted reveals skipped
    assert all(not r["assisted"] for r in s.attempts() if r["session_id"] == cb["session_id"])


def test_progress_excludes_assisted_rows(tmp_path):
    s = practice.Store(tmp_path)
    s.log({"item_id": "a", "kind": "quiz_item", "correct": True, "assisted": True, "revealed": True})
    s.log({"item_id": "b", "kind": "quiz_item", "correct": False, "assisted": False, "revealed": True})
    assert practice.progress(s)["closed_book_items"] == 1 and practice.progress(s)["closed_book_accuracy"] == 0.0


# ------------------------------------------------------------------ LS-4
AGENDA = "Garden basics\n1. Soil and mulch: keeping moisture\n2. Raised beds and drainage\nPart 3: Pests and companion planting\n"


def test_prequestions_from_agenda_items_questions_only(tmp_path):
    assert pq.agenda_items(AGENDA) == ["Soil and mulch: keeping moisture", "Raised beds and drainage",
                                       "Pests and companion planting"]
    raw = json.dumps({"questions": [{"item": 1, "q": "Why does mulch keep soil moist?"},
                                    {"item": 2, "q": "How do raised beds drain? Why?"},
                                    {"item": 9, "q": "Out of range?"}, {"item": 3, "q": "Which plants deter pests?"}]})
    rows = pq.generate("Garden", AGENDA, lambda p: raw)
    assert [r["text"] for r in rows] == ["Why does mulch keep soil moist?", "Which plants deter pests?"]
    assert all(r["guess"] is None and r["resolved"] is None for r in rows)


def test_guesses_before_class_and_late_flag_then_resolution_and_cards(tmp_path):
    live = tmp_path / "20300105"
    rows = pq.save(live, [{"pq_id": "pq1", "agenda_item": "Soil and mulch", "text": "Why does mulch keep soil moist?",
                           "guess": None, "guess_t": None, "late": False, "resolved": None, "got_it": None, "became_card": False},
                          {"pq_id": "pq2", "agenda_item": "Pests", "text": "Which plants deter pests?",
                           "guess": None, "guess_t": None, "late": False, "resolved": None, "got_it": None, "became_card": False}])
    t0 = dt.datetime(2030, 1, 5, 4, 30, tzinfo=dt.timezone.utc)
    assert pq.guess(live, "pq1", "it blocks evaporation", t0 - dt.timedelta(minutes=10))["late"] is False
    (live / "whisper_segments.jsonl").write_text(json.dumps({"t": t0.isoformat(), "text": "Mulch keeps soil moist."}) + "\n")
    assert pq.guess(live, "pq2", "marigolds", t0 + dt.timedelta(minutes=5))["late"] is True
    lines = [{"line_id": "live-20300105/t/0", "t": t0.isoformat(), "text": "Mulch keeps soil moist because it shades the soil and slows evaporation."},
             {"line_id": "live-20300105/t/1", "t": t0.isoformat(), "text": "Raised beds drain faster."}]
    out = pq.resolve(live, lines, lambda p: '{"answered": {"P1": [1]}}')
    assert out[0]["resolved"]["status"] == "answered" and out[0]["resolved"]["line_ids"] == ["live-20300105/t/0"]
    assert out[1]["resolved"]["status"] == "not_covered" and not out[1]["became_card"]
    assert out[0]["became_card"] and "Why does mulch" in pq.cards_tsv(out, "Garden")
    assert pq.mark(live, "pq1", True)["became_card"] is False and pq.cards_tsv(pq.load(live), "Garden") == ""


def test_spread_covers_start_and_end_of_a_long_lecture():
    rows = [f"[L{i}] line {i} " + "x" * 90 for i in range(1, 1001)]
    s = quiz.spread(rows, budget=6000, windows=6)
    assert len(s) <= 6200 and "[L1] " in s and "[L834] " in s and "[L1000] " not in s.split("...")[0]


def test_recap_is_scored_on_tldr_points_not_the_whole_sheet(tmp_path):
    store = practice.Store(tmp_path / "p")
    tldr = ("* Trust architecture decides what the model may do and who has the final say [10:19:04–10:21:31].\n"
            "* Autonomy runs from copilot to autopilot and must match the cost of a mistake [slide 6].")
    s = practice.start_recap(store, "L1", "Trust", "a long cheat sheet\n- point one\n- point two\n- point three",
                             "cheat_sheet", score_from=tldr)
    practice.attempt(store, s["session_id"], "copilot to autopilot depends on the cost of a mistake")
    r = practice.reveal_recap(store, s["session_id"])
    assert [p["missed"] for p in r["points"]] == [True, False]
    assert "10:19" not in r["points"][0]["text"] and r["content"].startswith("a long cheat sheet")
