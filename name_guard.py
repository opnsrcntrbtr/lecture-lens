"""Attendee names out of transcripts and slide text, before anything is written or sent
to a model. Names are removed when there is evidence they are names: an author in the
meeting app's Q&A panel, or a capitalised word outside the dictionary used where people
are addressed ("Thanks, Zorvan", "Zorvan, go ahead", a line that opens with "Zorvan B."). Staff
(LIVE_QA_STAFF) and words in an allow list are kept. Everything else that only looks like a
name is listed for a person to review, never removed silently."""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

REPLACEMENT = "an attendee"
CAP = r"[A-Z][a-z]{2,}"
# where people are addressed: after a cue word, before ", go ahead", or a line opening "Name X."
# cue words in any case; the name itself must be capitalised (14 Nov: a case-insensitive
# pattern took "so, dependencies" and "right, timestamp" for names)
CUE_BEFORE = re.compile(r"\b(?i:thanks|thank you|yes|okay|ok|so|right|perfect|hi|hello|sure|over to you|go ahead)"
                        rf",?\s+({CAP}(?:\s+{CAP})?)\b(?=[,.?!]|\s*$)")
CUE_AFTER = re.compile(rf"\b({CAP}(?:\s+{CAP})?),\s+(?i:go ahead|please|you're|you are|over to you|your question)")
NAME_TOKEN = re.compile(r"^(?:[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?|[A-Z]\.?)$")
# a line that opens with a name and an initial ("Zorvan B. I think"): one word alone is too often a product
LINE_OPEN = re.compile(rf"^(?:\[[\d:]+\]\s*)?({CAP}\s+[A-Z])\.\s")
# words the system dictionary lacks but that are not names
COMMON = {"okay", "ok", "labeling", "labelling", "everyone", "anyone", "someone", "hi", "hello", "yeah", "thanks"}
# chat lines read "<name> to Everyone" / "<name> to Me"
CHAT_TO = re.compile(r"\s+to\s+(everyone|me|you)\b.*$", re.I)
WORD = re.compile(rf"\b{CAP}\b")


@functools.lru_cache(maxsize=2)
def load_dictionary(path: str = "/usr/share/dict/words") -> frozenset[str]:
    p = Path(os.environ.get("STUDY_DICT", path))
    return frozenset(w.strip().lower() for w in p.read_text(errors="ignore").splitlines()) if p.exists() else frozenset()


def load_allow(path: Path | None) -> set[str]:
    """Words that look like names but are not (products, places, course terms); one per line."""
    if not path or not path.exists():
        return set()
    return {l.split("#")[0].strip().lower() for l in path.read_text().splitlines() if l.split("#")[0].strip()}


def staff_names(env: str | None = None) -> set[str]:
    raw = os.environ.get("LIVE_QA_STAFF", "") if env is None else env
    out = set()
    for n in raw.split(","):
        n = n.strip()
        if n:
            out.add(n.lower())
            out.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", n) if w.lower() not in {"prof", "professor", "dr"})
    return out


def _unknown(word: str, dictionary: set[str], keep: set[str]) -> bool:
    w = word.lower()
    return w not in keep and w not in COMMON and (not dictionary or w not in dictionary)


def looks_like_name(author: str) -> bool:
    """1-4 Title-case words or initials. Rejects what a mis-parsed panel can hand over as an
    author: question text, acronyms ("PRD"), digits, a sentence ending in ? or a full stop."""
    toks = author.split()
    if not 1 <= len(toks) <= 4 or author.endswith("?"):
        return False
    if author.endswith(".") and not re.fullmatch(r"[A-Z]\.", toks[-1]):
        return False
    return all(NAME_TOKEN.match(t) for t in toks)


