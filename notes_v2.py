"""NG-3 notes v2: outline first, extract verbatim, write from sources, then audit.

1. Outline: the whole numbered transcript (with slide titles and the agenda) in one call;
   sections come back as contiguous line ranges, and the title is chosen from a closed list
   of candidates (calendar event, portal segment, first slide, manifest). Any other title is
   not possible; an empty list raises a review flag.
2. Extract: per section, verbatim definitions, numbers, examples, decisions and caveats with
   their line numbers; a quote not found in its cited lines is dropped (deterministic).
3. Write: per section, from that section's extracts and its own lines, never from earlier
   summaries. Every paragraph or bullet must carry a timecode inside the section's range;
   one that does not gets the time of its best-matching line, and the repair is counted.
4. Audit: numbers and capitalised terms in the notes must appear in an extract or the
   section's lines; sentences that share little with their section are counted.

Prompts put the source first and the task last, so the server can reuse the prefill.
Outputs (beside the current notes, which are not touched): <lecture>/notes_v2/
  outline.json, extracts.jsonl, notes.md, notes_audit.json
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import capstone          # exact_span
import cite

TYPES = ("definition", "number", "example", "decision", "caveat")
TC = re.compile(r"\[(\d{1,2}:\d{2}:\d{2})\]")
MIN_SECTION = 12


def _secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def _obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def numbered(lines: list[dict]) -> tuple[list[dict], str]:
    """Transcript lines numbered L1..Ln (slides shown inline as context, not numbered)."""
    said = [l for l in lines if "/t/" in l["line_id"] and l.get("t")]
    slides = sorted((l for l in lines if "/s/" in l["line_id"] and l.get("t")), key=lambda l: l["t"])
    out, si = [], 0
    rows = []
    for n, l in enumerate(said, 1):
        while si < len(slides) and slides[si]["t"] <= l["t"]:
            out.append(f"   [SLIDE {slides[si]['t'][:5]}] {' '.join(slides[si]['text'].split()[:25])}")
            si += 1
        rows.append({**l, "n": n})
        out.append(f"L{n} [{l['t']}] {l['text']}")
    return rows, "\n".join(out)


OUTLINE_PROMPT = """TRANSCRIPT (numbered lines; SLIDE lines show what was on screen):
{transcript}

AGENDA (as planned; the class may differ):
{agenda}

TITLE CANDIDATES:
{titles}

TASK: Divide the class as it was delivered into 4 to 12 contiguous sections, in order,
covering lines 1 to {n}. Give each a short heading naming what was taught. Choose the title
by number from TITLE CANDIDATES only.
Reply with JSON only: {{"title_choice": 1, "sections": [{{"heading": "...", "from": 1, "to": 120}}]}}"""


def outline(rows: list[dict], text: str, agenda: str, titles: list[str], llm) -> dict:
    n = len(rows)
    d = _obj(llm(OUTLINE_PROMPT.format(transcript=text, agenda=agenda or "(none)", n=n,
                                       titles="\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1)) or "(none)")))
    secs = []
    for s in d.get("sections") or []:
        try:
            a, b = max(1, int(s.get("from"))), min(n, int(s.get("to")))
        except (TypeError, ValueError):
            continue
        if b >= a and (s.get("heading") or "").strip():
            secs.append({"heading": s["heading"].strip()[:90], "from": a, "to": b})
    secs.sort(key=lambda s: s["from"])
    fixed = []
    for s in secs:                       # contiguous, no overlap, gaps go to the previous section
        if fixed and s["from"] <= fixed[-1]["to"]:
            s["from"] = fixed[-1]["to"] + 1
        if fixed and s["from"] > fixed[-1]["to"] + 1:
            fixed[-1]["to"] = s["from"] - 1
        if s["from"] <= s["to"]:
            fixed.append(s)
    if not fixed:
        fixed = [{"heading": "The class", "from": 1, "to": n}]
    fixed[0]["from"], fixed[-1]["to"] = 1, n
    merged = []
    for s in fixed:                      # fold tiny sections into the previous one
        if merged and s["to"] - s["from"] + 1 < MIN_SECTION:
            merged[-1]["to"] = s["to"]
        else:
            merged.append(s)
    for i, s in enumerate(merged, 1):
        s["section_id"] = f"s{i}"
        s["t0"], s["t1"] = rows[s["from"] - 1]["t"], rows[s["to"] - 1]["t"]
    tc = d.get("title_choice")
    ok = isinstance(tc, int) and 1 <= tc <= len(titles)
    return {"title_choice": tc if ok else None, "title": titles[tc - 1] if ok else (titles[0] if titles else ""),
            "title_candidates": titles, "title_source": "model_choice" if ok else ("first_candidate" if titles else "none"),
            "title_review": not ok, "sections": merged}


EXTRACT_PROMPT = """SECTION LINES ({heading}):
{lines}

