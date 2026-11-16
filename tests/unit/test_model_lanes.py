"""NG-6 model lanes: priority among waiting callers, time-boxed skips, slot metrics, streaming timings."""
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import study  # noqa: E402


@pytest.fixture
def lock(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_LLM_LOCK", str(tmp_path / ".llm.lock"))
    return tmp_path


def rows(tmp):
    p = tmp / "llm_slot_metrics.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_interactive_waiter_goes_before_a_waiting_build(lock):
    order, held = [], threading.Event()

    def holder():
        with study.llm_slot("build"):
            held.set()
            time.sleep(0.8)

    def caller(kind, delay):
        time.sleep(delay)
        with study.llm_slot(kind):
            order.append(kind)
            time.sleep(0.3)

    t = [threading.Thread(target=holder)]
    t[0].start(); held.wait()
    t += [threading.Thread(target=caller, args=("build", 0.05)), threading.Thread(target=caller, args=("interactive", 0.2))]
    for x in t[1:]:
        x.start()
    for x in t:
        x.join()
    assert order == ["interactive", "build"]


def test_time_boxed_caller_is_skipped_at_its_deadline(lock):
    held, done = threading.Event(), threading.Event()

    def holder():
        with study.llm_slot("build"):
            held.set()
            done.wait(3)

    th = threading.Thread(target=holder); th.start(); held.wait()
    t0 = time.time()
    with pytest.raises(study.LLMBusy):
        with study.llm_slot("time_boxed", deadline=time.time() + 0.5):
            pass
    assert 0.4 <= time.time() - t0 < 2
    done.set(); th.join()
    skipped = [r for r in rows(lock) if r["kind"] == "time_boxed"]
    assert skipped and skipped[0]["outcome"] == "skipped"


def test_stale_waiters_are_ignored(lock):
    wd = lock / ".llm.wait"
    wd.mkdir()
    ghost = wd / "3-interactive-999999-abcdef.wait"     # a dead process
    ghost.touch()
    old = wd / f"3-interactive-{os.getpid()}-aaaaaa.wait"
    old.touch()
    os.utime(old, (time.time() - 120, time.time() - 120))
    with study.llm_slot("build"):
        pass
    assert not ghost.exists() and not old.exists()


def test_metrics_record_wait_outcome_and_tokens(lock, monkeypatch):
    def fake(*a, **k):
        study._LAST.update({"prompt_tokens": 100, "completion_tokens": 50})
        time.sleep(0.05)
        return "ok"
    monkeypatch.setattr(study, "_llm", fake)
    assert study.llm("s", "u", slot="interactive") == "ok"
    r = rows(lock)[-1]
    assert r["kind"] == "interactive" and r["outcome"] == "ran" and r["completion_tokens"] == 50
    assert r["decode_tps"] and r["waited_s"] >= 0 and "run_s" in r


def test_streaming_measures_time_to_first_token(lock, monkeypatch):
    sse = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n', b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
           b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2}}\n', b"data: [DONE]\n"]

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def __iter__(self): return iter(sse)

    monkeypatch.setattr(study._OPENER, "open", lambda req, timeout=0: Resp())
    monkeypatch.setenv("STUDY_LLM_STREAM", "1")
    with study.llm_slot("interactive"):
        out = study._llm("s", "u")
    assert out == "Hello" and study._LAST["completion_tokens"] == 2
    assert rows(lock)[-1]["prefill_s"] is not None


def test_lane_summary_per_kind():
    import lecture_kit as lk
    rows = [{"kind": "interactive", "waited_s": w, "outcome": "ran", "prefill_s": 0.1, "decode_tps": 60} for w in (0.1, 0.2, 3.0)]
    rows += [{"kind": "background", "waited_s": 5.0, "outcome": "skipped"}]
    s = lk.lane_summary(rows)["by_kind"]
    assert s["interactive"]["calls"] == 3 and s["interactive"]["median_wait_s"] == 0.2 and s["interactive"]["p95_wait_s"] == 3.0
    assert s["background"]["skipped"] == 1 and "build" not in s