def panel_names(authors: set[str], dictionary: set[str], keep: set[str]) -> set[str]:
    """Full author names, plus each part that is not an ordinary word."""
    out = set()
    for a in authors:
        a = CHAT_TO.sub("", a).strip()
        if not looks_like_name(a):
            continue
        parts = [p for p in a.split() if len(p) >= 3]
        if not parts or all(p.lower() in keep for p in parts):
            continue
        if len(a.split()) > 1:
            out.add(a)
        out.update(p for p in parts if _unknown(p, dictionary, keep))
    return out


def addressed_names(lines: list[str], dictionary: set[str], keep: set[str], where: dict | None = None) -> set[str]:
    """Names used to address people, from cue patterns, all parts outside the dictionary
    (single initials are dropped from the result but matched with the name). `where`, if
    given, collects the line numbers of each address."""
    found = set()
    for i, line in enumerate(lines):
        text = re.sub(r"^\[[\d:]+\]\s*", "", line)
        hits = [m.group(1) for m in CUE_BEFORE.finditer(text)] + [m.group(1) for m in CUE_AFTER.finditer(text)]
        m = LINE_OPEN.match(text)
        if m:
            hits.append(m.group(1))
        for h in hits:
            parts = [p.strip(".") for p in h.split()]
            first = parts[0]
            if _unknown(first, dictionary, keep):
                new = {first} | {p for p in parts[1:] if len(p) >= 3 and _unknown(p, dictionary, keep)}
                found |= new
                if where is not None:
                    for n in new:
                        where.setdefault(n, set()).add(i)
    return found


def redactor(names: set[str]):
    names = {n for n in names if len(n) >= 3}
    if not names:
        return lambda text: text
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # a trailing initial ("Zorvan B.") goes with the name
    pat = re.compile(rf"\b(?:{alt})\b(?:\s+[A-Z]\.(?=\s|$))?")
    return lambda text: pat.sub(REPLACEMENT, text)


def review_candidates(lines: list[str], dictionary: set[str], keep: set[str], min_count: int = 2) -> dict[str, int]:
    """Capitalised words outside the dictionary that remain: for a person to check."""
    counts: dict[str, int] = {}
    for line in lines:
        text = re.sub(r"^\[[\d:]+\]\s*", "", line)
        for i, m in enumerate(WORD.finditer(text)):
            w = m.group(0)
            if m.start() == 0 or text[max(0, m.start() - 2):m.start()].strip() in {".", "?", "!"}:
                continue   # sentence-initial capitals say nothing
            if _unknown(w, dictionary, keep):
                counts[w] = counts.get(w, 0) + 1
    return {w: c for w, c in sorted(counts.items(), key=lambda x: -x[1]) if c >= min_count}


def guard(lines: list[str], panel_authors: set[str] | None = None, dictionary: set[str] | None = None,
          allow: set[str] | None = None, staff: set[str] | None = None) -> tuple[list[str], dict]:
    """Redacted lines and a report {removed: [...], review: {word: count}}."""
    dictionary = load_dictionary() if dictionary is None else dictionary
    keep = (allow or set()) | (staff_names() if staff is None else staff) | {REPLACEMENT.split()[-1]}
    where: dict[str, set] = {}
    names = panel_names(panel_authors or set(), dictionary, keep) | addressed_names(lines, dictionary, keep, where)
    # a single word used in many lines outside address positions is a term, not a person:
    # it goes to review, never removed silently
    limit = max(5, len(lines) // 100)
    counts, review_extra = {}, {}
    for n in names:
        pat = re.compile(rf"\b{re.escape(n)}\b")
        hit = [i for i, l in enumerate(lines) if pat.search(l)]
        if " " not in n and len(set(hit) - where.get(n, set())) > limit:
            review_extra[n] = len(hit)
        else:
            counts[n] = len(hit)
    red = redactor(set(counts))
    out = [red(l) for l in lines]
    review = review_candidates(out, dictionary, keep)
    review.update(review_extra)
    return out, {"removed": dict(sorted(counts.items())), "review": dict(sorted(review.items(), key=lambda x: -x[1]))}
