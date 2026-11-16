"""Pre-class prequestions (LS-4): guess before class, check after.

Before class, three to five questions are written on the agenda's central items (questions
only). The learner types a guess for each; a guess made after the class transcript has begun
is kept but marked late and left out of the pretest count. After class, each prequestion is
resolved by retrieval over the day's class lines (never by agenda position): answered, with
the lines and their time beside the guess, or not_covered. Answered prequestions become
review cards unless the learner marks the guess as right.

live/<day>/prequestions.json:
  [{pq_id, agenda_item, text, guess, guess_t, late, resolved: {status: answered|not_covered,
    line_ids, quote, t}, got_it, became_card}]
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import cite

PROMPT = """CLASS: {title}
AGENDA ITEMS (quoted):
{items}

Write one prequestion for each agenda item: a question the learner can try to answer before class, about the
item's central idea, at most 30 words, exactly one question mark. Questions only, no answers.
Reply with JSON only: {{"questions": [{{"item": 1, "q": "...?"}}]}}"""

RESOLVE_PROMPT = """For each prequestion, decide which of its numbered class passages answer it (give the specific
idea asked, not just mention the topic). When in doubt, it is not answered.
Reply with JSON only: {{"answered": {{"P1": [passage numbers], "P2": []}}}}

{blocks}"""

ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)]|part\s+\w+[:.)-]|module\s+\w+[:.)-]|session\s+\w+[:.)-])\s*(.+)$", re.I)


def agenda_items(agenda: str, k: int = 5) -> list[str]:
    """The agenda's main items: bulleted or numbered lines (or 'Part 2: ...'), else its first lines."""
    items = [m.group(1).strip() for m in (ITEM.match(l) for l in agenda.splitlines()) if m and len(m.group(1).split()) >= 2]
    if not items:
        items = [l.strip() for l in agenda.splitlines() if len(l.split()) >= 3]
    seen, out = set(), []
    for i in items:
        if i.lower() not in seen:
            seen.add(i.lower())
            out.append(i[:200])
    return out[:k]


def _obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def generate(title: str, agenda: str, llm, k: int = 4) -> list[dict]:
    items = agenda_items(agenda, k)
    if not items:
        return []
    d = _obj(llm(PROMPT.format(title=title, items="\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)))))
    out = []
    for q in d.get("questions") or []:
        try:
            idx = int(q.get("item")) - 1
        except (TypeError, ValueError, AttributeError):
            continue
        text = str(q.get("q", "")).strip()
        if not 0 <= idx < len(items) or text.count("?") != 1 or not text.endswith("?") or len(text.split()) > 30:
            continue
        out.append({"pq_id": f"pq{len(out) + 1}", "agenda_item": items[idx], "text": text, "guess": None,
                    "guess_t": None, "late": False, "resolved": None, "got_it": None, "became_card": False})
    return out


def path(live: Path) -> Path:
    return live / "prequestions.json"


def load(live: Path) -> list[dict]:
    return json.loads(path(live).read_text()) if path(live).exists() else []


def save(live: Path, rows: list[dict]) -> list[dict]:
    live.mkdir(parents=True, exist_ok=True)
    path(live).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return rows


def class_started(live: Path, now: dt.datetime) -> bool:
    f = live / "whisper_segments.jsonl"
    if not f.exists():
        return False
    for l in f.read_text().splitlines():
        if l.strip():
            t = cite.parse_t(json.loads(l).get("t", ""))
            if t and t <= now:
                return True
    return False


def guess(live: Path, pq_id: str, text: str, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = load(live)
    r = next((r for r in rows if r["pq_id"] == pq_id), None)
    if r is None:
        raise KeyError(pq_id)
    r.update({"guess": text.strip(), "guess_t": now.isoformat(timespec="seconds"), "late": class_started(live, now)})
    save(live, rows)
    return r


def resolve(live: Path, lines: list[dict], llm, skipped: list[str] | None = None) -> list[dict]:
    """lines: the day's guarded class lines [{line_id, t, text}]. `skipped`: agenda parts the
    class did not cover (NG-4); a prequestion on one of them is not_covered without retrieval,
    because a retrieved line from elsewhere would be a false answer."""
    import agenda
    rows = load(live)
    if not rows:
        return rows
    skip_parts = [{"title": t} for t in (skipped or [])]
    cand, blocks = {}, []
    for n, r in enumerate(rows, 1):
        if skip_parts and agenda.match_part(r["agenda_item"], skip_parts):
            cand[f"P{n}"] = []
            r["_skipped"] = True
            continue
        want = cite.words(r["text"] + " " + r["agenda_item"])
        scored = sorted(((len(want & cite.words(l["text"])), -k, l) for k, l in enumerate(lines)), key=lambda x: (-x[0], x[1]))
        ps = [l for s, _, l in scored[:3] if s >= 2]
        cand[f"P{n}"] = ps
        if ps:
            blocks.append(f"P{n}: {r['text']}\n" + "\n".join(f"  [{i}] {p['text'][:400]}" for i, p in enumerate(ps, 1)))
    ans = (_obj(llm(RESOLVE_PROMPT.format(blocks="\n\n".join(blocks)))).get("answered") or {}) if blocks else {}
    for n, r in enumerate(rows, 1):
        hit = []
        for x in ans.get(f"P{n}") or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(cand[f"P{n}"]):
                hit.append(cand[f"P{n}"][i - 1])
        if r.pop("_skipped", False):
            r["resolved"] = {"status": "not_covered", "line_ids": [], "quote": None, "t": None,
                             "reason": "skipped in class (agenda map)"}
        elif hit:
            r["resolved"] = {"status": "answered", "line_ids": [h["line_id"] for h in hit],
                             "quote": " ".join(h["text"] for h in hit[:2])[:600], "t": hit[0].get("t")}
        else:
            r["resolved"] = {"status": "not_covered", "line_ids": [], "quote": None, "t": None}
        r["became_card"] = r["resolved"]["status"] == "answered" and r.get("got_it") is not True
    return save(live, rows)


def mark(live: Path, pq_id: str, got_it: bool) -> dict:
    rows = load(live)
    r = next(r for r in rows if r["pq_id"] == pq_id)
    r["got_it"] = got_it
    r["became_card"] = bool(r.get("resolved") and r["resolved"]["status"] == "answered" and not got_it)
    save(live, rows)
    return r


def cards_tsv(rows: list[dict], title: str) -> str:
    """Anki rows for prequestions that became cards: question, the class lines, a tag."""
    out = []
    for r in rows:
        if r.get("became_card") and r.get("resolved"):
            when = ""
            t = cite.parse_t(r["resolved"].get("t") or "")
            if t:
                when = f" [{t.astimezone():%H:%M}]"
            out.append(f"{r['text']}\t{(r['resolved']['quote'] or '').replace(chr(9), ' ')}{when}\t{title} prequestion")
    return "\n".join(out) + ("\n" if out else "")
