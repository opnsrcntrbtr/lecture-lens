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


# ------------------------------------------------------------------ questions v2 (NG-5)
# Knowledge units first, questions without forced answers. A round: (1) the model extracts
# claims, definitions, decisions, examples and numbers from the new window, each citing line
# IDs that must exist; (2) for each selected unit the prompt names the unit, the question
# type and the cognitive level outright, and asks for the question only; (3) one constrained
# check asks which retrieved class lines already answer each question. Answered questions go
# to the self-check pile with the class lines quoted as the draft (extractive, never
# generated); the rest go to the ask pile with no draft. A "the lecture does not specify"
# draft cannot be produced. The requested level is recorded, never judged by a model.

LEVELS = ("understand", "apply", "analyse", "evaluate")
TYPES2 = ("clarify", "challenge", "practical")
UNIT_KINDS = ("claim", "definition", "decision", "example", "number")
TIME_BOX_S = 240

UNITS_PROMPT = """LIVE CLASS: {title}
NEW CLASS LINES SINCE THE LAST ROUND (speech-to-text; quoted; each with its ID):
{lines}

List up to {n} knowledge units taught in these lines: claims, definitions, decisions, examples or numbers.
Each unit cites the IDs of the lines that state it, from the list above only.
Reply with JSON only: {{"units": [{{"kind": "claim", "summary": "<at most 20 words>", "line_ids": ["L3", "L4"]}}]}}"""

QUESTIONS_PROMPT = """LIVE CLASS: {title}
Write one question per unit below, as the learner asking the lecturer in the Q&A. Each question must be of the
TYPE and cognitive LEVEL given for that unit, use only terms from the unit's quoted lines, have exactly one
question mark and fewer than 45 words. Say "you said" or "the lecture", never "the transcript". Questions only:
no answers.
TYPES: clarify = ask how a criterion, threshold or framework works; challenge = test the claim against a limit,
a counter-example or an evidence-bank fact; practical = ask how to apply it (tools, costs, evals, trade-offs).

{units}

EVIDENCE BANK (you may name one id in a challenge question):
{bank}

Reply with JSON only: {{"questions": [{{"unit_id": "u1", "q": "...?"}}]}}"""

ANSWERED_PROMPT = """For each question, decide which of its numbered class passages already answer it: the passage
must give the specific thing asked (the criterion, number, method, limit or trade-off), not merely mention the topic
or restate the claim the question is about. When in doubt, it is not answered.
Reply with JSON only: {{"answered": {{"Q1": [passage numbers], "Q2": []}}}}

{blocks}"""


def _json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def window_lines(live: Path, frm: dt.datetime, now: dt.datetime, guard) -> list[dict]:
    """Guarded transcript lines in [frm, now] with the evidence store's live line IDs
    (live-<day>/t/<n>, n counting non-empty segments from the start of the day)."""
    segs = [s for s in load_jsonl(live / "whisper_segments.jsonl") if s.get("text")]
    keep = [(i, s) for i, s in enumerate(segs) if frm <= (cite.parse_t(s.get("t", "")) or now) <= now]
    texts = guard([s["text"].strip() for _, s in keep])
    day = live.name
    return [{"line_id": f"live-{day}/t/{i}", "t": s.get("t"), "text": t} for (i, s), t in zip(keep, texts)]


def day_lines(live: Path, now: dt.datetime, guard) -> list[dict]:
    segs = [s for s in load_jsonl(live / "whisper_segments.jsonl") if s.get("text")]
    keep = [(i, s) for i, s in enumerate(segs) if (cite.parse_t(s.get("t", "")) or now) <= now]
    texts = guard([s["text"].strip() for _, s in keep])
    return [{"line_id": f"live-{live.name}/t/{i}", "t": s.get("t"), "text": t} for (i, s), t in zip(keep, texts)]


def parse_units(raw: str, lines: list[dict]) -> list[dict]:
    """Units whose cited lines exist and whose summary shares at least two content words with
    them (a unit the lines do not state is dropped)."""
    short = {f"L{k}": l for k, l in enumerate(lines, 1)}
    out = []
    for u in _json(raw).get("units") or []:
        if not isinstance(u, dict):
            continue
        ids = [i for i in (u.get("line_ids") or []) if i in short]
        summary = str(u.get("summary") or "").strip()
        if not ids or not summary or len(summary.split()) > 30:
            continue
        cited = " ".join(short[i]["text"] for i in ids)
        if len(cite.words(summary) & cite.words(cited)) < 2:
            continue
        out.append({"unit_id": f"u{len(out) + 1}", "kind": u.get("kind") if u.get("kind") in UNIT_KINDS else "claim",
                    "summary": summary, "line_ids": [short[i]["line_id"] for i in ids], "quote": cited[:600],
                    "t": short[ids[0]]["t"]})
    return out


def select_units(units: list[dict], prior_qs: list[str], k: int) -> list[dict]:
    """Up to k units, kinds varied, none whose summary an earlier question already covers."""
    fresh = [u for u in units if not any(repeats(u["summary"], p, dup=0.45, contained=0.7) for p in prior_qs)]
    out, kinds = [], []
    for u in sorted(fresh, key=lambda u: (kinds.count(u["kind"]), -len(cite.words(u["quote"])))):
        if len(out) >= k:
            break
        out.append(u)
        kinds.append(u["kind"])
    return out


