"""qa_export: read a meeting app's own Q&A export (the text file a webinar saves:
"Author      10:11 AM" then the message, blank line, next entry) and use it as the
reference for a class's Q&A.

- parse_export: entries, in file order
- export_threads: a question followed by staff replies; the export lists many
  threads twice, so threads are merged on (author, time, text)
- capture_recall: how much of the export the live capture saw
- anonymise: staff keep their names; every other author becomes "Attendee"
  ("You" for the learner), and attendee names inside messages are replaced

The raw export holds attendee names: keep it in the lecture folder (not in git)
and only ever pass anonymised threads on.
"""
from __future__ import annotations

import re

YOU = re.compile(r"\(you\)\s*$", re.I)   # the export marks the learner's own entries
HEADER = re.compile(r"^(.+?)\s{2,}(\d{1,2}:\d{2}\s?[AP]M)\s*$", re.M)


def _clean(text: str) -> str:
    return text.replace("﻿", "").replace(" ", " ").replace("\xa0", " ")


def parse_export(text: str) -> list[dict]:
    text = _clean(text)
    ms = list(HEADER.finditer(text))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[m.end():end].strip()
        if body:
            out.append({"author": m.group(1).strip(), "time": m.group(2).upper().replace("  ", " "), "text": body})
    return out


def export_threads(entries: list[dict], staff: set[str]) -> list[dict]:
    threads: dict[tuple, dict] = {}
    order: list[tuple] = []
    cur = None
    for e in entries:
        if e["author"] in staff:
            if cur is not None:
                a = {"by": e["author"], "time": e["time"], "text": e["text"]}
                if a not in cur["answers"]:
                    cur["answers"].append(a)
            continue
        key = (e["author"], e["time"], e["text"])
        if key not in threads:
            threads[key] = {"author": e["author"], "time": e["time"], "q": e["text"], "answers": []}
            order.append(key)
        cur = threads[key]
    return [threads[k] for k in order]


def norm(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())[:n]


def capture_recall(ref: list[dict], captured: list[dict]) -> dict:
    """Share of reference questions and staff replies found in the captured threads
    (matched on the first 40 letters and digits, which survives doubling and cut-offs)."""
    cq = {norm(t["q"]) for t in captured}
    ca = {norm(a["text"]) for t in captured for a in t["answers"]}
    rq = {norm(t["q"]) for t in ref}
    ra = {norm(a["text"]) for t in ref for a in t["answers"]}
    seen = lambda k, pool: any(k == c or (min(len(k), len(c)) >= 12 and (k.startswith(c) or c.startswith(k))) for c in pool)
    hit_q = {k for k in rq if seen(k, cq)}
    hit_a = {k for k in ra if seen(k, ca)}
    missed = [t for t in ref if norm(t["q"]) not in hit_q]
    return {"questions": len(rq), "questions_found": len(hit_q),
            "replies": len(ra), "replies_found": len(hit_a),
            "question_recall": round(len(hit_q) / max(1, len(rq)), 3),
            "reply_recall": round(len(hit_a) / max(1, len(ra)), 3),
            "missed_times": sorted({t["time"] for t in missed})}


def anonymise(threads: list[dict], staff: set[str], me: set[str] | None = None) -> list[dict]:
    """Drop attendee names: as authors and inside any message."""
    me = me or set()
    people = {t["author"] for t in threads} - staff
    names = sorted({p for p in people if p.lower() != "anonymous attendee"}, key=len, reverse=True)
    staff_words = {w for s in staff for w in s.split()}
    parts = sorted({w for p in names for w in p.split() if len(w) > 2 and w[0].isupper() and w not in staff_words},
                   key=len, reverse=True)
    pat = re.compile(r"\b(" + "|".join(map(re.escape, names + parts)) + r")\b") if names else None
    hide = (lambda s: pat.sub("another attendee", s)) if pat else (lambda s: s)
    out = []
    for t in threads:
        by = "You" if t["author"] in me or YOU.search(t["author"]) else "Attendee"
        out.append({"by": by, "time": t["time"], "q": hide(t["q"]),
                    "answers": [dict(a, text=hide(a["text"])) for a in t["answers"]]})
    return out
