"""Citations for an answer shown in the app: the public sources it names (evidence bank)
and the lecture moments that support it, i.e. transcript lines near the question's time
that share the most content words with the question and answer, plus the slide on screen
then. Deterministic: no model call. Attendee names are removed from the lines shown."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

STOP = {"that", "this", "with", "from", "have", "what", "when", "which", "should", "would", "could", "about",
        "there", "their", "they", "them", "then", "than", "into", "your", "will", "were", "been", "does", "also",
        "only", "more", "most", "some", "such", "each", "very", "just", "like", "right", "okay", "yeah", "know",
        "think", "going", "want", "need", "make", "here", "where", "other", "these", "those", "said", "says"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{3,}", text.lower()) if w not in STOP}


def _norm_token(w: str) -> str:
    """Lightweight stdlib normalization: strip hyphens and regular English plurals."""
    w = w.lower().replace("-", "")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("sses"):
        return w[:-2]
    if len(w) >= 4 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def norm_words(text: str) -> set[str]:
    """Normalized content words and numeric tokens for citation grounding."""
    raw = re.findall(r"[a-z][a-z0-9\-]{2,}|\b\d+(?:\.\d+)?\b", (text or "").lower())
    out = set()
    for w in raw:
        if w in STOP:
            continue
        nw = _norm_token(w)
        if nw not in STOP and (len(nw) >= 3 or nw[0].isdigit()):
            out.add(nw)
    return out


def parse_timecode_s(tc: str) -> tuple[float | None, bool]:
    """Parse HH:MM:SS or MM:SS into (seconds, valid_components)."""
    parts = tc.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None, False
    if len(nums) == 3:
        h, m, s = nums
        return float(h * 3600 + m * 60 + s), (0 <= m < 60 and 0 <= s < 60 and h >= 0)
    if len(nums) == 2:
        m, s = nums
        return float(m * 60 + s), (0 <= s < 60 and m >= 0)
    return None, False


def parse_t(s: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def moments(segments: list[dict], at: dt.datetime, text: str, window_min: float = 10, k: int = 3,
            min_shared: int = 2) -> list[dict]:
    """Top k lines within the window sharing at least min_shared content words with text,
    best first, ties to the nearer line; each with the shared words."""
    want = words(text)
    lo, hi = at - dt.timedelta(minutes=window_min), at + dt.timedelta(minutes=window_min)
    scored = []
    for s in segments:
        t = parse_t(s.get("t", ""))
        if t is None or not (lo <= t <= hi):
            continue
        shared = want & words(s.get("text", ""))
        if len(shared) >= min_shared:
            scored.append((len(shared), -abs((t - at).total_seconds()), t, s["text"], sorted(shared)))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [{"t": t.isoformat(), "text": txt, "shared": sh} for _, _, t, txt, sh in scored[:k]]


def slide_at(slides: list[dict], at: dt.datetime) -> dict | None:
    """The last slide shown at or before the time (whiteboards and breaks skipped)."""
    best = None
    for s in slides:
        t = parse_t(s.get("t", ""))
        if t is not None and t <= at and (s.get("kind") or "slide") == "slide":
            if best is None or t >= best[0]:
                best = (t, s)
    return {"t": best[0].isoformat(), "title": best[1].get("title", "")} if best else None


def sources(ids: list[str], bank: list[dict]) -> list[dict]:
    by = {e["id"]: e for e in bank}
    return [{k: by[i].get(k) for k in ("id", "title", "fact", "url", "date")} for i in ids if i in by]


def lecture_segments(lectures: Path, at: dt.datetime) -> list[dict]:
    """Absolute-time lines from a built lecture whose span holds the time (for days without
    a live folder): transcript.md timecodes are offsets from the manifest's start."""
    for man in lectures.glob("*/manifest.json"):
        try:
            m = json.loads(man.read_text())
            start = parse_t(m["start"])
            end = start + dt.timedelta(minutes=float(m.get("minutes") or 0))
        except Exception:
            continue
        if not (start <= at <= end):
            continue
        tr = man.parent / ("transcript_merged.md" if (man.parent / "transcript_merged.md").exists() else "transcript.md")
        out = []
        for line in tr.read_text().splitlines() if tr.exists() else []:
            mm = re.match(r"^\[(\d+):(\d\d):(\d\d)\]\s*(.*)", line)
            if mm:
                off = int(mm.group(1)) * 3600 + int(mm.group(2)) * 60 + int(mm.group(3))
                out.append({"t": (start + dt.timedelta(seconds=off)).isoformat(), "text": mm.group(4)})
        return out
    return []


_RE_TIMECODE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
_RE_SLIDE = re.compile(r"\[slide\s+(\d+)\]", re.I)
_RE_BRACKET_TAG = re.compile(r"\[[^\]]+\]")


