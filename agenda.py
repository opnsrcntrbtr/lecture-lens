"""NG-4 agenda reconciliation: notes follow the class as delivered.

The agenda's parts are mapped to windows of the transcript, and each part gets a status:
  covered   taught in runs of 10 minutes or more, adding up to at least 30% of its planned
            minutes (and at least 10)
  partial   taught in such runs, but less than that
  moved     covered or partial, but delivered out of the agenda's order: the start of its
            longest run is not on the in-order sequence that carries the most minutes
            ('coverage' keeps which)
  skipped   no run of 10 minutes (passing mentions are counted in mentions_min)
Windows that match no part, in runs of 10 minutes or more, are 'unplanned'.

Matching: the model labels every five-minute window with the part it is teaching, or none
(recaps of earlier sessions, topics off the agenda, breaks, polls, Q&A), from the slide on
screen and a short extract, in chunks of twelve windows; three passes are combined by
majority. A lone window between two of the same label takes that label. Each run of windows
with one label then gets a second, longer look (does this excerpt teach this part?), and a
run that fails becomes none. The model chooses from known ids only. A lexical score per
window and part is the cross-check; agreement with it is the confidence.

Contract: <lecture>/agenda_map.json
  {"parts": [{agenda_item, item_id, planned_min, status, section_ids, t0, t1, minutes, confidence}],
   "sections": [{section_id, t0, t1, label, lexical}], "unplanned": [section_id], "made": ...}
"""
from __future__ import annotations

import json
import math
import re

import cite

WINDOW_MIN = 5
UNPLANNED_MIN = 10
PART = re.compile(r"^\s*(?:\d+[.)]\s*)?part\s+(\w+)\s*[·:.\-–—)]*\s*(.+)$", re.I)
MINUTES = re.compile(r"(?:about|~|approx\.?|around)?\s*(\d{2,3})\s*(?:minutes|mins?)\b", re.I)
END = re.compile(r"^\s*(?:close|pre-?read|question to bring|there is a|break\b|homework|before the next)", re.I)
# logistics written inside a part (a line announcing a break, or saying how the session
# closes): kept out of the part's detail, which is matched against what was taught
ASIDE = re.compile(r"\b(?:[\w-]+[- ]minute|short|coffee|lunch|tea)\s+break\b|\bbreak\s+(?:follows|after|before)\b"
                   r"|\bsession\s+(?:closes|ends|wraps\s+up)\b", re.I)
STOP = set("""a an the and or but of to in on for with by from as at is are was were be this that these those
it its you your we our can how what when which who why will would should could more most about into""".split())


def parse_agenda(text: str) -> list[dict]:
    """Parts ('Part 1 · Title (about 50 minutes)' or 'Part 1 Title (~45 min): detail'), each
    with the lines under it until the next part or a closing line."""
    parts, cur = [], None
    for line in (text or "").splitlines():
        m = PART.match(line)
        if m:
            title = m.group(2).strip()
            mins = MINUTES.search(title)
            cur = {"item_id": f"p{len(parts) + 1}", "label": f"Part {m.group(1)}",
                   "title": re.sub(r"\s*\(([^)]*)\)\s*", " ", title.split(":")[0]).strip(" ·:-"),
                   "detail": title.split(":", 1)[1].strip() if ":" in title else "",
                   "planned_min": int(mins.group(1)) if mins else None}
            parts.append(cur)
            continue
        if cur is not None:
            if END.match(line):
                cur = None
            elif line.strip() and not ASIDE.search(line):
                cur["detail"] = (cur["detail"] + " " + line.strip()).strip()
    return parts


def _secs(t: str) -> int:
    h, m, s = (int(x) for x in (t or "00:00:00").split(":"))
    return h * 3600 + m * 60 + s


