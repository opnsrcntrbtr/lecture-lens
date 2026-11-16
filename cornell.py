"""LS-5 Cornell-style notes view: notes, a cue column of questions, and your own summary.

Extracts are the notes' bullets that cite a lecture time ([hh:mm:ss] or a range); each is
linked to the transcript lines in that range. Cue questions are written per section from
those extracts and every cue names at least one extract. Some cues ask to explain why, or
how the idea would apply to your own product. Cues also become cards (cards_cues.tsv,
answered by the extract and its time). The summary box is yours and never pre-filled.

Files: <lecture>/extracts.jsonl, <lecture>/cues.jsonl, <lecture>/cards_cues.tsv,
       practice/summaries/<lecture>.md (learner-written)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import cite

TC = re.compile(r"\[(\d{1,2}:\d{2}:\d{2})(?:\s*[–-]\s*(\d{1,2}:\d{2}:\d{2}))?\]")
SKIP_SECTIONS = re.compile(r"action items|open questions|how the class ran|on the agenda", re.I)
KINDS = ("cue", "explain", "apply")


def _secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "section"


def sections(notes: str) -> list[dict]:
    out, cur = [], None
    for line in notes.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            cur = {"section_id": _slug(m.group(1)), "heading": m.group(1).strip(), "lines": []}
            out.append(cur)
        elif cur is not None and not line.startswith("<!--"):
            cur["lines"].append(line)
    for s in out:
        s["markdown"] = "\n".join(s.pop("lines")).strip()
    return out


def extracts(notes: str, lines: list[dict], pad_s: int = 30) -> list[dict]:
    """Bullets that cite a time, with the transcript lines in that range."""
    said = [l for l in lines if "/t/" in l["line_id"] and l.get("t")]
    out = []
    for s in sections(notes):
        if SKIP_SECTIONS.search(s["heading"]):
            continue
        for b in s["markdown"].splitlines():
            if not re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", b):
                continue
            m = TC.search(b)
            if not m:
                continue
            a = _secs(m.group(1))
            z = _secs(m.group(2)) if m.group(2) else a + 60
            hit = [l for l in said if a - pad_s <= _secs(l["t"]) <= z + pad_s]
            text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", TC.sub("", b)).strip(" ,;")
            text = re.sub(r"\[slide[^\]]*\]|\*\*", "", text).strip(" ,;")
            if len(text.split()) < 4:
                continue
            out.append({"extract_id": f"x{len(out) + 1}", "section_id": s["section_id"], "quote": text,
                        "t0": m.group(1), "t1": m.group(2) or m.group(1),
                        "line_ids": [l["line_id"] for l in hit][:40]})
    return out


CUE_PROMPT = """LECTURE NOTES SECTION: {heading}
EXTRACTS (numbered; each is a point from the lecture):
{extracts}

Write Cornell cue questions for this section: one question per extract that a learner would
answer from memory, plus at most two "explain why" questions and one "how would this apply to
your own product" question, each tied to the extract(s) it draws on. A question must not
contain its answer. At most 25 words each, one question mark each.
Reply with JSON only: {{"cues": [{{"kind": "cue" or "explain" or "apply", "extracts": ["x1"], "question": "...?"}}]}}"""


def cue_problems(q: str, ex_texts: list[str]) -> list[str]:
    why = []
    q = (q or "").strip()
    if q.count("?") != 1 or not q.endswith("?"):
        why.append("one question mark, at the end")
    if len(q.split()) > 25:
        why.append("over 25 words")
    qw = cite.norm_words(q)
    for t in ex_texts:
        tw = cite.norm_words(t)
        if tw and len(tw - qw) / len(tw) < 0.4:   # the question restates most of the answer
            why.append("contains its answer")
            break
    return why


def make_cues(extract_rows: list[dict], heading_of: dict[str, str], llm) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for e in extract_rows:
        by.setdefault(e["section_id"], []).append(e)
    out = []
    for sid, exs in by.items():
        ids = {e["extract_id"]: e for e in exs}
        raw = llm(CUE_PROMPT.format(heading=heading_of.get(sid, sid),
                                    extracts="\n".join(f"{e['extract_id']}: {e['quote'][:400]}" for e in exs))) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            cues = json.loads(m.group(0)).get("cues", []) if m else []
        except (ValueError, AttributeError):
            cues = []
        for c in cues if isinstance(cues, list) else []:
            refs = [x for x in (c.get("extracts") or []) if x in ids]
            kind = c.get("kind") if c.get("kind") in KINDS else "cue"
            q = (c.get("question") or "").strip()
            if not refs or cue_problems(q, [ids[x]["quote"] for x in refs]):
                continue
            out.append({"cue_id": f"c{len(out) + 1}", "section_id": sid, "kind": kind, "question": q,
                        "extract_ids": refs})
    return out


def cards_tsv(cues: list[dict], extract_rows: list[dict]) -> str:
    ex = {e["extract_id"]: e for e in extract_rows}
    rows = []
    for c in cues:
        if c["kind"] != "cue":
            continue
        e = [ex[x] for x in c["extract_ids"]]
        rows.append("\t".join([c["question"].replace("\t", " "), " ".join(x["quote"] for x in e).replace("\t", " "),
                               f"[{e[0]['t0']}]"]))
    return "\n".join(rows) + ("\n" if rows else "")


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def view(folder: Path, summaries: Path) -> dict:
    notes = (folder / "notes.md").read_text() if (folder / "notes.md").exists() else ""
    cues = read_jsonl(folder / "cues.jsonl")
    exs = {e["extract_id"]: e for e in read_jsonl(folder / "extracts.jsonl")}
    out = []
    for s in sections(notes):
        out.append({**s, "cues": [{**c, "times": [exs[x]["t0"] for x in c["extract_ids"] if x in exs]}
                                  for c in cues if c["section_id"] == s["section_id"]]})
    sp = summaries / f"{folder.name}.md"
    return {"sections": out, "summary": sp.read_text() if sp.exists() else "",
            "summary_prompt": "In your own words: what are the three ideas from this class you would explain to a colleague?",
            "built": bool(cues)}


def save_summary(summaries: Path, lecture: str, text: str) -> dict:
    summaries.mkdir(parents=True, exist_ok=True)
    (summaries / f"{lecture}.md").write_text(text)
    return {"saved": True, "words": len(text.split())}
