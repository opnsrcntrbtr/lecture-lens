"""CT-3 course glossary: the terms a course uses, from slide text and the evidence bank, with
three uses that never change a transcript by themselves:

1. Allow-list for the name guard: a glossary word is never removed as a name (Zillow, Klarna,
   LlamaIndex). Anything the attendee roster matches, or that the learner marked "remove" in
   Names review, is kept out of the glossary, so it cannot shield a classmate's name.
2. Correction suggestions: transcript words that sound or spell close to a glossary term but
   are not it ("Zilo" for Zillow), listed with their line IDs for review. Never applied.
3. A prompt list for transcription later (CT-3 proper, after the engine bake-off M-1).

lectures/glossary.json: {"built_at", "terms": [{term, aliases, source: slide|evidence|manual,
count, status: active|review|removed}]}. Acronyms, CamelCase names and evidence-bank words
are active; a capitalised word seen only on slides waits for review (it could be a person's
name), and `add` confirms it. Manual and removed entries survive a rebuild.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

ACRONYM = re.compile(r"\b[A-Z][A-Z0-9&]{1,6}s?\b")
CAMEL = re.compile(r"\b[A-Z]?[a-z]+[A-Z][A-Za-z0-9]+\b|\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b")
CAPWORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
FUNC_TAIL = ("How", "Where", "What", "Why", "When", "Which", "Who", "The", "And", "For", "With", "Worth", "This",
             "That", "Then", "From", "Into", "Your", "Our")
STOP_ACRONYMS = {"I", "A", "OK", "AM", "PM", "US", "UK", "OF", "TO", "IN", "ON", "AND", "THE", "FOR", "IT", "IS", "OR", "AN",
                 "AT", "BY", "BE", "WE", "MY", "NO", "SO", "UP", "IF", "AS", "DO", "GO", "VS", "ETC", "QA", "Q&A", "VIEW",
                 "THINK", "ABOUT", "WRITE", "OF", "PART", "STEP", "NOTE", "WHAT", "WHY", "HOW", "NEW", "ALL", "ONE", "TWO",
                 "YES", "NEVER", "DATA", "HARM", "MONEY"}


def _texts(lectures: Path) -> list[tuple[str, str]]:
    """(source, text) from every live folder's slides and evidence bank."""
    out = []
    for live in sorted((lectures / "live").glob("2*")):
        f = live / "slides.jsonl"
        if f.exists():
            for l in f.read_text().splitlines():
                try:
                    s = json.loads(l)
                except ValueError:
                    continue
                if (s.get("kind") or "slide") == "slide":
                    out.append(("slide", f"{s.get('title', '')}\n{s.get('text', '')}"))
        e = live / "evidence.json"
        if e.exists():
            try:
                out += [("evidence", f"{x.get('title', '')}. {x.get('fact', '')}") for x in json.loads(e.read_text())]
            except (ValueError, AttributeError):
                pass
    bank = lectures / "evidence_bank.json"
    if bank.exists():
        try:
            out += [("evidence", f"{x.get('title', '')}. {x.get('fact', '')}") for x in json.loads(bank.read_text())]
        except (ValueError, AttributeError):
            pass
    return out


def candidates(texts: Iterable[tuple[str, str]], in_dictionary: Callable[[str], bool]) -> dict[str, dict]:
    """Acronyms and CamelCase product names (seen once is enough), and capitalised words that
    are not dictionary words (seen on two slides, or once in the evidence bank): {term: {count, source}}."""
    cnt: Counter = Counter()
    src: dict[str, str] = {}
    for source, text in texts:
        found = set()
        for m in ACRONYM.finditer(text):
            t = m.group(0)
            t = t[:-1] if t.endswith("s") and t[:-1].isupper() and len(t) > 2 else t
            if t not in STOP_ACRONYMS and not t.isdigit():
                found.add(t)
        found |= {m.group(0) for m in CAMEL.finditer(text)}
        found |= {m.group(0) for m in CAPWORD.finditer(text) if not in_dictionary(m.group(0).lower())}
        for t in found:
            cnt[t] += 1
            src.setdefault(t, source)
            if source == "evidence":      # seen in the curated evidence bank: no review needed
                src[t] = "evidence"
    out = {}
    for t, n in cnt.items():
        kind = "acronym" if t.isupper() else "camel" if CAMEL.fullmatch(t) else "word"
        if kind == "acronym" and len(t) >= 4 and in_dictionary(t.lower()):
            continue                     # an upper-case heading word (EXAMPLE, NOTICE), not an acronym
        if kind == "camel" and (len(t) > 16 or (t[0].islower() and len(t) >= 8) or re.search(r"[A-Z]{4,}[a-z]", t)
                                or re.search(r"[a-z](%s)$" % "|".join(FUNC_TAIL), t)):
            continue                     # words run together by OCR (mapWhere, MatrixHow, agendaUNDERSTAND...)
        if kind == "camel" and n < 2 and src[t] != "evidence":
            continue                     # a product name recurs; a one-off join is usually OCR
        if kind == "word" and n < 2 and src[t] != "evidence":   # the evidence bank is curated: once is enough
            continue
        out[t] = {"count": n, "source": src[t], "kind": kind}
    return out