def _clock(s: int) -> str:
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def windows(lines: list[dict], minutes: int = WINDOW_MIN) -> list[dict]:
    """Transcript lines grouped into fixed windows by wall-clock time, with the text of any
    slide shown in the window (slide titles are the strongest cue to the agenda part)."""
    said = [l for l in lines if "/t/" in l["line_id"] and l.get("t")]
    if not said:
        return []
    start = _secs(said[0]["t"])
    by: dict[int, dict] = {}
    for l in lines:
        if not l.get("t") or ("/t/" not in l["line_id"] and "/s/" not in l["line_id"]):
            continue
        k = (_secs(l["t"]) - start) // (minutes * 60)
        if k < 0:
            continue
        w = by.setdefault(k, {"said": [], "slides": []})
        w["said" if "/t/" in l["line_id"] else "slides"].append(l)
    out = []
    for k in sorted(by):
        w = by[k]
        if not w["said"]:
            continue
        out.append({"section_id": f"w{k:02d}", "t0": _clock(start + k * minutes * 60),
                    "t1": _clock(start + (k + 1) * minutes * 60), "line_ids": [l["line_id"] for l in w["said"]],
                    "text": " ".join(l["text"] for l in w["said"]),
                    "slides": " ".join(l["text"] for l in w["slides"])})
    return out


def _words(text: str) -> set[str]:
    return cite.norm_words(text) - STOP


def lexical(parts: list[dict], secs: list[dict]) -> list[dict[str, float]]:
    """Per window: IDF-weighted share of each part's words present in the window."""
    n = len(secs) or 1
    wsets = [_words(s["text"] + " " + s.get("slides", "")) for s in secs]
    df: dict[str, int] = {}
    for ws in wsets:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    idf = lambda w: math.log((n + 1) / (df.get(w, 0) + 1)) + 1
    out = []
    for ws in wsets:
        row = {}
        for p in parts:
            pw = _words(p["title"] + " " + p["detail"])
            tot = sum(idf(w) for w in pw) or 1
            row[p["item_id"]] = round(sum(idf(w) for w in pw & ws) / tot, 3)
        out.append(row)
    return out


def extract(text: str, n: int = 60) -> str:
    w = text.split()
    if len(w) <= n:
        return text
    h = n // 2
    mid = len(w) // 2
    return " ".join(w[:h]) + " … " + " ".join(w[mid: mid + h])


LABEL_PROMPT = """THIS SESSION'S AGENDA (parts in planned order; each part's detail lists what it covers):
{parts}

TRANSCRIPT WINDOWS (five minutes each; SLIDE is the slide on screen, SAID is a short extract):
{windows}

For each window, name the agenda part it is teaching. Use a part only when the window teaches
that part's own detail, not merely shared words. Say "none" for: recaps of earlier sessions or
modules, topics the agenda does not list (even close ones), breaks, polls, logistics, audio
checks and open Q&A. When unsure, say "none".
Choose only from: {ids}, none.
Reply with JSON only: {{"labels": {{"w00": "p1", "w01": "none", ...}}}}"""


def _window_line(s: dict) -> str:
    sl = " ".join(s.get("slides", "").split()[:30])
    return f"{s['section_id']} [{s['t0'][:5]}]" + (f" SLIDE: {sl} |" if sl else "") + f" SAID: {extract(s['text'], 70)}"


def label(parts: list[dict], secs: list[dict], llm, chunk: int = 12) -> dict[str, str]:
    """Labels in chunks of `chunk` windows (each call sees the whole agenda), so a long class
    is not judged from one crowded prompt."""
    ids = [p["item_id"] for p in parts]
    agenda = "\n".join(f"{p['item_id']}: {p['title']}. {p['detail'][:400]}" for p in parts)
    out = {}
    for i in range(0, len(secs), chunk):
        part = secs[i:i + chunk]
        raw = llm(LABEL_PROMPT.format(parts=agenda, windows="\n".join(_window_line(s) for s in part),
                                      ids=", ".join(ids))) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group(0)) if m else {}
        except ValueError:
            d = {}
        labels = d.get("labels", d) if isinstance(d, dict) else {}
        out.update({s["section_id"]: (labels.get(s["section_id"]) if labels.get(s["section_id"]) in ids else "none")
                    for s in part})
    return out


VERIFY_PROMPT = """AGENDA PART: {title}
WHAT IT COVERS: {detail}
OTHER PARTS OF THIS SESSION: {others}

CLASS EXCERPT ({t0} to {t1}):
SLIDES: {slides}
SAID: {said}

Is this excerpt teaching this agenda part's own content, as opposed to recapping an earlier
session, teaching another part, or a topic the agenda does not list?
Reply with JSON only: {{"teaches": true or false}}"""


