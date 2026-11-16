"""Citations: lecture moments by shared words near the time, the slide then, sources from the bank. Invented fixtures."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import cite  # noqa: E402

T0 = dt.datetime(2030, 1, 1, 5, 0, tzinfo=dt.timezone.utc)
iso = lambda m: (T0 + dt.timedelta(minutes=m)).isoformat()
SEGS = [{"t": iso(0), "text": "Mulch keeps soil moist through dry weeks."},
        {"t": iso(3), "text": "Water early in the morning so leaves dry before evening."},
        {"t": iso(4), "text": "Raised beds drain faster, so water raised beds more often."},
        {"t": iso(30), "text": "Water raised beds more often in summer heat."}]
SLIDES = [{"t": iso(-5), "title": "Soil basics"}, {"t": iso(2), "title": "Watering raised beds"},
          {"t": iso(3), "title": "Back shortly", "kind": "whiteboard"}]


def test_moments_rank_by_shared_words_inside_the_window():
    m = cite.moments(SEGS, T0 + dt.timedelta(minutes=5), "How often should raised beds get water?")
    assert [x["t"] for x in m] == [iso(4)]           # the 30-min line is outside +-10 min
    assert set(m[0]["shared"]) == {"raised", "beds", "water", "often"}


def test_slide_at_skips_whiteboards():
    assert cite.slide_at(SLIDES, T0 + dt.timedelta(minutes=5))["title"] == "Watering raised beds"
    assert cite.slide_at(SLIDES, T0 - dt.timedelta(minutes=10)) is None


def test_sources_keep_bank_order_of_ids_and_drop_unknown():
    bank = [{"id": "S1", "title": "Garden guide", "fact": "Mulch holds water.", "url": "https://example.org/g"}]
    assert cite.sources(["S9", "S1"], bank) == [{"id": "S1", "title": "Garden guide", "fact": "Mulch holds water.",
                                                 "url": "https://example.org/g", "date": None}]


def test_lecture_segments_from_a_built_lecture(tmp_path):
    f = tmp_path / "20300101-1030_Garden"; f.mkdir()
    (f / "manifest.json").write_text(json.dumps({"start": iso(0), "minutes": 60}))
    (f / "transcript.md").write_text("# Transcript\n\n_x_\n\n[00:04:00] Raised beds drain faster.\n")
    segs = cite.lecture_segments(tmp_path, T0 + dt.timedelta(minutes=10))
    assert segs == [{"t": iso(4), "text": "Raised beds drain faster."}]
    assert cite.lecture_segments(tmp_path, T0 + dt.timedelta(hours=3)) == []


def test_verify_citations_valid_and_normalized():
    slides = [
        {"n": 1, "at": "00:00:30", "title": "KV-Cache Basics", "description": "Explains kv-cache memory reuse."},
        {"n": 2, "at": "00:05:00", "title": "Unit Economics", "description": "Token pricing and gross margins."},
    ]
    segs = [
        {"at": "00:01:00", "text": "Using a kvcache cuts latency across transformer models."},
        {"at": "00:05:20", "text": "Gross margin improves when token cost drops."},
    ]
    notes = (
        "- Key-value reuse via kv-cache reduces latency for transformer model inference [00:01:10] [slide 1].\n"
        "- Token pricing drives gross margin [05:15] [slide 2].\n"
    )
    assert cite.verify_citations(notes, slides, segs, duration_s=600) == []


def test_verify_citations_catches_out_of_range_and_ungrounded():
    slides = [{"n": 1, "at": "00:01:00", "title": "Soil", "description": "Mulch retains soil moisture."}]
    segs = [{"at": "00:01:00", "text": "Mulch retains soil moisture in dry weather."}]
    notes = (
        "- Mulch retains moisture [00:01:05] [slide 1].\n"
        "- Ghost claim beyond end [00:45:00].\n"
        "- Invalid slide [slide 0] and missing slide [slide 5].\n"
        "- Quantum entanglement speeds up rockets [00:01:10] [slide 1].\n"
    )
    w = cite.verify_citations(notes, slides, segs, duration_s=300)
    kinds = [x["kind"] for x in w]
    assert "timestamp_out_of_range" in kinds
    assert kinds.count("slide_out_of_range") == 2
    assert "ungrounded_timestamp" in kinds
    assert "ungrounded_slide" in kinds