def _timed_segments(transcript_segments: list[dict], start: dt.datetime | None = None) -> list[tuple[float, set[str]]]:
    """Normalize transcript segments into (offset_s, norm_words) pairs."""
    raw: list[tuple[float | None, dt.datetime | None, str]] = []
    earliest: dt.datetime | None = start
    for seg in transcript_segments or []:
        txt = str(seg.get("text") or "")
        if "offset_s" in seg and seg["offset_s"] is not None:
            raw.append((float(seg["offset_s"]), None, txt))
        elif seg.get("at"):
            sec, ok = parse_timecode_s(str(seg["at"]))
            raw.append((sec if ok else None, None, txt))
        elif seg.get("t"):
            t = parse_t(str(seg["t"]))
            if t is not None and (earliest is None or t < earliest):
                earliest = t
            raw.append((None, t, txt))
    out: list[tuple[float, set[str]]] = []
    for off, t, txt in raw:
        if off is None and t is not None and earliest is not None:
            off = (t - earliest).total_seconds()
        if off is not None:
            out.append((off, norm_words(txt)))
    return out


def _sentence_around(line: str, pos: int) -> str:
    """Return the sentence/clause around character index `pos`, falling back to `line`."""
    lo = max(line.rfind(". ", 0, pos), line.rfind("! ", 0, pos), line.rfind("? ", 0, pos))
    lo = 0 if lo == -1 else lo + 2
    hi_cands = [i for i in (line.find(". ", pos), line.find("! ", pos), line.find("? ", pos)) if i != -1]
    hi = min(hi_cands) + 1 if hi_cands else len(line)
    frag = line[lo:hi].strip()
    return frag if norm_words(_RE_BRACKET_TAG.sub(" ", frag)) else line


def verify_citations(
    text: str,
    valid_slides: list[dict] | int,
    transcript_segments: list[dict],
    min_shared: int = 1,
    duration_s: float | None = None,
    window_s: float = 90.0,
    start: dt.datetime | None = None,
) -> list[dict]:
    """Deterministic citation linter (pure stdlib).

    Checks every [HH:MM:SS] / [MM:SS] and [slide N] reference in `text`:
    - Out-of-range timestamps (outside [0, duration_s] or invalid mm/ss).
    - Out-of-range slide numbers (outside 1..len(valid_slides)).
    - Grounding: the citing sentence/bullet must share >= `min_shared` normalized
      content/numeric tokens with transcript lines within ±window_s (or with
      the cited slide's title/description/screen_text).
    """
    warnings: list[dict] = []
    timed = _timed_segments(transcript_segments, start=start)
    eff_dur = duration_s
    if eff_dur is None and timed:
        eff_dur = max(off for off, _ in timed) + window_s

    is_slide_list = isinstance(valid_slides, list)
    n_slides = len(valid_slides) if is_slide_list else int(valid_slides or 0)

    for line_no, line in enumerate((text or "").splitlines(), 1):
        for m in _RE_TIMECODE.finditer(line):
            tc = m.group(1)
            sec, valid_parts = parse_timecode_s(tc)
            if not valid_parts or sec is None or sec < 0 or (eff_dur is not None and sec > eff_dur):
                warnings.append({
                    "kind": "timestamp_out_of_range",
                    "ref": m.group(0),
                    "at_s": sec,
                    "line": line_no,
                    "reason": f"timestamp {m.group(0)} outside lecture range",
                })
                continue
            if min_shared > 0 and timed:
                ctx = _RE_BRACKET_TAG.sub(" ", _sentence_around(line, m.start()))
                ctx_w = norm_words(ctx)
                if ctx_w:
                    near_w: set[str] = set()
                    for off, sw in timed:
                        if abs(off - sec) <= window_s:
                            near_w |= sw
                    if len(ctx_w & near_w) < min_shared:
                        warnings.append({
                            "kind": "ungrounded_timestamp",
                            "ref": m.group(0),
                            "at_s": sec,
                            "line": line_no,
                            "reason": f"no shared content words within ±{int(window_s)}s of {m.group(0)}",
                        })

        for m in _RE_SLIDE.finditer(line):
            n = int(m.group(1))
            if n < 1 or n > n_slides:
                warnings.append({
                    "kind": "slide_out_of_range",
                    "ref": m.group(0),
                    "slide": n,
                    "line": line_no,
                    "reason": f"{m.group(0)} out of range (1..{n_slides})",
                })
                continue
            if min_shared > 0 and is_slide_list:
                sl = valid_slides[n - 1]
                sl_txt = " ".join(str(sl.get(k) or "") for k in ("title", "description", "screen_text", "text"))
                sl_w = norm_words(sl_txt)
                ctx = _RE_BRACKET_TAG.sub(" ", _sentence_around(line, m.start()))
                ctx_w = norm_words(ctx)
                if sl_w and ctx_w and len(ctx_w & sl_w) < min_shared:
                    warnings.append({
                        "kind": "ungrounded_slide",
                        "ref": m.group(0),
                        "slide": n,
                        "line": line_no,
                        "reason": f"{m.group(0)} shares fewer than {min_shared} content words with slide {n}",
                    })

    return warnings
