"""Capstone copilot (CC-0, CC-1, CC-2): gates, refusals, hint ladder, calculator, pre-mortem,
seeded drills. The model is a stub; every product and request is invented."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone as cs          # noqa: E402
import capstone_review as cr   # noqa: E402
import capstone_sim as sim     # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "capstone_samples"

AUTHORING_REQUESTS = [  # 40 invented phrasings; every one must get the fixed refusal
    "Write my problem statement for the PRD.", "Can you draft the trust architecture section?",
    "Rewrite this paragraph so it sounds more professional.", "Please rephrase my acceptance criteria.",
    "Give me a paragraph on the moat.", "Draft the press release for me.", "Could you write the FAQ answers?",
    "Fill in the unit economics section.", "Complete the system card for me.", "Polish my postmortem please.",
    "Just write it.", "Make this sound better.", "Turn my notes into a PRD section.",
    "What should I write for the fallback section?", "Generate an executive summary of my capstone.",
    "Can you improve my stop condition?", "Fix the wording of my eval thresholds for me.",
    "Write the root cause analysis.", "Give me the wording for the customer message.",
    "I need a draft of the risk section.", "Compose the incident communication to customers.",
    "Rewrite.", "Paraphrase my moat argument so it reads stronger.", "Please write the go-to-market plan.",
    "Draft it again but shorter.", "Can you produce the evidence ladder?", "Write a sample answer for the panel question.",
    "Give me a model answer for question 3.", "Help me write the opportunity sizing.", "Let's draft the FAQ together.",
    "Expand my launch plan for me.", "Could you tighten this section?", "Can you put together the cost model?",
    "Write the action items for my postmortem.", "Spell out the acceptance criteria for me.",
    "Show me a version of my trust brief that would pass.", "Create the system card.",
    "Edit my PRD intro please.", "Give me some text for the moat section.", "Now rewrite the whole strategy.",
]
LEGIT = [
    "Why is my acceptance criterion not testable?", "Which checks failed?", "Should I rewrite the trust section?",
    "What does the rubric look for in the moat criterion?", "Run the strategy review.", "Start a drill.",
    "Is my stop condition specific enough?", "Where in the lecture was fallback design covered?",
    "What would a finance reviewer ask about my cost model?", "Do I need a regulator message for this incident?",
    "How did the lecture define a golden set?", "Explain the hint you gave me.",
]


def ws_with(tmp_path, folder="A"):
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("prd", SAMPLES / folder / "prd.md", "prd")
    ws.register("strategy", SAMPLES / folder / "strategy.md", "strategy")
    return ws


def finding(ws, **kw):
    text, sha = ws.text("prd")
    f = {"finding_id": "f1", "artefact_id": "prd", "artefact_sha": sha,
         "quote": "The agent should be fast and helpful when suggesting reorders.", "criterion_id": "requirements",
         "check_id": "acceptance_testable", "verdict": "fail", "issue": "A tester could not decide pass or fail here.",
         "question_to_learner": "What measurement would tell a tester this criterion is met?", "hint_level": 0,
         "lecture_refs": [], "evidence_refs": [], "reviewer_view": "general", "confidence": 1.0}
    f.update(kw)
    return f, text, sha


def test_every_authoring_request_is_refused_and_legit_ones_are_not():
    assert len(AUTHORING_REQUESTS) == 40
    missed = [r for r in AUTHORING_REQUESTS if not cs.is_authoring_request(r)]
    assert missed == []
    wrongly = [r for r in LEGIT if cs.is_authoring_request(r)]
    assert wrongly == []
    r = cs.respond(AUTHORING_REQUESTS[0])
    assert r["refused"] and r["text"] == cs.REFUSAL and r["question_to_learner"].endswith("?") and r["hint_level"] == 0


def test_a_good_finding_passes_the_gates(tmp_path):
    f, text, sha = finding(ws_with(tmp_path))
    assert cs.gate_finding(f, text, sha) == []


@pytest.mark.parametrize("change, reason", [
    ({"quote": "The agent should be fast, helpful and cheap."}, "quote is not an exact substring"),
    ({"suggested_text": "A user can..."}, "unknown field"),
    ({"rewrite": "x"}, "unknown field"),
    ({"issue": "word " * 50, "question_to_learner": "Why " + "so " * 20 + "?"}, "exceed"),
    ({"question_to_learner": "What is measured? And by whom?"}, "exactly one question"),
    ({"question_to_learner": "Tell me what is measured."}, "exactly one question"),
    ({"issue": "Reviewers will ask about \"latency under 200 ms\" here."}, "quoted string"),
    ({"issue": "Great start, but a tester could not decide pass or fail."}, "praise"),
    ({"issue": "You could say a user approves in three taps."}, "rewrite cue"),
    ({"issue": "Replace it with a measurable target."}, "rewrite cue"),
    ({"verdict": "maybe"}, "verdict"),
    ({"artefact_sha": "0" * 64}, "artefact changed"),
])
def test_gates_reject(tmp_path, change, reason):
    f, text, sha = finding(ws_with(tmp_path), **change)
    assert any(reason in w for w in cs.gate_finding(f, text, sha))


def test_registering_tracks_versions_and_only_reads(tmp_path):
    p = tmp_path / "mine.md"
    p.write_text("Version one of my PRD.")
    ws = cs.Workspace(tmp_path / "cap").init()
    r1 = ws.register("prd", p, "prd")
    p.write_text("Version two of my PRD.")
    r2 = ws.refresh()[0]
    assert r1["version"] == 1 and r2["version"] == 2 and r1["sha256"] != r2["sha256"]
    assert p.read_text() == "Version two of my PRD."
    with pytest.raises(ValueError):
        ws.register("essay", p)


def test_hint_ladder_needs_attempts_and_level_3_is_another_product(tmp_path):
    ws = ws_with(tmp_path)
    f, _, _ = finding(ws)
    assert cs.next_hint(0, attempted=False) == 0 and cs.next_hint(0, True) == 1 and cs.next_hint(3, True) == 3
    assert cs.hint(f, 0, ws.rubric())["text"] == f["question_to_learner"]
    h1 = cs.hint(f, 1, ws.rubric())["text"]
    assert "Testable requirements" in h1 and "fast and helpful" in h1
    assert "No lecture moment" in cs.hint(f, 2, ws.rubric())["text"]
    h3 = cs.hint(f, 3, ws.rubric())["text"]
    assert "library-booking" in h3 and "Pantry" not in h3 and len(h3.split()) <= 120


class StubLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, system, user):
        assert cs.INTEGRITY in system            # integrity rules on every turn
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return r if isinstance(r, str) else json.dumps(r)


def reply(verdict, quote="The agent should be fast and helpful when suggesting reorders.", **kw):
    return {"verdict": verdict, "artefact_id": "prd", "quote": quote,
            "issue": kw.get("issue", "A tester could not decide pass or fail."),
            "question": kw.get("question", "What would a tester measure here?")}


def test_checker_unanimity_and_gating(tmp_path):
    ws = ws_with(tmp_path)
    r = cr.run_check(ws, "acceptance_testable", StubLLM([reply("fail")] * 3))
    assert r["verdict"] == "fail" and r["finding"]["confidence"] == 1.0 and r["finding"]["reviewer_view"] == "general"
    r = cr.run_check(ws, "acceptance_testable", StubLLM([reply("fail"), reply("pass"), reply("fail")]))
    assert r["verdict"] == "unclear"
    r = cr.run_check(ws, "acceptance_testable", StubLLM([reply("fail", quote="Words the learner never wrote.")] * 3))
    assert r["verdict"] == "fail" and r["finding"] is None and len(r["gate_failures"]) == 3
    r = cr.run_check(ws, "acceptance_testable", StubLLM(["not json", reply("fail"), reply("fail")]))
    assert r["verdict"] == "unclear" and r["runs"][0] == "invalid"
    empty = cs.Workspace(tmp_path / "empty").init()
    assert cr.run_check(empty, "moat_base_model", StubLLM([reply("fail")]))["note"].startswith("no artefact")


def test_review_logs_findings_and_gate_pass_rate(tmp_path):
    ws = ws_with(tmp_path)
    r = cr.review(ws, StubLLM([reply("fail")] * 30), ["acceptance_testable", "stop_condition"])
    assert r["gate_pass_rate"] == 1.0
    t = [json.loads(l) for l in (ws.root / "telemetry.jsonl").read_text().splitlines()]
    assert t[-1]["kind"] == "review" and t[-1]["findings"] == 2


def test_unit_economics_matches_hand_worked_fixtures():
    r = cr.unit_econ({"cost_per_call": 0.004, "calls_per_task": 6, "retry_rate": 0.2, "failure_rate": 0.05,
                      "tasks_per_user_month_median": 4, "tasks_per_user_month_p90": 20, "price_per_user_month": 2.0,
                      "learner_cost_per_successful_outcome": 0.0303})
    assert r["cost_per_successful_outcome"] == pytest.approx(0.030316, abs=1e-5)
    assert r["cost_per_user_month_p90"] == pytest.approx(0.6063, abs=1e-3)
    assert r["gross_margin_user_p90"] == pytest.approx(0.6968, abs=1e-3)
    assert r["checks"][0]["ok"]
    bad = cr.unit_econ({"cost_per_call": 0.1, "calls_per_task": 10, "tasks_per_user_month_p90": 100,
                        "price_per_user_month": 5, "learner_cost_per_successful_outcome": 0.5})
    assert bad["warning"] and not bad["checks"][0]["ok"]
    with pytest.raises(ValueError):
        cr.unit_econ({"cost_per_call": 1, "calls_per_task": 1, "failure_rate": 1})


def test_premortem_proposes_only_uncovered_categories_without_mitigations():
    mine = [{"text": "Cost blow-up from retries"}, {"text": "Personal data leak in summaries"}]
    props = cr.premortem_propose(mine)
    cats = {p["category"] for p in props}
    assert "cost_blowup" not in cats and "personal_data_leak" not in cats and "prompt_injection" in cats
    assert all(p["mitigation"] is None and p["class"] is None and p["text"].endswith("?") for p in props)


def test_faq_keeps_only_single_short_questions(tmp_path):
    ws = ws_with(tmp_path)
    q = cr.faq_questions(ws, StubLLM([{"questions": ["What happens when stock runs out?", "Great idea, right?",
                                                     "Two? Questions?", "You could say it is cheap?"]}]))
    assert q == ["What happens when stock runs out?"]


def test_same_seed_same_scenario_and_injects_lint(tmp_path):
    ws = ws_with(tmp_path)
    a, b = sim.make_scenario(7, ws), sim.make_scenario(7, ws)
    assert a == b and len(a["injects"]) == 3 and [i["t_offset_min"] for i in a["injects"]] == [0, 7, 14]
    assert a["bound_gap"]["artefact_id"] == "prd" and "automatically" in a["bound_gap"]["quote"]
    assert all(sim.lint_inject(i) == [] for i in a["injects"])
    assert sim.lint_inject({"role": "legal", "text": "x", "tests_objective": "severity_call",
                            "expected_evidence": "y", "permissible_branches": ["one"]}) == ["needs at least two permissible branches"]
    view = sim.public_view(a, 1)
    assert "tests_objective" not in json.dumps(view) and len(view["injects"]) == 1


def test_drill_self_rating_before_critic_and_clock_based_declaration(tmp_path):
    ws = ws_with(tmp_path, "D")
    d = sim.Drill(ws, 11, day="2030-01-05")
    t0 = dt.datetime(2030, 1, 5, 10, 0, tzinfo=dt.timezone.utc)
    d.start(now=t0)
    assert len(d.next(now=t0 + dt.timedelta(minutes=1), practice=False)["injects"]) == 1   # not yet due
    assert len(d.next(now=t0 + dt.timedelta(minutes=8), practice=False)["injects"]) == 2
    sc = d.scenario()
    for k, i in enumerate(sc["injects"]):
        d.decide(i["inject_id"], "SEV2", "pause the action and route to people", {
            "customer": "We found a problem with suggestions and paused them while we fix it.", "internal": "x",
            "regulator": ""}, "no", "golden set lacks this case", now=t0 + dt.timedelta(minutes=3 + 7 * k))
    with pytest.raises(RuntimeError):
        d.hotwash()
    with pytest.raises(ValueError):
        d.self_rate({"severity_call": "A"})
    d.self_rate({o: "S" for o in sim.OBJECTIVES})
    hw = d.hotwash()
    by = {r["objective_id"]: r for r in hw["aar"]}
    assert by["time_to_declare"]["critic_rating"] == "P" and "3.0 min" in by["time_to_declare"]["observation_quote"]
    assert by["fallback_in_artefacts"]["critic_rating"] == "P" and by["severity_call"]["critic_rating"] == "P"
    assert all(r["self_rating"] == "S" for r in hw["aar"]) and "tests_objective" in json.dumps(hw["scenario"])


def test_postmortem_findings_quote_the_learner(tmp_path):
    pm = tmp_path / "pm.md"
    pm.write_text("# Postmortem\nRoot cause: the retrieval index was stale.\n- Action: fix the index\n"
                  "The on-call engineer was careless with the deploy.\n")
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("postmortem", pm, "pm")
    checks = {f["check_id"] for f in sim.postmortem_findings(ws, "pm")}
    assert checks == {"missing_numbers", "shallow_root_cause", "vague_actions", "blame"}


def test_cli_refuses_during_capture_and_runs_a_drill(tmp_path, monkeypatch, capsys):
    import capstone_cli as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path / "cap")
    monkeypatch.setattr(cli, "capture_running", lambda: True)
    cli.main(["review"])
    assert "capture is running" in json.loads(capsys.readouterr().out)["error"]
    cli.main(["status"])
    assert json.loads(capsys.readouterr().out)["locked"] is True
    monkeypatch.setattr(cli, "capture_running", lambda: False)
    cli.main(["sample"]); capsys.readouterr()
    cli.main(["ask", "Rewrite my PRD intro."])
    assert json.loads(capsys.readouterr().out)["refused"] is True
    cli.main(["sim", "start", "--seed", "3"])
    s = json.loads(capsys.readouterr().out)
    assert s["seed"] == 3 and len(s["injects"]) == 1 and "tests_objective" not in json.dumps(s)
    cli.main(["sim", "decide", "--inject", "i1", "--severity", "SEV1", "--containment", "pause all orders now",
              "--customer", "We paused automatic orders while we check a problem.", "--golden", "no"])
    assert json.loads(capsys.readouterr().out)["decision"]["severity"] == "SEV1"
    cli.main(["sim", "hotwash"])
    assert "rate yourself first" in json.loads(capsys.readouterr().out)["error"]
    cli.main(["econ", "--inputs", json.dumps({"cost_per_call": 0.003, "calls_per_task": 5})])
    assert json.loads(capsys.readouterr().out)["cost_per_successful_outcome"] == 0.015
    cli.main(["premortem", "propose"])
    assert "your own" in json.loads(capsys.readouterr().out)["error"]


def test_exact_span_repairs_whitespace_and_markdown_but_never_invents():
    text = "## Trust\nPantry Pal places the **weekly order** automatically\nevery Sunday."
    assert cs.exact_span("places the weekly order automatically every Sunday", text) == \
        "places the **weekly order** automatically\nevery Sunday"
    assert cs.exact_span("“Pantry Pal places”", text) == "Pantry Pal places"
    assert cs.exact_span("places the monthly order", text) is None


def test_apostrophes_are_not_quotes(tmp_path):
    f, text, sha = finding(ws_with(tmp_path), issue="The learner's criterion doesn't say what a tester measures.")
    assert cs.gate_finding(f, text, sha) == []


def test_findings_carry_exploratory_until_measured(tmp_path, monkeypatch, capsys):
    import capstone_cli as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path / "cap")
    monkeypatch.setattr(cli, "HERE", tmp_path)
    monkeypatch.setattr(cli, "capture_running", lambda: False)
    assert set(cli.checker_card().values()) == {"exploratory"}
    (tmp_path / "eval" / "results").mkdir(parents=True)
    (tmp_path / "eval" / "results" / "20300101-000000-capstone-m11.json").write_text(json.dumps(
        {"card": [{"check_id": "stop_condition", "status": "ok"}]}))
    card = cli.checker_card()
    assert card["stop_condition"] == "ok" and card["moat_base_model"] == "exploratory"
