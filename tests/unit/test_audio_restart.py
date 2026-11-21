"""lecture audio-restart: waits for recorded audio to be saved, then restarts audio only."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as lk  # noqa: E402

UTC = dt.timezone.utc


class Clock:
    def __init__(self):
        self.t = dt.datetime(2030, 1, 5, 5, 0, tzinfo=UTC)

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += dt.timedelta(seconds=s)


def setup(monkeypatch, starts, posts):
    """starts(now) -> newest chunk start; posts collects the API calls."""
    clock = Clock()
    monkeypatch.setattr(lk, "_mode_state", lambda: {"running": True})
    monkeypatch.setattr(lk.capture_health, "newest_chunks",
                        lambda d: {"System Audio (output)": (Path("x.mp4"), starts(clock.t))})
    monkeypatch.setattr(lk, "_sp_post", lambda path, timeout=20: posts.append(path) or (True, ""))
    return clock


def test_waits_for_saving_to_catch_up_then_restarts_audio_only(monkeypatch):
    posts = []
    t_restart = {}
    def starts(now):
        # saving is 4 minutes behind and catches up 60 s later; after the restart chunks are fresh
        if "/audio/start" in posts:
            return now - dt.timedelta(seconds=35)
        return now - dt.timedelta(seconds=270 if now < dt.datetime(2030, 1, 5, 5, 1, tzinfo=UTC) else 40)
    clock = setup(monkeypatch, starts, posts)
    rep = lk.audio_restart(wait_s=300, sleep=clock.sleep, now=clock.now)
    assert rep["ok"] and posts == ["/audio/stop", "/audio/start"]


def test_refuses_while_recorded_audio_is_unsaved(monkeypatch):
    posts = []
    clock = setup(monkeypatch, lambda now: now - dt.timedelta(seconds=300), posts)
    rep = lk.audio_restart(wait_s=60, sleep=clock.sleep, now=clock.now)
    assert not rep["ok"] and rep["unsaved_s"] >= 240 and "not saved yet" in rep["error"] and posts == []
    rep = lk.audio_restart(wait_s=0, force=True, settle_s=10, sleep=clock.sleep, now=clock.now)
    assert posts == ["/audio/stop", "/audio/start"] and not rep["ok"]        # forced; no new chunk seen


def test_not_running(monkeypatch):
    monkeypatch.setattr(lk, "_mode_state", lambda: {"running": False})
    assert lk.audio_restart() == {"ok": False, "error": "capture is not running"}
