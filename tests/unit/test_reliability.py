"""Reliability pack: one caller at a time on the local model, the follower ending itself,
and the After-class name check. Invented fixtures."""
import datetime as dt
import fcntl
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import study  # noqa: E402
import live_class as lc  # noqa: E402
import lecture_kit as lk  # noqa: E402

UTC = dt.timezone.utc
T = dt.datetime(2030, 1, 1, 7, 30, tzinfo=UTC)
m = lambda x: dt.timedelta(minutes=x)


def test_background_caller_skips_when_the_model_is_held(tmp_path, monkeypatch):
    lock = tmp_path / ".llm.lock"
    monkeypatch.setenv("STUDY_LLM_LOCK", str(lock))
    monkeypatch.setitem(study.SLOT_WAIT, "background", 0.5)
    held = open(lock, "a+"); fcntl.flock(held, fcntl.LOCK_EX)
    t0 = time.monotonic()
    try:
        with study.llm_slot("background"):
            raise AssertionError("must not get the slot")
    except study.LLMBusy as e:
        assert "background" in str(e)
    assert time.monotonic() - t0 < 6
    fcntl.flock(held, fcntl.LOCK_UN); held.close()
    with study.llm_slot("background"):   # free again
        pass


def test_llm_uses_the_slot_kind_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_LLM_LOCK", str(tmp_path / ".llm.lock"))
    monkeypatch.setenv("STUDY_LLM_SLOT", "background")
    seen = {}
    real = study.llm_slot
    def spy(kind=None):
        seen["kind"] = kind or study.os.environ["STUDY_LLM_SLOT"]
        return real(kind)
    monkeypatch.setattr(study, "llm_slot", spy)
    monkeypatch.setattr(study, "_llm", lambda *a, **k: "ok")
    assert study.llm("s", "u") == "ok" and seen["kind"] == "background"


def test_class_ended_needs_end_quiet_and_no_meeting_window():
    end = T
    assert not lc.class_ended(T - m(1), end, None, None)                 # before the scheduled end
    assert not lc.class_ended(T + m(30), end, T + m(25), T + m(29))      # overrun: still talking
    assert not lc.class_ended(T + m(30), end, T + m(15), T + m(28))      # quiet, but the meeting is on screen
    assert lc.class_ended(T + m(30), end, T + m(15), T + m(20))          # quiet and the window is gone
    assert lc.class_ended(T + m(2), end - m(30), end - m(20), end - m(25))  # ended early, noticed after the end
    assert not lc.class_ended(T + m(90), None, None, None)               # no schedule: only --until stops it


def test_names_left_counts_lines_without_printing_names(tmp_path):
    f = tmp_path / "lec"; f.mkdir()
    (f / "notes.md").write_text("# Garden\n- an attendee asked about mulch\n- Zorvan asked about beds\n")
    (f / "transcript.md").write_text("[00:01:00] Thanks, an attendee.\n")
    vault = tmp_path / "v.md"; vault.write_text("Zorvan B. and Zorvan again\n")
    left = lk.names_left(f, ["Zorvan"], vault)
    assert left == {"notes.md": 1, "v.md": 1}
    assert lk.names_left(f, [], vault) == {}
