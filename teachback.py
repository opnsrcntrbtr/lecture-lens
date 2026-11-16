"""LS-6 teach-back with planted errors: practise diagnosis.

A 'colleague' explains a section of the lecture in short statements, two or three of which
are planted misconceptions. Each planted statement comes with the correct version and the
extract it contradicts; each other statement cites the extract that supports it. Before a
statement set is shown, the support checker (NG-2, main model yes/no) must find every
unplanted statement and every correct version supported by its cited lines, and every
planted statement not supported; statements that fail are dropped, and a set needs at least
two planted and four unplanted to ship. You mark the statements you think are wrong and may
write a correction. Scoring compares your marks with the hidden key and does not judge your
free text.

Files: practice/teachback/<id>.json
  {id, lecture, topic, statements: [{s_id, text, planted, line_ids, extract_id, correct?, why?}],
   learner_marks, corrections, caught, missed, false_alarms, scored}
"""
from __future__ import annotations

import json
import random
import re
import uuid
from pathlib import Path

import cite

MIN_PLANTED, MIN_TRUE = 2, 4

WRITE_PROMPT = """You are a colleague explaining "{topic}" from a lecture, in {n} short statements
(at most 30 words each). Exactly {k} statements must be plausible but wrong: each contradicts
one extract below (a common confusion, a swapped term, a wrong direction or number). The
other statements must be correct and supported by an extract. Do not say which are wrong in
the statement text.

EXTRACTS:
{extracts}

Reply with JSON only: {{"statements": [{{"text": "...", "extract": "x1", "planted": false}},
{{"text": "...", "extract": "x2", "planted": true, "correct": "<the correct statement>", "why": "<one line>"}}]}}"""


def _json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def generate(topic: str, extract_rows: list[dict], passages_of, llm, checker, n: int = 10, k: int = 4,
             seed: int | None = None, rounds: int = 2) -> dict:
    """passages_of(extract) -> list of source passage texts; checker: cite.Checker. Asks for
    more statements than are shown (n, k) and tries up to `rounds` times, because the checker
    drops some."""
    ex = {e["extract_id"]: e for e in extract_rows}
    keep, dropped = [], []
    for _ in range(rounds):
        raw = llm(WRITE_PROMPT.format(topic=topic, n=n, k=k,
                                      extracts="\n".join(f"{e['extract_id']}: {e['quote'][:400]}" for e in extract_rows)))
        _check(_json(raw).get("statements") or [], ex, passages_of, checker, keep, dropped)
        if sum(s["planted"] for s in keep) >= MIN_PLANTED and sum(not s["planted"] for s in keep) >= MIN_TRUE:
            break
    return _finish(topic, keep, dropped, seed)


def _check(rows, ex, passages_of, checker, keep, dropped):
    seen = {s["text"] for s in keep}

    def supported(text, e):
        ps = passages_of(e) or [e["quote"]]
        return any(p >= checker.threshold for p in checker.fn(text, ps))

    for r in rows if isinstance(rows, list) else []:
        e = ex.get(r.get("extract"))
        text = (r.get("text") or "").strip()
        if not e or not text or len(text.split()) > 40 or text in seen:
            dropped.append("bad row")
            continue
        tw = cite.norm_words(text)
        if any(len(tw & cite.norm_words(k["text"])) / max(1, len(tw | cite.norm_words(k["text"]))) >= 0.6 for k in keep):
            dropped.append("near-duplicate")
            continue
        if r.get("planted"):
            corr = (r.get("correct") or "").strip()
            if not corr or supported(text, e) or not supported(corr, e):
                dropped.append("planted error not shown to contradict its extract")
                continue
        elif not supported(text, e):
            dropped.append("true statement not supported")
            continue
        keep.append({"text": text, "planted": bool(r.get("planted")), "extract_id": e["extract_id"],
                     "line_ids": e.get("line_ids", [])[:6], "t0": e.get("t0"),
                     **({"correct": r["correct"].strip(), "why": (r.get("why") or "").strip()} if r.get("planted") else {})})
        seen.add(text)


def _finish(topic, keep, dropped, seed):
    planted = [s for s in keep if s["planted"]]
    true = [s for s in keep if not s["planted"]]
    if len(planted) < MIN_PLANTED or len(true) < MIN_TRUE:
        return {"ok": False, "error": f"not enough checked statements ({len(planted)} planted, {len(true)} true)",
                "dropped": dropped}
    stmts = planted[:3] + true[:5]
    random.Random(seed).shuffle(stmts)
    for i, s in enumerate(stmts, 1):
        s["s_id"] = f"s{i}"
    return {"ok": True, "topic": topic, "statements": stmts, "dropped": dropped}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root) / "teachback"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, d: dict) -> dict:
        (self.root / f"{d['id']}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
        return d

    def load(self, tid: str) -> dict:
        return json.loads((self.root / f"{tid}.json").read_text())

    def new(self, lecture: str, gen: dict) -> dict:
        d = {"id": uuid.uuid4().hex[:10], "lecture": lecture, "topic": gen["topic"], "statements": gen["statements"],
             "learner_marks": [], "corrections": {}, "caught": None, "missed": None, "false_alarms": None,
             "scored": False}
        return self.save(d)


def public(d: dict) -> dict:
    """What the learner sees: no key until scored."""
    st = [{"s_id": s["s_id"], "text": s["text"]} for s in d["statements"]]
    if d["scored"]:
        st = [{"s_id": s["s_id"], "text": s["text"], "planted": s["planted"], "marked": s["s_id"] in d["learner_marks"],
               "correct": s.get("correct"), "why": s.get("why"), "t0": s.get("t0")} for s in d["statements"]]
    return {"id": d["id"], "lecture": d["lecture"], "topic": d["topic"], "statements": st, "scored": d["scored"],
            **({k: d[k] for k in ("caught", "missed", "false_alarms")} if d["scored"] else {})}


def score(store: Store, tid: str, marks: list[str], corrections: dict | None = None) -> dict:
    d = store.load(tid)
    if d["scored"]:
        return public(d)
    ids = {s["s_id"] for s in d["statements"]}
    marks = [m for m in marks if m in ids]
    planted = {s["s_id"] for s in d["statements"] if s["planted"]}
    d.update({"learner_marks": marks, "corrections": {k: v for k, v in (corrections or {}).items() if k in ids},
              "caught": len(planted & set(marks)), "missed": len(planted - set(marks)),
              "false_alarms": len(set(marks) - planted), "scored": True})
    store.save(d)
    return public(d)
