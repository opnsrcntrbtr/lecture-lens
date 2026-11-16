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
