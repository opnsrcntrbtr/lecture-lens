"""Shared helpers for the mastery check and the guided tutor: built lectures, their own questions,
the name filter, contact redaction, key-term grading and evidence lines. No model calls.

  lectures(base)            built lectures [(folder, id, title)] in recording order
  cards(folder)             flashcards [{front, back}] from cards.tsv (timecodes stripped)
  quiz(folder)              practice quiz items [{q, options, answer, why}] from practice_quiz.json
  scrub(texts, base)        name filter (name_guard) over a list of strings
  redact_contacts(text)     emails and phone numbers never reach a question
  key_terms(text)           salient words of an answer
  grade(answer, key)        (correct, pct): share of the key's terms the answer covers (>= 0.7)
  evidence(folder, text)    up to 3 lecture lines that best match, with their source and timecode
  open_answer(base, q, id)  a reference answer for an open question from the lectures' own cards
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMECODE = re.compile(r"\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}\]|\s*\[slide \d+\]|\s*\[\d{1,3}(?:,\s*\d{1,3})*\]", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w:])\+?\d(?:[\s-]?\d){9,13}(?![\w:])")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9+-]{2,}")
STOP = set("""the and for are but not you all any can had her was one our out day get has him his how man new
now old see two way who boy did its let put say she too use that with have this will your from they know want
been good much some time very when come here just like long make many more only over such take than them well
were what into also each which their there about would these other could after first never where those being
because between while does doing done through during before under again further then once same both most
should own why hes shes lets thats whats theyre youre dont cant wont isnt arent wasnt lecture slide notes""".split())


def lectures_dir(environ=None) -> Path:
    env = os.environ if environ is None else environ
    return Path(os.path.expanduser(env.get("STUDY_LECTURE_DIR", str(HERE / "lectures"))))


def practice_dir(base: Path, environ=None) -> Path:
    env = os.environ if environ is None else environ
    if env.get("STUDY_PRACTICE_DIR"):
        return Path(os.path.expanduser(env["STUDY_PRACTICE_DIR"]))
    sib = Path(base).parent / "practice"
    return sib if sib.exists() else HERE / "practice"


def _manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lectures(base: Path) -> list[tuple[Path, str, str]]:
    base = Path(base)
    if not base.is_dir():
        return []
    rows = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "manifest.json").exists():
            m = _manifest(d)
            rows.append((d, str(m.get("id") or d.name), str(m.get("title") or d.name), str(m.get("start") or "")))
    rows.sort(key=lambda r: (r[3], r[0].name))
    return [(d, i, t) for d, i, t, _ in rows]


def find(base: Path, lecture_id: str):
    for row in lectures(base):
        if lecture_id in (row[1], row[0].name):
            return row
    return None


def redact_contacts(text: str) -> str:
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text or ""))


def clean(text) -> str:
    return re.sub(r"\s+", " ", redact_contacts(TIMECODE.sub("", str(text or "")).replace("**", ""))).strip()


def cards(folder: Path) -> list[dict]:
    out = []
    f = Path(folder) / "cards.tsv"
    if f.is_file():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            bits = line.split("\t")
            if len(bits) >= 2 and clean(bits[0]) and clean(bits[1]):
                out.append({"front": clean(bits[0]), "back": clean(bits[1])})
    return out


def quiz(folder: Path) -> list[dict]:
    """Practice quiz items with authored options (practice_quiz.json, or quiz.jsonl from quiz.py)."""
    out = []
    pq = Path(folder) / "practice_quiz.json"
    rows = []
    if pq.is_file():
        try:
            raw = json.loads(pq.read_text(encoding="utf-8", errors="replace"))
            rows = (raw.get("questions") if isinstance(raw, dict) else raw) or []
        except ValueError:
            rows = []
    for q in rows:
        if not isinstance(q, dict):
            continue
        stem = clean(q.get("q") or q.get("stem") or q.get("question"))
        opts = [clean(o) for o in q.get("options") or q.get("choices") or [] if clean(o)]
        ans = q.get("answer") if "answer" in q else q.get("key")
        if isinstance(ans, str) and len(ans) == 1 and ans.upper() in "ABCDEF":
            ans = "ABCDEF".index(ans.upper())
        if stem and len(opts) >= 2 and isinstance(ans, int) and 0 <= ans < len(opts):
            out.append({"q": stem, "options": opts, "answer": ans, "why": clean(q.get("why") or q.get("explanation"))})
    return out


def scrub(texts: list[str], base: Path | None = None) -> list[str]:
    """The name filter over generated text; course terms in the allow lists are kept."""
    if not texts:
        return []
    try:
        import name_guard
        allow = set()
        if base:
            allow = name_guard.load_allow(Path(base) / "name_allow.txt") | name_guard.load_allow(Path(base) / "glossary.txt")
        out, _ = name_guard.guard(list(texts), panel_authors=set(), allow=allow)
        return out
    except Exception:
        return list(texts)


def key_terms(text: str, limit: int = 12) -> list[str]:
    seen = []
    for w in WORD.findall(clean(text).lower()):
        if w not in STOP and w not in seen:
            seen.append(w)
    return seen[:limit]


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def grade(answer: str, key: str) -> tuple[bool, int]:
    terms = key_terms(key)
    if not terms or not (answer or "").strip():
        return False, 0
    got = {_stem(w) for w in WORD.findall(answer.lower())}
    hit = sum(1 for t in terms if _stem(t) in got)
    share = hit / len(terms)
    return (share >= 0.7 or answer.strip().lower() == clean(key).lower()), int(round(share * 100))


def evidence(folder: Path, text: str, k: int = 3) -> list[dict]:
    """Lecture lines from notes.md and slides.md that share the most terms with `text`."""
    want = {_stem(t) for t in key_terms(text, 20)}
    scored = []
    for name in ("notes.md", "slides.md"):
        f = Path(folder) / name
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if len(s) < 20 or s.startswith("#"):
                continue
            words = {_stem(w) for w in WORD.findall(s.lower())}
            score = len(want & words)
            if score >= 2:
                scored.append((score, name, s))
    scored.sort(key=lambda r: -r[0])
    m = _manifest(Path(folder))
    return [{"lecture_id": str(m.get("id") or Path(folder).name), "source": n, "snippet": redact_contacts(s[:300])}
            for _, n, s in scored[:k]]


def open_answer(base: Path, question: str, lecture_id: str | None = None) -> dict | None:
    """The best-matching flashcard across the lectures (or one lecture) as the reference answer."""
    want = {_stem(t) for t in key_terms(question, 20)}
    best = None
    for folder, lid, title in lectures(base):
        if lecture_id and lecture_id not in (lid, folder.name):
            continue
        for c in cards(folder):
            words = {_stem(w) for w in WORD.findall((c["front"] + " " + c["back"]).lower())}
            score = len(want & words)
            if score and (best is None or score > best[0]):
                best = (score, folder, lid, c)
    if not best or best[0] < 2:
        return None
    _, folder, lid, c = best
    return {"lecture_id": lid, "folder": folder, "key": c["back"], "card_question": c["front"],
            "cites": evidence(folder, c["front"] + " " + c["back"])}
