"""Successive relearning for every key item, inside today's review minutes.

Key items are the questions the mastery check has asked (a cumulative sample from every lecture).
An item is relearned in sessions: in each session it is answered until it is right once (retrieval to
criterion), and the sessions are spaced RELEARN_GAP days apart. After SECURE_SESSIONS spaced sessions
it is secure and the flashcard scheduler (FSRS) carries it from there. A miss makes it due again.
Rawson et al. (2018): about 80% recall a week after three spaced criterion sessions; spacing need not
expand (Latimier et al. 2021); exam close (STUDY_EXAM_DATE within a week): sessions one day apart.

One budget: relearning uses only what is left of today's review minutes after the FSRS cards
(spaced.Reviews.session), minus the relearning already done today.

Files: <practice>/mastery_check/items.jsonl (one row per item answered in a relearning session);
the mastery check's own tries are read from checks.jsonl and their session files.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import re
from pathlib import Path

import mastery_check as mc

RELEARN_GAP = 2           # days between criterion sessions
SECURE_SESSIONS = 3       # spaced criterion sessions before an item counts as secure
SECONDS_PER_ITEM = 30     # estimate for one multiple-choice relearning item


def key(lecture_id: str, question: str) -> str:
    norm = re.sub(r"\W+", " ", (question or "").lower()).strip()
    return hashlib.sha1(f"{lecture_id}|{norm}".encode()).hexdigest()[:12]


def _today() -> dt.date:
    return dt.datetime.now().astimezone().date()


def _day(iso: str | None) -> dt.date | None:
    try:
        t = dt.datetime.fromisoformat(str(iso))
        return (t if t.tzinfo else t.astimezone()).astimezone().date()
    except (TypeError, ValueError):
        return None


def exam_date(environ=None) -> dt.date | None:
    env = os.environ if environ is None else environ
    try:
        return dt.date.fromisoformat(env.get("STUDY_EXAM_DATE", "")[:10])
    except ValueError:
        return None


def gap_days(today: dt.date | None = None, environ=None) -> int:
    today = today or _today()
    ex = exam_date(environ)
    return 1 if ex and 0 <= (ex - today).days <= 7 else RELEARN_GAP


def history(mdir: Path) -> dict[str, dict]:
    """Per item: {lecture_id, question, results: [(day, ok)]} from mastery tries and relearning sessions."""
    items: dict[str, dict] = {}
    def add(lid, q, day, ok):
        if not (lid and q and day):
            return
        it = items.setdefault(key(lid, q), {"lecture_id": lid, "question": q, "results": []})
        it["results"].append((day, bool(ok)))
    for c in mc.attempts(mdir):
        day = _day(c.get("at"))
        sess = _session(mdir, c.get("check_id"))
        missed = {(m.get("lecture_id"), m.get("question")) for m in c.get("missed") or []}
        for it in (sess or {}).get("items") or []:
            add(it.get("lecture_id"), it.get("question"), day, (it.get("lecture_id"), it.get("question")) not in missed)
    p = mdir / "items.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            add(r.get("lecture_id"), r.get("question"), _day(r.get("t")), r.get("ok"))
    return items


def _session(mdir: Path, cid: str | None) -> dict | None:
    for sub in ("mock_exam_sessions", "sessions"):
        f = mdir / sub / f"{cid}.json"
        if cid and f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                return None
    return None


def state(results: list[tuple], today: dt.date, gap: int) -> dict:
    """Spaced criterion sessions: a day with a right answer counts when it is `gap` days after the
    previous counted day. A miss after the last right answer makes the item due now."""
    days_ok = sorted({d for d, ok in results if ok})
    last_miss = max((d for d, ok in results if not ok), default=None)
    counted = []
    for d in days_ok:
        if not counted or (d - counted[-1]).days >= gap:
            counted.append(d)
    last_ok = days_ok[-1] if days_ok else None
    missed_since = last_miss is not None and (last_ok is None or last_miss > last_ok)
    secure = len(counted) >= SECURE_SESSIONS and not missed_since
    due = missed_since or (not secure and last_ok is not None and (today - last_ok).days >= gap)
    return {"sessions": len(counted), "secure": secure, "due": bool(due), "missed": missed_since,
            "last_ok": last_ok.isoformat() if last_ok else None,
            "next": None if secure or due or last_ok is None else (last_ok + dt.timedelta(days=gap)).isoformat()}


def budget_minutes(base: Path, prac: Path, mdir: Path, now: dt.datetime | None = None) -> float:
    """What is left of today's review minutes after the FSRS cards and today's relearning."""
    try:
        import spaced
        s = spaced.Reviews(prac, base).session(now)
        left = float(s.get("minutes_left") or 0) - float(s.get("planned_minutes") or 0)
    except Exception:
        left = 5.0
    today = (now or dt.datetime.now().astimezone()).astimezone().date()
    done = 0.0
    p = mdir / "items.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if _day(r.get("t")) == today:
                done += float(r.get("seconds") or SECONDS_PER_ITEM) / 60
    return round(max(0.0, left - done), 1)


def plan(lectures_dir=None, practice_dir=None, limit: int | None = None, seed: int | None = None, environ=None) -> dict:
    """Today's relearning: due items (misses first, then the longest since a right answer), as
    multiple choice, within the minutes left after the FSRS cards (at least 3 when any are due)."""
    base, mdir = mc._dirs(lectures_dir, practice_dir)
    prac = mdir.parent
    today, gap = _today(), gap_days(environ=environ)
    hist = history(mdir)
    rows = []
    for k, it in hist.items():
        st = state(it["results"], today, gap)
        rows.append({"key": k, **{x: it[x] for x in ("lecture_id", "question")}, **st})
    due = sorted([r for r in rows if r["due"]], key=lambda r: (not r["missed"], r["last_ok"] or ""))
    minutes = budget_minutes(base, prac, mdir)
    fit = int(minutes * 60 // SECONDS_PER_ITEM)
    n = min(len(due), limit if limit is not None else max(3, fit))
    rng = random.Random(seed)
    pools = {lid: mc.pool(f, lid, t) for f, lid, t in mc.lectures(base)}
    every = [b for _i, bs in pools.values() for b in bs][:400]
    items = []
    for r in due:
        if len(items) >= n:
            break
        srcs, backs = pools.get(r["lecture_id"], ([], []))
        src = next((s for s in srcs if key(r["lecture_id"], s["question"]) == r["key"]), None)
        it = mc._as_item(src, rng, backs, every) if src else None
        if it:
            items.append({**it, "key": r["key"], "item_id": r["key"], "sessions": r["sessions"]})
    counts = {"items": len(rows), "secure": sum(1 for r in rows if r["secure"]), "due": len(due),
              "in_progress": sum(1 for r in rows if not r["secure"] and not r["due"])}
    nxt = min((r["next"] for r in rows if r["next"]), default=None)
    if not rows:
        msg = "Relearning starts after your first mastery check."
    elif counts["due"]:
        msg = (f"{counts['due']} item(s) due for relearning; {len(items)} fit in today's review time "
               f"(about {round(len(items) * SECONDS_PER_ITEM / 60, 1)} min of {minutes} left).")
    else:
        msg = (f"Nothing due. {counts['secure']} of {counts['items']} items secure"
               + (f"; next relearning {dt.date.fromisoformat(nxt):%a %d %b}." if nxt else "."))
    return {"ok": True, "gap_days": gap, "budget_minutes": minutes, "counts": counts, "message": msg,
            "items": items, "next": nxt, "exam_date": exam_date(environ).isoformat() if exam_date(environ) else None}


def record(results_json, seconds: int = 0, lectures_dir=None, practice_dir=None) -> dict:
    """A finished relearning session: [{key, lecture_id, question, first_try_ok}]. Every item reached
    criterion (right once); first-try answers are what count as the day's result."""
    _, mdir = mc._dirs(lectures_dir, practice_dir)
    try:
        rows = json.loads(results_json) if isinstance(results_json, str) else results_json
    except ValueError as e:
        return {"ok": False, "error": f"invalid results: {e}"}
    rows = [r for r in rows or [] if isinstance(r, dict) and r.get("lecture_id") and r.get("question")]
    if not rows:
        return {"ok": False, "error": "no items"}
    per = max(1, int(seconds or 0) // len(rows)) if seconds else SECONDS_PER_ITEM
    t = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    mdir.mkdir(parents=True, exist_ok=True)
    with (mdir / "items.jsonl").open("a", encoding="utf-8") as fh:
        for r in rows:
            # every item reached criterion this session (right once), which is what counts as a session
            fh.write(json.dumps({"t": t, "lecture_id": r["lecture_id"], "question": r["question"], "ok": True,
                                 "first_try_ok": bool(r.get("first_try_ok")), "source": "relearn", "seconds": per}) + "\n")
    first = sum(1 for r in rows if r.get("first_try_ok"))
    gap = gap_days()
    nxt = _today() + dt.timedelta(days=gap)
    return {"ok": True, "items": len(rows), "first_try_right": first,
            "message": f"{first} of {len(rows)} right first time; all relearned. Next relearning from {nxt:%a %d %b}."}
