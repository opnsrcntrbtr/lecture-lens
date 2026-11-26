"""Guided tutor v2: the tutor never holds the answer it is guiding you toward.

A guided episode, as a small state machine in code (the model writes one hint at a time):

  start    the committed answer (key) is fixed once and kept in the session file:
           from a mastery-check item (its key and explanation), or, for an open question, the best
           matching flashcard, rewritten once by the local model into a short answer from the same lines.
  attempt  you answer in your own words; graded in code against the key.
  hint     the next rung of a ladder that persists per question, across chats:
             1 a narrower question  2 where in the lecture (timecode or section)  3 one worked step
           The hint writer never sees the key; a leak guard drops any hint that repeats most of the
           key's terms (template fallback). With the model off, templates only.
  reveal   allowed after 2 attempts, or when you say "just tell me" (logged as asked).
  exit     one closed-book question from the same lecture, graded by index: next item without help.

Evidence (research report, 25 Nov 2026): unguarded chat tutors raised practice but cut exam scores 17%
(Bastani et al., PNAS 2025); sequenced, solution-grounded tutors gained 0.6-1.3 SD and a system prompt
alone could not scaffold (Kestin et al. 2025); a check-only grader halved answer giveaways (Khan Academy
2026); answer keys kept out of the tutor's context (LearnHarness); hint rung kept across chats.

Never for graded coursework: questions that look like a pasted graded quiz are refused.
Files: <practice>/tutor/sessions/<id>.json, ladder.json, episodes.jsonl.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import study_items as si

REVEAL_AFTER = 2          # attempts before the answer may be shown without asking
MAX_RUNG = 3
LEAK_SHARE = 0.5          # a hint repeating at least this share of the key's terms is a leak
RUNG_NAMES = {1: "a narrower question", 2: "where in the lecture", 3: "one worked step"}
SYS_HINT = ("You are a patient tutor. You do NOT know the final answer and must never state a full answer. "
            "Write exactly one {kind} that helps the learner reason their way to it, in at most 40 words, "
            "plain text, no preamble. Use only the lecture excerpts.")
GRADED = re.compile(r"\b(question\s+\d+\s+of\s+\d+|\(\s*\d+(\.\d+)?\s*(point|pt|mark)s?\s*\)|submit (your )?answer|"
                    r"attempts? (left|remaining)|graded (quiz|assignment|test)|skill check)\b", re.I)


def _dirs(lectures_dir=None, practice_dir=None):
    base = Path(lectures_dir).expanduser() if lectures_dir else si.lectures_dir()
    prac = Path(practice_dir).expanduser() if practice_dir else si.practice_dir(base)
    return base, prac / "tutor"


def looks_like_graded_quiz(text: str) -> bool:
    t = text or ""
    options = len(re.findall(r"(?m)^\s*(?:[A-Da-d][).]|\([A-Da-d]\))\s+\S", t))
    return bool(GRADED.search(t)) or options >= 3


def qhash(lecture_id: str, question: str) -> str:
    norm = re.sub(r"\W+", " ", (question or "").lower()).strip()
    return hashlib.sha1(f"{lecture_id}|{norm}".encode()).hexdigest()[:12]


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _folder(base: Path, lid: str):
    return si.find(base, lid) if lid else None


def _key_terms(key: str) -> list[str]:
    return si.key_terms(key or "", limit=12)


def leaks(hint: str, key: str) -> bool:
    terms = _key_terms(key)
    if len(terms) < 3 or not hint:
        return False
    h = re.sub(r"[-/_]+", " ", hint.lower())
    hit = sum(1 for t in terms if t.lower() in h)
    return hit / len(terms) >= LEAK_SHARE


def _timecode(cites: list[dict]) -> str:
    for c in cites:
        m = re.search(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", c.get("snippet") or "")
        if m:
            return f"the lecture around [{m[1]}] ({c.get('source') or 'notes'})"
    if cites:
        return f"the part of {cites[0].get('source') or 'the notes'} that covers this"
    return "the lecture section this question comes from"


def _from_item(base: Path, prac: Path, ref: str):
    """mastery:<check_id>:<item_id> -> (lecture_id, question, key, explanation)."""
    kind, sid, iid = (ref.split(":", 2) + ["", ""])[:3]
    sess = {"mastery": prac / "mastery_check" / "sessions"}.get(kind)
    if not sess or not re.fullmatch(r"[A-Za-z0-9_.-]+", sid or ""):
        return None
    data = _load(sess / f"{sid}.json", {})
    it = next((x for x in data.get("items") or [] if x.get("item_id") == iid), None)
    if not it:
        return None
    why = ((data.get("mastery") or {}).get("why") or {}).get(iid, "")
    return it.get("lecture_id") or "", it.get("question") or "", it.get("expected_answer") or "", why


def _their_reasoning(prac: Path, ref: str) -> str:
    """What the learner wrote for this item when they answered it in the mastery check."""
    kind, sid, iid = (ref.split(":", 2) + ["", ""])[:3]
    log = prac / "mastery_check" / "checks.jsonl"
    for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines() if log.is_file() else []):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("check_id") == sid:
            return next((r.get("why") or "" for r in rec.get("reasons") or [] if r.get("item_id") == iid), "")
    return ""


SYS_KEY = ("Write a model answer to the learner's question in at most 80 words, using only the lecture "
           "excerpts. Plain sentences, no preamble, no markdown, no slide layout descriptions.")


def _model_answer(question: str, raw: str, cites: list[dict]) -> str:
    """One model call at start: a clean reference answer (kept hidden in the session)."""
    if os.environ.get("STUDY_TUTOR_LLM", "1") != "1":
        return ""
    excerpts = "\n".join(f"- {c.get('snippet', '')[:300]}" for c in cites) + f"\n- {raw[:900]}"
    try:
        import study
        text = study.llm(SYS_KEY, f"Question: {question}\nLecture excerpts:\n{excerpts}\n\nModel answer:",
                         max_tokens=240, temperature=0.2).strip()
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", re.sub(r"[*#`]+", "", text)).strip()
    return text if 20 <= len(text) <= 900 else ""


def start(question: str = "", lecture_id: str | None = None, item_ref: str | None = None,
          lectures_dir=None, practice_dir=None) -> dict:
    base, tdir = _dirs(lectures_dir, practice_dir)
    prac = tdir.parent
    explanation, cites, source, reasoning = "", [], "open", ""
    if item_ref:
        got = _from_item(base, prac, item_ref)
        if not got:
            return {"ok": False, "error": f"unknown item: {item_ref}"}
        lecture_id, question, key, explanation = got
        source = item_ref.split(":", 1)[0]
        reasoning = _their_reasoning(prac, item_ref)
        f = _folder(base, lecture_id)
        if f:
            cites = si.evidence(f[0], f"{question} {key}")
    else:
        question = (question or "").strip()
        if not question:
            return {"ok": False, "error": "ask a question"}
        if looks_like_graded_quiz(question):
            return {"ok": False, "error": "This looks like a graded quiz question. The tutor only works on your own "
                                           "lecture material."}
        r = si.open_answer(base, question, lecture_id or None)
        if not r:
            return {"ok": False, "error": "nothing in your lectures covers this yet"}
        key, cites, lecture_id = r["key"], r["cites"], r["lecture_id"]
        explanation = ""
        model_key = _model_answer(question, key, cites)
        if model_key:   # a readable answer written once from the same lecture lines; the raw lines stay as evidence
            key = si.scrub([model_key], base)[0].strip()
    if not key.strip():
        return {"ok": False, "error": "this item has no answer to guide toward"}
    qh = qhash(lecture_id, question)
    ladder = _load(tdir / "ladder.json", {})
    prior = ladder.get(qh) or {}
    sid = f"tutor-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}-{qh[:6]}"
    sess = {"id": sid, "question": question, "lecture_id": lecture_id, "source": source, "qhash": qh,
            "key": key, "explanation": explanation, "cites": cites, "attempts": [],
            "rung": int(prior.get("rung") or 0), "prior_attempts": int(prior.get("attempts") or 0),
            "hints": [], "revealed": None, "exit": None, "started": _now(), "reasoning": reasoning}
    try:
        import learner
        sess["brief"] = learner.brief(lecture_id, lectures_dir=base, practice_dir=prac)
    except Exception:
        sess["brief"] = ""
    _save(tdir / "sessions" / f"{sid}.json", sess)
    return view(sess, note=("You worked on this before: the hints carry on where you left off."
                            if sess["rung"] or sess["prior_attempts"] else None))


def view(s: dict, note: str | None = None, **extra) -> dict:
    """What the app may see: never the key until it is revealed."""
    tries = len(s["attempts"]) + s.get("prior_attempts", 0)
    solved = any(a.get("correct") for a in s["attempts"])
    out = {"ok": True, "id": s["id"], "question": s["question"], "lecture_id": s["lecture_id"],
           "source": s["source"], "rung": s["rung"], "max_rung": MAX_RUNG,
           "next_hint": RUNG_NAMES.get(s["rung"] + 1), "attempts": len(s["attempts"]), "solved": solved,
           "can_reveal": solved or tries >= REVEAL_AFTER, "revealed": s["revealed"], "hints": s["hints"],
           "last": s["attempts"][-1] if s["attempts"] else None, "note": note, "brief": s.get("brief") or "",
           "exit": ({k: v for k, v in s["exit"].items() if k != "correct_index"} if s.get("exit") else None)}
    if s["revealed"]:
        out["answer"] = s["key"]
        out["explanation"] = s.get("explanation") or ""
        out["cites"] = s.get("cites") or []
    out.update(extra)
    return out


def _session(tdir: Path, sid: str):
    if not re.fullmatch(r"tutor-[A-Za-z0-9-]+", sid or ""):
        return None
    s = _load(tdir / "sessions" / f"{sid}.json", None)
    return s if isinstance(s, dict) else None


def _ladder_update(tdir: Path, s: dict) -> None:
    ladder = _load(tdir / "ladder.json", {})
    ladder[s["qhash"]] = {"rung": s["rung"], "attempts": len(s["attempts"]) + s.get("prior_attempts", 0),
                          "lecture_id": s["lecture_id"], "updated": _now()}
    _save(tdir / "ladder.json", ladder)


def attempt(sid: str, text: str, lectures_dir=None, practice_dir=None) -> dict:
    base, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    if not s:
        return {"ok": False, "error": f"unknown session: {sid}"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "write your answer first, even a rough one"}
    ok, pct = si.grade(text, s["key"])   # key-term coverage, in code
    g = {"correct": ok, "pct": pct,
         "verdict": "Correct" if ok else ("Partially Correct" if pct >= 25 else "Needs Review")}
    correct = bool(g.get("correct"))
    # the key's missing terms are not returned: they would give the answer away
    row = {"t": _now(), "text": text, "pct": g.get("pct", 0), "verdict": g.get("verdict") or "", "correct": correct}
    s["attempts"].append(row)
    if correct and not s["revealed"]:
        s["revealed"] = "earned"
    _save(tdir / "sessions" / f"{sid}.json", s)
    _ladder_update(tdir, s)
    feedback = ("Yes, that's it." if correct else
                "Partly there. Try a hint, or try again in your own words." if row["verdict"] == "Partially Correct" else
                "Not yet. A hint will narrow it down.")
    return view(s, feedback=feedback)


def _write_hint(s: dict, rung: int, brief: str = "") -> tuple[str, str]:
    """(hint, how): model when allowed and it does not leak; else a template."""
    excerpts = "\n".join(f"- {c.get('snippet', '')[:300]}" for c in s.get("cites") or [])
    last = s["attempts"][-1]["text"] if s["attempts"] else (s.get("reasoning") or "(no attempt yet)")
    template = {1: f"Before the full answer: what problem is “{s['question'].rstrip('?')}” about, "
                   "and which idea from the lecture would you use first?",
                2: f"Look at {_timecode(s.get('cites') or [])}.",
                3: "Write the first step only: name the key idea, then say in one sentence why it applies here."}[rung]
    if rung == 2:
        return template, "pointer"
    if os.environ.get("STUDY_TUTOR_LLM", "1") != "1" or not excerpts:
        return template, "template"
    try:
        import study
        text = study.llm(SYS_HINT.format(kind=RUNG_NAMES[rung]),
                         f"Question: {s['question']}\nLearner's last attempt: {last}\n"
                         + (f"About this learner: {brief}\n" if brief else "")
                         + f"Lecture excerpts:\n{excerpts}\n\nWrite the {RUNG_NAMES[rung]}.",
                         max_tokens=160, temperature=0.4).strip()
    except Exception:
        return template, "template"
    text = re.sub(r"\s+", " ", text)[:400]
    if not text or leaks(text, s["key"]):
        return template, "template (the model's hint gave the answer away)"
    return text, "model"


def hint(sid: str, lectures_dir=None, practice_dir=None) -> dict:
    base, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    if not s:
        return {"ok": False, "error": f"unknown session: {sid}"}
    if s["rung"] >= MAX_RUNG:
        return view(s, feedback="That was the last hint. Try once more, or show the answer.")
    s["rung"] += 1
    brief = s.get("brief") or ""
    text, how = _write_hint(s, s["rung"], "" if brief.startswith("No study record") else brief)
    text = si.scrub([text], base)[0]
    s["hints"].append({"rung": s["rung"], "kind": RUNG_NAMES[s["rung"]], "text": text, "how": how, "t": _now()})
    _save(tdir / "sessions" / f"{sid}.json", s)
    _ladder_update(tdir, s)
    return view(s)


def reveal(sid: str, just_tell_me: bool = False, lectures_dir=None, practice_dir=None) -> dict:
    _, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    if not s:
        return {"ok": False, "error": f"unknown session: {sid}"}
    v = view(s)
    if not s["revealed"]:
        if not v["can_reveal"] and not just_tell_me:
            return dict(v, ok=False, error=f"Try it first: the answer opens after {REVEAL_AFTER} attempts "
                                            "(or choose “Just tell me”).")
        s["revealed"] = "earned" if v["can_reveal"] else "asked"
        _save(tdir / "sessions" / f"{sid}.json", s)
    return view(s)


def exit_check(sid: str, seed: int | None = None, lectures_dir=None, practice_dir=None) -> dict:
    """One closed-book question from the same lecture (never the one just worked on)."""
    import mastery_check
    base, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    if not s:
        return {"ok": False, "error": f"unknown session: {sid}"}
    if not s["revealed"]:
        return {"ok": False, "error": "finish the question first (solve it or show the answer)"}
    if s.get("exit"):
        return view(s)
    f = _folder(base, s["lecture_id"])
    lecs = mastery_check.lectures(base)
    pools = {lid: mastery_check.pool(folder, lid, t) for folder, lid, t in lecs}
    every = [b for _items, backs_ in pools.values() for b in backs_][:400]
    items, backs = pools.get(f[1], ([], [])) if f else ([], [])
    rng = random.Random(seed if seed is not None else time.time_ns())
    rng.shuffle(items)
    # same lecture first; a lecture with one question borrows one from another lecture (still closed book)
    others = [x for lid, (its, _b) in pools.items() if not f or lid != f[1] for x in its]
    rng.shuffle(others)
    for src in items + others:
        if qhash(s["lecture_id"], src["question"]) == s["qhash"]:
            continue
        it = mastery_check._as_item(src, rng, backs, every)
        if it:
            g = si.scrub([it["question"]] + it["choices"], base)
            s["exit"] = {"question": g[0], "choices": g[1:], "correct_index": it["correct_index"],
                         "answer": None, "correct": None}
            _save(tdir / "sessions" / f"{sid}.json", s)
            return view(s)
    return view(s, feedback="No closed-book question available for this lecture.")


def answer_exit(sid: str, choice: int, lectures_dir=None, practice_dir=None) -> dict:
    _, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    if not s or not s.get("exit"):
        return {"ok": False, "error": "no closed-book question in this session"}
    e = s["exit"]
    if e.get("answer") is None:
        e["answer"] = int(choice)
        e["correct"] = int(choice) == e["correct_index"]
        _save(tdir / "sessions" / f"{sid}.json", s)
        started = dt.datetime.fromisoformat(s["started"])
        row = {"id": sid, "qhash": s["qhash"], "lecture_id": s["lecture_id"], "source": s["source"],
               "attempts": len(s["attempts"]), "solved": any(a["correct"] for a in s["attempts"]),
               "max_rung": s["rung"], "reveal": s["revealed"], "exit_correct": e["correct"],
               "minutes": round((dt.datetime.now().astimezone() - started).total_seconds() / 60, 1), "t": _now()}
        with (tdir / "episodes.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    out = view(s, feedback=("Right, without help." if e["correct"] else
                            "Not this time. It is worth a look in Review or the mastery check."))
    out["exit"] = dict(out["exit"] or {}, correct_index=e["correct_index"], correct=e["correct"])
    return out


def show(sid: str, lectures_dir=None, practice_dir=None) -> dict:
    _, tdir = _dirs(lectures_dir, practice_dir)
    s = _session(tdir, sid)
    return view(s) if s else {"ok": False, "error": f"unknown session: {sid}"}


def stats(lectures_dir=None, practice_dir=None) -> dict:
    """Judge-free outcomes from the episode log: next item without help, reveal reasons, rungs."""
    _, tdir = _dirs(lectures_dir, practice_dir)
    rows = []
    p = tdir / "episodes.jsonl"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    n = len(rows)
    pct = lambda k: round(100 * sum(1 for r in rows if k(r)) / n) if n else None
    return {"ok": True, "episodes": n, "exit_correct_pct": pct(lambda r: r.get("exit_correct")),
            "asked_reveal_pct": pct(lambda r: r.get("reveal") == "asked"),
            "solved_pct": pct(lambda r: r.get("solved")),
            "mean_rung": round(sum(r.get("max_rung", 0) for r in rows) / n, 2) if n else None}
