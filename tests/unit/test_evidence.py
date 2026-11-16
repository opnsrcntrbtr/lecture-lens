"""Evidence store (NG-1) and citations v3 (NG-2). Invented fixtures: a gardening class."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import cite      # noqa: E402
import evidence  # noqa: E402

UTC = dt.timezone.utc
T0 = dt.datetime(2030, 1, 1, 5, 0, tzinfo=UTC)
TRANSCRIPT = """# Gardening 101 — transcript

_captured_
[00:00:10] Welcome to the class on soil and water.
[00:01:00] Mulch keeps soil moist through dry weeks.
[00:01:30] Compost feeds the worms that loosen clay.
[00:05:00] Water early in the morning so leaves dry before evening.
[00:05:40] Raised beds drain faster, so water raised beds more often.
[00:09:00] Tomatoes need staking once they pass knee height.
"""
SLIDES = """# Slides

## 1. at 00:00:50

![[01_000050.jpg]]

Soil basics: mulch, compost and clay

## 2. at 00:04:50

![[02_000450.jpg]]

Watering raised beds
"""


def make_lecture(tmp_path, name="20300101-0500_Gardening-101", transcript=TRANSCRIPT):
    f = tmp_path / name
    (f / "slides").mkdir(parents=True)
    (f / "transcript.md").write_text(transcript)
    (f / "slides.md").write_text(SLIDES)
    (f / "slides" / "01_000050.jpg").write_bytes(b"x")
    (f / "manifest.json").write_text(json.dumps({"title": "Gardening 101", "start": T0.isoformat(), "minutes": 10}))
    return f


def build(tmp_path, guard=lambda ls: ls, embed=None, model=None):
    f = make_lecture(tmp_path)
    title, recs = evidence.lecture_records(f, guard)
    con = evidence.connect(tmp_path / "index.sqlite")
    rep = evidence.index_lecture(con, f.name, title, recs, [r["t0"] for r in recs if r["kind"] == "s"],
                                 embed=embed, embed_model=model, guard_version="2")
    return con, f, rep


def test_records_carry_stable_ids_times_and_slide_files(tmp_path):
    f = make_lecture(tmp_path)
    title, recs = evidence.lecture_records(f, lambda ls: ls)
    assert title == "Gardening 101"
    t = [r for r in recs if r["kind"] == "t"]
    assert [r["idx"] for r in t] == list(range(6)) and t[1]["t0"] == 60.0
    assert t[1]["t_abs"] == (T0 + dt.timedelta(seconds=60)).isoformat()
    s = [r for r in recs if r["kind"] == "s"]
    assert [(r["idx"], r["t0"], r["slide_file"]) for r in s] == [(1, 50.0, "01_000050.jpg"), (2, 290.0, "02_000450.jpg")]


def test_guard_runs_before_anything_is_stored(tmp_path):
    seen = []
    guard = lambda ls: (seen.extend(ls), [l.replace("worms", "[redacted]") for l in ls])[1]
    con, _, _ = build(tmp_path, guard=guard)
    assert any("worms" in l for l in seen)
    assert not con.execute("SELECT count(*) FROM lines WHERE text LIKE '%worms%'").fetchone()[0]
    assert not con.execute("SELECT count(*) FROM chunks WHERE text LIKE '%worms%'").fetchone()[0]


def test_passages_split_at_slide_boundaries_and_keep_line_ids(tmp_path):
    con, f, rep = build(tmp_path)
    key = evidence.lecture_key(f.name)
    assert key.startswith("20300101-0500.") and rep["chunks"] == 6   # 4 transcript passages + 2 slides
    rows = {r["chunk_id"]: json.loads(r["line_ids"]) for r in con.execute("SELECT * FROM chunks WHERE source='transcript'")}
    assert rows[f"{key}/t/0-0"] == [f"{key}/t/0"]                       # before slide 1 at 00:00:50
    assert rows[f"{key}/t/1-2"] == [f"{key}/t/1", f"{key}/t/2"]         # slide 1 window
    assert rows[f"{key}/t/3-4"] == [f"{key}/t/3", f"{key}/t/4"]         # slide 2 window from 00:04:50
    assert rows[f"{key}/t/5-5"] == [f"{key}/t/5"]                       # after a silence over two minutes
    ctx = con.execute("SELECT context FROM chunks WHERE chunk_id=?", (f"{key}/t/1-2",)).fetchone()[0]
    assert ctx.startswith("Gardening 101 · Soil basics") and ctx.endswith("00:01:00")


def test_two_folders_in_the_same_minute_get_different_keys():
    a, b = evidence.lecture_key("20300101-0500_Gardening-101"), evidence.lecture_key("20300101-0500_Webinar")
    assert a != b and a.split(".")[0] == b.split(".")[0] == "20300101-0500"
    assert evidence.lecture_key("live/20300101") == "live-20300101"


def test_rebuild_is_idempotent_and_reembeds_only_changes(tmp_path):
    calls = []
    def embed(texts):
        calls.append(len(texts))
        return [[float(len(t)), 1.0, 0.5] for t in texts]
    con, f, rep = build(tmp_path, embed=embed, model="toy-embed")
    assert rep["embedded"] == 6 and sum(calls) == 6
    title, recs = evidence.lecture_records(f, lambda ls: ls)
    again = evidence.index_lecture(con, f.name, title, recs, [r["t0"] for r in recs if r["kind"] == "s"],
                                   embed=embed, embed_model="toy-embed")
    assert again["embedded"] == 0 and again["unchanged"] == 6 and sum(calls) == 6
    (f / "transcript.md").write_text(TRANSCRIPT.replace("knee height", "waist height"))
    title, recs = evidence.lecture_records(f, lambda ls: ls)
    third = evidence.index_lecture(con, f.name, title, recs, [r["t0"] for r in recs if r["kind"] == "s"],
                                   embed=embed, embed_model="toy-embed")
    assert third["embedded"] == 1 and third["unchanged"] == 5
    assert con.execute("SELECT value FROM meta WHERE key='embed_model'").fetchone()[0] == "toy-embed"


def test_lexical_search_ranks_by_rare_words_and_filters_by_time(tmp_path):
    con, f, _ = build(tmp_path)
    hits = evidence.search(con, "how often should raised beds be watered", k=3, sources=("transcript",))
    assert hits and hits[0]["chunk_id"].endswith("/t/3-4") and hits[0]["score_dense"] is None
    near = evidence.search(con, "raised beds water", k=5, near=(T0 + dt.timedelta(seconds=30), 60))
    assert all(h["chunk_id"].split("/")[-1] in ("0-0", "1-2", "1") for h in near)
    assert evidence.search(con, "the and of", k=3) == []


def test_dense_leg_fuses_by_reciprocal_rank(tmp_path):
    vocab = ["tomato", "stak", "mulch", "water"]
    embed = lambda texts: [[t.lower().count(w) + 0.01 for w in vocab] for t in texts]
    con, f, _ = build(tmp_path, embed=embed, model="toy")
    hits = evidence.search(con, "tomato staking", k=2, embed=embed, embed_model="toy", sources=("transcript",))
    assert hits[0]["chunk_id"].endswith("/t/5-5") and hits[0]["score_dense"] is not None


def test_split_sentences_skips_questions_and_bullets():
    s = cite.split_sentences("Water raised beds more often [00:05:40]. Why? Because they drain.\n- Mulch keeps soil moist.")
    assert s == ["Water raised beds more often [00:05:40].", "Because they drain.", "Mulch keeps soil moist."]


def _lint(con, text, checker=None):
    search = lambda q, k=8: evidence.search(con, q, k=k)
    return cite.lint(text, search=search, lines_of=lambda ids: evidence.lines_of(con, ids), checker=checker,
                     valid_slides=2, transcript_segments=[], duration_s=600)


def test_uncalibrated_checker_never_claims_support(tmp_path):
    con, _, _ = build(tmp_path)
    r = _lint(con, "Raised beds drain faster and need more frequent watering. Penguins migrate south in winter.")
    assert [s["status"] for s in r["sentences"]] == ["related", "unsupported"]
    c = r["sentences"][0]["citations"][0]
    assert c["quote"].startswith("Raised beds drain faster") and c["line_ids"][0].endswith("/t/4")
    assert c["t0"] == 340.0 and c["support_p"] is not None
    assert r["checker"]["calibrated"] is False
    assert r["metrics"]["citation_recall"] is None and r["metrics"]["related"] == 1


def test_calibrated_checker_marks_supported_and_unsupported(tmp_path):
    con, _, _ = build(tmp_path)
    ck = cite.Checker("toy", "1", 0.5, lambda s, ps: [1.0 if "drain" in p and "drain" in s else 0.0 for p in ps])
    cal = cite.calibrate(ck, [{"sentence": "beds drain", "passage": "beds drain fast", "label": 1},
                              {"sentence": "beds drain", "passage": "tomatoes need stakes", "label": 0}], target=0.85)
    assert cal["passes"] and cal["tpr"] == 1.0 and cal["tnr_ci"][1] == 1.0
    ck = cite.calibrated(ck, cal)
    r = _lint(con, "Raised beds drain faster. Mulch keeps soil moist through dry weeks.", checker=ck)
    assert [s["status"] for s in r["sentences"]] == ["supported", "unsupported"]
    assert r["metrics"]["citation_recall"] == 0.5 and r["metrics"]["citation_precision"] is not None


def test_calibration_must_match_the_exact_checker():
    ck = cite.Checker("toy", "2", 0.5, lambda s, ps: [1.0] * len(ps))
    cal = {"passes": True, "checker": {"name": "toy", "version": "1", "threshold": 0.5}}
    assert cite.calibrated(ck, cal).calibrated is False
    assert cite.calibrated(ck, None).calibrated is False


def test_one_linter_verify_citations_is_lint_without_search():
    text = "Water early [00:05:00]. See [slide 9]."
    segs = [{"at": "00:05:00", "text": "Water early in the morning"}]
    assert cite.verify_citations(text, 2, segs, duration_s=600) == cite.lint(
        text, valid_slides=2, transcript_segments=segs, duration_s=600)["warnings"]
    assert {w["kind"] for w in cite.verify_citations(text, 2, segs, duration_s=600)} == {"slide_out_of_range"}


def test_llm_checker_parses_constrained_reply():
    ck = cite.llm_checker(lambda prompt: 'sure: {"support": [2]}')
    assert ck.fn("claim", ["a", "b", "c"]) == [0.0, 1.0, 0.0]
    assert cite.llm_checker(lambda p: "no json").fn("claim", ["a"]) == [0.0]


def test_wilson_interval():
    lo, hi = cite.wilson(85, 100)
    assert 0.76 < lo < 0.78 and 0.90 < hi < 0.92


def test_checker_label_moves_a_pair_from_todo_to_labelled(tmp_path, capsys):
    import argparse
    import lecture_kit as k
    todo, done = tmp_path / "todo.jsonl", tmp_path / "pairs.jsonl"
    todo.write_text("".join(json.dumps({"sentence": f"s{i}", "passage": f"p{i}", "label": None}) + "\n" for i in range(3)))
    ns = lambda **kw: argparse.Namespace(**{"action": "label", "out": str(todo), "pairs": str(done), "i": 1, "label": 1, **kw})
    k.cmd_checker(ns())
    out = json.loads(capsys.readouterr().out)
    assert out["labelled"] == 1 and out["remaining"] == 2 and [p["sentence"] for p in out["todo"]] == ["s0", "s2"]
    assert json.loads(done.read_text())["label"] == 1 and json.loads(done.read_text())["sentence"] == "s1"
    k.cmd_checker(ns(i=9))
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_ask_answers_from_built_lectures_when_screenpipe_is_down(tmp_path, monkeypatch, capsys):
    import argparse
    import study
    con, f, _ = build(tmp_path)
    con.close()
    monkeypatch.setenv("STUDY_LECTURE_DIR", str(tmp_path))
    monkeypatch.setattr(study, "SP", "http://127.0.0.1:9")          # nothing listens there
    asked = {}
    def fake_llm(system, user, **kw):
        asked["user"] = user
        return "Water raised beds more often [1]."
    monkeypatch.setattr(study, "llm", fake_llm)
    monkeypatch.setattr(study, "parse_window", lambda since, date: (T0 - dt.timedelta(days=1), T0 + dt.timedelta(days=1)))
    a = argparse.Namespace(question="how often should raised beds be watered", since="100000d", date=None,
                           app=None, think=False, json=True, show_sources=False)
    study.cmd_ask(a)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["retrieval"] == "lecture index" and "not running" in out["note"]
    assert out["sources"][0]["src"].startswith("lecture") and "Raised beds drain faster" in asked["user"]


def test_wall_clock_timecodes_in_a_merged_transcript(tmp_path):
    f = make_lecture(tmp_path, transcript="# t\n\n_\n[10:05:00] Mulch keeps soil moist.\n[10:09:30] Water early.\n")
    import os, time
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"; time.tzset()
    try:
        _, recs = evidence.lecture_records(f, lambda ls: ls)
    finally:
        if old is None: os.environ.pop("TZ")
        else: os.environ["TZ"] = old
        time.tzset()
    t = [r for r in recs if r["kind"] == "t"]
    assert t[0]["t_abs"].startswith("2030-01-01T10:05:00") and t[0]["t0"] == (10 * 60 + 5 - 5 * 60) * 60.0