def verify(parts: list[dict], secs: list[dict], labels: dict[str, str], llm) -> dict[str, str]:
    """Second look at each run of windows labelled with one part (a longer excerpt, yes or
    no); a run that fails becomes 'none'. Removes look-alike recaps of earlier sessions."""
    by = {p["item_id"]: p for p in parts}
    ids = [s["section_id"] for s in secs]
    out = dict(labels)
    i = 0
    while i < len(secs):
        lab = labels.get(ids[i])
        j = i
        while j + 1 < len(secs) and labels.get(ids[j + 1]) == lab:
            j += 1
        if lab in by:
            run = secs[i:j + 1]
            p = by[lab]
            said = " ".join(extract(x["text"], max(40, 320 // len(run))) for x in run)
            slides = " / ".join(" ".join(x.get("slides", "").split()[:25]) for x in run if x.get("slides"))
            raw = llm(VERIFY_PROMPT.format(title=p["title"], detail=p["detail"][:500],
                                           others="; ".join(q["title"] for q in parts if q is not p),
                                           t0=run[0]["t0"][:5], t1=run[-1]["t1"][:5], slides=slides or "(none)",
                                           said=said)) or ""
            m = re.search(r"\{.*\}", raw, re.S)
            try:
                ok = bool(json.loads(m.group(0)).get("teaches")) if m else True
            except (ValueError, AttributeError):
                ok = True
            if not ok:
                for x in run:
                    out[x["section_id"]] = "none"
        i = j + 1
    return out


def _lis(seq: list[int], weight: list[float] | None = None) -> set[int]:
    """Indexes (into seq) of the strictly increasing subsequence with the largest total weight
    (by default its length): the parts that ran in the planned order."""
    n = len(seq)
    weight = weight or [1.0] * n
    best, prev = list(weight), [-1] * n
    for i in range(n):
        for j in range(i):
            if seq[j] < seq[i] and best[j] + weight[i] > best[i]:
                best[i], prev[i] = best[j] + weight[i], j
    if not n:
        return set()
    i = max(range(n), key=lambda k: best[k])
    out = set()
    while i >= 0:
        out.add(i)
        i = prev[i]
    return out


def _runs(idx: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in idx:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def smooth(secs: list[dict], labels: dict[str, str]) -> dict[str, str]:
    """A lone window between two windows with the same label takes that label (flicker)."""
    ids = [s["section_id"] for s in secs]
    out = dict(labels)
    for i in range(1, len(ids) - 1):
        a, b, c = labels.get(ids[i - 1]), labels.get(ids[i]), labels.get(ids[i + 1])
        if a == c and b != a:
            out[ids[i]] = a
    return out


def reconcile(parts: list[dict], secs: list[dict], labels: dict[str, str]) -> dict:
    lex = lexical(parts, secs)
    labels = smooth(secs, labels)
    rows = []
    for p in parts:
        mine = [i for i, s in enumerate(secs) if labels.get(s["section_id"]) == p["item_id"]]
        runs = [r for r in _runs(mine) if len(r) * WINDOW_MIN >= 10]      # taught, not just mentioned
        minutes = len(mine) * WINDOW_MIN
        need = max(10, 0.3 * (p["planned_min"] or 30))
        coverage = "skipped" if not runs else ("covered" if minutes >= need else "partial")
        taught = [i for r in runs for i in r]
        agree = [i for i in taught if max(lex[i], key=lex[i].get) == p["item_id"]]
        rows.append({"agenda_item": f"{p['label']}: {p['title']}", "item_id": p["item_id"],
                     "runs": [[secs[r[0]]["t0"], secs[r[-1]]["t1"]] for r in runs],
                     "planned_min": p["planned_min"], "status": coverage, "coverage": coverage,
                     "section_ids": [secs[i]["section_id"] for i in taught],
                     "t0": secs[taught[0]]["t0"] if taught else None, "t1": secs[taught[-1]]["t1"] if taught else None,
                     "minutes": len(taught) * WINDOW_MIN, "mentions_min": minutes - len(taught) * WINDOW_MIN,
                     "confidence": round(len(agree) / len(taught), 2) if taught else None,
                     "_first": max(runs, key=len)[0] if runs else None})   # where the bulk of it was taught
    delivered = [r for r in rows if r["_first"] is not None]
    keep = _lis([r["_first"] for r in delivered], [float(r["minutes"]) for r in delivered])
    for k, r in enumerate(delivered):
        if k not in keep:
            r["status"] = "moved"
    for r in rows:
        r.pop("_first")
    unplanned, run = [], []
    for s in secs + [None]:
        if s is not None and labels.get(s["section_id"]) == "none":
            run.append(s["section_id"])
            continue
        if len(run) * WINDOW_MIN >= UNPLANNED_MIN:
            unplanned += run
        run = []
    return {"parts": rows,
            "sections": [{"section_id": s["section_id"], "t0": s["t0"], "t1": s["t1"],
                          "label": labels.get(s["section_id"], "none"), "lexical": lex[i]} for i, s in enumerate(secs)],
            "unplanned": unplanned}


NOTES_START, NOTES_END = "<!-- agenda-map -->", "<!-- /agenda-map -->"


def notes_block(m: dict) -> str:
    """Delivered order, plus parts on the agenda that were not covered (no generated content)."""
    lines = [NOTES_START, "## How the class ran against the agenda", ""]
    spans = sorted(((a, b, r) for r in m["parts"] for a, b in r.get("runs") or []), key=lambda x: x[0])
    for a, b, r in spans:
        tag = {"moved": " (taught out of the planned order)", "partial": " (partly covered)"}.get(r["status"], "")
        tag += " (uncertain)" if r.get("uncertain") else ""
        lines.append(f"- [{a[:5]}–{b[:5]}] {r['agenda_item']}{tag}")
    skipped = [r for r in m["parts"] if r["status"] == "skipped"]
    if skipped:
        lines += ["", "### On the agenda, not covered", ""] + [f"- {r['agenda_item']}" for r in skipped]
    if m["unplanned"]:
        secs = {s["section_id"]: s for s in m["sections"]}
        lines += ["", f"_Not on the agenda: {len(m['unplanned']) * WINDOW_MIN} minutes, starting "
                      f"{secs[m['unplanned'][0]]['t0'][:5]}._"]
    return "\n".join(lines + [NOTES_END])


def apply_to_notes(notes: str, block: str) -> str:
    """Replace the block if present, else append it; the rest of the notes is untouched."""
    if NOTES_START in notes and NOTES_END in notes:
        a, rest = notes.split(NOTES_START, 1)
        _, b = rest.split(NOTES_END, 1)
        return a + block + b
    return notes.rstrip() + "\n\n" + block + "\n"


def match_part(text: str, parts: list[dict], share: float = 0.5) -> dict | None:
    """The agenda part whose title words best overlap `text` (at least `share` of them)."""
    tw = _words(text)
    best, score = None, 0.0
    for p in parts:
        pw = _words(p.get("title") or p.get("agenda_item") or "")
        if pw:
            sc = len(pw & tw) / len(pw)
            if sc > score:
                best, score = p, sc
    return best if score >= share else None


def build(agenda_text: str, lines: list[dict], llm) -> dict:
    parts = parse_agenda(agenda_text)
    if not parts:
        return {"ok": False, "error": "no parts found in the agenda (expected lines like 'Part 1 · Title')"}
    secs = windows(lines)
    if not secs:
        return {"ok": False, "error": "no timed transcript lines"}
    voted, agree = vote(parts, secs, llm)
    labels = verify(parts, secs, smooth(secs, voted), llm)
    m = reconcile(parts, secs, labels)
    for s_ in m["sections"]:
        s_["agreement"] = agree.get(s_["section_id"])
    for r in m["parts"]:
        # how settled the label passes were over this part's windows, and its neighbours
        ids = r["section_ids"] or [x["section_id"] for x in m["sections"] if r["item_id"] in voted.get(x["section_id"], "")]
        vals = [agree[i] for i in ids if i in agree]
        r["agreement"] = round(sum(vals) / len(vals), 2) if vals else None
        r["uncertain"] = r["agreement"] is not None and r["agreement"] < 0.75
    return {"ok": True, **m}


def vote(parts: list[dict], secs: list[dict], llm, runs: int = 3) -> dict[str, str]:
    """Majority label per window over `runs` labelling passes (a local model is not fully
    repeatable even at temperature 0); no majority means 'none'."""
    passes = [label(parts, secs, llm) for _ in range(runs)]
    out, agree = {}, {}
    for s in secs:
        got = [p[s["section_id"]] for p in passes]
        top = max(set(got), key=got.count)
        out[s["section_id"]] = top if got.count(top) * 2 > runs else "none"
        agree[s["section_id"]] = round(got.count(top) / runs, 2)
    return out, agree
