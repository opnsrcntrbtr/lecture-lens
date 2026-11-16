"""CC-3 readiness, CC-5 panel simulator and CC-6 evaluation, with invented artefacts and fake models."""
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone as cs              # noqa: E402
import capstone_eval as ce         # noqa: E402
import capstone_panel as cp        # noqa: E402
import capstone_readiness as rd    # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "capstone_samples"
GOLDEN = "Release threshold: reorder precision at least 0.85 on the golden set"


def ws_with(tmp_path):
    for f in ("prd.md", "strategy.md"):
        shutil.copy(SAMPLES / "A" / f, tmp_path / f)
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("prd", tmp_path / "prd.md", "prd")
    ws.register("strategy", tmp_path / "strategy.md", "strategy")
    return ws


# ------------------------------------------------------------------ CC-3
def test_demonstrated_needs_a_real_quote(tmp_path):
    r = rd.Readiness(ws_with(tmp_path))
    with pytest.raises(ValueError):
        r.rate("eval_golden_set", "demonstrated")
    with pytest.raises(ValueError):
        r.rate("eval_golden_set", "demonstrated", {"artefact_id": "prd", "quote": "we have a great golden set"})
    row = r.rate("eval_golden_set", "demonstrated", {"artefact_id": "prd", "quote": GOLDEN})
    assert row["evidence"]["quote"] == GOLDEN
    assert r.rate("latency_budget", "partial")["evidence"] is None
    assert r.rate("rollback_time", "not_observed")["rating"] == "not_observed"


def test_editing_the_artefact_makes_evidence_stale(tmp_path):
    ws = ws_with(tmp_path)
    r = rd.Readiness(ws)
    r.rate("eval_golden_set", "demonstrated", {"artefact_id": "prd", "quote": GOLDEN})
    (tmp_path / "prd.md").write_text((tmp_path / "prd.md").read_text().replace("0.85", "0.9"))
    ws.refresh()
    st = {i["item_id"]: i["rating"] for i in r.status()["items"]}
    assert st["eval_golden_set"] == "stale" and st["latency_budget"] == "unrated"


def test_eval_run_pointer_and_ready_flag(tmp_path):
    ws = ws_with(tmp_path)
    (ws.root / "eval" / "judge_card.json").write_text("[]")
    r = rd.Readiness(ws)
    with pytest.raises(ValueError):
        r.rate("judge_agreement", "demonstrated", {"eval_run_id": "nope"})
    r.rate("judge_agreement", "demonstrated", {"eval_run_id": "judge_card"})
    assert r.status()["ready"] is False
    for i in rd.ITEMS:
        if i["gate"] in rd.GATES:
            r.rate(i["item_id"], "demonstrated", {"eval_run_id": "judge_card"})
    assert r.status()["ready"] is True


def test_candidates_point_at_passages(tmp_path):
    c = rd.candidates(ws_with(tmp_path), "eval_golden_set")
    assert c and any("golden set" in x["quote"].lower() for x in c)


def test_governance_triage_is_a_checklist():
    snap = cs.GOVERNANCE_SNAPSHOT
    t = rd.triage({"users_in_eu": "y", "chatbot": "y"}, snap)
    assert [i["obligation"][:10] for i in t["to_verify"]] == ["Article 50"]
    assert all(i["verify"] and i["as_of"] == snap["as_of"] for i in t["to_verify"])
    assert len(rd.triage({"india_personal_data": "yes"}, snap)["to_verify"]) == 2
    assert rd.triage({}, snap)["to_verify"] == [] and "not legal advice" in t["note"]


def test_system_card_audit_lists_missing_sections():
    a = rd.audit_card("# System card\n## Intended use\nPlans meals.\n## Limitations\nNo allergens yet.\n"
                      "It hands off to a person when unsure.")
    assert "intended_use" in a["present"] and "limitations" in a["present"]
    assert "fallback" in a["mentioned_only"] and "data_handling" in a["missing"] and not a["complete"]


# ------------------------------------------------------------------ CC-5
class Fake:
    """Scripted model: returns queued replies in order, then the default."""
    def __init__(self, *replies, default='{"question": "What number backs the size of this opportunity?"}'):
        self.q, self.default, self.calls = list(replies), default, 0

    def __call__(self, system, user):
        self.calls += 1
        return self.q.pop(0) if self.q else self.default


T0 = dt.datetime(2030, 1, 5, 10, 0, tzinfo=dt.timezone.utc)
ANSWER = "We sized it from 300 pilot households and their weekly reorders over two months."


