"""CT-6 gap fill: fill a late start or a hole in a lecture's transcript from the class
recording's audio, re-decoded with whisper, and say how trustworthy each filled time is.

Spec C's rules stand: existing lines and their times never change; filled lines are a
separate source (transcript_pre.jsonl, and transcript_pre.md to read) that builds use beside
the transcript; recordings contribute audio only, so a DRM page's player is fine; every
filled line passes the caller's name guard before it is written.

How the recording is placed on the class clock:
1. Coarse offset. Short probes of the recording are decoded, nearest the hole first
   (assuming the recording began with the class), then every 5 minutes, until three-word
   anchors agree on one offset. An anchor is a word triple found once in the probe and once
   in the transcript.
2. Fine offset. The hole plus a margin each side is decoded with word times. Anchors before
   and after the hole give one offset per side. With both, the offset is interpolated across
   the hole (that absorbs a small clock-rate difference); with one side it is constant.
3. Cut. The decoded words are lined up by word order with the last words before the hole and
   the first words after it, and only the words in between are kept, so nothing already in
   the transcript is written twice. When nothing lines up, the hole's times decide.
4. Confidence. Each line gets align_conf in [0, 1] from the anchors' spread and count and
   whether both sides are anchored; below 0.7 the app shows it as "approximate time".

Pure functions plus `fill_lecture` (files). The transcriber is injectable for tests:
transcribe(recording, start_s, dur_s) -> [{w, t, t1}] with t in seconds from the start of
the recording.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import statistics
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Callable

norm = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that", "this", "so", "we", "you", "i",
        "be", "are", "for", "on", "with", "as", "what", "like", "know", "right", "um", "uh", "okay", "yeah"}
APPROX_BELOW = 0.7
ENGINE = "whisper.cpp large-v3-turbo q8 (gap fill)"


# ------------------------------------------------------------------ words and anchors
def line_words(lines: list[tuple[float, str]], wps: float = 2.6) -> list[dict]:
    """Word times for transcript lines that only carry a start time: spread evenly to the next
    line's start when the words would fill it at 1.6 words a second or more (continuous
    speech), else at `wps` words a second from the line's start."""
    out = []
    lines = sorted(lines)
    for i, (t, text) in enumerate(lines):
        ws = text.split()
        if not ws:
            continue
        nxt = lines[i + 1][0] if i + 1 < len(lines) else t + len(ws) / wps
        span = nxt - t
        dur = max(0.5, span if span <= len(ws) / 1.6 else len(ws) / wps)
        step = dur / len(ws)
        out += [{"w": w, "t": t + k * step, "t1": t + (k + 1) * step} for k, w in enumerate(ws)]
    return out


def anchors(dec: list[dict], ref: list[dict]) -> list[tuple[float, float]]:
    """(decoded time, transcript time) for each word triple found exactly once in both."""
    dn, rn = [norm(w["w"]) for w in dec], [norm(w["w"]) for w in ref]
    grams = lambda n: [tuple(n[i:i + 3]) for i in range(len(n) - 2)]
    dc, rc = Counter(grams(dn)), Counter(grams(rn))
    rpos = {g: i for i, g in enumerate(grams(rn)) if rc[g] == 1}
    out = []
    for i, g in enumerate(grams(dn)):
        if dc[g] == 1 and g in rpos and all(g) and not all(x in STOP for x in g):
            out.append((dec[i]["t"], ref[rpos[g]]["t"]))
    return out


def consensus(pairs: list[tuple[float, float]], tol: float = 6.0, min_n: int = 3) -> dict | None:
    """The offset (transcript minus recording time) most anchors agree on, within `tol`:
    {offset, n, mad, center} or None when fewer than `min_n` agree."""
    if len(pairs) < min_n:
        return None
    offs = [r - d for d, r in pairs]
    best = max(offs, key=lambda o: sum(1 for x in offs if abs(x - o) <= tol))
    inl = [(d, r) for d, r in pairs if abs((r - d) - best) <= tol]
    if len(inl) < min_n:
        return None
    o = statistics.median(r - d for d, r in inl)
    inl = [(d, r) for d, r in pairs if abs((r - d) - o) <= tol]
    o = statistics.median(r - d for d, r in inl)
    mad = statistics.median(abs((r - d) - o) for d, r in inl)
    return {"offset": o, "n": len(inl), "mad": mad, "center": statistics.median(d for d, _ in inl)}


