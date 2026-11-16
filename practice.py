"""Attempt-first study sessions (LS-1): every answer-giving aid follows a retrieval attempt.

A recap or cheat sheet opens on an empty "write what you remember" box; it is revealed only
after an attempt of at least 15 characters or an explicit "just show me", which is logged as
a skipped attempt. The revealed text marks the points the attempt missed. A practice quiz
asks for an answer and a confidence rating before the key; hints (where in the lecture, then
one wrong option removed) are logged. Each assisted session ends with two or three
closed-book items, and only closed-book results count as progress.

Rows go to practice/attempts.jsonl:
  {item_id, kind, t, attempt_text, hint_level, skipped_attempt, revealed, confidence,
   correct, assisted, lecture_id, session_id}
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import uuid
from pathlib import Path

import cite
import quiz

MIN_ATTEMPT = 15
CONFIDENCE = ("sure", "unsure", "guess")
CLOSED_BOOK_N = 3


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def can_reveal(attempt_text: str | None, skip: bool) -> bool:
    """The reveal control's rule: a real attempt, or an explicit skip."""
    return skip or len((attempt_text or "").strip()) >= MIN_ATTEMPT


def key_points(markdown: str) -> list[str]:
    """Bullets and short paragraphs of a recap or cheat sheet, as plain text."""
    pts = []
    for line in markdown.splitlines():
        t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        t = re.sub(r"\s*\[[^\]]*\d[^\]]*\](?:,\s*)?", " ", t).strip(" ,")   # timecode and slide citations
        t = re.sub(r"[*_`#>|]", "", t).strip()
        if len(cite.norm_words(t)) >= 3 and not line.lstrip().startswith("#"):
            pts.append(t)
    return pts


STOP = set("""a an the and or but of to in on for with by from as at is are was were be been being it its this that
these those there their they them we you your our can could should would will may might must not no do does did has have
had so such than then also only very more most less into over under about between which who whom what when where why how
all any each other some""".split())


