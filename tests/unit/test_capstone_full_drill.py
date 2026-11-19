"""CC-2 full incident session: five injects on the real clock, comms cadence, reporting
clocks from the dated snapshot, a written golden-set case, action items from the postmortem.
Invented sample products only."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone as cs          # noqa: E402
import capstone_sim as sim     # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "capstone_samples"
T0 = dt.datetime(2030, 1, 5, 10, 0, tzinfo=dt.timezone.utc)
m = lambda k: T0 + dt.timedelta(minutes=k)


def ws_with(tmp_path, folder="A"):
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("prd", SAMPLES / folder / "prd.md", "prd")
    return ws


def seed_for(family, ws):
    return next(s for s in range(500) if sim.make_scenario(s, ws, micro=False)["failure_class"] == family)


def test_every_family_has_five_linted_injects_and_micro_is_unchanged(tmp_path):
    ws = ws_with(tmp_path, "D")
    for fam, injects in sim.INJECTS.items():
        assert len(injects) == 5, fam
        objs = {i[2] for i in injects}
        assert {"severity_call", "regulatory_clock", "golden_set_update"} <= objs, fam
        assert len({i[0] for i in injects}) >= 3, fam                 # at least three roles speak
    for seed in range(20):
        full, micro = sim.make_scenario(seed, ws, micro=False), sim.make_scenario(seed, ws)
        assert [i["t_offset_min"] for i in full["injects"]] == [0, 10, 20, 35, 50] and full["time_box_min"] == 60
        assert all(sim.lint_inject(i) == [] for i in full["injects"])
        assert micro["injects"] == [dict(i, t_offset_min=o) for i, o in zip(full["injects"][:3], [0, 7, 14])]
        assert sim.make_scenario(seed, ws, micro=False) == full


def test_real_time_session_unlocks_on_the_clock_and_shows_only_the_next_time(tmp_path):
    ws = ws_with(tmp_path)
    d = sim.Drill(ws, 5, day="2030-01-05")
    v = d.start(now=T0, micro=False)
    assert v["real_time"] and v["next_at_min"] == 10 and v["n_injects"] == 5 and len(v["injects"]) == 1
    assert len(d.next(now=m(9))["injects"]) == 1                          # not yet: no practice shortcut
    v = d.next(now=m(10.5))
    assert len(v["injects"]) == 2 and v["next_at_min"] == 20 and v["elapsed_min"] == 10.5
    assert "tests_objective" not in json.dumps(v) and "expected_evidence" not in json.dumps(v)
    p = sim.Drill(ws, 6, day="2030-01-05")
    p.start(now=T0, micro=False, real_time=False)                         # practice mode on request
    assert len(p.next(now=m(1))["injects"]) == 2


def run_full(ws, seed, *, regulator="", cadence=10, golden_case=True, customer_next=True):
    d = sim.Drill(ws, seed, day="2030-01-05")
    d.start(now=T0, micro=False)
    sc = d.scenario()
    for k, inj in enumerate(sc["injects"]):
        t = inj["t_offset_min"] + 2
        rationale = ("A golden-set case: a customer message with hidden instructions should be refused and routed to a person."
                     if golden_case and inj["tests_objective"] == "golden_set_update" else "short")
        d.decide(inj["inject_id"], "SEV2", "pause the action tool and route to people", {
            "customer": "We paused account changes while we fix a problem." + (" Next update by 10:30." if customer_next else ""),
            "internal": "Action tool off, routing to people, investigating the cause.",
            "regulator": regulator if inj["tests_objective"] == "regulatory_clock" else ""}, "no", rationale, now=m(t))
    for k in range(0, 60, cadence):
        d.update("internal", f"Status at minute {k}: still contained, cause being traced.", now=m(k + 1))
    d.self_rate({o: "S" for o in sim.OBJECTIVES})
    return d


def test_cadence_clock_and_golden_case_are_rated_from_the_record(tmp_path):
    ws = ws_with(tmp_path)
    seed = seed_for("prompt_injection_excessive_agency", ws)
    d = run_full(ws, seed, regulator="Report to CERT-In within 6 hours and review the 72 hour breach clock with legal.")
    hw = d.hotwash(now=m(62))
    by = {r["objective_id"]: r for r in hw["aar"]}
    assert by["comms_cadence_honesty"]["critic_rating"] == "P", by["comms_cadence_honesty"]
    assert by["regulatory_clock"]["critic_rating"] == "P" and "verify" in by["regulatory_clock"]["observation_quote"]
    assert by["golden_set_update"]["critic_rating"] == "P"
    assert {c["regime"] for c in hw["clocks"]} >= {"GDPR", "India CERT-In Directions (2022)"}
    assert all(c["verify"] and c["as_of"] for c in hw["clocks"])
    assert all(r["self_rating"] == "S" for r in hw["aar"])


def test_weak_records_lose_the_ratings(tmp_path):
    ws = ws_with(tmp_path)
    seed = seed_for("personal_data_leak", ws)
    d = run_full(ws, seed, regulator="We will tell them soon.", cadence=40, golden_case=False, customer_next=False)
    by = {r["objective_id"]: r for r in d.hotwash(now=m(62))["aar"]}
    assert by["comms_cadence_honesty"]["critic_rating"] in ("S", "M")
    assert by["regulatory_clock"]["critic_rating"] == "S" and "no reporting window" in by["regulatory_clock"]["observation_quote"]
    assert by["golden_set_update"]["critic_rating"] == "S"
    d2 = run_full(ws_with(tmp_path / "b"), seed, regulator="")
    assert {r["objective_id"]: r for r in d2.hotwash(now=m(62))["aar"]}["regulatory_clock"]["critic_rating"] == "M"


def test_action_items_come_from_the_postmortem(tmp_path):
    ws = ws_with(tmp_path)
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text("# Postmortem\nFor 42 minutes 1,400 customers saw a wrong window. Root cause: the index was stale "
                    "because the rebuild job failed silently.\n- Action: alert on rebuild failure, owner: platform, by 2030-01-20\n")
    bad.write_text("# Postmortem\nRoot cause: the index was stale.\n- Action: fix the index\n")
    ws.register("postmortem", good, "pm_good"); ws.register("postmortem", bad, "pm_bad")
    seed = seed_for("hallucinated_policy", ws)
    rate = lambda pm: {r["objective_id"]: r for r in run_full(ws, seed).hotwash(now=m(62), postmortem=pm)["aar"]}["action_item_quality"]["critic_rating"]
    assert rate("pm_good") == "P" and rate("pm_bad") == "M"
    assert {r["objective_id"]: r for r in run_full(ws, seed).hotwash(now=m(62))["aar"]}["action_item_quality"]["critic_rating"] == "U"


def test_an_older_snapshot_gains_the_clocks_without_losing_edits(tmp_path):
    root = tmp_path / "cap"
    root.mkdir()
    (root / "governance_snapshot.json").write_text(json.dumps({"as_of": "2026-12-01", "items": [{"regime": "mine"}]}))
    g = cs.governance(root)
    assert g["as_of"] == "2026-12-01" and g["items"] == [{"regime": "mine"}]
    assert {c["hours"] for c in g["clocks"]} == {72, 6, 360}


def test_cli_full_session_with_updates(tmp_path, monkeypatch, capsys):
    import capstone_cli as cli
    monkeypatch.setattr(cli, "ROOT", tmp_path / "cap")
    monkeypatch.setattr(cli, "capture_running", lambda: False)
    cli.main(["sample"]); capsys.readouterr()
    cli.main(["sim", "start", "--seed", "4", "--full"])
    s = json.loads(capsys.readouterr().out)
    assert s["real_time"] and s["time_box_min"] == 60 and s["next_at_min"] == 10
    cli.main(["sim", "next"])
    assert len(json.loads(capsys.readouterr().out)["injects"]) == 1       # the clock decides
    cli.main(["sim", "update", "--audience", "customer", "--text", "We paused it; next update by 10:30."])
    assert json.loads(capsys.readouterr().out)["update"]["audience"] == "customer"
    cli.main(["sim", "update", "--audience", "press", "--text", "x"])
    assert "audience must be" in json.loads(capsys.readouterr().out)["error"]
    cli.main(["sim", "show"])
    assert len(json.loads(capsys.readouterr().out)["updates"]) == 1