def test_one_question_per_turn_and_no_concessions(tmp_path):
    ws = ws_with(tmp_path)
    p = cp.Panel(ws, 7, day="2030-01-05")
    p.start(now=T0)
    v = p.next_question(Fake('{"question": "Fair point. But what is the size? And who pays?"}',
                             '{"question": "You are right, that is convincing?"}'), now=T0)
    first = v["turns"][0]
    assert first["text"].count("?") == 1 and "Fair point" not in first["text"]
    assert p.turns()[0]["source"] == "template" and "concession" in p.turns()[0]["rejected"][0]
    assert p.next_question(Fake(), now=T0)["error"] == "answer the current question first"


def test_followup_quotes_the_answer_or_falls_back(tmp_path):
    ws = ws_with(tmp_path)
    p = cp.Panel(ws, 8, day="2030-01-05")
    p.start(now=T0)
    p.next_question(Fake(), now=T0)
    p.answer(ANSWER, now=T0)
    good = json.dumps({"quote": "300 pilot households and their weekly",
                       "question": 'You said "300 pilot households and their weekly" reorders: how many dropped out?'})
    v = p.next_question(Fake(good), now=T0)
    assert v["turns"][-1]["is_followup"] and p.turns()[-1]["source"] == "model"
    p.answer("About forty stopped in the first month because of delivery slots.", now=T0)
    # next is a fresh primary question (one follow-up per answer)
    v = p.next_question(Fake(), now=T0)
    assert not v["turns"][-1]["is_followup"]
    p.answer("Because the pilot showed it.", now=T0)
    v = p.next_question(Fake('{"quote": "not in the answer at all", "question": "Why?"}',
                             '{"quote": "Because", "question": "Because what?"}'), now=T0)
    t = p.turns()[-1]
    assert t["is_followup"] and t["source"] == "template" and t["quoted_span"] in "Because the pilot showed it."
    m = p.metrics()
    assert m["stacked_question_rate"] == 0 and m["followups"] == 2


def test_template_followup_has_five_words():
    q, quote = cp.template_followup(ANSWER)
    assert len(quote.split()) >= 5 and quote in ANSWER and q.count("?") == 1 and len(q.split()) <= 40


def test_time_box_ends_the_session(tmp_path):
    p = cp.Panel(ws_with(tmp_path), 9, day="2030-01-05")
    p.start(minutes=10, now=T0)
    v = p.next_question(Fake(), now=T0 + dt.timedelta(minutes=11))
    assert v["done"] and v["note"] == "time is up"


def test_twenty_pushbacks_never_concede(tmp_path):
    """M-12 by construction: a sycophantic model's concessions never reach the learner."""
    ws = ws_with(tmp_path)
    p = cp.Panel(ws, 10, day="2030-01-05")
    p.start(minutes=600, now=T0)
    syco = '{"quote": "I still think it works fine", "question": "You are right, I agree, so what next?"}'
    shown = 0
    for i in range(20):
        st = p.state()
        if not st["queue"] and not st["awaiting_answer"] and p.turns() and p.turns()[-1]["is_followup"]:
            st["queue"].append(["methodologist", "measurable_goals"])
            p._save(st)
        if not p.state()["awaiting_answer"]:
            p.next_question(Fake(default=syco), now=T0)
        p.answer("I still think it works fine, trust me on this one.", now=T0)
        v = p.next_question(Fake(default=syco), now=T0)
        shown += sum(1 for t in v["turns"] if t["speaker"] != "learner" and cp.CONCEDE.search(t["text"]))
        if p.state()["awaiting_answer"]:
            p.answer("Again, it just works.", now=T0)
    assert shown == 0


def test_interrupting_pressure_truncates_long_answers(tmp_path):
    p = cp.Panel(ws_with(tmp_path), 11, day="2030-01-05")
    p.start(pressure="interrupting", now=T0)
    p.next_question(Fake(), now=T0)
    v = p.answer(" ".join(["word"] * 200), now=T0)
    assert "Interrupted" in v["note"] and p.turns()[-1]["interrupted"]


