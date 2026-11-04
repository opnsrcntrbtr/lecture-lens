"""Unit tests for the pure parts of lecture_kit and transcript_ref. No model,
no database, no running capture: these run in seconds on every change.

    .venv-eval/bin/python -m pytest tests/unit -q
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as k          # noqa: E402
import transcript_ref as tr      # noqa: E402

UTC = dt.timezone.utc


def frame(minute, text="", app="Zoom", trigger="visual_change", second=0):
    return {"ts": dt.datetime(2026, 10, 3, 4, minute, second, tzinfo=UTC), "window_name": "Zoom Webinar",
            "browser_url": None, "text": text, "capture_trigger": trigger, "app_name": app,
            "snapshot_path": None, "video_chunk_id": 1, "offset_index": 0, "id": minute * 60 + second}


# --- long transcripts: nothing may be lost when a session exceeds one context ---
def test_long_transcript_is_fully_covered():
    lines = [f"[00:{i // 60:02d}:{i % 60:02d}] " + "word " * 40 for i in range(450)]   # ~95,000 chars
    parts = k._split_lines(lines, 24000)
    assert len(parts) >= 4
    assert "\n".join(parts) == "\n".join(lines)
    assert all(len(p) <= 24000 for p in parts)


def test_split_keeps_an_oversized_line_whole():
    parts = k._split_lines(["x" * 30000, "short"], 24000)
    assert parts == ["x" * 30000, "short"]


# --- sessions ---
def test_gap_splits_sessions_and_short_ones_are_dropped():
    fr = [frame(m) for m in (0, 2, 5, 8)] + [frame(m) for m in (30, 31)]
    ss = k.sessions(fr, gap_min=10)
    assert len(ss) == 1 and ss[0]["minutes"] == 8.0


def test_window_session_keeps_early_audio(monkeypatch):
    """A live class: audio from 10:06, first saved frame at 10:16. The session must start at 10:06."""
    monkeypatch.setattr(k, "frames_since", lambda con, start: [frame(46), frame(50), frame(59)])
    start = dt.datetime(2026, 10, 3, 4, 36, tzinfo=UTC); end = dt.datetime(2026, 10, 3, 5, 59, tzinfo=UTC)
    s = k.window_session(None, start, end, "Intro class")
    assert s["start"] == start and s["minutes"] == 83.0 and len(s["frames"]) == 3
    assert s["title"] == "Intro class"


def test_window_session_without_frames_still_builds(monkeypatch):
    monkeypatch.setattr(k, "frames_since", lambda con, start: [])
    s = k.window_session(None, dt.datetime(2026, 10, 3, 4, 36, tzinfo=UTC), dt.datetime(2026, 10, 3, 5, 0, tzinfo=UTC))
    assert s["frames"] == [] and s["title"] == "Untitled lecture"


def test_local_time_accepts_clock_and_iso():
    t = k._local_time("10:06", day=dt.datetime(2026, 10, 3, 12, 0))
    assert t.tzinfo is not None and t.astimezone().strftime("%H:%M") == "10:06"
    assert k._local_time("2026-10-03T10:06:00+05:30") == dt.datetime(2026, 10, 3, 4, 36, tzinfo=UTC)


def test_zoom_window_title_is_generic():
    assert k.GENERIC_TITLE.match("Zoom Webinar") and k.GENERIC_TITLE.match("zoom")
    assert not k.GENERIC_TITLE.match("Unit economics of LLM products")


# --- keyframes: slides from one deck share a template ---
def test_ui_chrome_is_not_slide_text():
    toks = k._text_tokens("Audio settings Audio settings Open chat panel 1 new message Leave UNIT 4 Photosynthesis")
    assert "audio" not in toks and "unit" in toks and "photosynthesis" in toks


def test_two_slides_of_one_deck_differ_by_text():
    a = k._text_tokens("UNIT 4 WEEKS 8-10 Photosynthesis light reactions chloroplast thylakoid membrane ATP synthase gradient")
    b = k._text_tokens("UNITS 07-09 Cellular respiration glycolysis Krebs cycle mitochondria electron transport chain yield")
    assert k._jaccard(a, b) < 0.5 and k._jaccard(a, a) == 1.0


# --- cards ---
def test_salvage_reads_objects_from_broken_json():
    raw = '[{"q": "a?", "a": "b"}, {"q": "c?", "a": "d"}, {"q": "trunc'
    got = k._salvage_json_objects(raw)
    assert [c["q"] for c in got] == ["a?", "c?"]


# --- transcript comparison ---
def test_wer_counts_deletions_separately():
    ref = "the quick brown fox jumps over the lazy dog".split()
    hyp = "the quick fox jumps over a lazy dog".split()
    s, d, i = tr.align(ref, hyp)
    assert (s, d, i) == (1, 1, 0)


def test_only_disfluencies_are_dropped():
    # "so" and "right" are often real words; dropping them hid real deletions.
    assert tr.words("Uh, so, the model, right, is non-deterministic") == \
        ["so", "the", "model", "right", "is", "non", "deterministic"]
    assert tr.normalize("You know, all of it") == ["you", "know", "all", "of", "it"]


def test_normaliser_makes_formatting_differences_free():
    assert tr.normalize("twenty five planets") == tr.normalize("25 planets") == ["25", "planets"]
    assert tr.normalize("one hundred and twenty stars") == ["120", "stars"]
    assert tr.normalize("two thousand and six") == ["2006"]
    assert tr.normalize("We don't know; it's 0.82.") == ["we", "do", "not", "know", "it", "is", "0.82"]
    assert tr.normalize("10,000 cells (inaudible) grew 5%") == ["10000", "cells", "grew", "5", "percent"]
    assert tr.normalize("one and two") == ["1", "and", "2"]
    assert tr.normalize("nine eleven") == ["9", "11"]
    assert tr.normalize("seventy two thousand four hundred") == ["72400"]


def test_normalised_wer_ignores_formatting_only_differences():
    ref = tr.normalize("The probe reached twenty-five kilometres, didn't it?")
    hyp = tr.normalize("the probe reached 25 kilometres did not it")
    assert tr.align(ref, hyp) == (0, 0, 0)


def test_zoom_export_is_parsed_with_speakers(tmp_path):
    f = tmp_path / "z.txt"
    f.write_text("10:01:09 --> 10:01:18\nHost: Good morning.\n\n10:17:43 --> 10:18:01\nProf. Ada Example: Welcome.\n")
    z = tr.parse_zoom(f)
    assert z[1][2] == "Prof. Ada Example" and z[0][0] == 10 * 3600 + 69


# --- eval harness ---
def test_judge_scores_the_answer_not_its_source_list():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
    import generate_outputs as go
    ans = "H2 takes 12-24 months [2].\n\n**Sources used:**\n- [2] Tue 29 Sep 15:31 · audio · learn.example.edu"
    assert go.answer_body(ans) == "H2 takes 12-24 months [2]."
    assert go.answer_body("No list here.") == "No list here."


# --- recall-first notes (retrieval before rereading) ---
def test_recall_first_prefers_reasoning_cards_and_keeps_notes():
    notes = "## TL;DR\n- Stars fuse hydrogen."
    cards = [{"q": "What is a light year?", "a": "A distance", "kind": "recall"},
             {"q": "Why do stars shine?", "a": "Fusion releases energy", "kind": "why", "at": "00:04:00"},
             {"q": "Red dwarf or giant: which lives longer?", "a": "Red dwarf", "kind": "compare"},
             {"q": "A star runs out of hydrogen: what next?", "a": "It expands", "kind": "apply"}]
    out = k.recall_first(notes, cards)
    assert out.startswith("## Before you read")
    head, body, tail = out.split("\n\n---\n\n")
    assert body == notes                                   # notes untouched, in the middle
    assert "light year" not in head and "Fusion" not in head   # no answers before the notes
    assert tail.splitlines()[2:] == ["1. Fusion releases energy [00:04:00]", "2. Red dwarf", "3. It expands"]
    assert head.count("\n1. ") + head.count("\n2. ") + head.count("\n3. ") == 3


def test_recall_first_without_cards_returns_notes_unchanged():
    assert k.recall_first("notes", []) == "notes"
    assert "1. Only recall?" in k.recall_first("notes", [{"q": "Only recall?", "a": "Yes", "kind": "recall"}])
