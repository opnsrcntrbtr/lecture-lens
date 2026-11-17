"""CT-2 event-driven second pass: chunk closing, overlap and hold-back, catch-up, silence, line IDs."""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import live_asr  # noqa: E402

T0 = dt.datetime(2026, 1, 5, 4, 30, tzinfo=dt.timezone.utc)
DEV = "System Audio (output)"


def chunk(d, k, age=10):
    t = T0 + dt.timedelta(seconds=30 * k)
    p = d / f"{DEV}_{t.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    p.write_text("x")
    os.utime(p, (time.time() - age, time.time() - age))
    return p


# speech: one word per second across the whole recording, "w<second>"
def fake_transcribe(path, prev, overlap_s, prompt=""):
    cs = live_asr.file_start(path.name).timestamp()
    ov = overlap_s if prev is not None else 0.0
    words = []
    s = cs - ov
    while s < cs + 30:
        words.append({"w": f"w{int(round(s - T0.timestamp()))}", "t": s - (cs - ov), "t1": s - (cs - ov) + 0.4})
        s += 1.0
    return words, ov


def asr(tmp, monkeypatch, **kw):
    monkeypatch.setattr(live_asr, "peak_db", lambda p: -20.0)
    monkeypatch.setattr(live_asr, "duration", lambda p: 30.0)
    out = tmp / "out"
    out.mkdir(exist_ok=True)
    return live_asr.EventASR(tmp, DEV, T0, out, "live-20300105", transcribe=fake_transcribe, log=lambda *a: None, **kw), out


def words_written(out):
    rows = [json.loads(l) for l in (out / "transcript.jsonl").read_text().splitlines()]
    return [w for r in rows for w in r["text"].split()], rows


def test_every_word_once_across_chunk_edges(tmp_path, monkeypatch):
    for k in range(4):
        chunk(tmp_path, k)
    a, out = asr(tmp_path, monkeypatch, max_behind=5)
    assert a.poll() == 4
    a.flush()
    ws, rows = words_written(out)
    assert ws == [f"w{i}" for i in range(0, 120)]                         # none lost, none twice
    assert [r["line_id"] for r in rows] == [f"live-20300105/t/{i}" for i in range(len(rows))]
    m = [json.loads(l) for l in (out / "asr_metrics.jsonl").read_text().splitlines()]
    assert [x["overlap"] for x in m] == [False, True, True, True]


def test_catch_up_skips_the_overlap_but_never_audio(tmp_path, monkeypatch):
    for k in range(6):
        chunk(tmp_path, k)
    a, out = asr(tmp_path, monkeypatch, max_behind=3)
    a.poll()
    a.flush()
    ws, _ = words_written(out)
    assert ws == [f"w{i}" for i in range(0, 180)]
    m = [json.loads(l) for l in (out / "asr_metrics.jsonl").read_text().splitlines()]
    assert not any(x["overlap"] for x in m)


def test_a_chunk_still_being_written_waits(tmp_path, monkeypatch):
    chunk(tmp_path, 0)
    chunk(tmp_path, 1, age=0)        # just modified, and no newer chunk
    a, _ = asr(tmp_path, monkeypatch)
    assert [n for _, n in a.closed()] == [sorted(os.listdir(tmp_path))[0]]


def test_digital_silence_is_skipped(tmp_path, monkeypatch):
    chunk(tmp_path, 0)
    a, out = asr(tmp_path, monkeypatch)
    monkeypatch.setattr(live_asr, "peak_db", lambda p: -120.0)
    a.poll()
    assert not (out / "transcript.jsonl").read_text().strip()
    assert json.loads((out / "asr_metrics.jsonl").read_text())["words"] == 0


def test_line_ids_continue_after_a_restart(tmp_path, monkeypatch):
    chunk(tmp_path, 0)
    a, out = asr(tmp_path, monkeypatch)
    a.poll()
    first = a.next_line
    chunk(tmp_path, 1)
    b, _ = asr(tmp_path, monkeypatch)
    assert b.next_line == first and b.held == a.held and live_asr.file_start(b.last) is not None
    b.poll()
    b.flush()
    ws, rows = words_written(out)
    assert ws == [f"w{i}" for i in range(0, 60)] and len({r["line_id"] for r in rows}) == len(rows)


def test_lines_break_on_pause_and_sentence_end():
    w = [{"w": "Mulch", "t": 0, "t1": .3}, {"w": "helps.", "t": .4, "t1": .7}, {"w": "Water", "t": .8, "t1": 1.0},
         {"w": "early", "t": 3.0, "t1": 3.2}]
    assert [[x["w"] for x in ln] for ln in live_asr.lines_from_words(w)] == [["Mulch", "helps."], ["Water"], ["early"]]
