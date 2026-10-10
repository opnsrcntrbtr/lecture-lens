import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import os
os.environ["LIVE_SLIDE_MARKER"] = r"UNIT\s*\d+\s*/\s*LESSON\s*\d+"  # this deck's marker (invented)
import live_class as lc

UTC = dt.timezone.utc


def T(h, m, s=0):
    return dt.datetime(2026, 1, 5, h, m, s, tzinfo=UTC)


def test_file_start_and_finished_files():
    names = ["System Audio (output)_2026-01-05_04-30-00.mp4", "System Audio (output)_2026-01-05_04-30-30.mp4",
             "System Audio (output)_2026-01-05_04-31-00.mp4", "App Tap (output)_2026-01-05_04-30-00.mp4", "notes.txt"]
    assert lc.file_start(names[0]) == T(4, 30)
    got = lc.finished_files(names, "System Audio (output)", T(4, 30), T(4, 31, 20))
    # the 04:31:00 file is still being written at 04:31:20
    assert [n for _, n in got] == names[:2]


def test_batches_split_on_size_and_gap():
    files = [(T(4, 30) + dt.timedelta(seconds=30 * i), f"f{i}") for i in range(12)]
    files.append((T(5, 0), "late"))
    b = lc.batches(files, size=10)
    assert [len(x) for x in b] == [10, 2, 1]


def test_place_segments_uses_file_starts_and_drops_overlap():
    starts = [T(4, 30), T(4, 30, 30), T(4, 31)]
    segs = [{"from_ms": 5000, "text": " overlap words"}, {"from_ms": 31000, "text": " stars form in clouds"},
            {"from_ms": 62000, "text": " [BLANK_AUDIO]"}, {"from_ms": 65000, "text": " gravity pulls gas inward"}]
    out = lc.place_segments(segs, [30.0, 30.0, 30.0], starts, keep_from=T(4, 30, 30))
    assert [o["text"] for o in out] == ["stars form in clouds", "gravity pulls gas inward"]
    assert out[0]["t"] == T(4, 30, 31).isoformat()
    assert out[1]["t"] == T(4, 31, 5).isoformat()


def test_noise_filter_body_and_title():
    assert lc.is_noise("Audio settings Audio settings Audio settings Audio settings Audio settings Video & effects")
    shot = ("Dr. Example\nzm II | Dr. Example is talking ...UNIT 2 / LESSON 3Photosynthesis in Desert Plants"
            "CAM plants open stomata at night• to save waterSchool • Botany Course")
    assert not lc.is_noise(shot)
    assert lc.slide_body("Settings\nVideo & effects\nAudio") is None
    body = lc.slide_body(shot)
    assert body.startswith("Photosynthesis") and "is talking" not in body
    assert lc.slide_title(body) == "Photosynthesis in Desert Plants"


def test_new_slides_dedupes_similar_text():
    a = "zm | UNIT 1 / LESSON 1Photosynthesis in Desert Plants CAM plants open stomata at night to save water"
    b = "Zm • K presenter is talking UNIT 1 / LESSON 1Photosynthesis in Desert Plants CAM plants open stomata at night to save water"
    c = "UNIT 1 / LESSON 1The Calvin Cycle fixes carbon dioxide into sugar using ATP and NADPH"
    rows = [{"t": "1", "text": a}, {"t": "2", "text": b}, {"t": "3", "text": c}]
    out = lc.new_slides(rows, None)
    assert [o["t"] for o in out] == ["1", "3"]
    assert lc.new_slides([{"t": "4", "text": c}], lc.tokens(lc.slide_body(c))) == []
    assert lc.new_slides([{"t": "5", "text": "Settings Audio Video & effects Share screen Statistics"}], None) == []


def test_render_lists_outline_and_slides():
    md = lc.render("Desert botany", T(4, 30), [{"t": T(4, 31).isoformat(), "title": "CAM plants", "text": ""}],
                   [{"from": T(4, 30).isoformat(), "to": T(4, 40).isoformat(), "bullets": "- stomata open at night"}], "ok", 120)
    assert "# LIVE — Desert botany" in md and "CAM plants" in md and "stomata open at night" in md


def test_partly_covered_slide_is_the_same_slide():
    full = "UNIT 1 / LESSON 1Agenda for today: soil types, water retention, root depth, crop choice, irrigation"
    part = "UNIT 1 / LESSON 1Agenda for today: soil types, water retention, root depth"
    assert len(lc.new_slides([{"t": "1", "text": full}, {"t": "2", "text": part}], None)) == 1


def test_parse_questions_keeps_only_verified_sources_and_drops_repeats():
    raw = 'Sure: [{"type": "challenge", "q": "Does the soil matrix cover drought years?", "answer": "The lecture said [3] dry years matter; see S1 and S99.", "sources": ["S1", "S99"]},' \
          ' {"type": "clarify", "q": "Does the soil matrix cover drought years at all?", "answer": "dup", "sources": []},' \
          ' {"type": "weird", "q": "How are cut-offs for root depth chosen?", "answer": "Not said yet.", "sources": []},' \
          ' {"q": "", "answer": "empty question"}]'
    out = lc.parse_questions(raw, {"S1", "S2"}, asked=["What crops grow in clay?"])
    assert [q["sources"] for q in out] == [["S1"], []]
    assert "[3]" not in out[0]["answer"]
    named_only = lc.parse_questions('[{"q": "Why clay?", "answer": "Lecture only.", "sources": ["S1"]}]', {"S1"})
    assert named_only[0]["sources"] == []
    assert out[1]["type"] == "clarify"
    assert lc.parse_questions("no json here", {"S1"}) == []
    assert lc.parse_questions(raw, {"S1"}, asked=["Does the soil matrix cover drought years?"])[0]["q"].startswith("How are")


def test_render_shows_questions_with_sources_and_merges_repeated_slides():
    ev = {"S1": {"id": "S1", "title": "Soil survey 2025", "fact": "x"}}
    qs = [{"t": T(4, 40).isoformat(), "type": "challenge", "q": "Is clay always bad?", "answer": "No.", "sources": ["S1"]}]
    slides = [{"t": T(4, 31).isoformat(), "title": "Agenda", "text": ""}, {"t": T(4, 32).isoformat(), "title": "Agenda", "text": ""}]
    md = lc.render("Soils", T(4, 30), slides, [], "ok", 10, qs, ev)
    assert "Is clay always bad?" in md and "Soil survey 2025" in md
    assert md.count("Agenda") == 1


def test_without_a_marker_any_non_chrome_text_is_slide_text(monkeypatch):
    monkeypatch.setattr(lc, "SLIDE_MARKER", None)
    assert lc.slide_body("Settings\nVideo & effects\nAudio") is None
    assert lc.slide_body("Crop rotation keeps soil nitrogen up across seasons").startswith("Crop rotation")