def holes_in(words: list[dict], frm: float, to: float, min_s: float = 60.0) -> list[tuple[float, float]]:
    """Spans inside [frm, to] with no transcript word for at least `min_s`: (start, end), where
    start is the end of the last word before (or frm) and end the next word (or to)."""
    inside = sorted((w["t"], w.get("t1", w["t"])) for w in words if frm <= w["t"] < to)
    before = [w.get("t1", w["t"]) for w in words if w["t"] < frm]
    after = [w["t"] for w in words if w["t"] >= to]
    a = max(before) if before and max(before) > frm - min_s else frm
    out = []
    for t, t1 in inside:
        if t - a >= min_s:
            out.append((a, t))
        a = max(a, t1)
    b = min(after) if after and min(after) < to + min_s else to
    if b - a >= min_s:
        out.append((a, b))
    return out


# ------------------------------------------------------------------ placing and cutting
def coarse_offset(transcribe, recording, rec_len: float, ref: list[dict], guess: float, hole: tuple[float, float],
                  probe_s: float = 60.0, step_s: float = 300.0, log=lambda *a: None) -> dict | None:
    """Offset of the recording on the class clock from short probes (see module doc)."""
    a, b = hole
    first = [a - probe_s - 30 - guess, b + 30 - guess]
    grid = [k * step_s for k in range(int(max(0.0, rec_len - probe_s) // step_s) + 1)]
    grid.sort(key=lambda p: min(abs(p - x) for x in first))
    tried = []
    for p in first + grid:
        p = max(0.0, min(p, max(0.0, rec_len - probe_s)))
        if any(abs(p - q) < probe_s / 2 for q in tried):
            continue
        tried.append(p)
        c = consensus(anchors(transcribe(recording, p, probe_s), ref))
        log(f"gapfill: probe at {p:.0f} s: " + (f"offset {c['offset']:.1f} s from {c['n']} anchors" if c else "no agreement"))
        if c:
            return c
        if len(tried) >= 12:
            break
    return None


def place(dec: list[dict], ref: list[dict], hole: tuple[float, float], coarse: float, margin: float) -> dict:
    """Fine offsets from anchors on each side of the hole; returns {map, left, right, jump}."""
    a, b = hole
    left_ref = [w for w in ref if a - margin - 60 <= w["t"] <= a + 1]
    right_ref = [w for w in ref if b - 1 <= w["t"] <= b + margin + 60]
    left = consensus(anchors([w for w in dec if w["t"] + coarse <= a + 30], left_ref)) if left_ref else None
    right = consensus(anchors([w for w in dec if w["t"] + coarse >= b - 30], right_ref)) if right_ref else None
    jump = bool(left and right and abs(left["offset"] - right["offset"]) > 20)

    def mapping(t):
        if left and right and not jump and right["center"] > left["center"]:
            f = min(1.0, max(0.0, (t - left["center"]) / (right["center"] - left["center"])))
            return t + left["offset"] + f * (right["offset"] - left["offset"])
        if left and (not right or jump):
            return t + left["offset"]
        if right:
            return t + right["offset"]
        return t + coarse
    return {"map": mapping, "left": left, "right": right, "jump": jump}


def cut(mapped: list[dict], ref: list[dict], hole: tuple[float, float], n_ctx: int = 40) -> list[dict]:
    """The decoded words that belong in the hole: after the last word matching the transcript
    before it, before the first word matching the transcript after it (by word order)."""
    a, b = hole
    tail = [w for w in ref if w["t"] < a + 1][-n_ctx:]
    head = [w for w in ref if w["t"] >= b - 1][:n_ctx]
    start_i = end_i = None
    if tail:
        zone = [i for i, w in enumerate(mapped) if w["t"] < a + 30]
        ta, tb = [norm(w["w"]) for w in tail], [norm(mapped[i]["w"]) for i in zone]
        bl = [m for m in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_matching_blocks()
              if m.size >= 2 and m.a + m.size >= len(ta) - 3]
        if bl:
            m = max(bl, key=lambda m: m.b + m.size)
            start_i = zone[m.b + m.size - 1] + 1 + (len(ta) - (m.a + m.size))
    if head:
        zone = [i for i, w in enumerate(mapped) if w["t"] > b - 30]
        ha, hb = [norm(w["w"]) for w in head], [norm(mapped[i]["w"]) for i in zone]
        bl = [m for m in difflib.SequenceMatcher(None, ha, hb, autojunk=False).get_matching_blocks()
              if m.size >= 2 and m.a <= 3]
        if bl:
            m = min(bl, key=lambda m: m.b)
            end_i = zone[m.b] - m.a
    if start_i is None:      # with nothing before (a late start) a little slack costs no duplicate
        start_i = next((i for i, w in enumerate(mapped) if w["t"] >= (a if tail else a - 2.0)), len(mapped))
    if end_i is None:
        end_i = next((i for i, w in enumerate(mapped) if w["t"] >= b), len(mapped))
    return mapped[max(0, start_i):max(0, end_i)]


def confidence(pl: dict, t: float) -> float:
    """align_conf for a filled time: 1 minus an estimated error over 10 s."""
    l, r = pl["left"], pl["right"]
    if not l and not r:
        return 0.0
    sides = [s for s in (l, r) if s]
    e = 1.4826 * max(s["mad"] for s in sides)
    if sum(s["n"] for s in sides) < 6:
        e += 2.0
    if len(sides) == 1:       # constant offset: a clock-rate difference grows with distance
        e += 0.004 * abs(t - (pl["map"](sides[0]["center"])))
    if pl["jump"]:
        e += 10.0
    return round(max(0.0, min(1.0, 1 - e / 10)), 2)


def lines_from_words(words: list[dict], gap_s: float = 0.8, max_words: int = 25) -> list[list[dict]]:
    out, cur = [], []
    for w in words:
        if cur and (w["t"] - cur[-1].get("t1", cur[-1]["t"]) > gap_s or len(cur) >= max_words
                    or re.search(r"[.?!]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def fill(existing: list[dict], frm: float, to: float, recording, rec_len: float,
         transcribe: Callable, guess_offset: float, margin: float = 90.0, log=lambda *a: None) -> dict:
    """Fill [frm, to] (class clock, epoch seconds) from a recording. `existing` are the
    transcript's words ({w, t}). Returns {holes: [...], lines: [{t, t1, text, align_conf, approx,
    words, rec_t}], error?}."""
    holes = holes_in(existing, frm, to)
    if not holes:
        return {"holes": [], "lines": [], "error": "nothing missing in that window"}
    coarse = coarse_offset(transcribe, recording, rec_len, existing, guess_offset, holes[0], log=log)
    if not coarse:
        return {"holes": holes, "lines": [], "error": "the recording does not line up with the transcript (no agreeing anchors)"}
    out, info = [], []
    for a, b in holes:
        r0 = max(0.0, a - margin - coarse["offset"])
        r1 = min(rec_len, b + margin - coarse["offset"])
        if r1 <= r0:
            info.append({"from": a, "to": b, "error": "outside the recording"})
            continue
        dec = transcribe(recording, r0, r1 - r0)
        pl = place(dec, existing, (a, b), coarse["offset"], margin)
        mapped = [{"w": w["w"], "t": pl["map"](w["t"]), "t1": pl["map"](w.get("t1", w["t"])), "rec_t": w["t"]} for w in dec]
        kept = cut(mapped, existing, (a, b))
        for ln in lines_from_words(kept):
            c = confidence(pl, ln[0]["t"])
            out.append({"t": ln[0]["t"], "t1": ln[-1]["t1"], "text": " ".join(w["w"] for w in ln),
                        "align_conf": c, "approx": c < APPROX_BELOW, "rec_t": round(ln[0]["rec_t"], 2),
                        "wt": [round(w["t"] - ln[0]["t"], 1) for w in ln], "hole": [a, b]})
        side = lambda s: {"offset_s": round(s["offset"], 2), "anchors": s["n"], "mad_s": round(s["mad"], 2)} if s else None
        info.append({"from": a, "to": b, "words": len(kept), "left": side(pl["left"]), "right": side(pl["right"]),
                     "jump": pl["jump"]})
    return {"holes": info, "lines": out, "coarse_offset_s": round(coarse["offset"], 2), "coarse_anchors": coarse["n"]}


# ------------------------------------------------------------------ audio
def media_length(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip() or 0)


def whisper_transcribe(recording: Path, start_s: float, dur_s: float) -> list[dict]:
    """Word-level whisper over one window of a recording (audio track only)."""
    import live_asr
    with tempfile.TemporaryDirectory() as td:
        cut_f = Path(td) / "w.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}", "-t", f"{dur_s:.3f}", "-i", str(recording),
                        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(cut_f)], check=True, timeout=600)
        words, _ = live_asr.whisper_words(cut_f, None, 0.0, timeout=max(300, int(dur_s)))
    return [{"w": w["w"], "t": start_s + w["t"], "t1": start_s + w["t1"]} for w in words]


def replay_audio(data_dir: Path, t0: dt.datetime, t1: dt.datetime, out: Path,
                 device: str = "System Audio (output)") -> tuple[Path, dt.datetime]:
    """Join the engine's audio chunks for [t0, t1) (a recording replayed on this Mac) into one
    file; returns it and the first chunk's start time."""
    import live_asr
    picked = []
    for p in sorted(Path(data_dir).glob(f"{device}_*.mp4")):
        t = live_asr.file_start(p.name)
        if t and t0 <= t < t1:
            picked.append((t, p))
    if not picked:
        raise FileNotFoundError("no audio chunks in that window")
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for _, p in picked))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-vn", "-c:a", "aac",
                    "-b:a", "64k", str(out)], check=True, timeout=900)
    lst.unlink()
    return out, picked[0][0]


