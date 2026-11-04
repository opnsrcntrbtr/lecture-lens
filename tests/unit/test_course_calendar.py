import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import course_calendar as cc  # noqa: E402

CAL = {"sessions": [
    {"week": 0, "title": "Kick-off", "date": "2030-01-05", "start": "10:00", "end": "11:30", "tz": "Asia/Kolkata", "module": "", "module_title": ""},
    {"week": 1, "title": "Topic A", "date": "2030-01-12", "start": "10:00", "end": "13:00", "tz": "Asia/Kolkata", "module": "Module 1", "module_title": "Alpha"},
    {"week": 2, "title": "Topic B", "date": "2030-01-26", "start": "10:00", "end": "13:00", "tz": "Asia/Kolkata", "module": "Module 1", "module_title": "Alpha"},
]}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def test_session_at_matches_slot_with_slack():
    s = cc.session_at(dt.datetime(2030, 1, 12, 9, 50, tzinfo=IST), CAL)
    assert s["title"] == "Topic A" and cc.module_label(s) == "Module 1"
    # a capture in UTC on the same morning still maps
    assert cc.session_at(dt.datetime(2030, 1, 12, 4, 34, tzinfo=dt.timezone.utc), CAL)["week"] == 1


def test_session_at_rejects_other_times():
    assert cc.session_at(dt.datetime(2030, 1, 12, 20, 0, tzinfo=IST), CAL) is None   # evening replay
    assert cc.session_at(dt.datetime(2030, 1, 19, 10, 0, tzinfo=IST), CAL) is None   # holiday week


def test_orientation_has_no_module():
    assert cc.module_label(cc.session_at(dt.datetime(2030, 1, 5, 10, 5, tzinfo=IST), CAL)) == ""


def test_next_session_and_describe():
    now = dt.datetime(2030, 1, 13, 9, 0, tzinfo=IST)
    s = cc.next_session(now, CAL)
    assert s["title"] == "Topic B"
    assert "in 13 days" in cc.describe(s, now) and "Module 1: Alpha" in cc.describe(s, now)
    assert cc.next_session(dt.datetime(2031, 1, 1, tzinfo=IST), CAL) is None


def test_missing_calendar_is_empty(tmp_path):
    assert cc.load(tmp_path / "nope.json") == {"sessions": []}