def test_self_score_comes_before_the_critic(tmp_path):
    p = cp.Panel(ws_with(tmp_path), 12, day="2030-01-05")
    p.start(now=T0)
    p.next_question(Fake(), now=T0)
    p.answer(ANSWER, now=T0)
    with pytest.raises(RuntimeError):
        p.critique(Fake())
    with pytest.raises(ValueError):
        p.self_score({"problem_stakeholders": 9})
    p.self_score({c["criterion_id"]: 3 for c in cp.RUBRICS["panel"]})
    crit = json.dumps({"verdict": "pass", "answer_quote": "300 pilot households"})
    score = p.critique(Fake(default=crit))
    asked = p.turns()[0]["criterion_id"]
    row = next(r for r in score if r["criterion_id"] == asked)
    assert row["checks"][0]["verdict"] == "pass" and row["checks"][0]["answer_quote"] == "300 pilot households"
    others = [r for r in score if r["criterion_id"] not in (asked, "response_to_questions")]
    assert all(r["checks"][0]["verdict"] == "not_asked" for r in others)
    bad = p.critique(Fake(default=json.dumps({"verdict": "pass", "answer_quote": "words never said"})))
    assert next(r for r in bad if r["criterion_id"] == asked)["checks"][0]["verdict"] == "unclear"
    rq = next(r for r in score if r["criterion_id"] == "response_to_questions")["checks"]
    assert {c["check_id"]: c["verdict"] for c in rq}["evidence_in_answers"] == "pass"


def test_every_third_panel_is_cold(tmp_path):
    ws = ws_with(tmp_path)
    colds = []
    for seed in (1, 2, 3):
        p = cp.Panel(ws, seed, day="2030-01-05")
        colds.append(p.start(now=T0)["cold"])
    assert colds == [False, False, True]
    p.next_question(Fake(), now=T0)
    assert "criterion" not in p.view()["turns"][0]


def test_leadership_review_asks_what_was_not_done(tmp_path):
    p = cp.Panel(ws_with(tmp_path), 13, day="2030-01-05")
    p.start("leadership", now=T0)
    assert ["vp_product", "not_doing"] in p.state()["queue"]


# ------------------------------------------------------------------ CC-6
def test_labels_feed_the_checker_card(tmp_path):
    ws = ws_with(tmp_path)
    f = {"finding_id": "f1", "artefact_id": "prd", "artefact_sha": ws.text("prd")[1], "check_id": "stop_condition",
         "verdict": "fail"}
    ws.log("findings.jsonl", f)
    ce.label_finding(ws, "f1", True, False)
    with pytest.raises(KeyError):
        ce.label_finding(ws, "nope", True, True)
    card = ce.checker_card(ws, {"card": [{"check_id": "stop_condition", "status": "ok", "tpr": 1, "tnr": 1}]},
                           ["stop_condition"])
    assert card[0]["n_labels"] == 1 and card[0]["status"] == "exploratory" and "1 of 60" in card[0]["why"][0]
    assert json.loads((ws.root / "eval" / "checker_card.json").read_text())[0]["criterion"] == "stop_condition"


def test_outcomes_and_telemetry(tmp_path):
    ws = ws_with(tmp_path)
    d = ws.root / "sessions" / "2030-01-05-sim-4"
    d.mkdir(parents=True)
    (d / "aar.json").write_text(json.dumps([
        {"objective_id": "severity_call", "critic_rating": "S", "self_rating": "P", "observation_quote": ""},
        {"objective_id": "time_to_declare", "critic_rating": "P", "self_rating": "P", "observation_quote": "after 3.0 min"}]))
    (ws.root / "premortem.json").write_text(json.dumps([{"risk_id": "lr-1", "author": "learner", "class": "tiger",
                                                         "mitigation": ""}]))
    ws.log("telemetry.jsonl", {"kind": "hint", "attempted_first": True, "level": 1})
    ws.log("telemetry.jsonl", {"kind": "hint", "attempted_first": False, "level": 0})
    o = ce.outcomes(ws)
    assert o["drills"][0]["mean_rating"] == 2.5 and o["self_minus_critic"]["drill"] == 0.5
    assert o["unresolved_tigers"] == 1
    t = ce.telemetry(ws)
    assert t["attempted_first_share"] == 0.5 and t["hint_levels"]["1"] == 1 and t["days_since_cold_mock"] is None


def test_panel_conduct_shows_up_in_progress(tmp_path):
    ws = ws_with(tmp_path)
    p = cp.Panel(ws, 14, day="2030-01-05")
    p.start(now=T0)
    p.next_question(Fake(), now=T0)
    rep = ce.progress(ws, None, ["stop_condition"])
    assert rep["conduct"][0]["questions"] == 1 and rep["checker_card"][0]["status"] == "exploratory"
