"""Capture health, silent-audio and no-frames alerts, and the gap ledger (CT-1).

Pure functions decide; small readers gather the three values the engine leaves behind (age
of the newest audio chunk, that chunk's signal level, age of the newest kept frame). Nothing
here starts, stops or restarts capture: the app is the only place that happens.

  probe(...)          one record {t, capture_running, audio_chunk_age_s, audio_level_db,
                      frame_age_s, frames_expected, status}
  alert(records)      the failing status that has lasted ALERT_AFTER_S, or None
  gaps(...)           [{from, to, cause, filled}] from the calendar window, the audio-chunk
                      times and the health records
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

PROBE_EVERY_S = 30
ALERT_AFTER_S = 90          # a failure must last this long before anyone is told
AUDIO_STALE_S = 90          # no new audio chunk for this long: the engine stopped writing
FRAME_STALE_S = 90          # no kept frame while frames are expected
SILENCE_DB = -90.0          # digital silence (all-zero samples) only; a quiet break is not silent
LATE_START_S = 5 * 60       # Spec C: calendar start to first capture
HOLE_S = 3 * 60             # Spec C: an internal hole
STATUSES = ("ok", "stopped", "audio_stalled", "silent_audio", "no_frames")
CHUNK_NAME = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.(?:mp4|m4a|wav)$")


def _utc(t: dt.datetime | None) -> dt.datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def parse_t(s) -> dt.datetime | None:
    try:
        return _utc(dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ decisions (pure)
def probe(now: dt.datetime, capture_running: bool, audio_chunk_t: dt.datetime | None,
          audio_level_db: float | None, frame_t: dt.datetime | None, frames_expected: bool,
          drm_paused: bool = False) -> dict:
    """One health record. Checked in order: stopped, audio stalled, silent audio, no frames.
    Frames are only expected while a meeting runs (and never while protected video pauses
    the screen); quiet speech is never 'silent': only digital silence is."""
    now = _utc(now)
    a_age = (now - _utc(audio_chunk_t)).total_seconds() if audio_chunk_t else None
    f_age = (now - _utc(frame_t)).total_seconds() if frame_t else None
    if not capture_running:
        status = "stopped"
    elif a_age is None or a_age > AUDIO_STALE_S:
        status = "audio_stalled"
    elif audio_level_db is not None and audio_level_db <= SILENCE_DB:
        status = "silent_audio"
    elif frames_expected and not drm_paused and (f_age is None or f_age > FRAME_STALE_S):
        status = "no_frames"
    else:
        status = "ok"
    return {"t": now.isoformat(timespec="seconds"), "capture_running": bool(capture_running),
            "audio_chunk_age_s": None if a_age is None else round(a_age, 1),
            "audio_level_db": None if audio_level_db is None else round(audio_level_db, 1),
            "frame_age_s": None if f_age is None else round(f_age, 1),
            "frames_expected": bool(frames_expected and not drm_paused), "status": status}


def alert(records: list[dict], after_s: float = ALERT_AFTER_S) -> dict | None:
    """The failure the newest records share, once it has lasted `after_s` (measured from the
    first record of the unbroken run to the newest). 'stopped' is the late-start reminder's
    business, not an alert."""
    if not records:
        return None
    last = records[-1]
    st = last["status"]
    if st in ("ok", "stopped"):
        return None
    first = last
    for r in reversed(records):
        if r["status"] != st:
            break
        first = r
    lasted = (parse_t(last["t"]) - parse_t(first["t"])).total_seconds()
    if lasted < after_s:
        return None
    return {"status": st, "since": first["t"], "lasted_s": round(lasted), "message": MESSAGES[st]}


MESSAGES = {
    "audio_stalled": "No new audio for over 90 seconds: the engine may have stopped writing. Check Capture.",
    "silent_audio": "Audio is digital silence: macOS may be refusing the audio tap. Check Screen & System Audio "
                    "Recording for Lecture Lens.",
    "no_frames": "No screen frames kept for over 90 seconds while the meeting runs. Keep the meeting window "
                 "visible; check Screen & System Audio Recording.",
}


def late_start_due(now: dt.datetime, start: dt.datetime, capture_running: bool, sent: int,
                   first_after_s: int = 60, every_s: int = 60, until_s: int = 600) -> bool:
    """The late-start reminder: from `first_after_s` after the scheduled start, every
    `every_s`, for `until_s`, while capture is not running. `sent` is how many went out."""
    if capture_running:
        return False
    since = (_utc(now) - _utc(start)).total_seconds()
    if since < first_after_s or since > until_s:
        return False
    return sent < 1 + int((since - first_after_s) // every_s)


def gaps(cal_start: dt.datetime, cal_end: dt.datetime, chunk_times: list[dt.datetime],
         health: list[dict] | None = None, filled: list[dict] | None = None) -> list[dict]:
    """Holes in a class's capture, Spec C thresholds: calendar start to the first audio chunk
    over 5 minutes (late_start); a hole between chunks over 3 minutes (cause from the health
    records inside it: silent_audio, engine_stopped, else unknown); last chunk to the
    calendar end over 3 minutes (engine_stopped). Audio chunks are written through silence,
    so a quiet break is not a hole."""
    s, e = _utc(cal_start), _utc(cal_end)
    ts = sorted(_utc(t) for t in chunk_times if t and s - dt.timedelta(minutes=30) <= _utc(t) <= e)
    out = []

    def cause_in(a, b):
        hs = [h for h in (health or []) if a <= parse_t(h["t"]) <= b]
        if any(h["status"] == "silent_audio" for h in hs):
            return "silent_audio"
        if any(h["status"] in ("audio_stalled", "stopped") for h in hs):
            return "engine_stopped"
        return "unknown"

    def add(a, b, cause):
        f = any(parse_t(x.get("from")) <= a and b <= parse_t(x.get("to")) for x in (filled or []))
        out.append({"from": a.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
                    "to": b.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
                    "minutes": round((b - a).total_seconds() / 60, 1), "cause": cause, "filled": f})

    if not ts:
        add(s, e, "late_start")
        return out
    if (ts[0] - s).total_seconds() > LATE_START_S:
        add(s, ts[0], "late_start")
    for a, b in zip(ts, ts[1:]):
        if a >= s and (b - a).total_seconds() > HOLE_S:
            add(a, b, cause_in(a, b))
    if (e - ts[-1]).total_seconds() > HOLE_S and ts[-1] >= s:
        add(ts[-1], e, "engine_stopped")
    return out


# ------------------------------------------------------------------ readers (engine outputs)
def chunk_time(path: Path | str) -> dt.datetime | None:
    """Time in an audio chunk's file name ('System Audio (output)_2026-11-16_11-26-01.mp4', UTC)."""
    m = CHUNK_NAME.search(str(path))
    if not m:
        return None
    return dt.datetime.fromisoformat(f"{m[1]}T{m[2]}:{m[3]}:{m[4]}+00:00")