def assign(units: list[dict], round_n: int) -> list[dict]:
    """Type and level named outright, rotating so a round mixes them."""
    return [{**u, "type": TYPES2[(round_n + i) % len(TYPES2)], "requested_level": LEVELS[(round_n + i) % len(LEVELS)]}
            for i, u in enumerate(units)]


def parse_questions(raw: str, units: list[dict], prior: list[str], bank_ids: set[str]) -> list[dict]:
    by = {u["unit_id"]: u for u in units}
    seen = list(prior)
    out = []
    for it in _json(raw).get("questions") or []:
        if not isinstance(it, dict) or it.get("unit_id") not in by:
            continue
        u = by[it["unit_id"]]
        q = tidy(str(it.get("q") or "").strip())
        if q.count("?") != 1 or not q.endswith("?") or len(q.split()) >= 45:
            continue
        if len(cite.words(q) & cite.words(u["quote"] + " " + u["summary"])) < 2:
            continue
        sims = [jaccard(q, p) for p in seen]
        if any(repeats(q, p) for p in seen):
            continue
        out.append({**u, "q": q, "max_sim_prior": round(max(sims, default=0.0), 3),
                    "evidence_ids": sorted(set(re.findall(r"\bS\d+\b", q)) & bank_ids)})
        seen.append(q)
    return out


def top_passages(q: str, lines: list[dict], k: int = 3) -> list[dict]:
    want = cite.words(q)
    scored = sorted(((len(want & cite.words(l["text"])), -n, l) for n, l in enumerate(lines)), key=lambda x: (-x[0], x[1]))
    return [l for s, _, l in scored[:k] if s >= 2]


def route(qs: list[dict], day: list[dict], llm) -> list[dict]:
    """One constrained call: which retrieved class lines answer each question. Answered ->
    self_check with those lines as the draft; otherwise ask, with no draft."""
    blocks, cand = [], {}
    for n, q in enumerate(qs, 1):
        own = set(q.get("line_ids") or [])   # the lines the question came from restate it; they do not answer it
        ps = top_passages(q["q"], [l for l in day if l["line_id"] not in own])
        cand[f"Q{n}"] = ps
        blocks.append(f"Q{n}: {q['q']}\n" + "\n".join(f"  [{i}] {p['text'][:400]}" for i, p in enumerate(ps, 1)))
    ans = _json(llm(ANSWERED_PROMPT.format(blocks="\n\n".join(blocks)))).get("answered") or {} if blocks else {}
    out = []
    for n, q in enumerate(qs, 1):
        ids = []
        for x in ans.get(f"Q{n}") or []:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        hit = [cand[f"Q{n}"][i - 1] for i in ids if 1 <= i <= len(cand[f"Q{n}"])]
        if hit:
            draft = " ".join(f"[{(cite.parse_t(h['t']) or dt.datetime.now(dt.timezone.utc)).astimezone():%H:%M}] {h['text']}"
                             for h in hit[:2])
            out.append({**q, "pile": "self_check", "answered_in_class": True, "draft_answer": draft[:700],
                        "answer_line_ids": [h["line_id"] for h in hit]})
        else:
            out.append({**q, "pile": "ask", "answered_in_class": False, "draft_answer": None, "answer_line_ids": []})
    return out


def rank(qs: list[dict], k: int) -> list[dict]:
    """Ask pile first; then grounding, a named source, type variety."""
    def score(q):
        return (q["pile"] == "ask", len(cite.words(q["q"]) & cite.words(q["quote"])), bool(q["evidence_ids"]))
    out, types = [], []
    for q in sorted(qs, key=score, reverse=True):
        if len(out) >= k:
            break
        if types.count(q["type"]) >= 2 and len({x["type"] for x in qs}) > 1 and len(qs) > k:
            continue
        out.append(q)
        types.append(q["type"])
    return out


def section_v2(n: int, clock: str, topic: str, rows: list[dict]) -> str:
    lines = [f"# Round {n} ({clock}): {topic}", ""]
    ask = [r for r in rows if r["pile"] == "ask"]
    sc = [r for r in rows if r["pile"] == "self_check"]
    if ask:
        lines += ["## Ask now"] + [f"{i}. {r['q']} ({r['type']}, {r['requested_level']})" for i, r in enumerate(ask, 1)] + [""]
    if sc:
        lines += ["## Self-check later (answered in class)"]
        lines += [x for i, r in enumerate(sc, 1) for x in (f"{i}. {r['q']}", f"   - _In class:_ {r['draft_answer']}")]
        lines.append("")
    return "\n".join(lines)


