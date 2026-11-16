"""Attendee names out of transcripts and slide text, before anything is written or sent
to a model. Names are removed when there is evidence they are names: an author in the
meeting app's Q&A panel, or a capitalised word outside the dictionary used where people
are addressed ("Thanks, Zorvan", "Zorvan, go ahead", a line that opens with "Zorvan B."). Staff
(LIVE_QA_STAFF) and words in an allow list are kept. Everything else that only looks like a
name is listed for a person to review, never removed silently.

v2 (EP-3) adds a roster layer: names of people seen in any class's Q&A panel, plus an
optional private attendee list, are matched against every word by exact form, an
Indic-aware phonetic key and an edit-distance ratio, so speech-to-text spellings of the
same name ("Keelam"/"Khilam") are caught. Close but uncertain matches go to review. The
learner's decisions (remove/keep) are applied on every run. The roster and decisions live
in a private, gitignored folder and never appear in logs, notes or messages."""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

REPLACEMENT = "an attendee"
GUARD_VERSION = "2.1"
# Roster thresholds, fixed by the M-9 benchmark (tools/name_guard_bench.py)
THRESHOLDS = {"phonetic": 0.80, "edit": 0.90, "review": 0.78, "min_len": 5}
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
# Indian-English common nouns and places that the system dictionary lacks; never names
NOT_NAMES = {"kirana", "mandi", "mandis", "aadhaar", "aadhar", "lakh", "lakhs", "crore", "crores", "paisa", "rupee",
             "rupees", "dhaba", "jugaad", "chai", "sarkari", "gram", "panchayat", "india", "indian", "bharat", "mumbai",
             "delhi", "bangalore", "bengaluru", "chennai", "kolkata", "hyderabad", "pune", "ahmedabad", "jaipur",
             "lucknow", "noida", "gurgaon", "gurugram", "kerala", "karnataka", "maharashtra", "tamil", "nadu",
             "telangana", "gujarat", "punjab", "bengal", "bihar", "odisha", "assam", "goa", "shimla", "simla",
             "hinglish", "tanglish", "whatsapp", "paytm", "upi", "ondc", "zomato", "swiggy", "flipkart",
             "myntra", "nykaa", "meesho", "makemytrip", "sarvam", "jugalbandi", "nvidia", "coursera", "klarna",
             "waymo", "tesla", "openai", "anthropic", "gemini", "qwen", "llama", "copilot", "autopilot", "firefly",
             "figma", "jira", "asana", "notion", "canva", "razorpay", "phonepe", "zerodha", "ola", "uber",
             "google", "microsoft", "amazon", "apple", "meta", "linkedin", "gartner", "salesforce", "netflix",
             "spotify", "youtube", "chatgpt", "claude", "perplexity", "github", "slack", "zoom", "excel", "python",
             "deepseek", "mistral", "hugging", "openai", "azure", "aws", "oracle", "ibm", "adobe", "airbnb", "stripe",
             "duolingo", "grammarly", "cursor", "replit", "lovable", "mckinsey", "deloitte", "accenture", "infosys",
             "wipro", "tcs", "hdfc", "icici", "sbi", "airtel", "jio", "reliance", "tata", "mahindra", "byju", "unacademy"}
# chat lines read "<name> to Everyone" / "<name> to Me"
CHAT_TO = re.compile(r"\s+to\s+(everyone|me|you)\b.*$", re.I)
WORD = re.compile(rf"\b{CAP}\b")


@functools.lru_cache(maxsize=2)
def load_dictionary(path: str = "/usr/share/dict/words") -> frozenset[str]:
    p = Path(os.environ.get("STUDY_DICT", path))
    return frozenset(w.strip().lower() for w in p.read_text(errors="ignore").splitlines()) if p.exists() else frozenset()


_LOWER_DICT: dict[str, frozenset] = {}


def load_lower_dictionary(path: str = "/usr/share/dict/words") -> frozenset[str]:
    """Only the entries written in lower case: common words. The full word list also holds
    proper names ("Rhodes", many surnames), which must not shield the rest of a name."""
    p = Path(os.environ.get("STUDY_DICT", path))
    key = str(p)
    if key not in _LOWER_DICT:
        _LOWER_DICT[key] = frozenset(w.strip() for w in p.read_text(errors="ignore").splitlines()
                                     if w.strip() and w.strip().islower()) if p.exists() else frozenset()
    return _LOWER_DICT[key]


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