def chunk_times(data_dir: Path, since: dt.datetime | None = None) -> list[dt.datetime]:
    out = []
    for p in Path(data_dir).iterdir() if Path(data_dir).exists() else []:
        t = chunk_time(p.name)
        if t and (since is None or t >= since):
            out.append(t)
    return sorted(out)


def newest_chunk(data_dir: Path) -> tuple[Path | None, dt.datetime | None]:
    best = (None, None)
    for p in Path(data_dir).iterdir() if Path(data_dir).exists() else []:
        t = chunk_time(p.name)
        if t and (best[1] is None or t > best[1]):
            best = (p, t)
    return best


def newest_chunks(data_dir: Path) -> dict[str, tuple[Path, dt.datetime]]:
    """The newest chunk of each audio device ('App Tap (output)', 'System Audio (output)', ...)."""
    best: dict[str, tuple[Path, dt.datetime]] = {}
    for p in Path(data_dir).iterdir() if Path(data_dir).exists() else []:
        t = chunk_time(p.name)
        if not t:
            continue
        dev = p.name[: CHUNK_NAME.search(p.name).start()]
        if dev not in best or t > best[dev][1]:
            best[dev] = (p, t)
    return best


def combined_level(levels: list[float | None]) -> float | None:
    """The loudest device decides: in a break the meeting app's own tap can be all zeros
    while system audio still carries room tone, which is not silence."""
    vals = [v for v in levels if v is not None]
    return max(vals) if vals else None


def level_db(path: Path, ffmpeg: str = "ffmpeg", timeout: int = 20) -> float | None:
    """Peak level of an audio file in dB (ffmpeg volumedetect); -inf (all zeros) as -120."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"max_volume:\s*(-?inf|-?[\d.]+) dB", r.stderr)
    if not m:
        return None
    return -120.0 if "inf" in m[1] else float(m[1])


def append(path: Path, record: dict, keep: int = 2000) -> list[dict]:
    """Append a record to capture_health.jsonl and return the recent records (oldest first)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    rows.append(record)
    rows = rows[-keep:]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows
