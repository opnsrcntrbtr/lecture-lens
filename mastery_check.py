"""Mastery check: an untimed, cumulative review of every lecture in the course so far.

* Cumulative and interleaved: questions come from every built lecture, and no two in a row come
  from the same lecture while another one still has questions left.
* Questions are the lecture's own: its practice quiz (authored options and the reason) and its
  flashcards, asked as multiple choice with other cards' answers as the wrong options, so every
  option reads alike and grading is exact.
* Mastery bar: you pass at 90% (STUDY_MASTERY_BAR). Retry as often as you like. A retry starts with
  what you missed (options reshuffled), plus fresh questions from every lecture.
* Explanations: every answer, right or wrong, comes back with the expected answer, the quiz's reason
  when it has one, and the lecture evidence (graded in code, no model call).
* Calibration: how sure you were compared with how you did.
* Stays cumulative: after a pass, new lectures make it due again.
* Successive relearning: after a try, the misses can be relearned on the spot until each is answered
  right once (retrieval to criterion), and the next try falls due RELEARN_DAYS later (Rawson and
  Dunlosky, Curr Dir Psychol Sci 2022: retrieval to criterion, then spaced relearning sessions).

Evidence: cumulative module review with a 90% pass bar and unlimited retries had the largest effect
in a self-selected pilot (d = 0.66); explanatory feedback beats correct-answer feedback, and practice
with feedback improves calibration (Asher and Carvalho, Educ Psychol Rev 2026).

Attempts live in <practice>/mastery_check/: the sessions and checks.jsonl.
mastery.py is the separate read-only calibration view (LS-7).
Nothing here touches graded quizzes: questions are built from your own lecture notes and cards.
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

DEFAULT_BAR = 90
RELEARN_DAYS = 2


def bar(environ=None) -> int:
    env = os.environ if environ is None else environ
    try:
        return max(50, min(100, int(env.get("STUDY_MASTERY_BAR") or DEFAULT_BAR)))
    except ValueError:
        return DEFAULT_BAR


def _dirs(lectures_dir=None, practice_dir=None) -> tuple[Path, Path]:
    base = Path(lectures_dir).expanduser() if lectures_dir else si.lectures_dir()
    prac = Path(practice_dir).expanduser() if practice_dir else si.practice_dir(base)
    return base, prac / "mastery_check"


def lectures(base: Path) -> list[tuple[Path, str, str]]:
    """Built lectures in the order they were recorded."""
    return si.lectures(base)


def attempts(mdir: Path) -> list[dict]:
    p = mdir / "checks.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _pct(n: int, d: int) -> int:
    return int(round(100 * n / d)) if d else 0


def status(lectures_dir=None, practice_dir=None, environ=None) -> dict:
    """Where you stand: not taken, retry, passed, or due again because of new lectures."""
    base, mdir = _dirs(lectures_dir, practice_dir)
    lecs = lectures(base)
    b = bar(environ)
    hist = attempts(mdir)
    passes = [h for h in hist if h.get("passed")]
    last = hist[-1] if hist else None
    last_pass = passes[-1] if passes else None
    covered = set(last_pass.get("scope") or []) if last_pass else set()
    new = [t for _, lid, t in lecs if lid not in covered] if last_pass else []
    since = hist[hist.index(last_pass) + 1:] if last_pass else hist
    with_q = sum(1 for f, lid, t in lecs if pool(f, lid, t)[0])
    due_at = None
    if not lecs:
        state, msg = "no_lectures", "Build a lecture first: the mastery check asks about every lecture you have built."
    elif not hist:
        state, msg = "not_taken", f"Not taken yet. Questions from {with_q} lecture(s); pass at {b}%."
    elif last is not last_pass:
        missed = len(last.get("missed") or [])
        state = "retry"
        seen = [_when(last.get("at"))] + [_when(r.get("at")) for r in relearns(mdir) if r.get("check_id") == last.get("check_id")]
        due = max(t for t in seen if t) + dt.timedelta(days=RELEARN_DAYS) if any(seen) else None
        due_at = due.isoformat(timespec="minutes") if due else None
        now = dt.datetime.now().astimezone()
        when = "now" if not due or due <= now else f"from {due:%a %d %b}"
        msg = (f"Last try {last.get('score_pct', 0)}% (pass at {b}%). Next try due {when}: it starts with the "
               f"{missed} you missed, options reshuffled.")
    elif new:
        state = "due_new"
        msg = (f"Passed at {last_pass.get('score_pct', 0)}% on {str(last_pass.get('at', ''))[:10]}. "
               f"{len(new)} new lecture(s) since: take it again to keep it cumulative.")
    else:
        state = "passed"
        msg = (f"Passed at {last_pass.get('score_pct', 0)}% on {str(last_pass.get('at', ''))[:10]}, "
               f"covering all {len(covered)} lecture(s).")
    return {"ok": True, "state": state, "message": msg, "bar": b, "lecture_count": len(lecs),
            "attempts": len(hist), "tries_since_pass": len(since),
            "best_since_pass": max([h.get("score_pct", 0) for h in since], default=None),
            "last_score": last.get("score_pct") if last else None,
            "last_at": last.get("at") if last else None,
            "passed_at": last_pass.get("at") if last_pass else None,
            "new_lectures": new[:10], "due_at": due_at, "lectures_with_questions": with_q}


def _when(s):
    try:
        t = dt.datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def relearns(mdir: Path) -> list[dict]:
    p = mdir / "relearn.jsonl"
    out = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return [r for r in out if isinstance(r, dict)]


def relearned(check_id: str, items: int, rounds: int, lectures_dir=None, practice_dir=None) -> dict:
    """Record that a try's misses were relearned to criterion (each answered right once)."""
    _, mdir = _dirs(lectures_dir, practice_dir)
    cid = str(check_id or "").strip()
    if not any(h.get("check_id") == cid for h in attempts(mdir)):
        return {"ok": False, "error": f"unknown or ungraded check id: {cid}"}
    rec = {"check_id": cid, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "items": max(0, int(items)), "rounds": max(1, int(rounds))}
    mdir.mkdir(parents=True, exist_ok=True)
    with (mdir / "relearn.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    nxt = _when(rec["at"]) + dt.timedelta(days=RELEARN_DAYS)
    return {"ok": True, **rec, "next_try": nxt.isoformat(timespec="minutes"),
            "message": f"Relearned. The next try is due from {nxt:%a %d %b}: spacing it out is what makes it stick."}


TIMECODE = re.compile(r"\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}\]|\s*\[slide \d+\]", re.I)


def _clean(t: str) -> str:
    return si.clean(t)


def pool(folder: Path, lid: str, title: str) -> tuple[list[dict], list[str]]:
    """The lecture's own questions: practice quiz items (authored options) and flashcards.
    Returns (items, card answers for wrong options)."""
    items, backs = [], []
    for q in si.quiz(folder):
        items.append({"lecture_id": lid, "lecture_title": title, "kind": "mcq", "question": q["q"],
                      "answer": q["options"][q["answer"]], "options": q["options"], "why": q["why"], "source": "quiz"})
    for c in si.cards(folder):
        front, back = c["front"], c["back"]
        if len(front) >= 8 and len(back) >= 4:
            backs.append(back)
            items.append({"lecture_id": lid, "lecture_title": title, "kind": "mcq", "question": front,
                          "answer": back, "options": None, "why": "", "source": "card"})
    return items, backs


def _as_item(src: dict, rng: random.Random, backs: list[str], spare: list[str]) -> dict | None:
    """Mock-exam item; a card gets three other card answers (this lecture first) as wrong options."""
    ans = src["answer"]
    if src.get("options"):
        opts = list(src["options"])
    else:
        others = [b for b in dict.fromkeys(backs) if b.lower() != ans.lower()]
        rng.shuffle(others)
        if len(others) < 3:
            extra = [b for b in dict.fromkeys(spare) if b.lower() != ans.lower() and b not in others]
            rng.shuffle(extra)
            others += extra
        if len(others) < 3:
            return None
        opts = [ans] + others[:3]
    rng.shuffle(opts)
    return {"lecture_id": src["lecture_id"], "lecture_title": src["lecture_title"], "kind": "mcq",
            "question": src["question"], "choices": opts, "correct_index": opts.index(ans),
            "expected_answer": ans, "concept": "", "why": src.get("why") or ""}


def interleave(queues: dict[str, list[dict]]) -> list[dict]:
    """Greedy: always take from the lecture with most questions left that is not the previous one."""
    qs = {k: list(v) for k, v in queues.items() if v}
    order = list(qs)
    out, prev = [], None
    while qs:
        cands = [k for k in order if k in qs and k != prev] or [k for k in order if k in qs]
        k = max(cands, key=lambda c: (len(qs[c]), -order.index(c)))
        out.append(qs[k].pop(0))
        if not qs[k]:
            del qs[k]
        prev = k
    return out


def start(lectures_dir=None, practice_dir=None, per_lecture: int = 2, max_items: int = 30,
          seed: int | None = None, environ=None) -> dict:
    """Build the next attempt: last try's misses first, then fresh questions from every lecture."""
    base, mdir = _dirs(lectures_dir, practice_dir)
    lecs = lectures(base)
    if not lecs:
        return {"ok": False, "error": "no built lectures yet"}
    hist = attempts(mdir)
    last = hist[-1] if hist else None
    retry = bool(last and not last.get("passed"))
    attempt_no = 1 + len(hist)
    rng = random.Random(seed if seed is not None else time.time_ns())
    per = max(1, min(per_lecture, max_items // max(1, len(lecs))))
    asked_before = set(last.get("questions") or []) if last else set()
    missed_q = {(m.get("lecture_id"), m.get("question")) for m in (last.get("missed") or [])} if retry else set()
    # least recently asked first: how many earlier tries asked each question
    seen_count: dict[str, int] = {}
    for h in hist:
        for q in h.get("questions") or []:
            seen_count[q] = seen_count.get(q, 0) + 1

    pools = {lid: pool(f, lid, t) for f, lid, t in lecs}
    every_back = [b for _, backs in pools.values() for b in backs]
    queues: dict[str, list[dict]] = {}
    for _, lid, _ in lecs:
        srcs, backs = pools[lid]
        if not srcs:
            continue
        rng.shuffle(srcs)
        again = [x for x in srcs if (lid, x["question"]) in missed_q]
        fresh = sorted([x for x in srcs if (lid, x["question"]) not in missed_q],
                       key=lambda x: (x["question"] in asked_before, seen_count.get(x["question"], 0)))
        picked = []
        for x in again:
            it = _as_item(x, rng, backs, every_back)
            if it:
                picked.append(dict(it, retry=True))
        n_fresh = 0
        for x in fresh:
            if n_fresh >= per:
                break
            it = _as_item(x, rng, backs, every_back)
            if it:
                picked.append(dict(it, retry=False)); n_fresh += 1
        if picked:
            queues[lid] = picked
    ordered = interleave(queues)
    if len(ordered) > max_items:   # over the cap: keep every retry and one question per lecture
        keep, seen = [], set()
        for it in ordered:
            if it["retry"] or it["lecture_id"] not in seen:
                keep.append(it); seen.add(it["lecture_id"])
        room = max(0, max_items - len(keep))
        keep += [it for it in ordered if it not in keep][:room]
        ordered = interleave({k: [it for it in keep if it["lecture_id"] == k] for k in queues})
    if not ordered:
        return {"ok": False, "error": "no questions yet: the lectures need a practice quiz or at least four flashcards"}
    titles = {lid: t for _, lid, t in lecs}
    flags = [it["retry"] for it in ordered]
    whys_raw = [it.get("why") or "" for it in ordered]
    for n, it in enumerate(ordered):
        it["item_id"] = f"m{attempt_no}-{n + 1}"
        it.setdefault("lecture_title", titles.get(it["lecture_id"], ""))
    items = _scrub_items(ordered, base)
    whys = [w.strip() for w in si.scrub(whys_raw, base)]
    for it, r in zip(items, flags):
        it["retry"] = r
    now = dt.datetime.now(dt.timezone.utc)
    cid = f"mastery-{now.strftime('%Y%m%d%H%M%S')}-{hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:6]}"
    scope = sorted({it["lecture_id"] for it in items})
    sess_dir = mdir / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    tmp = sess_dir / f".{cid}.tmp"
    tmp.write_text(json.dumps({"exam_id": cid, "created_at": now.isoformat().replace("+00:00", "Z"), "minutes": 0,
                               "revisit_missed": retry, "lectures_dir": str(base), "items": items,
                               "mastery": {"attempt": attempt_no, "scope": scope, "bar": bar(environ),
                                           "why": {it["item_id"]: w for it, w in zip(items, whys) if w}}},
                              indent=1), encoding="utf-8")
    tmp.replace(sess_dir / f"{cid}.json")
    return {"ok": True, "check_id": cid, "attempt": attempt_no, "bar": bar(environ), "retry": retry,
            "retry_count": sum(flags), "lecture_count": len(scope), "item_count": len(items),
            "items": items, "scrubbed": True}


def _scrub_items(items: list[dict], base: Path) -> list[dict]:
    """The name filter over every text field of the items."""
    flat = []
    for it in items:
        flat += [it.get("lecture_title") or "", it.get("question") or "", it.get("expected_answer") or ""] + list(it["choices"])
    g = si.scrub(flat, base)
    out, pos = [], 0
    for it in items:
        n = len(it["choices"])
        out.append({"item_id": it["item_id"], "lecture_id": it["lecture_id"], "lecture_title": g[pos],
                    "kind": "mcq", "question": g[pos + 1], "choices": g[pos + 3: pos + 3 + n],
                    "correct_index": it["correct_index"], "expected_answer": g[pos + 2], "concept": ""})
        pos += 3 + n
    return out


def _grade(sess: dict, answers_json, elapsed_seconds: int, base: Path) -> dict:
    """Multiple choice graded by index; every answer explained with the expected answer and evidence."""
    try:
        answers = json.loads(answers_json) if isinstance(answers_json, str) else answers_json
    except ValueError as e:
        return {"ok": False, "error": f"invalid answers_json: {e}"}
    if not isinstance(answers, list):
        return {"ok": False, "error": "answers_json must be a JSON array"}
    by_id = {str(a.get("item_id")): a for a in answers if isinstance(a, dict)}
    folders = {lid: f for f, lid, _ in si.lectures(base)}
    graded, over = [], 0
    for it in sess.get("items") or []:
        a = by_id.get(it["item_id"]) or {}
        conf = a.get("confidence") if a.get("confidence") in ("sure", "unsure", "guess") else "unsure"
        ci = a.get("choice_index")
        ok = isinstance(ci, int) and ci == it.get("correct_index")
        picked = it["choices"][ci] if isinstance(ci, int) and 0 <= ci < len(it["choices"]) else ""
        cites = si.evidence(folders[it["lecture_id"]], it["question"] + " " + it["expected_answer"]) if it["lecture_id"] in folders else []
        expl = f"Expected answer: {it['expected_answer']}." + (f" Grounded in {cites[0]['source']}: {cites[0]['snippet']}" if cites else "")
        if conf == "sure" and not ok:
            over += 1
        graded.append({"item_id": it["item_id"], "lecture_id": it["lecture_id"], "question": it["question"],
                       "concept": "", "correct": ok, "user_answer": picked, "expected_answer": it["expected_answer"],
                       "confidence": conf, "explanation": expl, "citations": cites})
    n = len(graded)
    right = sum(1 for g in graded if g["correct"])
    return {"ok": True, "exam_id": sess.get("exam_id"), "score_pct": int(round(100 * right / max(1, n))),
            "correct_count": right, "missed_count": n - right, "item_count": n,
            "elapsed_seconds": max(0, int(elapsed_seconds or 0)), "avg_seconds_per_item": round((elapsed_seconds or 0) / max(1, n), 1),
            "overconfident_misses": over, "graded_items": graded, "scrubbed": True}


def calibration(graded: list[dict]) -> dict:
    """How sure you were against how you did."""
    groups = {}
    for g in graded:
        c = g.get("confidence") if g.get("confidence") in ("sure", "unsure", "guess") else "unsure"
        n, r = groups.get(c, (0, 0))
        groups[c] = (n + 1, r + (1 if g.get("correct") else 0))
    parts = [f"{c.capitalize()} {n} ({r} right)" for c in ("sure", "unsure", "guess") if c in groups
             for n, r in [groups[c]]]
    sn, sr = groups.get("sure", (0, 0))
    ln = groups.get("unsure", (0, 0))[0] + groups.get("guess", (0, 0))[0]
    lr = groups.get("unsure", (0, 0))[1] + groups.get("guess", (0, 0))[1]
    if sn >= 3 and sr / sn < 0.8:
        verdict = "You were sure more often than you were right. Slow down on the ones that feel easy."
    elif ln >= 3 and lr / ln >= 0.8:
        verdict = "You know more than you think: most answers you doubted were right."
    elif graded:
        verdict = "Your confidence matched your results."
    else:
        verdict = ""
    return {"line": " · ".join(parts), "verdict": verdict, "overconfident": sn - sr,
            "groups": {c: {"n": n, "right": r} for c, (n, r) in groups.items()}}


def submit(check_id: str, answers_json, elapsed_seconds: int = 0, lectures_dir=None, practice_dir=None,
           environ=None) -> dict:
    """Grade an attempt (with explanations), record it and say what comes next."""
    base, mdir = _dirs(lectures_dir, practice_dir)
    cid = str(check_id or "").strip()
    if not re.fullmatch(r"mastery-[A-Za-z0-9_.-]+", cid):
        return {"ok": False, "error": f"invalid check id: {cid}"}
    sess_path = mdir / "sessions" / f"{cid}.json"
    if not sess_path.is_file():
        return {"ok": False, "error": f"unknown check id: {cid}"}
    if any(h.get("check_id") == cid for h in attempts(mdir)):
        return {"ok": False, "error": "this attempt was already submitted; start a new one"}
    sess = json.loads(sess_path.read_text(encoding="utf-8"))
    meta = sess.get("mastery") or {}
    res = _grade(sess, answers_json, elapsed_seconds, base)
    if not res.get("ok"):
        return res
    b = int(meta.get("bar") or bar(environ))
    graded = res.get("graded_items") or []
    for g in graded:   # the quiz's own reason, when it has one
        w = (meta.get("why") or {}).get(g.get("item_id"))
        if w:
            g["explanation"] = f"{g.get('explanation', '').strip()} Why: {w}".strip()
    items = {it["item_id"]: it for it in sess.get("items") or []}
    by_lec: dict[str, list[int]] = {}
    for g in graded:
        n_r = by_lec.setdefault(g.get("lecture_id") or "", [0, 0])
        n_r[0] += 1
        n_r[1] += 1 if g.get("correct") else 0
    titles = {it["lecture_id"]: it.get("lecture_title") or it["lecture_id"] for it in items.values()}
    lec_rows = [{"lecture_id": k, "title": titles.get(k, k), "asked": n, "right": r, "pct": _pct(r, n)}
                for k, (n, r) in by_lec.items()]
    lec_rows.sort(key=lambda x: (x["pct"], x["title"]))
    passed = res.get("score_pct", 0) >= b
    cal = calibration(graded)
    missed = [{"lecture_id": g.get("lecture_id"), "concept": g.get("concept"), "question": g.get("question")}
              for g in graded if not g.get("correct")]
    rec = {"check_id": cid, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "attempt": meta.get("attempt"), "scope": meta.get("scope") or sorted(by_lec), "bar": b,
           "score_pct": res.get("score_pct", 0), "correct": res.get("correct_count", 0),
           "items": res.get("item_count", 0), "passed": passed, "missed": missed,
           "questions": [g.get("question") for g in graded], "calibration": cal["groups"],
           "by_lecture": [{"lecture_id": r["lecture_id"], "asked": r["asked"], "right": r["right"]} for r in lec_rows]}
    mdir.mkdir(parents=True, exist_ok=True)
    with (mdir / "checks.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    weakest = [r for r in lec_rows if r["pct"] < b][:3]
    if passed:
        nxt = "Passed. It comes back when you build new lectures, so it stays cumulative."
    else:
        need = max(0, -(-b * res.get("item_count", 0) // 100) - res.get("correct_count", 0))
        where = ", ".join(r["title"] for r in weakest) or "the missed questions"
        nxt = (f"{need} more right answer(s) would have passed. Read the explanations, then try again: "
               f"the next try starts with your misses. Weakest: {where}.")
    return dict(res, check_id=cid, attempt=meta.get("attempt"), bar=b, passed=passed, calibration=cal,
                by_lecture=lec_rows, next=nxt)


def history(lectures_dir=None, practice_dir=None, limit: int = 10) -> dict:
    _, mdir = _dirs(lectures_dir, practice_dir)
    rows = [{k: h.get(k) for k in ("check_id", "at", "attempt", "score_pct", "passed", "bar", "items")}
            for h in attempts(mdir)]
    return {"ok": True, "history": rows[-max(1, limit):][::-1]}