def metrics(live: Path, add: dict | None = None) -> dict:
    """curate_metrics.json: rounds, questions, asked, answered, skipped rounds."""
    f = live / "curate_metrics.json"
    m = json.loads(f.read_text()) if f.exists() else {"rounds": 0, "questions": 0, "ask": 0, "self_check": 0,
                                                       "asked": 0, "answered": 0, "skipped": []}
    for k, v in (add or {}).items():
        if k == "skipped":
            m["skipped"] = (m.get("skipped") or [])[-20:] + [v]
        else:
            m[k] = m.get(k, 0) + v
    rows = curated(load_jsonl(live / "questions.jsonl"))
    threads = json.loads((live / "qa_threads.json").read_text()) if (live / "qa_threads.json").exists() else []
    asked = [r for r in rows if asked_in(r.get("q", ""), threads)]
    m["asked"] = len(asked)
    m["answered"] = sum(1 for r in asked if (asked_in(r["q"], threads) or {}).get("answers"))
    f.write_text(json.dumps(m, indent=1))
    return m


def run_v2(live: Path, llm, now: dt.datetime, title: str, guard=lambda lines: lines, k: int = 5,
           lookback_min: int = 25, min_words: int = 120, time_box_s: int = TIME_BOX_S, clock=None) -> dict:
    """One NG-5 round. llm(prompt) -> raw text. Rows keep the v1 fields the app reads (q,
    answer, sources, part, type, curated, round) and add q_id, unit_id, line_ids,
    requested_level, evidence_ids, max_sim_prior, answered_in_class, pile, draft_answer,
    asked, answered_t."""
    import time as _time
    clock = clock or _time.monotonic
    started = clock()
    q_f = live / "questions.jsonl"
    rows = load_jsonl(q_f)
    frm = since(rows, now - dt.timedelta(minutes=lookback_min))
    lines = window_lines(live, frm, now, guard)
    if sum(len(l["text"].split()) for l in lines) < min_words:
        return {"ok": True, "added": 0, "round": None, "note": f"not enough new transcript since {frm.astimezone():%H:%M}"}
    n = next_round(rows)

    def over(step):
        if clock() - started > time_box_s:
            metrics(live, {"skipped": {"t": now.isoformat(), "round": n, "at": step}})
            return {"ok": True, "added": 0, "round": None, "note": f"round skipped: over its {time_box_s} s time box at {step}"}
        return None

    numbered = "\n".join(f"[L{i}] {l['text']}" for i, l in enumerate(lines, 1))[-12000:]
    units = parse_units(llm(UNITS_PROMPT.format(title=title, lines=numbered, n=max(8, 2 * k))), lines)
    if (o := over("units")):
        return o
    threads = json.loads((live / "qa_threads.json").read_text()) if (live / "qa_threads.json").exists() else []
    prior = [t.get("q", "") for t in threads] + [r.get("q", "") for r in rows]
    chosen = assign(select_units(units, prior, k + 2), n)
    if not chosen:
        return {"ok": True, "added": 0, "round": None, "note": "no new knowledge units in this window"}
    bank = json.loads((live / "evidence.json").read_text()) if (live / "evidence.json").exists() else []
    unit_txt = "\n\n".join(f"{u['unit_id']} (TYPE {u['type']}, LEVEL {u['requested_level']}; {u['kind']}): "
                           f"{u['summary']}\n  quoted lines: {u['quote'][:400]}" for u in chosen)
    bank_txt = "\n".join(f"{e['id']}: {e['title']}: {e['fact']}" for e in bank) or "(empty)"
    qs = parse_questions(llm(QUESTIONS_PROMPT.format(title=title, units=unit_txt, bank=bank_txt)), chosen, prior,
                         {e["id"] for e in bank})
    if (o := over("questions")):
        return o
    if not qs:
        return {"ok": True, "added": 0, "round": None, "note": "the model returned no usable questions"}
    qs = rank(route(qs, day_lines(live, now, guard), llm), k)
    if (o := over("routing")):
        return o
    out = []
    for i, q in enumerate(qs, 1):
        text, draft = guard([q["q"], q["draft_answer"] or ""])
        out.append({"t": now.isoformat(), "q_id": f"r{n}q{i}", "round": n, "curated": True, "part": "now",
                    "type": q["type"], "q": text, "answer": draft, "sources": q["evidence_ids"],
                    "unit_id": f"r{n}{q['unit_id']}", "unit_kind": q["kind"], "unit_summary": q["summary"],
                    "line_ids": q["line_ids"], "requested_level": q["requested_level"],
                    "evidence_ids": q["evidence_ids"], "max_sim_prior": q["max_sim_prior"],
                    "answered_in_class": q["answered_in_class"], "pile": q["pile"],
                    "draft_answer": draft or None, "answer_line_ids": q["answer_line_ids"], "asked": False,
                    "answered_t": None, "version": 2})
    with q_f.open("a") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    topic = "; ".join(dict.fromkeys(u["summary"][:40] for u in chosen[:3]))
    write_markdown(live / "top_questions.md", title, section_v2(n, now.astimezone().strftime("%H:%M"), topic, out), bank)
    metrics(live, {"rounds": 1, "questions": len(out), "ask": sum(r["pile"] == "ask" for r in out),
                   "self_check": sum(r["pile"] == "self_check" for r in out)})
    return {"ok": True, "added": len(out), "round": n, "questions": out, "elapsed_s": round(clock() - started, 1)}
