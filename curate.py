"""Top questions for a live class, curated in rounds: each round reads what was taught
since the previous one (transcript, slides, outline), the questions already asked in the
meeting app's Q&A, earlier rounds and the verified evidence bank, and asks the local model
for a few questions to type now plus some to keep for later parts of the agenda.

Rows go to questions.jsonl with curated: true, round and part ("now" or "later"), so the
follower's Ask-now ranking and the app pick them up, and a "# Round N (HH:MM)" section goes
into top_questions.md. Sources must come from the bank and be named in the draft as (S#).
Attendee names are removed from the transcript before it reaches the model and from what
comes back."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import cite

Q_TYPES = ("clarify", "challenge", "practical", "capstone")
# tags a model adds that point at nothing the reader can see
TAGS = re.compile(r"\s*\[(?:lecture|q&a|reply|thread|transcript|slide|agenda)[^\]]*\]", re.I)


def tidy(text: str) -> str:
    """The learner speaks to the lecturer: no '[Transcript]' tags, no 'the transcript says'."""
    text = TAGS.sub("", text)
    text = re.sub(r"\bThe transcript\b", "The lecture", text)
    return re.sub(r"\bthe transcript\b", "the lecture", text).strip()
PARTS = ("now", "later")


def load_jsonl(f: Path) -> list[dict]:
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def curated(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("curated")]


def next_round(rows: list[dict]) -> int:
    return max([int(r.get("round") or 0) for r in curated(rows)] or [0]) + 1


def since(rows: list[dict], default: dt.datetime) -> dt.datetime:
    """Where this round's transcript starts: the previous curated round, else the default."""
    ts = [cite.parse_t(r["t"]) for r in curated(rows) if r.get("t")]
    ts = [t for t in ts if t]
    return max(ts) if ts else default


def jaccard(a: str, b: str) -> float:
    x, y = cite.words(a), cite.words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def repeats(q: str, prior: str, dup: float = 0.4, contained: float = 0.6) -> bool:
    """Same question reworded: high word overlap, or most of its words already in the other."""
    x, y = cite.words(q), cite.words(prior)
    return bool(x and y) and (len(x & y) / len(x | y) >= dup or len(x & y) / len(x) >= contained)


def grounded(q: str, context: str, min_shared: int = 3) -> bool:
    """Enough of the question's content words appear in the quoted context."""
    return len(cite.words(q) & cite.words(context)) >= min_shared


def asked_in(q: str, threads: list[dict], thr: float = 0.4) -> dict | None:
    """The Q&A thread that asked this question (same words), if any."""
    best = max(threads, key=lambda t: jaccard(q, t.get("q", "")), default=None)
    return best if best is not None and jaccard(q, best.get("q", "")) >= thr else None


