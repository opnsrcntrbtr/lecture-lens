"""Capture health, silent-audio and no-frames alerts, and the gap ledger (CT-1).

Pure functions decide; small readers gather the values the engine leaves behind (when the
newest audio chunk was saved, its signal level, age of the newest kept frame) and, when the
engine answers, its own view (is audio arriving, is the frame loop healthy). Nothing here
starts, stops or restarts capture: the app is the only place that happens.

Audio freshness is when a chunk was *saved*, not when it started. The engine saves each 30-s
chunk 30-90 s after it starts (the 21 Nov class: up to 82 s), so measuring from the start
raised false 'audio stalled' alerts while audio was flowing, and stopping capture then lost
the minute still in memory.

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
AUDIO_STALE_S = 90          # no audio chunk saved for this long: the engine stopped writing
FRAME_STALE_S = 90          # no kept frame while frames are expected
STATIC_FRAME_S = 600        # with the engine's frame loop healthy, an unchanged screen keeps no
                            # new frame (duplicates are skipped): only this long is a problem
CHUNK_S = 30                # the engine's audio chunk length
UNSAVED_WARN_S = 120        # recorded audio not yet saved: a stop or restart would drop it
ALERT_AFTER = {"audio_behind": 180}   # per-status alert delays where ALERT_AFTER_S is too eager
SILENCE_DB = -90.0          # digital silence (all-zero samples) only; a quiet break is not silent
LATE_START_S = 5 * 60       # Spec C: calendar start to first capture
HOLE_S = 3 * 60             # Spec C: an internal hole
STATUSES = ("ok", "stopped", "audio_stalled", "audio_behind", "silent_audio", "no_frames")
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
          drm_paused: bool = False, engine: dict | None = None,
          audio_write_lag_s: float | None = None, audio_unsaved_s: float | None = None) -> dict:
    """One health record. Checked in order: stopped, audio stalled (or behind), silent audio,
    no frames. `audio_chunk_t` is when the newest chunk was saved. `engine` is
    engine_signals(): when it says audio is arriving, a slow save is 'audio_behind', not a
    stall; when its frame loop is healthy, an unchanged screen is not 'no_frames' until
    STATIC_FRAME_S. Frames are only expected while a meeting runs (and never while protected
    video pauses the screen); quiet speech is never 'silent': only digital silence is."""
    now = _utc(now)
    engine = engine or {}
    a_age = (now - _utc(audio_chunk_t)).total_seconds() if audio_chunk_t else None
    f_age = (now - _utc(frame_t)).total_seconds() if frame_t else None
    frame_limit = STATIC_FRAME_S if engine.get("frame_ok") else FRAME_STALE_S
    if not capture_running:
        status = "stopped"
    elif a_age is None or a_age > AUDIO_STALE_S:
        status = "audio_behind" if engine.get("audio_active") else "audio_stalled"
    elif audio_unsaved_s is not None and audio_unsaved_s > UNSAVED_WARN_S:
        status = "audio_behind"     # chunks still being saved, but minutes of audio wait in memory
    elif audio_level_db is not None and audio_level_db <= SILENCE_DB:
        status = "silent_audio"
    elif frames_expected and not drm_paused and (f_age is None or f_age > frame_limit):
        status = "no_frames"
    else:
        status = "ok"
    rec = {"t": now.isoformat(timespec="seconds"), "capture_running": bool(capture_running),
           "audio_chunk_age_s": None if a_age is None else round(a_age, 1),
           "audio_level_db": None if audio_level_db is None else round(audio_level_db, 1),
           "frame_age_s": None if f_age is None else round(f_age, 1),
           "frames_expected": bool(frames_expected and not drm_paused), "status": status}
    if audio_write_lag_s is not None:
        rec["audio_write_lag_s"] = round(audio_write_lag_s, 1)
    if audio_unsaved_s is not None:
        rec["audio_unsaved_s"] = round(max(0.0, audio_unsaved_s), 1)
    if "audio_active" in engine:
        rec["engine_audio_active"] = bool(engine["audio_active"])
    if "frame_ok" in engine:
        rec["engine_frame_ok"] = bool(engine["frame_ok"])
    return rec


def engine_signals(health: dict, now: dt.datetime) -> dict:
    """What the engine's /health says: audio_active (its audio device had activity within
    AUDIO_STALE_S) and frame_ok (its frame loop reports ok). Missing fields stay missing."""
    out: dict = {}
    if not health:
        return out
    m = re.search(r"active \(last activity: (\d+)s ago\)", str(health.get("device_status_details") or ""))
    if m:
        out["audio_active"] = int(m[1]) <= AUDIO_STALE_S
    elif health.get("last_audio_timestamp"):
        t = parse_t(health["last_audio_timestamp"])
        if t:
            out["audio_active"] = (_utc(now) - t).total_seconds() <= AUDIO_STALE_S
    if health.get("audio_db_write_stalled"):
        out["audio_active"] = False          # arriving but not being saved: a real stall
    if "frame_status" in health:
        out["frame_ok"] = health.get("frame_status") == "ok"
    return out


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
    if lasted < max(after_s, ALERT_AFTER.get(st, 0)):
        return None
    return {"status": st, "since": first["t"], "lasted_s": round(lasted), "message": MESSAGES[st]}


MESSAGES = {
    "audio_stalled": "No audio saved for over 90 seconds and the engine reports no audio: it may have stopped. "
                     "Check Capture before restarting: a restart drops the audio not yet saved.",
    "audio_behind": "Audio is arriving but is being saved slowly (over 90 seconds behind). Keep capturing: "
                    "stopping now would lose the unsaved minute. It usually catches up.",
    "silent_audio": "Audio is digital silence. If the class is playing, macOS may be refusing the audio tap: check "
                    "Screen & System Audio Recording for Lecture Lens. If nothing is playing, this is expected.",
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


def saved_t(path: Path) -> dt.datetime | None:
    """When a chunk file was last written (its save time), UTC."""
    try:
        return dt.datetime.fromtimestamp(Path(path).stat().st_mtime, dt.timezone.utc)
    except OSError:
        return None


def unsaved_s(newest: dict[str, tuple[Path, dt.datetime]], now: dt.datetime) -> float | None:
    """Seconds of recorded audio not saved yet: now minus the end of the newest saved chunk
    (the 21 Nov 11:04 case: chunks kept being saved, each one 4 minutes late)."""
    starts = [_utc(t) for _, t in newest.values()]
    return max(0.0, (_utc(now) - max(starts)).total_seconds() - CHUNK_S) if starts else None


def newest_saved(newest: dict[str, tuple[Path, dt.datetime]]) -> tuple[dt.datetime | None, float | None]:
    """From newest_chunks(): the newest save time over all devices, and that chunk's save lag
    (seconds after its 30-s span ended)."""
    best, lag = None, None
    for p, start in newest.values():
        w = saved_t(p)
        if w and (best is None or w > best):
            best, lag = w, max(0.0, (w - _utc(start)).total_seconds() - CHUNK_S)
    return best, lag


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
