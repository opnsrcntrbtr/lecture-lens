"""Programme calendar lookups: which week, module and session a capture belongs to.

The calendar itself is course material, so it lives in the gitignored lecture folder
(lectures/calendar.json, or STUDY_CALENDAR). Nothing here hard-codes course content.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def path() -> Path:
    lect = Path(os.path.expanduser(os.environ.get("STUDY_LECTURE_DIR", str(HERE / "lectures"))))
    return Path(os.path.expanduser(os.environ.get("STUDY_CALENDAR", str(lect / "calendar.json"))))


def load(p: Path | None = None) -> dict:
    p = p or path()
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return {"sessions": []}


def _bounds(s: dict) -> tuple[dt.datetime, dt.datetime]:
    tz = ZoneInfo(s.get("tz", "Asia/Kolkata")) if ZoneInfo else dt.timezone(dt.timedelta(hours=5, minutes=30))
    d = dt.date.fromisoformat(s["date"])
    hh, mm = map(int, s.get("start", "00:00").split(":"))
    eh, em = map(int, s.get("end", "23:59").split(":"))
    return (dt.datetime.combine(d, dt.time(hh, mm), tz), dt.datetime.combine(d, dt.time(eh, em), tz))


def session_at(when: dt.datetime, cal: dict | None = None, slack_min: int = 90) -> dict | None:
    """The scheduled session a capture starting at `when` belongs to: same day, starting
    within `slack_min` of the slot (classes run late, people join early)."""
    cal = cal if cal is not None else load()
    if when.tzinfo is None:
        when = when.astimezone()
    best = None
    for s in cal.get("sessions", []):
        a, b = _bounds(s)
        if a - dt.timedelta(minutes=slack_min) <= when <= b + dt.timedelta(minutes=slack_min):
            gap = abs((when - a).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, s)
    return best[1] if best else None


def session_on(day: dt.date | str, cal: dict | None = None) -> dict | None:
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    cal = cal if cal is not None else load()
    return next((s for s in cal.get("sessions", []) if s["date"] == day.isoformat()), None)


def next_session(now: dt.datetime | None = None, cal: dict | None = None) -> dict | None:
    now = now or dt.datetime.now().astimezone()
    cal = cal if cal is not None else load()
    later = [s for s in cal.get("sessions", []) if _bounds(s)[1] >= now]
    return min(later, key=lambda s: _bounds(s)[0]) if later else None


def module_label(s: dict | None) -> str:
    if not s or not s.get("module"):
        return ""
    return s["module"]


def describe(s: dict, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now().astimezone()
    a, _ = _bounds(s)
    days = (a.date() - now.astimezone(a.tzinfo).date()).days
    when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
    mod = f"{s['module']}: {s.get('module_title','')}" if s.get("module") else "orientation"
    return f"Week {s['week']} · {a:%a %d %b %H:%M} ({when}) · {mod} · {s['title']}"
