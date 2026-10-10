import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as lk

UTC = dt.timezone.utc


def _session(minutes=4):
    s = dt.datetime(2026, 1, 5, 4, 30, tzinfo=UTC)
    return {"id": "x", "title": "Desert botany", "start": s, "end": s + dt.timedelta(minutes=minutes), "minutes": minutes, "frames": []}


def test_live_transcript_groups_parts_and_needs_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(lk, "LECTURES", tmp_path)
    s = _session()
    d = lk._live_dir(s); d.mkdir(parents=True)
    segs = [{"t": (s["start"] + dt.timedelta(seconds=10 * i)).isoformat(), "text": f"cacti store water {i}"} for i in range(24)]
    (d / "whisper_segments.jsonl").write_text("".join(json.dumps(x) + "\n" for x in segs))
    out = lk.live_transcript(s)
    assert out and len(out) == 8 and out[0]["text"].startswith("cacti store water 0 cacti")
    assert lk.live_transcript(_session(minutes=60)) is None          # 4 of 60 minutes covered
    monkeypatch.setenv("STUDY_TRANSCRIPT_SOURCE", "screenpipe")
    assert lk.live_transcript(s) is None


def test_live_qa_source_writes_qa_md_without_trivia(tmp_path, monkeypatch):
    monkeypatch.setattr(lk, "LECTURES", tmp_path)
    s = _session(); d = lk._live_dir(s); d.mkdir(parents=True)
    threads = [{"id": "a", "by": "Attendee", "time": "10:01 AM", "q": "Why do cacti store water in stems?", "answers": [{"by": "Tutor Tom", "time": "10:02 AM", "text": "Rain is rare."}]},
               {"id": "b", "by": "Attendee", "time": "10:03 AM", "q": "Thanks a lot!", "answers": []}]
    (d / "qa_threads.json").write_text(json.dumps(threads))
    (d / "qa_followups.jsonl").write_text(json.dumps({"thread": "a", "type": "clarify", "q": "Which cacti?", "answer": "Not covered yet."}) + "\n")
    folder = tmp_path / "lec"; folder.mkdir()
    src = lk.live_qa_source(s, folder)
    md = (folder / "qa.md").read_text()
    assert "Why do cacti" in md and "Which cacti?" in md and "Thanks a lot" not in md
    assert src.startswith("CLASS Q&A") and "Rain is rare" in src


def test_dedupe_cards_drops_same_answer_under_a_new_question():
    import lecture_kit as lk
    cards = [{"q": "Why do cacti store water?", "a": "Rain is rare in deserts, so stored water carries them through dry months."},
             {"q": "What is the main reason cacti keep water inside?", "a": "Rain is rare in deserts, so stored water carries them through the dry months."},
             {"q": "How do ferns cope with shade?", "a": "Broad thin fronds catch scattered light."}]
    assert [c["q"][:3] for c in lk.dedupe_cards(cards)] == ["Why", "How"]
