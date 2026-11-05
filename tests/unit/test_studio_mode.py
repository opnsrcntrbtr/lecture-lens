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