def missed(points: list[str], attempt: str, cover: float = 0.25, enough: int = 3) -> list[bool]:
    """For each point, True when the attempt shares fewer than `enough` of its content words
    and under `cover` of them (stop words ignored): a recap point is a paraphrase target,
    not a sentence to reproduce."""
    have = cite.norm_words(attempt or "") - STOP
    out = []
    for p in points:
        w = cite.norm_words(p) - STOP
        k = len(w & have)
        out.append(not w or (k < enough and k / len(w) < cover))
    return out


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)

    def log(self, row: dict) -> dict:
        row = {"item_id": None, "kind": None, "t": now_iso(), "attempt_text": None, "hint_level": 0,
               "skipped_attempt": False, "revealed": False, "confidence": None, "correct": None, "assisted": True,
               "lecture_id": None, "session_id": None, **row}
        with (self.root / "attempts.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def attempts(self) -> list[dict]:
        p = self.root / "attempts.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def save(self, s: dict) -> dict:
        (self.root / "sessions" / f"{s['session_id']}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False))
        return s

    def load(self, sid: str) -> dict:
        return json.loads((self.root / "sessions" / f"{sid}.json").read_text())


# ------------------------------------------------------------------ sessions
def start_recap(store: Store, lecture_id: str, title: str, content: str, kind: str = "recap",
                score_from: str | None = None) -> dict:
    """`content` is shown after reveal; the attempt is scored against `score_from` (the notes'
    TL;DR bullets) when given, else against every point of `content`."""
    s = {"session_id": uuid.uuid4().hex[:10], "kind": kind, "lecture_id": lecture_id, "title": title,
         "content": content, "score_from": score_from, "attempt": None, "revealed": False, "closed_book": [], "started": now_iso()}
    store.save(s)
    return {"session_id": s["session_id"], "kind": kind, "prompt": f"Write what you remember about {title}.",
            "reveal_enabled": False}


def attempt(store: Store, sid: str, text: str) -> dict:
    s = store.load(sid)
    s["attempt"] = (s.get("attempt") or "") + ("\n" if s.get("attempt") else "") + text
    store.save(s)
    return {"session_id": sid, "reveal_enabled": can_reveal(s["attempt"], False)}


def reveal_recap(store: Store, sid: str, skip: bool = False) -> dict:
    s = store.load(sid)
    if not can_reveal(s.get("attempt"), skip):
        return {"ok": False, "error": f"write at least {MIN_ATTEMPT} characters first, or choose Just show me"}
    pts = key_points(s.get("score_from") or s["content"])
    miss = missed(pts, s.get("attempt") or "")
    s["revealed"] = True
    store.save(s)
    store.log({"item_id": f"{s['kind']}:{s['lecture_id']}", "kind": s["kind"], "attempt_text": s.get("attempt"),
               "skipped_attempt": skip and not can_reveal(s.get("attempt"), False), "revealed": True,
               "lecture_id": s["lecture_id"], "session_id": sid, "assisted": True,
               "correct": round(1 - sum(miss) / len(miss), 3) if miss else None})
    return {"ok": True, "content": s["content"], "points": [{"text": p, "missed": m} for p, m in zip(pts, miss)],
            "recalled_share": round(1 - sum(miss) / len(miss), 3) if miss else None}


def start_quiz(store: Store, lecture_id: str, items: list[dict], n: int = 5, seed: int | None = None,
               closed_book: bool = False) -> dict:
    rng = random.Random(seed)
    pool = [i for i in items if i.get("lint", quiz.lint(i)).get("passed") and i.get("key_supported") is not False]
    rng.shuffle(pool)
    chosen = pool[:n]
    s = {"session_id": uuid.uuid4().hex[:10], "kind": "closed_book" if closed_book else "quiz", "lecture_id": lecture_id,
         "items": [], "pos": 0, "started": now_iso(), "seed": seed}
    for it in chosen:
        opts, key_pos, order = quiz.shuffled(it, rng)
        s["items"].append({"item_id": it["item_id"], "stem": it["stem"], "options": opts, "key_pos": key_pos,
                           "explanation": it.get("explanation", ""), "at": it.get("at", ""), "hint_level": 0,
                           "level": it.get("requested_level"),
                           "answer": None, "confidence": None, "revealed": False, "removed": None})
    store.save(s)
    return view(s)


def view(s: dict) -> dict:
    """What the learner sees for the current item: never the key before reveal."""
    if s["pos"] >= len(s["items"]):
        return {"session_id": s["session_id"], "kind": s["kind"], "done": True}
    it = s["items"][s["pos"]]
    v = {"session_id": s["session_id"], "kind": s["kind"], "done": False, "index": s["pos"], "of": len(s["items"]),
         "stem": it["stem"], "options": it["options"], "removed": it["removed"], "hint_level": it["hint_level"],
         "answered": it["answer"] is not None, "closed_book": s["kind"] == "closed_book"}
    if it["revealed"]:
        v.update({"key": it["key_pos"], "correct": it["answer"] == it["key_pos"], "explanation": it["explanation"],
                  "at": it["at"]})
    return v


def hint(store: Store, sid: str) -> dict:
    s = store.load(sid)
    if s["kind"] == "closed_book":
        return {"ok": False, "error": "closed-book items take no hints"}
    it = s["items"][s["pos"]]
    it["hint_level"] = min(it["hint_level"] + 1, 2)
    text = (f"Look at the lecture around {it['at'] or 'the section this covers'}." if it["hint_level"] == 1 else
            "One wrong option is crossed out.")
    if it["hint_level"] == 2 and it["removed"] is None:
        wrong = [i for i in range(len(it["options"])) if i != it["key_pos"] and i != it.get("answer")]
        it["removed"] = random.Random(sid + str(s["pos"])).choice(wrong) if wrong else None
    store.save(s)
    return {"ok": True, "hint": text, **view(s)}


def answer(store: Store, sid: str, choice: int, confidence: str) -> dict:
    s = store.load(sid)
    it = s["items"][s["pos"]]
    if confidence not in CONFIDENCE:
        return {"ok": False, "error": f"rate your confidence first: {', '.join(CONFIDENCE)}"}
    if not 0 <= choice < len(it["options"]):
        return {"ok": False, "error": "no such option"}
    it["answer"], it["confidence"], it["revealed"] = choice, confidence, True
    store.save(s)
    store.log({"item_id": it["item_id"], "kind": "quiz_item", "attempt_text": it["options"][choice],
               "hint_level": it["hint_level"], "revealed": True, "confidence": confidence,
               "correct": choice == it["key_pos"], "assisted": s["kind"] != "closed_book",
               "lecture_id": s["lecture_id"], "session_id": sid, "level": it.get("level"), "stem": it["stem"][:200]})
    return {"ok": True, **view(s)}


def skip_item(store: Store, sid: str) -> dict:
    """Just show me: reveal without an answer, logged as a skipped attempt."""
    s = store.load(sid)
    it = s["items"][s["pos"]]
    if s["kind"] == "closed_book":
        return {"ok": False, "error": "closed-book items need an answer"}
    it["revealed"] = True
    store.save(s)
    store.log({"item_id": it["item_id"], "kind": "quiz_item", "skipped_attempt": True, "revealed": True,
               "hint_level": it["hint_level"], "assisted": True, "lecture_id": s["lecture_id"], "session_id": sid})
    return {"ok": True, **view(s)}


def advance(store: Store, sid: str) -> dict:
    s = store.load(sid)
    if s["pos"] < len(s["items"]) and not s["items"][s["pos"]]["revealed"]:
        return {"ok": False, "error": "answer or choose Just show me first"}
    s["pos"] += 1
    store.save(s)
    return {"ok": True, **view(s)}


def closed_book_items(store: Store, items: list[dict], lecture_id: str, n: int = CLOSED_BOOK_N, seed: int | None = None) -> dict:
    """The closing items of an assisted session: items not seen in assisted practice recently."""
    seen = {a["item_id"] for a in store.attempts() if a.get("assisted")}
    fresh = [i for i in items if i["item_id"] not in seen] or items
    return start_quiz(store, lecture_id, fresh, n=n, seed=seed, closed_book=True)


def progress(store: Store) -> dict:
    """Unassisted results only, plus the skip rate."""
    rows = store.attempts()
    closed = [r for r in rows if not r.get("assisted") and r.get("correct") is not None]
    gated = [r for r in rows if r.get("assisted") and r.get("revealed")]
    by: dict[str, list] = {}
    for r in closed:
        by.setdefault(r.get("lecture_id") or "?", []).append(bool(r["correct"]))
    sure_wrong = sum(1 for r in closed if r.get("confidence") == "sure" and not r["correct"])
    return {"closed_book_items": len(closed),
            "closed_book_accuracy": round(sum(bool(r["correct"]) for r in closed) / len(closed), 3) if closed else None,
            "by_lecture": {k: {"n": len(v), "accuracy": round(sum(v) / len(v), 3)} for k, v in by.items()},
            "sure_and_wrong": sure_wrong,
            "skip_rate": round(sum(1 for r in gated if r.get("skipped_attempt")) / len(gated), 3) if gated else None,
            "assisted_reveals": len(gated)}
