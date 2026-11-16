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


# ------------------------------------------------------------------ questions v2 (NG-5)
class Stub2:
    """Three calls per round: units, questions, answered-routing."""
    def __init__(self, units, questions, answered):
        self.replies = [json.dumps(units), json.dumps(questions), json.dumps(answered)]
        self.prompts = []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies[min(len(self.prompts) - 1, 2)]


UNITS = {"units": [
    {"kind": "claim", "summary": "Raised beds drain faster so water them more often", "line_ids": ["L2", "L3"]},
    {"kind": "definition", "summary": "Mulch keeps soil moist through dry weeks", "line_ids": ["L1"]},
    {"kind": "number", "summary": "Unicorn soil needs nine litres", "line_ids": ["L99"]},          # no such line
    {"kind": "claim", "summary": "Rockets need fuel", "line_ids": ["L1"]}]}                      # not stated there


def test_v2_round_routes_piles_and_never_drafts_does_not_specify(tmp_path):
    d = folder(tmp_path)
    qs = {"questions": [
        {"unit_id": "u1", "q": "You said raised beds drain faster; how often should raised beds be watered in summer heat?"},
        {"unit_id": "u2", "q": "How does mulch keep soil moist through dry weeks?"},
        {"unit_id": "u2", "q": "Two? Questions?"}]}
    llm = Stub2(UNITS, qs, {"answered": {"Q1": [1], "Q2": []}})
    r = curate.run_v2(d, llm, T0 + dt.timedelta(minutes=40), "Garden class", guard=lambda ls: ls)
    assert r["round"] == 1 and r["added"] == 2
    rows = [json.loads(l) for l in (d / "questions.jsonl").read_text().splitlines()]
    by = {x["pile"]: x for x in rows}
    assert by["self_check"]["answered_in_class"] and "Raised beds drain faster" in by["self_check"]["draft_answer"]
    assert by["ask"]["draft_answer"] is None and by["ask"]["answer"] == ""
    assert all(not curate_dns(x) for x in rows)
    assert all(x["line_ids"] and x["line_ids"][0].startswith("live-live/t/") for x in rows)
    assert {x["requested_level"] for x in rows} <= set(curate.LEVELS) and {x["type"] for x in rows} <= set(curate.TYPES2)
    assert "TYPE clarify" in llm.prompts[1] or "TYPE challenge" in llm.prompts[1] or "TYPE practical" in llm.prompts[1]
    md = (d / "top_questions.md").read_text()
    assert "## Ask now" in md and "## Self-check later" in md
    m = json.loads((d / "curate_metrics.json").read_text())
    assert m["rounds"] == 1 and m["ask"] == 1 and m["self_check"] == 1


def curate_dns(row):
    import re as _re
    return bool(_re.search(r"does not specify|doesn't specify|not specified", str(row.get("answer") or ""), _re.I))


def test_v2_units_must_cite_real_lines_that_state_them():
    lines = [{"line_id": f"x/t/{i}", "t": None, "text": t} for i, t in enumerate(
        ["Mulch keeps soil moist through dry weeks.", "Raised beds drain faster.", "So water raised beds more often."])]
    us = curate.parse_units(json.dumps(UNITS), lines)
    assert [u["summary"] for u in us] == ["Raised beds drain faster so water them more often",
                                          "Mulch keeps soil moist through dry weeks"]
    assert us[0]["line_ids"] == ["x/t/1", "x/t/2"]


def test_v2_dedupes_against_the_day_and_records_similarity():
    units = [{"unit_id": "u1", "kind": "claim", "summary": "beds drain", "quote": "raised beds drain faster water more often",
              "line_ids": ["a"], "t": None, "type": "clarify", "requested_level": "apply"}]
    raw = json.dumps({"questions": [{"unit_id": "u1", "q": "How often should raised beds be watered?"}]})
    assert curate.parse_questions(raw, units, ["How often should raised beds be watered in summer?"], set()) == []
    got = curate.parse_questions(raw, units, ["Why does compost feed worms?"], set())
    assert len(got) == 1 and got[0]["max_sim_prior"] < 0.2


def test_v2_round_skips_over_its_time_box(tmp_path):
    d = folder(tmp_path)
    ticks = iter([0, 500, 500, 500])
    llm = Stub2(UNITS, {"questions": []}, {"answered": {}})
    r = curate.run_v2(d, llm, T0 + dt.timedelta(minutes=40), "Garden class", time_box_s=240, clock=lambda: next(ticks))
    assert r["added"] == 0 and "time box" in r["note"]
    assert json.loads((d / "curate_metrics.json").read_text())["skipped"][0]["at"] == "units"


def test_question_labels_m7(tmp_path, monkeypatch, capsys):
    import argparse
    import lecture_kit as k
    live = tmp_path / "lectures" / "live" / "20300101"
    live.mkdir(parents=True)
    (live / "questions.jsonl").write_text("".join(json.dumps({"q": f"Q{i}?", "answer": "", "round": 1}) + "\n" for i in range(3)))
    monkeypatch.setattr(k, "LECTURES", tmp_path / "lectures")
    monkeypatch.setattr(k, "HERE", tmp_path)
    ns = lambda **kw: argparse.Namespace(**{"action": "label", "day": "20300101", "i": 1, "would_ask": "y",
                                            "duplicate": "n", "answered": "y", "json": True, **kw})
    k.cmd_qlabels(ns())
    out = json.loads(capsys.readouterr().out)
    assert out["labelled"] == 1 and [t["i"] for t in out["todo"]] == [0, 2] and out["would_ask_share"] == 1.0
    lab = json.loads((tmp_path / "eval" / "golden" / "questions_labels_20300101.jsonl").read_text())
    assert lab["answered_in_class"] is True and lab["duplicate"] is False and "q" not in lab
    k.cmd_qlabels(ns(i=7))
    assert json.loads(capsys.readouterr().out)["ok"] is False
