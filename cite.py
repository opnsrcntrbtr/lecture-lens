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
