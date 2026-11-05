"""Browser mode (STUDY_LLM_PROVIDER=browser): the job file for Lecture Lens Studio, and
`lecture finish` turning the Studio's outputs into the vault note and manifest.
Fixtures are invented."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as lk  # noqa: E402
import live_class as lc  # noqa: E402
import study  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SESSION = {"id": "20300101-1000", "title": "Watering the garden", "start": dt.datetime(2030, 1, 1, 10, 0, tzinfo=IST),
           "minutes": 60.0}


def test_provider_switch(monkeypatch):
    monkeypatch.setattr(study, "PROVIDER", "browser")
    assert study.browser_mode()
    monkeypatch.setattr(study, "PROVIDER", "omlx")
    assert not study.browser_mode()
    monkeypatch.setenv("STUDY_LLM_PROVIDER", "Browser ")
    assert lc.browser_mode()
    monkeypatch.setenv("STUDY_LLM_PROVIDER", "omlx")
    assert not lc.browser_mode()


def test_generic_title_is_kept_in_browser_mode(monkeypatch):
    monkeypatch.setattr(study, "PROVIDER", "browser")
    monkeypatch.setattr(study, "llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call in browser mode")))
    assert lk.lecture_title({"title": "Untitled lecture"}, "some words") == "Untitled lecture"


def test_studio_job_carries_what_the_model_steps_need():
    job = lk.studio_job(SESSION, ["[00:00:10] Mulch keeps soil moist."], "DECK:\n1. Beds", "", 12)
    assert job["version"] == 1 and job["cards"] == 12
    assert job["transcript"] == ["[00:00:10] Mulch keeps soil moist."]
    assert job["deck_src"].startswith("DECK") and job["qa_src"] == ""
    assert "2030" in job["when"]
    assert dt.datetime.fromisoformat(job["created_at"]).tzinfo is not None
    json.dumps(job)   # serialisable


def test_finish_writes_vault_note_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(lk, "VAULT", tmp_path / "vault")
    folder = tmp_path / "lectures" / "20300101-1000_Watering-the-garden"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"id": SESSION["id"], "title": SESSION["title"],
        "start": SESSION["start"].isoformat(), "minutes": 60.0, "source": "learn.example.edu/lesson/1", "cards": 0}))
    (folder / "notes.md").write_text("# Watering the garden\n\n_byline_\n\n## TL;DR\n- water early\n\n## Key concepts\n- **Mulch**: holds moisture\n")
    (folder / "cards.tsv").write_text("Why mulch?\tHolds moisture.\tWatering the garden 00:01:00\nQ2\tA2\tWatering the garden \n")
    (folder / "slides.json").write_text(json.dumps([{"n": 1, "description": "## Beds"}, {"n": 2, "description": None}]))
    try:
        lk.finish(folder)
        raise AssertionError("finish must refuse without a Studio result")
    except FileNotFoundError:
        pass
    (folder / "studio_result.json").write_text(json.dumps({"status": "done", "engine": "gemini-nano", "seconds": 42,
                                                           "finished_at": "2030-01-01T12:00:00+00:00", "prompt_versions": {"notes": "notes-nano-v1"}}))
    note, man = lk.finish(folder)
    text = note.read_text()
    assert note.name == "20300101-1000 Watering the garden.md"
    assert "source: learn.example.edu/lesson/1" in text and "## TL;DR" in text and "_byline_" not in text
    assert "[[Mulch]]" in text and "(2 cards)" in text
    saved = json.loads((folder / "manifest.json").read_text())
    assert saved["llm"] == "gemini-nano (Lecture Lens Studio)" and saved["cards"] == 2 and saved["slides_described"] == 1
    assert saved["studio"]["prompt_versions"] == {"notes": "notes-nano-v1"}


def test_browser_build_keeps_studio_notes_and_calls_no_model(tmp_path, monkeypatch):
    """Merging main's recall-first notes (notes.md written after the cards) into the
    browser path must not call the model nor overwrite notes the Studio already wrote."""
    import datetime as dt
    monkeypatch.setattr(study, "PROVIDER", "browser")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call in browser mode"))
    monkeypatch.setattr(study, "llm", boom)
    monkeypatch.setattr(lk, "make_notes", boom)
    monkeypatch.setattr(lk, "make_cards", boom)
    monkeypatch.setattr(lk, "LECTURES", tmp_path / "lectures")
    monkeypatch.setattr(lk, "VAULT", tmp_path / "vault")
    t0 = dt.datetime(2030, 4, 1, 9, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(lk, "audio_between", lambda *a, **k: [{"ts": t0 + dt.timedelta(minutes=1),
                                                              "text": "Water the garden early in the morning."}])
    monkeypatch.setattr(lk, "live_transcript", lambda *a, **k: None)
    monkeypatch.setattr(lk, "keyframes", lambda *a, **k: [])
    monkeypatch.setattr(lk, "live_qa_source", lambda *a, **k: "")
    monkeypatch.setattr(lk.course_calendar, "session_at", lambda *a, **k: None)
    session = {"id": "s1", "title": "Watering the garden", "start": t0, "end": t0 + dt.timedelta(minutes=30),
               "minutes": 30, "frames": []}
    folder = tmp_path / "lectures" / f"s1_{lk.slugify(session['title'])}"
    folder.mkdir(parents=True)
    studio_notes = "# Watering the garden\n\n_notes by gemini-nano in Chrome_\n\n## TL;DR\n- water early\n"
    (folder / "notes.md").write_text(studio_notes)
    lk.build(session, con=None, describe=False, cards=20, quiet=True)
    assert (folder / "notes.md").read_text() == studio_notes
    assert (folder / "studio_job.json").exists()


def test_finish_keeps_the_recall_block_out_of_the_vault_note(tmp_path, monkeypatch):
    """Studio notes.md opens with recall questions (S4); the vault note gets the notes alone,
    as on the oMLX path."""
    monkeypatch.setattr(lk, "VAULT", tmp_path / "vault")
    folder = tmp_path / "20300101-1000_Watering-the-garden"; folder.mkdir()
    notes = "## TL;DR\n- water early\n\n## Key concepts\n- **Mulch**: holds moisture"
    block = lk.recall_first(notes, [{"q": "Why mulch?", "a": "Holds moisture", "kind": "why"}])
    (folder / "notes.md").write_text(f"# Watering the garden\n\n_byline_\n\n{block}\n")
    (folder / "manifest.json").write_text(json.dumps({"id": "20300101-1000", "title": "Watering the garden",
                                                      "start": "2030-01-01T10:00:00+05:30", "minutes": 60}))
    (folder / "studio_result.json").write_text(json.dumps({"engine": "gemini-nano", "citations_removed": 2}))
    note, man = lk.finish(folder)
    text = Path(note).read_text()
    assert "Before you read" not in text and "Answers to the opening questions" not in text
    assert "water early" in text and "[[Mulch]]" in text
    assert man["studio"]["citations_removed"] == 2
    assert lk.strip_recall_first(notes) == notes


def test_recall_first_matches_the_studio_fixture():
    """studio/src/core/recall.js is tested against the same file (node --test)."""
    fx = json.loads((Path(__file__).resolve().parents[2] / "studio/test/fixtures/recall.json").read_text())
    for c in fx["cases"]:
        assert lk.recall_first(c["notes"], c["cards"]) == c["expected"]


def test_audio_export_for_browser_transcription(tmp_path):
    import sqlite3
    data = tmp_path / "data"; data.mkdir()
    names = ["System Audio (output)_2030-01-01_04-30-00.mp4", "System Audio (output)_2030-01-01_04-30-30.mp4"]
    for n in names:
        (data / n).write_bytes(b"\0" * 8)
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE audio_chunks (id INTEGER PRIMARY KEY, file_path TEXT, timestamp TEXT)")
    con.executemany("INSERT INTO audio_chunks (file_path, timestamp) VALUES (?, ?)", [
        (str(data / names[0]), "2030-01-01T04:30:00+00:00"), (str(data / names[1]), "2030-01-01T04:30:30+00:00"),
        (str(data / "Mic_2030-01-01_04-30-00.mp4"), "2030-01-01T04:30:00+00:00"),
        (str(data / "System Audio (output)_2030-01-01_06-00-00.mp4"), "2030-01-01T06:00:00+00:00")])
    start = dt.datetime(2030, 1, 1, 10, 0, tzinfo=IST)            # 04:30 UTC
    chunks = lk.audio_chunk_files(con, start, start + dt.timedelta(minutes=5))
    assert [p.name for _, p in chunks] == names, "system audio only, inside the window"
    folder = tmp_path / "lecture"; folder.mkdir()
    listing = lk.export_audio(folder, chunks, start + dt.timedelta(seconds=10))
    assert [x["offset_s"] for x in listing] == [0.0, 20.0]
    assert all((folder / "audio" / x["file"]).exists() and " " not in x["file"] for x in listing)
    assert json.loads((folder / "audio.json").read_text()) == listing


def test_studio_job_marks_a_merged_transcript():
    assert lk.studio_job(SESSION, [], "", "", 4, "merged")["transcript_source"] == "merged"
    assert lk.studio_job(SESSION, [], "", "", 4)["transcript_source"] == "capture"