# ------------------------------------------------------------------ files
PRE_JSONL, PRE_MD = "transcript_pre.jsonl", "transcript_pre.md"


def read_pre(folder: Path) -> list[dict]:
    f = Path(folder) / PRE_JSONL
    return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds")


def write_pre(folder: Path, key: str, start: dt.datetime, result: dict, frm: float, to: float, source: str,
              guard: Callable[[list[str]], list[str]]) -> list[dict]:
    """Replace the filled lines for [frm, to] in transcript_pre.jsonl (others are kept) and
    rewrite transcript_pre.md. Texts pass `guard` first. Returns the new rows."""
    folder = Path(folder)
    keep = [r for r in read_pre(folder) if not (frm <= r["t_epoch"] < to)]
    n = max([int(str(r["line_id"]).rsplit("pre-", 1)[-1]) for r in read_pre(folder)] + [-1]) + 1
    texts = guard([ln["text"] for ln in result["lines"]])
    rows = []
    for ln, text in zip(result["lines"], texts):
        if not text.strip():
            continue
        rows.append({"line_id": f"{key}/t/pre-{n}", "t": _iso(ln["t"]), "t1": _iso(ln["t1"]), "t_epoch": round(ln["t"], 2),
                     "text": text, "source": "gapfill", "align_conf": ln["align_conf"], "approx": ln["approx"],
                     "engine": ENGINE, "recording": source, "rec_t": ln["rec_t"], "wt": ln["wt"],
                     "gap": [_iso(frm), _iso(to)]})
        n += 1
    allr = sorted(keep + rows, key=lambda r: r["t_epoch"])
    (folder / PRE_JSONL).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in allr))
    hms = lambda s: "%02d:%02d:%02d" % (max(0, s) // 3600, max(0, s) % 3600 // 60, max(0, s) % 60)
    md = ["# Filled from the recording", "",
          "_Lines the live transcript missed, re-transcribed from the class recording (audio only). "
          "Times are offsets from the class start, placed by matching words either side of the gap; "
          "≈ marks an approximate time._", ""]
    for r in allr:
        off = int(r["t_epoch"] - start.timestamp())
        md.append(f"[{hms(off)}] {'≈ ' if r['approx'] else ''}{r['text']}")
    (folder / PRE_MD).write_text("\n".join(md) + "\n")
    return rows


def source_lines(folder: Path, start: dt.datetime) -> list[str]:
    """Filled lines for a notes source, in transcript.md's "[HH:MM:SS] text" shape, labelled."""
    out = []
    for r in read_pre(folder):
        off = max(0, int(r["t_epoch"] - start.timestamp()))
        tag = "(from the recording, approximate time)" if r.get("approx") else "(from the recording)"
        out.append("[%02d:%02d:%02d] %s %s" % (off // 3600, off % 3600 // 60, off % 60, tag, r["text"]))
    return out