TASK: Copy out, word for word, the definitions, numbers, examples, decisions and caveats
taught in these lines. Each quote must be an exact run of words from the cited lines (you may
cut it short, never reword it). At most 12.
Reply with JSON only: {{"extracts": [{{"type": "definition", "quote": "...", "lines": [12, 13]}}]}}"""


def extract(section: dict, rows: list[dict], llm) -> tuple[list[dict], int]:
    mine = rows[section["from"] - 1: section["to"]]
    by = {r["n"]: r for r in mine}
    d = _obj(llm(EXTRACT_PROMPT.format(heading=section["heading"],
                                       lines="\n".join(f"L{r['n']} [{r['t']}] {r['text']}" for r in mine))))
    out, dropped = [], 0
    for e in d.get("extracts") or []:
        raw_ln = e.get("lines") if isinstance(e.get("lines"), list) else []
        ln = [int(str(x).lstrip("L")) for x in raw_ln if str(x).lstrip("L").isdigit() and int(str(x).lstrip("L")) in by]
        quote = (e.get("quote") or "").strip()
        if not ln or len(quote.split()) < 3:
            dropped += 1
            continue
        near = sorted({j for i in ln for j in (i - 1, i, i + 1) if j in by})   # a cited line may be off by one
        src = " ".join(by[i]["text"] for i in near)
        span = capstone.exact_span(quote, src)
        if span is None:
            dropped += 1                     # not verbatim from its cited lines
            continue
        out.append({"section_id": section["section_id"], "type": e.get("type") if e.get("type") in TYPES else "example",
                    "quote": span, "line_ids": [by[i]["line_id"] for i in sorted(set(ln))], "lines": sorted(set(ln)),
                    "t0": by[min(ln)]["t"]})
    return out, dropped


WRITE_PROMPT = """SECTION LINES ({heading}):
{lines}

VERIFIED EXTRACTS FROM THESE LINES:
{extracts}

TASK: Write study notes for this section as 3 to 8 bullets, using only these lines and
extracts. Keep every number, name of a framework and example exactly as given. End every
bullet with the time of the line it comes from in square brackets, like [10:21:04], using
only times shown in the section lines. No heading, no introduction.
Reply with the bullets only."""


def write(section: dict, rows: list[dict], extracts: list[dict], llm) -> tuple[str, int]:
    mine = rows[section["from"] - 1: section["to"]]
    a, b = _secs(section["t0"]), _secs(section["t1"])
    raw = llm(WRITE_PROMPT.format(heading=section["heading"],     # same line format as EXTRACT: shared prefill
                                  lines="\n".join(f"L{r['n']} [{r['t']}] {r['text']}" for r in mine),
                                  extracts="\n".join(f"- ({e['type']}, {e['t0']}) {e['quote']}" for e in extracts) or "(none)"))
    bullets, repaired = [], 0
    for line in (raw or "").splitlines():
        t = line.strip()
        if not re.match(r"^(?:[-*•]|\d+[.)])\s+", t):
            continue
        t = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", t)
        codes = [c for c in TC.findall(t) if a <= _secs(c) <= b]
        if not codes:                        # repair: time of the best-matching line in the section
            sc = cite.lexical_support(t, [r["text"] for r in mine])
            best = mine[max(range(len(mine)), key=lambda i: sc[i])]["t"] if mine else section["t0"]
            t = TC.sub("", t).rstrip(" .;,") + f". [{best}]"
            repaired += 1
        else:
            t = TC.sub(lambda m: m.group(0) if a <= _secs(m.group(1)) <= b else "", t)
        bullets.append(f"- {t.strip()}")
    return "\n".join(bullets), repaired


TLDR_PROMPT = """VERIFIED EXTRACTS (id: quote):
{extracts}

