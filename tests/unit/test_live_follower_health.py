import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import live_class as lc  # noqa: E402


def _now():
    return dt.datetime.now(dt.timezone.utc)


def test_take_sync_consumes_request(tmp_path):
    assert lc.take_sync(tmp_path) == set()
    (tmp_path / "sync_request.json").write_text(json.dumps({"what": ["questions", "bogus"]}))
    assert lc.take_sync(tmp_path) == {"questions"}
    assert not (tmp_path / "sync_request.json").exists()
    (tmp_path / "sync_request.json").write_text(json.dumps({"what": "all"}))
    assert lc.take_sync(tmp_path) == lc.SYNC_KINDS
    (tmp_path / "sync_request.json").write_text("not json")
    assert lc.take_sync(tmp_path) == set()


def test_fresh_chunk_uses_file_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "DATA", tmp_path)
    assert not lc._fresh_chunk(180)
    f = tmp_path / "System Audio (output)_2030-01-01_05-00-00.mp4"
    f.write_bytes(b"x")
    assert lc._fresh_chunk(180)
    old = time.time() - 600
    os.utime(f, (old, old))
    assert not lc._fresh_chunk(180)
    (tmp_path / "MacBook Pro Microphone (input)_x.mp4").write_bytes(b"x")   # mic files don't count
    assert not lc._fresh_chunk(180)


def test_audio_ok_and_healthy_without_microphone(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "DATA", tmp_path)            # no chunk files: rely on the timestamp
    fresh = {"frame_status": "ok", "audio_status": "no_input_device", "last_audio_timestamp": _now().isoformat()}
    stale = dict(fresh, last_audio_timestamp=(_now() - dt.timedelta(minutes=30)).isoformat())
    assert lc.audio_ok({"audio_status": "ok"})
    assert lc.audio_ok(fresh) and lc.healthy(fresh)
    assert not lc.audio_ok(stale) and not lc.healthy(stale)
    (tmp_path / "App Tap (output)_x.mp4").write_bytes(b"x")  # a fresh chunk wins over a lagging timestamp
    assert lc.audio_ok(stale) and lc.healthy(stale)
    assert not lc.healthy(dict(stale, frame_status="stale"))
    assert not lc.audio_ok({})


def test_window_ready_waits_for_transcript():
    now = _now()
    to = now - dt.timedelta(minutes=5)
    assert not lc.window_ready([], to, now)
    assert not lc.window_ready([{"t": (to - dt.timedelta(seconds=30)).isoformat()}], to, now)
    assert lc.window_ready([{"t": (to + dt.timedelta(seconds=1)).isoformat()}], to, now)
    assert lc.window_ready([], now - dt.timedelta(minutes=30), now)   # too late to keep waiting
