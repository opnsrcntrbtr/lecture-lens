"""Courses side by side (courses.py): registry, activation, capture scope, DRM verdicts. Invented fixtures."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import courses  # noqa: E402

REG = {"default": "garden",
       "courses": [{"id": "garden", "name": "Garden Basics", "kind": "live"},
                   {"id": "Pond Path", "name": "Pond Keeping Path", "short": "Ponds", "kind": "self_paced",
                    "sites": ["learn.example.org"], "title_strip": ["Example Learn"],
                    "generic_titles": ["pond keeping path"], "drm": "auto", "focus": "keeping a healthy pond"},
                   {"id": "pond-path", "name": "duplicate id is ignored"},
                   {"id": "bees", "name": "Bees", "kind": "weird", "sites": ["bees.example.net:subdomains"], "drm": True,
                    "nlm_drive_parent": "folder-bees"}]}


@pytest.fixture
def env(tmp_path):
    (tmp_path / "courses").mkdir()
    (tmp_path / "courses" / "courses.json").write_text(json.dumps(REG))
    return {"STUDY_COURSES": str(tmp_path / "courses" / "courses.json"),
            "STUDY_LECTURE_DIR": str(tmp_path / "lectures"), "STUDY_VAULT_SUBDIR": "Lectures",
            "STUDY_LECTURE_SITE": "garden.example.com", "STUDY_NLM_DRIVE_PARENT": "folder-main"}


def test_registry_is_checked(env):
    reg = courses.load(environ=env)
    assert [c["id"] for c in reg["courses"]] == ["garden", "pond-path", "bees"]
    assert reg["default"] == "garden" and courses.get(reg, "bees")["kind"] == "live"


def test_no_registry_changes_nothing(tmp_path):
    e = {"STUDY_COURSES": str(tmp_path / "none.json"), "STUDY_LECTURE_DIR": "/x"}
    assert courses.activate(["prog"], e) is None and e == {"STUDY_COURSES": str(tmp_path / "none.json"), "STUDY_LECTURE_DIR": "/x"}
    with pytest.raises(SystemExit):
        courses.activate(["prog", "--course", "bees"], dict(e))


def test_activate_switches_folders_and_back(env, tmp_path):
    argv = ["lecture", "list", "--course", "pond-path", "--json"]
    assert courses.activate(argv, env) == "pond-path" and argv == ["lecture", "list", "--json"]
    assert env["STUDY_LECTURE_DIR"] == str(tmp_path / "courses" / "pond-path")
    assert env["STUDY_CALENDAR"].endswith("pond-path/calendar.json")
    assert env["STUDY_PRACTICE_DIR"].endswith("pond-path/practice")
    assert env["STUDY_VAULT_SUBDIR"] == "Courses/Ponds" and env["STUDY_LECTURE_SITE"] == "learn.example.org"
    assert env["STUDY_LIVE_APPS"] == "" and env["STUDY_TITLE_STRIP"] == "Example Learn"
    assert env["STUDY_COURSE_FOCUS"] == "keeping a healthy pond"
    assert "STUDY_NLM_DRIVE_PARENT" not in env          # never the default course's Drive folder
    # a child process switching back to the default course gets the original values
    assert courses.activate(["x", "--course=garden"], env) == "garden"
    assert env["STUDY_LECTURE_DIR"] == str(tmp_path / "lectures") and env["STUDY_VAULT_SUBDIR"] == "Lectures"
    assert env["STUDY_NLM_DRIVE_PARENT"] == "folder-main" and "STUDY_CALENDAR" not in env
    assert "STUDY_PRACTICE_DIR" not in env
    env["STUDY_COURSE"] = "bees"
    assert courses.activate(["x"], env) == "bees" and env["STUDY_NLM_DRIVE_PARENT"] == "folder-bees"
    with pytest.raises(SystemExit):
        courses.activate(["x", "--course", "nope"], env)


def test_pages_map_to_courses(env):
    reg = courses.load(environ=env)
    assert courses.for_url("https://learn.example.org/ou/path/123", reg) == "pond-path"
    assert courses.for_url("https://hive.bees.example.net/video", reg) == "bees"
    assert courses.for_url("https://bees.example.net.evil.com/", reg) is None
    assert courses.for_url("https://elsewhere.com/", reg) is None
    assert courses.capture_sites(reg) == ["learn.example.org", "bees.example.net:subdomains"]


def test_drm_auto_follows_the_check(env):
    reg = courses.load(environ=env)
    assert courses.drm_patterns(reg, {}) == ["bees.example.net"]
    blanked = courses.drm_verdict([0.5, 0.45, 0.6, 0.4, 0.55, 0.02])
    assert blanked["blanked"] is True
    courses.record_drm("pond-path", blanked, env)
    st = courses.load_state(env)
    assert courses.drm_patterns(reg, st) == ["learn.example.org", "bees.example.net"]


def test_drm_verdict_needs_enough_frames_and_a_black_block():
    assert courses.drm_verdict([0.9, 0.9])["blanked"] is None
    assert courses.drm_verdict([0.01, 0.05, 0.0, 0.3, 0.02, 0.1])["blanked"] is False   # a dark slide now and then
    from PIL import Image
    img = Image.new("RGB", (200, 100), (30, 30, 30))                   # dark-theme slide: grey, not black
    assert courses.dark_share(img) == 0.0
    img.paste((0, 0, 0), (0, 0, 100, 100))
    assert 0.45 < courses.dark_share(img) < 0.55


def test_summary_counts_lectures(env, tmp_path):
    d = tmp_path / "courses" / "pond-path" / "20300105-1000_Water"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}")
    s = courses.summary(environ=env)
    pond = next(c for c in s["courses"] if c["id"] == "pond-path")
    assert pond["lectures"] == 1 and pond["vault_subdir"] == "Courses/Ponds" and pond["drm_on"] is False
    assert next(c for c in s["courses"] if c["default"])["root"] == str(tmp_path / "lectures")


def test_default_course_keeps_a_parents_explicit_folder(env, tmp_path):
    """A child process (tests, tools) that sets STUDY_LECTURE_DIR keeps it under the default course."""
    courses.activate(["x"], env)
    child = dict(env, STUDY_LECTURE_DIR=str(tmp_path / "fixture"))
    assert courses.activate(["y"], child) == "garden" and child["STUDY_LECTURE_DIR"] == str(tmp_path / "fixture")
    assert child["STUDY_COURSE_DEFAULT"] == "garden"
    courses.activate(["z", "--course", "pond-path"], child)
    courses.activate(["z", "--course", "garden"], child)
    assert child["STUDY_LECTURE_DIR"] == str(tmp_path / "fixture")


def test_capture_health_stays_with_the_default_course(env, tmp_path, monkeypatch):
    import os
    import lecture_kit as lk
    fake = dict(env)
    monkeypatch.setattr(os, "environ", fake)   # activate() writes freely; nothing leaks to other tests
    courses.activate(["x", "--course", "pond-path"], fake)
    assert fake["STUDY_LECTURE_DIR"].endswith("pond-path")
    assert lk._capture_root() == tmp_path / "lectures"
