"""merge_reference_transcript: whisper fills gaps inside the reference span; short chance matches are not anchors."""
import datetime as dt, importlib.util, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mrt", ROOT / "tools" / "merge_reference_transcript.py")
mrt = importlib.util.module_from_spec(spec); sys.path.insert(0, str(ROOT)); spec.loader.exec_module(mrt)
T0 = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.timezone.utc)


def at(s):
    return T0 + dt.timedelta(seconds=s)


def test_covered_near_and_gap():
    lines = [at(0), at(60), at(600)]
    assert mrt.covered(at(30), lines)
    assert not mrt.covered(at(300), lines)      # gap inside the span is not covered
    assert mrt.covered(at(650), lines)
    assert not mrt.covered(at(900), lines)


def test_gap_filled_and_chance_match_ignored(tmp_path, monkeypatch):
    live = tmp_path / "live"; live.mkdir()
    segs = [{"t": at(0).isoformat(), "text": "the garden has eight raised beds"},
            {"t": at(400).isoformat(), "text": "water the beans before the heat of noon"},
            {"t": at(900).isoformat(), "text": "okay the end"}]
    (live / "whisper_segments.jsonl").write_text("\n".join(json.dumps(s) for s in segs))
    ref = tmp_path / "ref.txt"
    ref.write_text("Speaker 1: The garden has eight raised beds.\nSpeaker 2: Okay the.\n")
    out = tmp_path / "out.md"
    monkeypatch.setattr(sys, "argv", ["x", str(live), str(ref), str(out), "--keep", "1", "--label", "1=Instructor"])
    mrt.main()
    text = out.read_text()
    assert "Instructor: The garden has eight" in text
    assert "Speaker: water the beans before" in text   # gap inside the span filled from whisper
    assert "Attendee: Okay the." in text
