"""FSRS-5 scheduling and the review log. Invented cards only; no model."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import review as rv  # noqa: E402

T0 = dt.datetime(2026, 3, 1, 9, 0, tzinfo=dt.timezone.utc)


def ev(day, ok, card="c1"):
    return {"card": card, "ts": (T0 + dt.timedelta(days=day)).isoformat(), "pass": ok}


def test_forgetting_curve_is_90_percent_at_stability():
    for s in (0.5, 3, 40):
        assert rv.retrievability(s, s) == pytest.approx(0.9)
        assert rv.interval_days(s * 10) == round(s * 10) or s * 10 > rv.MAX_INTERVAL_DAYS
    assert rv.retrievability(0, 5) == 1.0
    assert rv.retrievability(10, 5) < rv.retrievability(5, 5)


def test_lower_target_retention_means_longer_intervals():
    assert rv.interval_days(10, 0.8) > rv.interval_days(10, 0.9) > rv.interval_days(10, 0.95)


def test_first_answer_uses_initial_parameters():
    good, again = rv.replay([ev(0, True)]), rv.replay([ev(0, False)])
    assert good["stability"] == rv.W[2] and again["stability"] == rv.W[0]
    assert again["difficulty"] > good["difficulty"]
    assert 1 <= good["difficulty"] <= 10


def test_passes_grow_the_interval_and_a_fail_shrinks_it():
    log, day, gaps = [ev(0, True)], 0, []
    for _ in range(4):
        st = rv.replay(log)
        gap = rv.interval_days(st["stability"]); gaps.append(gap)
        day += gap; log.append(ev(day, True))
    assert gaps == sorted(gaps) and gaps[-1] > gaps[0]
    before = rv.replay(log)["stability"]
    after = rv.replay(log + [ev(day + gaps[-1], False)])
    assert after["stability"] < before and after["lapses"] == 1


def test_difficulty_stays_in_range_under_many_fails():
    st = rv.replay([ev(i, False) for i in range(20)])
    assert 1 <= st["difficulty"] <= 10


def test_queue_orders_due_new_and_flags_leeches(tmp_path):
    folder = tmp_path / "001_Astronomy"; folder.mkdir()
    (folder / "cards.tsv").write_text("Why do comets have two tails?\tDust and ions respond differently\tAstro 00:01:00\n"
                                      "What powers a main-sequence star?\tHydrogen fusion\tAstro 00:02:00\n"
                                      "Why is Mars red?\tIron oxide on its surface\tAstro 00:03:00\n")
    cards = rv.load_cards(folder)
    for d in range(3):
        rv.log_answer(folder, cards[0]["id"], False, T0 + dt.timedelta(days=d * 2))
    rv.log_answer(folder, cards[1]["id"], True, T0)
    due, new, leeches = rv.queue([folder], T0 + dt.timedelta(days=30))
    assert {c["id"] for c in due} == {cards[0]["id"], cards[1]["id"]}
    assert [c["due"] for c in due] == sorted(c["due"] for c in due)        # most overdue first
    assert [c["q"] for c in new] == ["Why is Mars red?"]
    assert [c["id"] for c in leeches] == [cards[0]["id"]]
    _, new_none, _ = rv.queue([folder], T0, new_limit=0)
    assert new_none == []


def test_log_round_trip_and_bad_lines_are_warned(tmp_path, capsys):
    rv.log_answer(tmp_path, "abc", True, T0)
    (tmp_path / "reviews.jsonl").open("a").write("not json\n")
    log = rv.load_log(tmp_path)
    assert log == {"abc": [{"card": "abc", "ts": T0.isoformat(), "pass": True}]}
    assert "not a review record" in capsys.readouterr().err


def test_card_id_ignores_case_and_spacing():
    assert rv.card_id("Why is Mars  red?") == rv.card_id("why is mars red?")
