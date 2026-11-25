"""Ask keeps to the active course (study.course_rows). Invented fixtures."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import study  # noqa: E402

REG = {"default": "garden", "courses": [{"id": "garden", "name": "Garden Basics", "kind": "live", "sites": ["garden.example.com"]},
                                        {"id": "ponds", "name": "Ponds", "kind": "self_paced", "sites": ["learn.example.org"]}]}
ROWS = [
    {"ts": "2030-01-05T10:00:00+00:00", "src": "ocr", "where": "https://garden.example.com/lesson/1", "text": "mulch"},
    {"ts": "2030-01-05T10:00:30+00:00", "src": "audio", "where": "System Audio (output)", "text": "mulch keeps soil moist"},
    {"ts": "2030-01-06T09:00:00+00:00", "src": "ocr", "where": "https://learn.example.org/path/2", "text": "pond pumps"},
    {"ts": "2030-01-06T09:01:00+00:00", "src": "audio", "where": "System Audio (output)", "text": "a pump moves water"},
    {"ts": "2030-01-07T11:00:00+00:00", "src": "audio", "where": "System Audio (output)", "text": "zoom class talk"},
    {"ts": "2030-01-07T11:00:10+00:00", "src": "accessibility", "where": "Zoom Meeting", "text": "class slide"},
]


def env(tmp_path, course):
    (tmp_path / "c.json").write_text(json.dumps(REG))
    return {"STUDY_COURSES": str(tmp_path / "c.json"), "STUDY_COURSE_ID": course}


def test_self_paced_course_keeps_its_pages_and_their_audio(tmp_path):
    out = study.course_rows(ROWS, env(tmp_path, "ponds"))
    assert [r["text"] for r in out] == ["pond pumps", "a pump moves water"]


def test_default_course_drops_only_other_courses(tmp_path):
    out = study.course_rows(ROWS, env(tmp_path, "garden"))
    assert [r["text"] for r in out] == ["mulch", "mulch keeps soil moist", "zoom class talk", "class slide"]


def test_no_registry_filters_nothing(tmp_path):
    assert study.course_rows(ROWS, {"STUDY_COURSES": str(tmp_path / "none.json")}) == ROWS