def parse(raw: str, bank_ids: set[str], prior: list[str]) -> list[dict]:
    """Well-formed questions only: one question each, a draft answer, sources that the draft
    names as (S#) and that are in the bank, no near-repeat of a prior or asked question."""
    try:
        m = re.search(r"\[.*\]", raw, re.S)
        items = json.loads(m.group(0)) if m else []
    except (json.JSONDecodeError, AttributeError):
        items = []
    seen = list(prior)
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        q, a = str(it.get("q", "")).strip(), str(it.get("answer", "")).strip()
        if not q or not a or q.count("?") != 1 or len(q.split()) > 60:
            continue
        if any(repeats(q, s) for s in seen):
            continue
        q, a = tidy(q), tidy(re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", a))
        src = sorted(set(re.findall(r"\bS\d+\b", a)) & bank_ids, key=lambda x: int(x[1:]))
        out.append({"type": it.get("type") if it.get("type") in Q_TYPES else "clarify", "q": q, "answer": a,
                    "sources": src, "part": it.get("part") if it.get("part") in PARTS else "now",
                    **({"for": str(it["for"])[:80]} if it.get("for") else {})})
        seen.append(q)
    return out


def prompt(title: str, agenda: str, slides: list[str], text: str, asked: list[str], earlier: list[str],
           bank: list[dict], k: int) -> str:
    bank_txt = "\n".join(f"{e['id']}: {e['title']} ({e.get('date', '')}): {e['fact']}" for e in bank)
    return (f"LIVE CLASS: {title}\nAGENDA (quoted): {agenda[:1500] or '(none)'}\n"
            f"SLIDES SINCE THE LAST ROUND (quoted): {slides}\n\n"
            f"TRANSCRIPT SINCE THE LAST ROUND (quoted; speech-to-text, may have errors):\n{text[-9000:]}\n\n"
            f"ALREADY ASKED IN THE Q&A (quoted; do not repeat):\n" + ("\n".join(f"- {q[:200]}" for q in asked[-40:]) or "(none)") + "\n\n"
            "EARLIER CURATED QUESTIONS (do not repeat):\n" + ("\n".join(f"- {q[:160]}" for q in earlier[-40:]) or "(none)") + "\n\n"
            f"VERIFIED EVIDENCE BANK (the only outside sources you may cite, by id):\n{bank_txt or '(empty)'}\n\n"
            f"Write {k} NEW questions the learner could post in the Q&A. At least three about what the TRANSCRIPT SINCE THE LAST ROUND "
            "covers (part \"now\"), not about topics of earlier questions; "
            "the rest for a later agenda part (part \"later\", with \"for\" naming that part). Mix types: clarify (criteria, "
            "thresholds, how a framework works), challenge (contrast the lecture with an evidence-bank fact), practical "
            "(tools, costs, evals, trade-offs). One question mark each, under 50 words, specific to this lecture, using only "
            "terms in the quoted text, written as the learner asking the lecturer (say 'you said' or 'the lecture', never 'the transcript'). Each draft answer under 90 words: what the lecture said, then evidence as (S#) only "
            "if it truly applies; numbers only from the transcript or the bank. If unsure, say so.\n"
            'Return ONLY a JSON array: [{"part": "now", "type": "clarify", "q": "...", "answer": "...", "sources": ["S3"]}]')


def section(n: int, clock: str, topic: str, rows: list[dict]) -> str:
    lines = [f"# Round {n} ({clock}): {topic}", ""]
    for part, head in (("now", "## Ask now"), ("later", "## Keep for later")):
        sel = [r for r in rows if r["part"] == part]
        if sel:
            lines.append(head)
            lines += [x for i, r in enumerate(sel, 1) for x in
                      (f"{i}. {r['q']}" + (f" ({', '.join(r['sources'])})" if r["sources"] else "") + (f" [for: {r['for']}]" if r.get("for") else ""),
                       f"   - _Draft:_ {r['answer']}")]
            lines.append("")
    return "\n".join(lines)


def write_markdown(f: Path, title: str, sec: str, bank: list[dict]) -> None:
    md = f.read_text() if f.exists() else (f"# Top questions: {title}\n\nRanked within each part. One ask each; ask when the "
                                          "topic comes up. Evidence ids refer to evidence.json (public sources only).\n\n## Sources\n")
    if "\n## Sources\n" not in md:
        md = md.rstrip("\n") + "\n\n## Sources\n"
    md = md.replace("\n## Sources\n", "\n" + sec + "\n## Sources\n", 1).rstrip("\n")
    for e in bank:
        if f"- {e['id']}:" not in md:
            md += f"\n- {e['id']}: {e['title']}"
    f.write_text(md + "\n")


def run(live: Path, llm, now: dt.datetime, title: str, guard=lambda lines: lines, k: int = 5,
        lookback_min: int = 25, min_words: int = 120) -> dict:
    """One round. llm(prompt) -> raw text; guard(lines) -> lines with attendee names removed."""
    q_f = live / "questions.jsonl"
    rows = load_jsonl(q_f)
    frm = since(rows, now - dt.timedelta(minutes=lookback_min))
    within = lambda s: frm <= (cite.parse_t(s.get("t", "")) or now) <= now   # never lines after "now" (replays)
    segs = [s for s in load_jsonl(live / "whisper_segments.jsonl") if within(s)]
    text = " ".join(guard([s["text"] for s in segs]))
    if len(text.split()) < min_words:
        return {"ok": True, "added": 0, "round": None, "note": f"not enough new transcript since {frm.astimezone():%H:%M}"}
    slides = [s["title"] for s in load_jsonl(live / "slides.jsonl")
              if within(s) and (s.get("kind") or "slide") == "slide"]
    threads = json.loads((live / "qa_threads.json").read_text()) if (live / "qa_threads.json").exists() else []
    asked = [t.get("q", "") for t in threads]
    earlier = [r["q"] for r in curated(rows)]
    bank = json.loads((live / "evidence.json").read_text()) if (live / "evidence.json").exists() else []
    agenda = (live / "agenda.txt").read_text() if (live / "agenda.txt").exists() else ""
    raw = llm(prompt(title, agenda, slides, text, asked, earlier, bank, k))
    qs = parse(raw, {e["id"] for e in bank}, asked + earlier + [r["q"] for r in rows])
    # "now" questions must come from what was taught since the last round; "later" ones from
    # the agenda (14 Nov: a first version re-asked earlier rounds' topics)
    new_ctx = text + " " + " ".join(slides)
    qs = [q for q in qs if grounded(q["q"], new_ctx if q["part"] == "now" else agenda + " " + new_ctx)]
    for q in qs:
        q["q"], q["answer"] = guard([q["q"], q["answer"]])
    if not qs:
        return {"ok": True, "added": 0, "round": None, "note": "the model returned no usable questions"}
    n = next_round(rows)
    with q_f.open("a") as f:
        for q in qs:
            f.write(json.dumps({"t": now.isoformat(), **q, "curated": True, "round": n}) + "\n")
    topic = "; ".join(dict.fromkeys(slides[-4:])) or "latest discussion"
    write_markdown(live / "top_questions.md", title, section(n, now.astimezone().strftime("%H:%M"), topic, qs), bank)
    return {"ok": True, "added": len(qs), "round": n, "questions": qs}
