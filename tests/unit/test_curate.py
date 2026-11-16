"""Curated top-question rounds: window since the last round, parsing, asked matching and the
markdown section. The model is a stub; fixtures are invented."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import curate  # noqa: E402

T0 = dt.datetime(2030, 1, 1, 5, 0, tzinfo=dt.timezone.utc)
iso = lambda m: (T0 + dt.timedelta(minutes=m)).isoformat()


def folder(tmp_path, rows=()):
    d = tmp_path / "live"; d.mkdir()
    (d / "whisper_segments.jsonl").write_text("".join(json.dumps({"t": iso(m), "text": f"Raised beds drain faster so water raised beds more often, said at minute {m}, with mulch keeping soil moist."}) + "\n" for m in range(0, 40)))
    (d / "slides.jsonl").write_text(json.dumps({"t": iso(30), "title": "Watering raised beds"}) + "\n")
    (d / "qa_threads.json").write_text(json.dumps([{"id": "a", "q": "Can moss grow on glass?", "answers": []}]))
    (d / "evidence.json").write_text(json.dumps([{"id": "S1", "title": "Garden guide", "fact": "Mulch holds water."}]))
    (d / "questions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "agenda.txt").write_text("Part 3: which sensor tells us the soil is dry, and when to water.")
    return d


RAW = json.dumps([
    {"part": "now", "type": "clarify", "q": "How often should raised beds be watered in summer?", "answer": "The lecture said more often; mulch helps (S1) [3].", "sources": ["S1", "S9"]},
    {"part": "later", "for": "Part 3", "type": "practical", "q": "Which sensor tells us the soil is dry?", "answer": "Not covered yet."},
    {"part": "now", "type": "clarify", "q": "Can moss grow on glass?", "answer": "Repeat of the Q&A."},
    {"part": "now", "type": "odd", "q": "Two questions? Really?", "answer": "Dropped."},
])


def test_round_appends_rows_and_markdown(tmp_path):
    d = folder(tmp_path, [{"t": iso(20), "q": "Old curated?", "answer": "x", "sources": [], "curated": True, "round": 2}])
    seen = {}
    def llm(p):
        seen["p"] = p
        return RAW
    r = curate.run(d, llm, T0 + dt.timedelta(minutes=40), "Garden class", guard=lambda ls: [l.replace("Zorvan", "an attendee") for l in ls])
    assert r["round"] == 3 and r["added"] == 2
    assert "minute 19" not in seen["p"] and "minute 25" in seen["p"]      # only since the last round
    rows = [json.loads(l) for l in (d / "questions.jsonl").read_text().splitlines()]
    new = [x for x in rows if x.get("round") == 3]
    assert new[0]["sources"] == ["S1"] and "[3]" not in new[0]["answer"] and new[1]["part"] == "later"
    md = (d / "top_questions.md").read_text()
    assert "# Round 3 (" in md and "## Ask now" in md and "## Keep for later" in md and "- S1: Garden guide" in md
    assert md.index("# Round 3") < md.index("## Sources")


def test_not_enough_transcript_is_a_note(tmp_path):
    d = folder(tmp_path)
    (d / "whisper_segments.jsonl").write_text(json.dumps({"t": iso(39), "text": "Short."}) + "\n")
    r = curate.run(d, lambda p: RAW, T0 + dt.timedelta(minutes=40), "Garden class")
    assert r["added"] == 0 and "not enough" in r["note"]


def test_asked_in_matches_by_words():
    threads = [{"q": "How often should raised beds be watered in the summer heat?", "time": "11:12 AM"}]
    assert curate.asked_in("How often should raised beds be watered in summer?", threads)["time"] == "11:12 AM"
    assert curate.asked_in("Which sensor tells us the soil is dry?", threads) is None


def test_reworded_repeats_and_ungrounded_now_questions_are_dropped(tmp_path):
    d = folder(tmp_path, [{"t": iso(20), "q": "How often should raised beds be watered in summer heat?", "answer": "x",
                           "sources": [], "curated": True, "round": 1}])
    raw = json.dumps([
        {"part": "now", "q": "In summer heat, how often should raised beds be watered?", "answer": "Repeat."},
        {"part": "now", "q": "Does a self-driving taxi ask a remote person for help?", "answer": "Off topic."},
        {"part": "now", "q": "Does mulch keep soil moist enough to water raised beds less often?", "answer": "Yes (S1)."},
    ])
    r = curate.run(d, lambda p: raw, T0 + dt.timedelta(minutes=40), "Garden class")
    assert [q["q"] for q in r["questions"]] == ["Does mulch keep soil moist enough to water raised beds less often?"]


def test_tidy_drops_tags_and_transcript_wording():
    assert curate.tidy("The transcript mentions mulch [Transcript]. See [slide 3] and (S1).") == "The lecture mentions mulch. See and (S1)."


def test_a_replayed_round_never_reads_lines_after_now(tmp_path):
    d = folder(tmp_path)
    seen = {}
    def llm(p):
        seen["p"] = p
        return RAW
    curate.run(d, llm, T0 + dt.timedelta(minutes=20), "Garden class", lookback_min=10)
    assert "minute 15" in seen["p"] and "minute 25" not in seen["p"] and "minute 39" not in seen["p"]
