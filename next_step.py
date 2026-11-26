"""Today's next step: one recommendation, worked out in code from the study record.

Order (retrieval that is due beats new study; the learner model, not the model, decides):
  1 relearning due      items missed or due for their next spaced criterion session (relearning.py)
  2 flashcards due      FSRS cards due today (spaced.py), within the weekly minutes
  3 mastery check       not taken, due again after new lectures, or a retry whose spacing has passed
  4 weakest lecture     the lecture with the lowest mastery-check share: work a missed question through
                        the guided tutor (hints first, then a closed-book question)
  5 all caught up       a stretch: the mastery check on the newest lecture, or nothing
Research: due reviews first, growth-zone work next, a little challenge (IntelliCode's 40/50/10 mix);
Khan Academy's plain-text summary of recent attempts and gaps (2026).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import mastery_check as mc


def steps(lectures_dir=None, practice_dir=None, environ=None) -> dict:
    base, mdir = mc._dirs(lectures_dir, practice_dir)
    prac = mdir.parent
    out = []
    # 1 relearning
    try:
        import relearning
        rp = relearning.plan(lectures_dir=base, practice_dir=prac, environ=environ)
        n = len(rp.get("items") or [])
        if n:
            out.append({"kind": "relearn", "title": f"Relearn {n} item(s)",
                        "detail": rp["message"], "minutes": round(n * relearning.SECONDS_PER_ITEM / 60, 1),
                        "target": "mastery"})
    except Exception:
        pass
    # 2 flashcards
    try:
        import spaced
        s = spaced.Reviews(prac, base).session()
        due = sum(1 for c in s.get("cards") or [] if c.get("kind") == "review")
        if due:
            out.append({"kind": "review", "title": f"Review {due} due card(s)",
                        "detail": f"About {s.get('planned_minutes')} min, lowest recall first.",
                        "minutes": s.get("planned_minutes"), "target": "review"})
    except Exception:
        pass
    # 3 mastery check
    st = mc.status(base, prac, environ)
    waiting = False
    if st["state"] == "retry" and st.get("due_at"):
        try:
            waiting = dt.datetime.fromisoformat(st["due_at"]) > dt.datetime.now().astimezone()
        except ValueError:
            waiting = False
    if st["state"] in ("not_taken", "due_new") or (st["state"] == "retry" and not waiting):
        out.append({"kind": "mastery", "title": {"not_taken": "Take the mastery check", "due_new": "Mastery check: new lectures",
                                                  "retry": "Try the mastery check again"}[st["state"]],
                    "detail": st["message"], "minutes": 10, "target": "mastery"})
    # 4 weakest lecture: a missed question through the guided tutor
    hist = mc.attempts(mdir)
    last = hist[-1] if hist else None
    if last and last.get("missed"):
        rows = sorted(last.get("by_lecture") or [], key=lambda r: (r.get("right", 0) / max(1, r.get("asked", 1)), r.get("lecture_id")))
        weakest = rows[0]["lecture_id"] if rows else last["missed"][0].get("lecture_id")
        miss = next((m for m in last["missed"] if m.get("lecture_id") == weakest), last["missed"][0])
        sess = None
        for sub in ("mock_exam_sessions", "sessions"):
            f = mdir / sub / f"{last.get('check_id')}.json"
            if f.is_file():
                sess = json.loads(f.read_text(encoding="utf-8"))
        it = next((x for x in (sess or {}).get("items") or [] if x.get("question") == miss.get("question")), None)
        if it:
            title = it.get("lecture_title") or weakest
            out.append({"kind": "tutor", "title": f"Work through a miss from “{title}”",
                        "detail": "The tutor keeps the answer back while you reason it out, then asks one question without help.",
                        "minutes": 5, "target": "tutor", "ref": f"mastery:{last['check_id']}:{it['item_id']}"})
    if not out:
        out.append({"kind": "done", "title": "All caught up",
                    "detail": "Nothing is due. A good moment to preview the next class or rest.", "minutes": 0, "target": "none"})
    return {"ok": True, "top": out[0], "steps": out[:4]}
