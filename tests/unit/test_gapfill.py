"""CT-6 gap fill: placing a recording on the class clock and cutting the hole, with a fake
transcriber over invented words (no audio, no model)."""
import datetime as dt
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import gapfill  # noqa: E402

CLASS0 = dt.datetime(2026, 1, 10, 4, 30, tzinfo=dt.timezone.utc).timestamp()
OFF = CLASS0 + 1234.5          # class time = recording time + OFF


def recording(seconds=3600, seed=7):
    rnd = random.Random(seed)
    vocab = [f"w{i:03d}" for i in range(400)]
    t, out = 0.0, []
    while t < seconds:
        d = rnd.uniform(0.25, 0.5)
        out.append({"w": rnd.choice(vocab) + ("." if rnd.random() < 0.06 else ""), "t": t, "t1": t + d * 0.9})
        t += d + (rnd.uniform(0.8, 2.0) if rnd.random() < 0.03 else 0.0)
    return out


def fake(rec):
    calls = []
    def transcribe(_recording, s, d):
        calls.append((s, d))
        return [dict(w) for w in rec if s <= w["t"] < s + d]
    transcribe.calls = calls
    return transcribe


def transcript(rec, h0, h1, line_words=20):
    """Class transcript lines (start time, text) with the lines starting in [h0, h1) removed,
    and the words of those lines (what a fill should bring back)."""
    cls = [{"w": w["w"], "t": w["t"] + OFF} for w in rec]
    groups = [cls[i:i + line_words] for i in range(0, len(cls), line_words)]
    keep = [g for g in groups if not (h0 <= g[0]["t"] < h1)]
    gone = [x["w"] for g in groups if h0 <= g[0]["t"] < h1 for x in g]
    return [(g[0]["t"], " ".join(x["w"] for x in g)) for g in keep], gone


def test_fill_recovers_the_hole_exactly_and_places_it():
    rec = recording()
    h0, h1 = OFF + 1500, OFF + 2100                       # ten minutes
    lines, want = transcript(rec, h0, h1)
    words = gapfill.line_words(lines)
    first_gone = next(w for w in rec if w["t"] + OFF >= h0 and w["w"] == want[0])
    tr = fake(rec)
    res = gapfill.fill(words, h0, h1, "rec", 3600, tr, guess_offset=CLASS0)
    assert not res.get("error")
    got = " ".join(l["text"] for l in res["lines"]).split()
    # nothing already in the transcript is written again, nothing in the hole is lost
    assert got == want
    t_first = res["lines"][0]["t"]
    assert abs(t_first - (first_gone["t"] + OFF)) < 1.5
    assert all(l["align_conf"] >= 0.7 and not l["approx"] for l in res["lines"])
    assert res["holes"][0]["left"] and res["holes"][0]["right"]
    assert len(tr.calls) <= 4                               # a probe or two, then the window


def test_late_start_is_filled_from_one_side():
    rec = recording()
    start = OFF + 300                                       # capture began 10 min after the class start
    lines, _ = transcript(rec, OFF + 0, start)
    lines = [l for l in lines if l[0] >= start]
    words = gapfill.line_words(lines)
    res = gapfill.fill(words, OFF - 0.0, start, "rec", 3600, fake(rec), guess_offset=CLASS0)
    assert not res.get("error")
    got = " ".join(l["text"] for l in res["lines"]).split()
    want = [w["w"] for w in rec if OFF <= w["t"] + OFF < lines[0][0]]
    assert got == want
    h = res["holes"][0]
    assert h["left"] is None and h["right"]


def test_a_recording_of_something_else_is_refused():
    rec, other = recording(seed=7), recording(seed=99)
    h0, h1 = OFF + 1500, OFF + 2100
    words = gapfill.line_words(transcript(rec, h0, h1)[0])
    res = gapfill.fill(words, h0, h1, "rec", 3600, fake(other), guess_offset=CLASS0)
    assert res["lines"] == [] and "line up" in res["error"]


def test_holes_in_skips_spans_that_still_have_words():
    words = [{"w": "a", "t": t, "t1": t + 0.3} for t in [0, 1, 2, 200, 201, 500]]
    assert gapfill.holes_in(words, 0, 600) == [(2.3, 200), (201.3, 500), (500.3, 600)]
    assert gapfill.holes_in(words, 0, 150) == [(2.3, 200)]      # to the next word


def test_consensus_ignores_outliers():
    pairs = [(t, t + 100.0 + (0.3 if t % 2 else -0.2)) for t in range(10)] + [(50, 999), (60, -400)]
    c = gapfill.consensus(pairs)
    assert abs(c["offset"] - 100.0) < 0.5 and c["n"] == 10
    assert gapfill.consensus(pairs[:2]) is None


def test_write_pre_guards_names_keeps_ids_and_replaces_a_refill(tmp_path):
    import evidence
    folder = tmp_path / "20260110-1000_Invented-Class"
    folder.mkdir()
    start = dt.datetime.fromtimestamp(CLASS0, dt.timezone.utc)
    (folder / "manifest.json").write_text(json.dumps({"title": "Invented", "start": start.isoformat(), "minutes": 60}))
    (folder / "transcript.md").write_text("# T\n\n_x_\n\n[00:00:10] first line here\n[00:30:00] after the gap\n")
    res = {"lines": [{"t": CLASS0 + 600, "t1": CLASS0 + 603, "text": "Zorblat asked about evals", "align_conf": 0.9,
                      "approx": False, "rec_t": 12.0, "wt": [0, 1, 2, 3]},
                     {"t": CLASS0 + 700, "t1": CLASS0 + 702, "text": "metrics matter", "align_conf": 0.5,
                      "approx": True, "rec_t": 112.0, "wt": [0, 1]}]}
    guard = lambda xs: [x.replace("Zorblat", "[name]") for x in xs]
    before = [(r["idx"], r["text"]) for r in evidence.lecture_records(folder, lambda x: x)[1] if r["kind"] == "t"]
    rows = gapfill.write_pre(folder, "k", start, res, CLASS0 + 500, CLASS0 + 1800, "rec.m4a", guard)
    assert [r["line_id"] for r in rows] == ["k/t/pre-0", "k/t/pre-1"]
    assert "Zorblat" not in (folder / "transcript_pre.jsonl").read_text() + (folder / "transcript_pre.md").read_text()
    assert "≈ metrics matter" in (folder / "transcript_pre.md").read_text()
    recs = [r for r in evidence.lecture_records(folder, lambda x: x)[1] if r["kind"] == "t"]
    assert [(r["idx"], r["text"]) for r in recs if not r.get("gapfill")] == before      # existing lines unchanged
    assert [r["idx"] for r in recs] == [0, "pre-0", "pre-1", 1]                          # in time order
    chunks = evidence.chunk(recs, "k", "Invented")
    assert {c["source"] for c in chunks if any(l.get("gapfill") for l in c["lines"])} == {"gapfill"}
    assert all(all(l.get("gapfill") for l in c["lines"]) or not any(l.get("gapfill") for l in c["lines"]) for c in chunks)
    gapfill.write_pre(folder, "k", start, res, CLASS0 + 500, CLASS0 + 1800, "rec.m4a", guard)   # a re-fill replaces
    assert len(gapfill.read_pre(folder)) == 2
    src = gapfill.source_lines(folder, start)
    assert src[1].startswith("[00:11:40] (from the recording, approximate time)")