def in_dictionary(word: str, dictionary) -> bool:
    """The word or a simple inflection of it is an ordinary word ("means", "called", "plays")."""
    w = word.lower()
    if not dictionary:
        return False
    if w in dictionary:
        return True
    for suf, back in (("s", ""), ("es", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("ly", ""), ("ies", "y"),
                      ("er", ""), ("ers", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and (w[: len(w) - len(suf)] + back) in dictionary:
            return True
    return False


def _unknown(word: str, dictionary: set[str], keep: set[str]) -> bool:
    w = word.lower()
    return w not in keep and w not in COMMON and w not in NOT_NAMES and not in_dictionary(w, dictionary)


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


# ---------------------------------------------------------------- roster layer (EP-3)
_DIGRAPHS = [("qu", "kw"), ("sch", "s"), ("ksh", "ks"), ("ph", "f"), ("bh", "b"), ("dh", "d"), ("th", "t"), ("kh", "k"),
             ("gh", "g"), ("jh", "j"), ("sh", "s"), ("zh", "j"), ("ck", "k"), ("q", "k"), ("z", "j"), ("w", "v"), ("x", "ks")]
_VOWELS = set("aeiouy")


def fold(word: str) -> str:
    """Spelling-folded form of a name as speech-to-text writes it: aspirates and common Indian
    transliteration variants folded (sh/s, v/w, th/t, ph/f, ch/kh/c/k, z/j), long vowels
    shortened (ee/i, oo/u, aa/a) and doubled letters collapsed. 'Seeleeni' -> 'silini'."""
    w = re.sub(r"[^a-z]", "", word.lower())
    for a, b in _DIGRAPHS:
        w = w.replace(a, b)
    w = w.replace("ch", "k").replace("c", "k").replace("h", "")
    for a, b in (("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"), ("aa", "a"), ("ou", "u"), ("y", "i")):
        w = w.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", w)


def phonetic_key(word: str) -> str:
    """First letter, then the consonant skeleton of the folded form. 'Keelam'/'Khilam' -> 'klm'."""
    w = fold(word)
    if not w:
        return ""
    head = "a" if w[0] in _VOWELS else w[0]
    return head + "".join(ch for ch in w[1:] if ch not in _VOWELS)


def similarity(a: str, b: str) -> float:
    """Edit-distance ratio in [0, 1] between folded forms (rapidfuzz when installed, else the
    standard library)."""
    a, b = fold(a), fold(b)
    try:
        from rapidfuzz.fuzz import ratio
        return ratio(a, b) / 100.0
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio()


class Roster:
    """Name parts of known attendees, indexed for exact, phonetic and edit-distance lookup.
    Parts that are ordinary words, staff names or allow-listed terms are not matched alone
    (a full multi-word name still is), so 'Rose' or 'Grace' never removes the English word."""

    def __init__(self, names, keep: set[str] | None = None, dictionary=None, thresholds: dict | None = None):
        self.t = dict(THRESHOLDS, **(thresholds or {}))
        keep = {k.lower() for k in (keep or set())}
        # common (lower-case) words only: the full list also holds proper names, which are
        # exactly what a roster part may be
        dictionary = load_lower_dictionary() if dictionary is None else dictionary
        self.full: set[str] = set()
        self.parts: dict[str, str] = {}
        self.by_key: dict[str, set[str]] = {}
        self.skipped_parts = 0
        for n in names:
            n = CHAT_TO.sub("", str(n)).strip()
            if not n:
                continue
            toks = n.split()
            if len(toks) > 1:
                self.full.add(n)
            for p in toks:
                p = p.strip(".'-")
                lp = p.lower()
                if len(lp) < 3 or not lp.isalpha():
                    continue
                if lp in keep or lp in COMMON or lp in NOT_NAMES or in_dictionary(lp, dictionary):
                    self.skipped_parts += 1
                    continue
                self.parts[lp] = p
                self.by_key.setdefault(phonetic_key(lp), set()).add(lp)

    def __len__(self):
        return len(self.parts)

    def match(self, token: str):
        """(decision, layer, score, part) for a word, or None. decision is 'remove' or 'review'."""
        memo = self.__dict__.setdefault("_memo", {})
        key = token.lower()
        if key not in memo:
            memo[key] = self._match(token)
        return memo[key]

    def _match(self, token: str):
        t = token.lower().strip(".'-")
        if len(t) < 3 or not t.isalpha():
            return None
        if t in self.parts:
            return ("remove", "roster-exact", 1.0, self.parts[t])
        if len(t) < self.t["min_len"]:
            return None
        ph = max(((similarity(t, p), p) for p in self.by_key.get(phonetic_key(t), ())), default=None)
        ed = max(((similarity(t, p), p) for p in self.parts
                  if phonetic_key(p)[:1] == phonetic_key(t)[:1] and abs(len(p) - len(t)) <= 2), default=None)
        if ph and ph[0] >= self.t["phonetic"]:
            return ("remove", "roster-phonetic", round(ph[0], 3), self.parts[ph[1]])
        if ed and ed[0] >= self.t["edit"]:
            return ("remove", "roster-edit", round(ed[0], 3), self.parts[ed[1]])
        top = max([x for x in (ph, ed) if x], default=None)
        if top and top[0] >= self.t["review"]:
            return ("review", "roster-phonetic" if top is ph else "roster-edit", round(top[0], 3), self.parts[top[1]])
        return None


def load_roster(private_dir: Path | None) -> list[str]:
    """Names from names_roster.json plus attendees.txt (one name per line) in the private folder."""
    if not private_dir:
        return []
    names = []
    f = Path(private_dir) / "names_roster.json"
    if f.exists():
        import json
        names += [r["name"] for r in json.loads(f.read_text()) if r.get("name")]
    a = Path(private_dir) / "attendees.txt"
    if a.exists():
        names += [l.split("#")[0].strip() for l in a.read_text().splitlines() if l.split("#")[0].strip()]
    return names


def update_roster(private_dir: Path, authors_by_day: dict[str, set[str]]) -> dict:
    """Add Q&A authors that look like names to names_roster.json ({name, source, first_seen}).
    Returns counts only."""
    import json
    private_dir = Path(private_dir)
    private_dir.mkdir(parents=True, exist_ok=True)
    f = private_dir / "names_roster.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    known = {r["name"] for r in rows}
    added = 0
    for day in sorted(authors_by_day):
        for a in sorted(authors_by_day[day]):
            a = CHAT_TO.sub("", a).strip()
            ordinary = all(t.lower().strip(".") in COMMON or len(t.strip(".")) < 2 for t in a.split())
            if looks_like_name(a) and not ordinary and a not in known:
                rows.append({"name": a, "source": "qa-panel", "first_seen": day})
                known.add(a)
                added += 1
    f.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return {"names": len(rows), "added": added}


def learn_names(private_dir: Path, tokens, source: str = "adjacent") -> int:
    """Add name parts learned in one text (the rest of a full name) to names_roster.json so
    every later guard removes them, in any lecture. Learner 'keep' decisions win. Returns
    how many were new (count only)."""
    import datetime as _dt
    import json
    private_dir = Path(private_dir)
    f = private_dir / "names_roster.json"
    rows = json.loads(f.read_text()) if f.exists() else []
    known = {r["name"].lower() for r in rows}
    keep = {t for t, d in load_decisions(private_dir).items() if d == "keep"}
    new = [t for t in sorted(set(tokens)) if t.lower() not in known and t.lower() not in keep]
    if not new:
        return 0
    day = _dt.date.today().strftime("%Y%m%d")
    rows += [{"name": t, "source": source, "first_seen": day} for t in new]
    private_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return len(new)


def load_decisions(private_dir: Path | None) -> dict[str, str]:
    """The learner's review decisions: {token (lower case): 'remove' | 'keep'}."""
    if not private_dir:
        return {}
    f = Path(private_dir) / "names_decisions.json"
    if not f.exists():
        return {}
    import json
    return {k.lower(): v for k, v in json.loads(f.read_text()).items() if v in ("remove", "keep")}


def save_decisions(private_dir: Path, remove: list[str], keep: list[str]) -> dict[str, str]:
    import json
    private_dir = Path(private_dir)
    private_dir.mkdir(parents=True, exist_ok=True)
    d = load_decisions(private_dir)
    d.update({t.lower(): "remove" for t in remove if t.strip()})
    d.update({t.lower(): "keep" for t in keep if t.strip()})
    (private_dir / "names_decisions.json").write_text(json.dumps(dict(sorted(d.items())), indent=1, ensure_ascii=False))
    return d


TOKEN = re.compile(r"\b[A-Za-z][A-Za-z'-]{2,}\b")
ESCAPES = re.compile(r"\\[ntr]")   # literal \n, \t inside JSON strings


def redactor(names: set[str]):
    names = {n for n in names if len(n) >= 3}
    if not names:
        return lambda text: text
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # a trailing initial ("Zorvan B.") goes with the name; a name right after a literal \n in
    # JSON text still counts as starting a word
    pat = re.compile(rf"(?:(?<=\\[ntr])|\b)(?:{alt})\b(?:\s+[A-Z]\.(?=\s|$))?")
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


_NAMEWORD = r"[A-Z][a-z][A-Za-z'-]+"
ADJ_AFTER = re.compile(rf"{REPLACEMENT}[ \t]+({_NAMEWORD})\b")
ADJ_BEFORE = re.compile(rf"(?:(?<=\\[ntr])|\b)({_NAMEWORD})[ \t]+{REPLACEMENT}")
DOUBLE = re.compile(rf"{REPLACEMENT}(?:[ \t]+{REPLACEMENT})+")
# "an attendee Rose." — a capitalised ordinary word right after a removed name and right before
# punctuation is the end of that name; only that occurrence goes (the word stays elsewhere)
ADJ_END = re.compile(rf"({REPLACEMENT})[ \t]+([A-Z][a-z][A-Za-z'-]+)(?=[.,;:!?]|\s*$)")
TITLES = {"professor", "prof", "sir", "madam", "maam", "ma'am", "doctor", "dr", "mr", "mrs", "ms", "team",
          "everyone", "all", "guys", "folks", "here", "there", "please", "thanks", "thank", "too", "also", "again"}


def _name_like(w: str, keep: set[str], dictionary: set[str]) -> bool:
    lw = w.lower()
    return not (lw in keep or lw in COMMON or lw in NOT_NAMES or in_dictionary(lw, dictionary)
                or lw in ("an", "attendee") or len(w) < 3 or len(w) > 12 or "-" in w or "'" in w)


def full_name_rest(lines: list[str], name_like) -> set[str]:
    """Capitalised words directly before or after a redaction placeholder that pass
    `name_like`: the other half of a full name whose first half was removed."""
    found: set[str] = set()
    for l in lines:
        if REPLACEMENT not in l:
            continue
        for m in ADJ_AFTER.finditer(l):
            if name_like(m.group(1)):
                found.add(m.group(1))
        for m in ADJ_BEFORE.finditer(l):
            if name_like(m.group(1)):
                found.add(m.group(1))
    return found


def guard(lines: list[str], panel_authors: set[str] | None = None, dictionary: set[str] | None = None,
          allow: set[str] | None = None, staff: set[str] | None = None, roster: "Roster | None" = None,
          decisions: dict[str, str] | None = None, hold: set[str] | None = None) -> tuple[list[str], dict]:
    """Redacted lines and a report {removed: {token: count}, review: {token: count},
    entries: [{token, count, layer, score, decision}], guard_version}."""
    strict = load_lower_dictionary() if dictionary is None else dictionary   # for the full-name rule
    dictionary = load_dictionary() if dictionary is None else dictionary
    decisions = decisions or {}
    keep = (allow or set()) | (staff_names() if staff is None else staff) | {REPLACEMENT.split()[-1]}
    keep |= {t for t, d in decisions.items() if d == "keep"}
    where: dict[str, set] = {}
    layer: dict[str, tuple[str, float]] = {}
    for n in panel_names(panel_authors or set(), dictionary, keep):
        layer[n] = ("panel", 1.0)
    for n in addressed_names(lines, dictionary, keep, where):
        layer.setdefault(n, ("cue", 1.0))
    roster_review: dict[str, tuple[str, float]] = {}
    exact: set[str] = set()
    if roster is not None and len(roster):
        seen: set[str] = set()
        for l in lines:
            for m in TOKEN.finditer(ESCAPES.sub(" ", l)):
                w = m.group(0)
                if w in seen:
                    continue
                seen.add(w)
                lw = w.lower()
                if lw in keep or lw in COMMON or lw in NOT_NAMES or in_dictionary(lw, strict) or \
                        (w.isupper() and len(w) > 1):
                    continue
                hit = roster.match(w)
                if not hit:
                    continue
                dec, lay, score, _ = hit
                # a spelling match (not exact) is only trusted on a capitalised word that the
                # caller has not put on hold; anything else goes to review
                if dec == "remove" and lay != "roster-exact" and (not w[0].isupper() or w in (hold or set())):
                    dec = "review"
                if dec == "remove":
                    layer.setdefault(w, (lay, score))
                    if lay == "roster-exact":
                        exact.add(w)
                else:
                    roster_review[w] = (lay, score)
        for full in roster.full:
            if any(full in l for l in lines):
                layer.setdefault(full, ("roster-exact", 1.0))
                exact.add(full)
    for t, d in decisions.items():
        if d != "remove":
            continue
        for l in lines:
            for m in re.finditer(rf"(?:(?<=\\[ntr])|\b){re.escape(t)}\b", l, re.I):
                layer.setdefault(m.group(0), ("learner", 1.0))
                exact.add(m.group(0))
    # a single word used in many lines outside address positions is a term, not a person:
    # it goes to review, never removed silently (exact roster and learner hits are exempt)
    limit = max(5, len(lines) // 100)
    counts, review_extra = {}, {}
    for n in layer:
        if n.lower() in keep:
            continue
        pat = re.compile(rf"(?:(?<=\\[ntr])|\b){re.escape(n)}\b")
        hit = [i for i, l in enumerate(lines) if pat.search(l)]
        if not hit:
            continue
        if n in (hold or set()) and n not in exact:
            review_extra[n] = len(hit)
            continue
        if " " not in n and n not in exact and len(set(hit) - where.get(n, set())) > limit:
            review_extra[n] = len(hit)
        else:
            counts[n] = len(hit)
    red = redactor(set(counts))
    out = [red(l) for l in lines]
    # the rest of a full name: a capitalised word right next to a removed name ("an attendee
    # Okafor", "Tamsin an attendee") that is no dictionary word, term or allowed name is the
    # other half of that name; it is removed everywhere, and its neighbours are checked again
    # a word the text itself also uses in lower case is an ordinary word, not a name
    lower_used = {w for l in out for w in re.findall(r"\b[a-z][a-z'-]{2,}\b", l)}
    name_like = lambda w: _name_like(w, keep, strict) and w.lower() not in lower_used
    adjacent = full_name_rest(out, name_like)
    if adjacent:
        red2 = redactor(adjacent)
        for _ in range(3):
            out = [red2(l) for l in out]
            more = full_name_rest(out, name_like) - adjacent
            if not more:
                break
            adjacent |= more
            red2 = redactor(adjacent)
        for w in adjacent:
            n = sum(1 for l in lines if re.search(rf"(?:(?<=\\[ntr])|\b){re.escape(w)}\b", l))
            counts[w] = max(n, 1)
            layer.setdefault(w, ("adjacent", 1.0))
    ended = 0

    def end_of_name(m):
        nonlocal ended
        w = m.group(2).lower()
        if w in keep or w in COMMON or w in NOT_NAMES or w in TITLES:
            return m.group(0)
        ended += 1
        return m.group(1)
    out = [ADJ_END.sub(end_of_name, l) for l in out]
    out = [DOUBLE.sub(REPLACEMENT, l) for l in out]   # "an attendee an attendee" reads as one
    review = review_candidates(out, dictionary, keep)
    review.update(review_extra)
    for w, (lay, sc) in roster_review.items():
        c = sum(1 for l in out if re.search(rf"\b{re.escape(w)}\b", l))
        if c:
            review[w] = c
    entries = [{"token": t, "count": c, "layer": layer[t][0], "score": layer[t][1], "decision": "removed"}
               for t, c in counts.items()]
    for t, c in review.items():
        lay, sc = roster_review.get(t) or (("frequency-gate", layer[t][1]) if t in review_extra else ("capitalised", 0.0))
        entries.append({"token": t, "count": c, "layer": lay, "score": sc, "decision": "review"})
    return out, {"removed": dict(sorted(counts.items())), "review": dict(sorted(review.items(), key=lambda x: -x[1])),
                 "entries": sorted(entries, key=lambda e: (e["decision"], -e["count"])), "guard_version": GUARD_VERSION,
                 "name_ends_removed": ended}