TASK: Pick the 5 extracts that carry the class's main ideas, and restate each in one plain
sentence that keeps its numbers and terms.
Reply with JSON only: {{"tldr": [{{"id": "e3", "text": "..."}}]}}"""


TERM = re.compile(r"\b([A-Z][a-z]+(?:[- ][A-Z][a-z]+)+)\b")
NUM = re.compile(r"(?<![\w:])\d+(?:\.\d+)?%?(?![\w:])")


def audit(md: str, extracts: list[dict], sections: list[dict], rows: list[dict], dropped: int, repaired: int,
          slides: str = "") -> dict:
    ex_text = " ".join(e["quote"] for e in extracts).lower()
    body = TC.sub("", "\n".join(l for l in md.splitlines() if not l.startswith("#")))   # headings are not claims
    src_text = (" ".join(r["text"] for r in rows) + " " + slides).lower()
    nums = set(NUM.findall(body))
    terms = set(TERM.findall(body))
    num_missing = sorted(n for n in nums if n.lower() not in ex_text and n.lower() not in src_text)
    term_missing = sorted(t for t in terms if t.lower() not in ex_text and t.lower() not in src_text)
    unref, total = 0, 0
    for s in sections:
        sec_lines = " ".join(r["text"] for r in rows[s["from"] - 1: s["to"]])
        for b in s.get("bullets", []):
            for sent in re.split(r"(?<=[.!?])\s+", TC.sub("", b)):
                if len(sent.split()) < 5:
                    continue
                total += 1
                unref += cite.lexical_support(sent, [sec_lines])[0] < 0.4
    nw = cite.norm_words(body)
    covered = sum(1 for e in extracts if cite.norm_words(e["quote"]) and
                  len(cite.norm_words(e["quote"]) & nw) / len(cite.norm_words(e["quote"])) >= 0.5)
    return {"extract_coverage": round(covered / len(extracts), 3) if extracts else None, "extracts": len(extracts),
            "dropped_extracts": dropped, "timecodes_repaired": repaired, "numbers_not_in_sources": num_missing,
            "terms_not_in_sources": term_missing[:20], "unreferenced_sentences": unref, "sentences": total}


def build(lines: list[dict], titles: list[str], agenda: str, llm) -> dict:
    t0 = time.time()
    rows, text = numbered(lines)
    if not rows:
        return {"ok": False, "error": "no timed transcript lines"}
    ol = outline(rows, text, agenda, titles, llm)
    t_outline = time.time() - t0
    exs, dropped, repaired = [], 0, 0
    for s in ol["sections"]:
        e, d = extract(s, rows, llm)
        dropped += d
        s["bullets_md"], r = write(s, rows, e, llm)
        s["bullets"] = s["bullets_md"].splitlines()
        repaired += r
        exs += e
    for i, e in enumerate(exs, 1):
        e["extract_id"] = f"e{i}"
    d = _obj(llm(TLDR_PROMPT.format(extracts="\n".join(f"{e['extract_id']}: {e['quote'][:300]}" for e in exs))))
    by = {e["extract_id"]: e for e in exs}
    tldr = [f"- {x['text'].strip()} [{by[x['id']]['t0']}]" for x in (d.get("tldr") or [])
            if isinstance(x, dict) and x.get("id") in by and (x.get("text") or "").strip()][:5]
    md = [f"# {ol['title']}", "", "## TL;DR", *tldr, ""]
    for s in ol["sections"]:
        md += [f"## {s['heading']}", f"_[{s['t0']}–{s['t1']}]_", "", s["bullets_md"] or "- (nothing verifiable in this section)", ""]
    notes = "\n".join(md).rstrip() + "\n"
    slides = " ".join(l["text"] for l in lines if "/s/" in l["line_id"])
    au = audit(notes, exs, ol["sections"], rows, dropped, repaired, slides)
    au.update({"title_source": ol["title_source"], "title_review": ol["title_review"],
               "seconds": {"outline": round(t_outline, 1), "total": round(time.time() - t0, 1)}})
    for s in ol["sections"]:
        s.pop("bullets", None)
    return {"ok": True, "outline": ol, "extracts": exs, "notes": notes, "audit": au}


def save(folder: Path, res: dict) -> Path:
    d = Path(folder) / "notes_v2"
    d.mkdir(exist_ok=True)
    (d / "outline.json").write_text(json.dumps({k: v for k, v in res["outline"].items()} |
                                               {"sections": [{k: v for k, v in s.items() if k != "bullets_md"}
                                                             for s in res["outline"]["sections"]]}, indent=1))
    (d / "extracts.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in res["extracts"]))
    (d / "notes.md").write_text(res["notes"])
    (d / "notes_audit.json").write_text(json.dumps(res["audit"], indent=1))
    return d