def aliases(term: str) -> list[str]:
    """How speech-to-text tends to write a term: split CamelCase, spaced or lower-case acronyms."""
    a = {term.lower()}
    if term.isupper() and len(term) <= 5:
        a.add(" ".join(term.lower()))
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", term)
    if len(parts) > 1:
        a.add(" ".join(p.lower() for p in parts))
    a.discard(term)
    return sorted(a)


def build(lectures: Path, is_name: Callable[[str], bool], in_dictionary: Callable[[str], bool],
          old: dict | None = None, now: dt.datetime | None = None) -> dict:
    """A fresh glossary; manual and removed entries from `old` are kept. Words the roster or
    the learner's Names review mark as names are left out."""
    old = old or {}
    keep = {e["term"]: e for e in old.get("terms", []) if e.get("source") == "manual" or e.get("status") == "removed"}
    terms = []
    for t, c in sorted(candidates(_texts(lectures), in_dictionary).items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        if t in keep or any(is_name(w) for w in re.findall(r"[A-Za-z]+", t) if not w.isupper()):
            continue
        # a capitalised word seen only on slides could be a person's name: it waits for review
        status = "review" if c["kind"] == "word" and c["source"] == "slide" else "active"
        terms.append({"term": t, "aliases": aliases(t), "source": c["source"], "kind": c["kind"], "count": c["count"],
                      "status": status})
    terms += list(keep.values())
    return {"built_at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "terms": terms}


def load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"terms": []}


def active(g: dict) -> list[dict]:
    return [e for e in g.get("terms", []) if e.get("status", "active") == "active"]


def allow_words(g: dict) -> set[str]:
    """Lower-case words the name guard must never remove."""
    return {w.lower() for e in active(g) for w in re.findall(r"[A-Za-z]{3,}", e["term"]) if not w.isupper()}


def add(g: dict, term: str) -> dict:
    g["terms"] = [e for e in g.get("terms", []) if e["term"] != term] + [
        {"term": term, "aliases": aliases(term), "source": "manual", "kind": "manual", "count": 0, "status": "active"}]
    return g


def remove(g: dict, term: str) -> dict:
    hit = [e for e in g.get("terms", []) if e["term"] == term]
    g["terms"] = [e for e in g.get("terms", []) if e["term"] != term] + \
        [{**(hit[0] if hit else {"term": term, "aliases": [], "source": "manual", "kind": "manual", "count": 0}), "status": "removed"}]
    return g


def suggestions(lines: list[dict], g: dict, similarity: Callable[[str, str], float], min_sim: float = 0.86,
                limit: int = 200, in_dictionary: Callable[[str], bool] | None = None) -> list[dict]:
    """Transcript words close to, but not equal to, a glossary word: [{heard, term, score,
    line_ids, count}], most frequent first. Acronyms and words under five letters are skipped
    (too many chance matches). For review only; nothing is changed."""
    targets = {}
    for e in active(g):
        for w in re.findall(r"[A-Za-z]{5,}", e["term"]):
            if not w.isupper():
                targets[w.lower()] = e["term"]
    where: dict[str, list] = {}          # each distinct word once, with the lines it is on
    shown: dict[str, str] = {}
    for l in lines:
        for w in set(re.findall(r"[A-Za-z]{4,}", l.get("text", ""))):
            lw = w.lower()
            if lw not in targets and not (in_dictionary and in_dictionary(lw)):   # real words are not mishearings
                where.setdefault(lw, []).append(l.get("line_id"))
                shown.setdefault(lw, w)
    by: dict[tuple[str, str], dict] = {}
    for lw, ids in where.items():
        near = [t for t in targets if abs(len(t) - len(lw)) <= 3 and t[0] == lw[0]
                and not (t.startswith(lw) or lw.startswith(t))]          # eval/evals is the same word
        best = max(((similarity(lw, t), t) for t in near), default=(0.0, ""))
        if best[0] >= min_sim:
            by[(lw, targets[best[1]])] = {"heard": shown[lw], "term": targets[best[1]], "score": round(best[0], 2),
                                          "line_ids": ids[:5], "count": len(ids)}
    return sorted(by.values(), key=lambda r: (-r["count"], -r["score"]))[:limit]


def review(g: dict) -> list[dict]:
    return [e for e in g.get("terms", []) if e.get("status") == "review"]
