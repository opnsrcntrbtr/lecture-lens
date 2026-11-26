"""Learner brief: a short plain-text summary of how one lecture is going, computed in code from the logs.

No model judges mastery here (LLM knowledge tracing updated in the wrong direction in Hooshyar et al.,
arXiv 2512.23036); the tutor reads the brief as plain text (Khan Academy, 2026: a plain-text summary of
recent attempts and gaps raised next-item correctness; the raw log as JSON did not).
Sources: practice/mastery_check/checks.jsonl, practice/attempts.jsonl, practice/tutor/episodes.jsonl.
"""
from __future__ import annotations

import json
from pathlib import Path

import study_items as si

MAX_CHARS = 600


def _rows(p: Path) -> list[dict]:
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


def facts(lecture_id: str, lectures_dir=None, practice_dir=None) -> dict:
    base = Path(lectures_dir).expanduser() if lectures_dir else si.lectures_dir()
    prac = Path(practice_dir) if practice_dir else si.practice_dir(base)
    lid = str(lecture_id or "")
    asked = right = 0
    misses: list[str] = []
    reasons: list[dict] = []
    approx = False
    for c in _rows(prac / "mastery_check" / "checks.jsonl"):
        mine = [m for m in c.get("missed") or [] if m.get("lecture_id") == lid]
        row = next((r for r in c.get("by_lecture") or [] if r.get("lecture_id") == lid), None)
        if row:                                    # exact counts (tries since 26 Nov)
            asked += int(row.get("asked") or 0)
            right += int(row.get("right") or 0)
        elif lid in (c.get("scope") or []):        # older tries: this lecture's share of the questions
            approx = True
            per = max(1, round(len(c.get("questions") or []) / max(1, len(c.get("scope") or [1]))))
            asked += per
            right += max(0, per - len(mine))
        misses += [m.get("question") or "" for m in mine]
        reasons += [r for r in c.get("reasons") or [] if r.get("lecture_id") == lid]
    prac_rows = [r for r in _rows(prac / "attempts.jsonl") if lid and lid in str(r.get("item_id") or r.get("lecture_id") or "")]
    unaided = [r for r in prac_rows if not r.get("assisted") and r.get("correct") is not None]
    eps = [e for e in _rows(prac / "tutor" / "episodes.jsonl") if e.get("lecture_id") == lid]
    wrong_why = [r for r in reasons if not r.get("correct")]
    return {"lecture_id": lid, "last_wrong_reasoning": (wrong_why[-1] if wrong_why else None), "mastery_approx": approx, "mastery_asked": asked, "mastery_right": right, "recent_misses": misses[-3:],
            "practice_unaided": len(unaided), "practice_unaided_right": sum(1 for r in unaided if r.get("correct")),
            "tutor_episodes": len(eps), "tutor_exit_right": sum(1 for e in eps if e.get("exit_correct")),
            "asked_for_answer": sum(1 for e in eps if e.get("reveal") == "asked")}


def brief(lecture_id: str, lectures_dir=None, practice_dir=None) -> str:
    f = facts(lecture_id, lectures_dir, practice_dir)
    parts = []
    if f["mastery_asked"]:
        parts.append(f"Mastery checks: {'about ' if f['mastery_approx'] else ''}{f['mastery_right']} of "
                     f"{f['mastery_asked']} right on this lecture.")
    if f["recent_misses"]:
        parts.append("Recently missed: " + "; ".join(q[:90] for q in f["recent_misses"]) + ".")
    if f["last_wrong_reasoning"]:
        r = f["last_wrong_reasoning"]
        parts.append(f"On a recent miss they reasoned: \u201c{r['why'][:140]}\u201d.")
    if f["practice_unaided"]:
        parts.append(f"Practice without help: {f['practice_unaided_right']} of {f['practice_unaided']} right.")
    if f["tutor_episodes"]:
        parts.append(f"Tutor: {f['tutor_exit_right']} of {f['tutor_episodes']} closed-book checks right afterwards"
                     + (f"; asked for the answer {f['asked_for_answer']} time(s)." if f["asked_for_answer"] else "."))
    return " ".join(parts)[:MAX_CHARS] or "No study record for this lecture yet."
