"""Rename and consolidate built lectures (lecture_manage.py) and register courses (courses.py). Invented fixtures."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import courses  # noqa: E402
import lecture_manage as lm  # noqa: E402


def lecture(root, ident, title, start, minutes, lines, slides=(), extra=None):
    d = root / f"{ident}_{title.replace(' ', '-')}"
    (d / "slides").mkdir(parents=True)
    man = {"id": ident, "title": title, "start": start, "end": None, "minutes": minutes, "cards": 1}
    man.update(extra or {})
    (d / "manifest.json").write_text(json.dumps(man))
    (d / "transcript.md").write_text(f"# Transcript — {title}\n\n_meta_\n\n" + "\n".join(lines) + "\n")
    (d / "notes.md").write_text(f"# {title}\n\n_x_\n\n- **Mulch** keeps soil moist [00:01:00].\n")
    (d / "cards.tsv").write_text(f"What keeps soil moist?\tMulch\t{title} 00:01:00\n")
    blocks = []
    for i, (at, desc) in enumerate(slides, 1):
        (d / "slides" / f"{i:02d}_{at.replace(':', '')}.jpg").write_bytes(b"jpg")
        blocks.append(f"## {i}. at {at}\n\n![[{i:02d}_{at.replace(':', '')}.jpg]]\n\n{desc}\n")
    (d / "slides.md").write_text(f"# Slides & visuals — {title}\n\n" + "\n".join(blocks))
    return d


def test_timecodes_shift():
    assert lm.shift_text("[00:01:00] a [12:30] b [slide 4]", 2400) == "[00:41:00] a [00:52:30] b [slide 4]"
    assert lm.hhmmss(3725) == "01:02:05" and lm.secs("01:02:05") == 3725


def test_rename_everywhere(tmp_path):
    d = lecture(tmp_path, "20300105-1000", "Soil Basics", "2030-01-05T04:30:00+00:00", 40, ["[00:01:00] mulch"])
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "20300105-1000 Soil Basics.md").write_text("---\nx: 1\n---\n\n# Soil Basics\n\nbody\n")
    r = lm.rename(d, "  Soil and   Mulch ", vault)
    assert r["ok"] and r["title"] == "Soil and Mulch" and {"notes.md", "transcript.md", "slides.md", "cards.tsv"} <= set(r["changed"])
    assert (d / "notes.md").read_text().startswith("# Soil and Mulch\n")
    assert "\tSoil and Mulch 00:01:00" in (d / "cards.tsv").read_text()
    assert not (vault / "20300105-1000 Soil Basics.md").exists()
    assert "# Soil and Mulch" in (vault / "20300105-1000 Soil and Mulch.md").read_text()
    man = json.loads((d / "manifest.json").read_text())
    assert man["title"] == "Soil and Mulch" and man["renamed_from"] == "Soil Basics" and d.name.startswith("20300105-1000_")
    assert not lm.rename(d, "  ")["ok"]


def test_merge_joins_on_one_timeline_and_keeps_sources(tmp_path):
    lect = tmp_path / "lectures"
    vault = tmp_path / "vault"
    vault.mkdir()
    a = lecture(lect, "20300105-1000", "Soil One", "2030-01-05T04:30:00+00:00", 40,
                ["[00:00:10] hello", "[00:39:00] end one"], [("00:05:00", "Soil layers")])
    b = lecture(lect, "20300106-1000", "Soil Two", "2030-01-06T04:30:00+00:00", 30,
                ["[00:01:00] part two"], [("00:02:00", "Compost heap")])
    (b / "qa.md").write_text("Q: why compost? [00:03:00]\n")
    (b / "resources").mkdir()
    (b / "resources" / "index.json").write_text(json.dumps([{"file": "reading.md"}]))
    (b / "resources" / "reading.md").write_text("reading")
    (vault / "20300106-1000 Soil Two.md").write_text("# Soil Two\n")
    # destination is the later lecture: the earlier one still comes first on the timeline
    r = lm.merge(b, [a], lect / "_superseded", vault)
    assert r["ok"] and r["parts"] == 2 and r["minutes"] == 70 and r["source_ids"] == ["20300105-1000"]
    t = (b / "transcript.md").read_text()
    assert t.index("[00:00:10] hello") < t.index("[00:41:00] part two") and "consolidated from 2" in t
    s = (b / "slides.md").read_text()
    assert "## 1. at 00:05:00" in s and "## 2. at 00:42:00" in s and "![[02_004200.jpg]]" in s
    assert sorted(p.name for p in (b / "slides").glob("*.jpg")) == ["01_000500.jpg", "02_004200.jpg"]
    assert "[00:43:00]" in (b / "qa.md").read_text() and (b / "resources" / "reading.md").exists()
    man = json.loads((b / "manifest.json").read_text())
    assert man["id"] == "20300106-1000" and man["start"].startswith("2030-01-05") and man["rebuild_pending"]
    assert [p["offset_s"] for p in man["timeline"]] == [0.0, 2400.0] and man["consolidated"][0]["id"] == "20300105-1000"
    # sources and the destination as it was are kept; the source's vault note moves aside
    assert not a.exists() and any(p.name.startswith(a.name + ".merged-into-") for p in (lect / "_superseded").iterdir())
    assert (Path(r["backup"]) / "transcript.md").read_text().count("part two") == 1
    assert lm.consolidated_ids(lect) == {"20300105-1000"}
    assert not lm.merge(b, [b], lect / "_superseded")["ok"]


def test_add_and_rename_courses(tmp_path):
    env = {"STUDY_COURSES": str(tmp_path / "courses" / "courses.json"), "STUDY_COURSE_NAME": "Garden Basics",
           "STUDY_LECTURE_DIR": str(tmp_path / "lectures")}
    r = courses.add("", "https://learn.example.org/path/pond-keeping/42", "Pond Keeping Path - Example Learn",
                    focus="keeping a healthy pond", environ=env)
    assert r["ok"] and r["course"] == "pond-keeping-path"
    reg = json.loads(Path(env["STUDY_COURSES"]).read_text())
    assert [c["id"] for c in reg["courses"]] == ["main", "pond-keeping-path"] and reg["default"] == "main"
    e = reg["courses"][1]
    assert e["sites"] == ["learn.example.org"] and e["title_strip"] == ["Example Learn"] and e["drm"] == "auto"
    assert e["kind"] == "self_paced" and "pond keeping path" in e["generic_titles"]
    assert r["root"].endswith("courses/pond-keeping-path")
    dup = courses.add("Again", "https://learn.example.org/other", environ=env)
    assert not dup["ok"] and dup["course"] == "pond-keeping-path"
    assert not courses.add("No URL", "learn.example.org", environ=env)["ok"]
    live = courses.add("Webinars", "https://zoom.us/j/1", environ=env)
    assert live["ok"] and live["entry"]["kind"] == "live"
    rn = courses.rename("pond-keeping-path", name="Ponds, properly", short="Ponds", environ=env)
    assert rn["ok"] and courses.get(courses.load(environ=env), "pond-keeping-path")["name"] == "Ponds, properly"
    assert courses.get(courses.load(environ=env), "pond-keeping-path")["short"] == "Ponds"
    assert not courses.rename("nope", name="x", environ=env)["ok"]


def test_chrome_tabs_are_parsed_front_first():
    reg = {"default": "a", "courses": [{"id": "a", "kind": "live"}, {"id": "p", "kind": "self_paced", "sites": ["learn.example.org"]}]}
    raw = "0\tInbox\thttps://mail.example.com/\n0\tSettings\tchrome://settings\n1\tLesson 2 - Example Learn\thttps://learn.example.org/x\n"
    tabs = courses.parse_tabs(raw, reg)
    assert [t["title"] for t in tabs] == ["Lesson 2 - Example Learn", "Inbox"] and tabs[0]["active"] and tabs[0]["course"] == "p"

    class R:
        returncode, stdout, stderr = 1, "", "execution error: Not authorized to send Apple events to Google Chrome. (-1743)"
    out = courses.chrome_tabs(run=lambda *a, **k: R(), reg=reg)
    assert not out["ok"] and "Automation" in out["error"]
