"""LS-3 FSRS-6 scheduling to a minutes budget, LS-7 mastery view, EP-5 privacy checks (invented data)."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import mastery   # noqa: E402
import privacy   # noqa: E402
import spaced    # noqa: E402

T0 = dt.datetime(2030, 1, 7, 9, tzinfo=dt.timezone.utc)   # a Monday


def lectures(tmp_path, n=40, module="Soil basics"):
    root = tmp_path / "lectures"
    f = root / "20300101-1000_Soil"
    f.mkdir(parents=True)
    (f / "manifest.json").write_text(json.dumps({"title": "Soil", "module": module}))
    (f / "cards.tsv").write_text("".join(f"Why does mulch help soil {i}?\tIt keeps water in, case {i}.\t[10:0{i % 10}]\n"
                                         for i in range(n)))
    return root


# ------------------------------------------------------------------ FSRS-6 maths
def test_fsrs6_basics():
    assert spaced.retrievability(10, 10) == pytest.approx(0.9)
    assert spaced.interval(10, 0.9) == 10 and spaced.interval(10, 0.8) > 10
    assert spaced.init_s(spaced.GOOD) == pytest.approx(2.3065)
    assert spaced.init_d(spaced.GOOD) == pytest.approx(6.4133 - 2.718281828 ** (0.8334 * 2) + 1, rel=1e-6)
    st = spaced.step(None, spaced.GOOD, T0)
    st2 = spaced.step(st, spaced.GOOD, T0 + dt.timedelta(days=3))
    assert st2["s"] > st["s"] and st2["reps"] == 2
    lapse = spaced.step(st2, spaced.AGAIN, T0 + dt.timedelta(days=20))
    assert lapse["s"] < st2["s"] and lapse["lapses"] == 1 and lapse["d"] > st2["d"]
    same = spaced.step(st, spaced.GOOD, T0 + dt.timedelta(hours=2))
    assert same["s"] >= st["s"]                       # short-term: a pass never lowers stability


# ------------------------------------------------------------------ cards, overrides, grading
def test_cards_overrides_and_grading(tmp_path):
    rv = spaced.Reviews(tmp_path / "practice", lectures(tmp_path, n=5))
    cards = rv.cards()
    assert len(cards) == 5 and cards[0]["module"] == "Soil basics"
    cid = cards[0]["card_id"]
    rv.override(cid, q="Edited question?")
    rv.override(cards[1]["card_id"], suspended=True)
    rv.override(cards[2]["card_id"], deleted=True)
    now = [c for c in rv.cards()]
    assert len(now) == 3 and now[0]["q"] == "Edited question?"
    g = rv.grade(cid, True, 9.5, now=T0)
    assert g["grade"] == spaced.GOOD and g["next_in_days"] >= 1
    with pytest.raises(KeyError):
        rv.grade("nope", True, now=T0)


def test_legacy_per_lecture_log_is_read(tmp_path):
    root = lectures(tmp_path, n=3)
    rv = spaced.Reviews(tmp_path / "practice", root)
    cid = rv.cards()[0]["card_id"]
    (root / "20300101-1000_Soil" / "reviews.jsonl").write_text(json.dumps({"card": cid, "ts": T0.isoformat(), "pass": False}) + "\n")
    assert rv.states()[cid]["lapses"] == 1
    rv.grade(cid, True, 7, now=T0 + dt.timedelta(days=2))
    last = json.loads((root / "20300101-1000_Soil" / "reviews.jsonl").read_text().splitlines()[-1])
    assert last["card"] == cid and last["pass"] is True and last["grade"] == spaced.GOOD   # same file, same keys


# ------------------------------------------------------------------ budget planning
def started_states(n, rng_days=30):
    out = []
    for i in range(n):
        st = spaced.step(None, spaced.GOOD, T0 - dt.timedelta(days=rng_days - i % rng_days))
        out.append(st)
    return out


@pytest.mark.parametrize("budget", [30, 60, 120])
def test_capped_schedule_stays_within_budget(budget):
    rv_caps = [budget / 7] * 7
    sim = spaced.simulate(started_states(300), 2000, 0.9, budget * 2, 8, spaced.SECONDS, T0.date(), cap_by_day=rv_caps)
    assert all(w <= budget * 1.1 for w in sim["weekly_minutes"])
    assert sum(sim["weekly_minutes"]) / 8 >= budget * 0.9      # enough work exists to fill it


def test_plan_picks_highest_retention_that_fits(tmp_path):
    rv = spaced.Reviews(tmp_path / "practice", lectures(tmp_path, n=40))
    rv.set(weekly_minutes=60)
    plan = rv.plan(today=T0.date())
    assert max(plan["weekly"]) <= 66 and plan["retention"] == spaced.RETENTIONS[0]
    rv.set(weekly_minutes=5, new_per_week=200)
    tight = rv.plan(today=T0.date())
    assert tight["new_per_week"] < 200 and ("note" in tight or max(tight["weekly"]) <= 5.5)


def test_class_days_are_easy_days(tmp_path):
    rv = spaced.Reviews(tmp_path / "practice", lectures(tmp_path, n=1))
    caps = rv.daily_caps(60, [5])
    assert caps[5] == pytest.approx(caps[0] / 2) and sum(caps) == pytest.approx(60)


def test_after_two_weeks_away_first_session_respects_cap_lowest_recall_first(tmp_path):
    root = lectures(tmp_path, n=60)
    rv = spaced.Reviews(tmp_path / "practice", root)
    rv.set(weekly_minutes=60, new_per_week=0)
    for i, c in enumerate(rv.cards()):
        rv.grade(c["card_id"], True, 10, now=T0 + dt.timedelta(minutes=i))
        if i % 3 == 0:
            rv.grade(c["card_id"], True, 10, now=T0 + dt.timedelta(days=2, minutes=i))
    back = T0 + dt.timedelta(days=16)
    s = rv.session(now=back, cap_minutes=5)
    shown = [c for c in s["cards"] if c["kind"] == "review"]
    assert s["planned_minutes"] <= 5 and s["overdue_not_shown"] > 0
    assert [c["recall"] for c in shown] == sorted(c["recall"] for c in shown)
    assert "streak" not in json.dumps(s)


# ------------------------------------------------------------------ LS-7 mastery
def test_mastery_counts_unassisted_only_and_lists_sure_wrong_first(tmp_path):
    root = lectures(tmp_path, n=6)
    pdir = tmp_path / "practice"
    rv = spaced.Reviews(pdir, root)
    for c in rv.cards():
        rv.grade(c["card_id"], True, 8, now=T0)
    rows = [{"item_id": f"i{k}", "kind": "quiz_item", "assisted": False, "correct": k % 2 == 0, "confidence": "sure",
             "lecture_id": "20300101-1000_Soil", "level": "apply" if k < 5 else "remember", "stem": f"stem {k}"}
            for k in range(6)]
    rows.append({"item_id": "x", "kind": "quiz_item", "assisted": True, "correct": False, "confidence": "sure",
                 "lecture_id": "20300101-1000_Soil"})
    (pdir / "attempts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    v = mastery.view(pdir, root, now=T0 + dt.timedelta(days=1))
    m = v["modules"][0]
    assert m["module"] == "Soil basics" and m["closed_book_items"] == 6 and m["closed_book_accuracy"] == 0.5
    assert m["apply_items"] == 5 and m["apply_accuracy"] == 0.6
    assert m["confidence_grid"]["sure"] == {"right": 3, "wrong": 3} and len(m["sure_and_wrong"]) == 3
    assert m["predicted_recall"] is not None and 0 < m["predicted_recall"] < 1


def test_mastery_needs_minimum_observations(tmp_path):
    root = lectures(tmp_path, n=2)
    pdir = tmp_path / "practice"
    rv = spaced.Reviews(pdir, root)
    rv.grade(rv.cards()[0]["card_id"], True, 8, now=T0)
    m = mastery.view(pdir, root, now=T0)["modules"][0]
    assert m["predicted_recall"] is None and m["closed_book_accuracy"] is None and not m["enough_data"]


# ------------------------------------------------------------------ EP-5 privacy
def test_endpoints_must_be_loopback():
    assert privacy.check_endpoints({"OMLX_BASE_URL": "http://127.0.0.1:8001/v1", "SCREENPIPE_URL": "http://localhost:3030"})["ok"]
    bad = privacy.check_endpoints({"OMLX_BASE_URL": "https://api.example.com/v1"})
    assert not bad["ok"] and "OMLX_BASE_URL" in bad["detail"] and "example" not in bad["detail"]


def test_eval_checks_flag_hosted_keys_without_printing_them(tmp_path):
    f = tmp_path / ".deepeval"
    f.write_text(json.dumps({"USE_LOCAL_MODEL": "YES", "LOCAL_MODEL_BASE_URL": "http://localhost:8001/v1"}))
    ok, tele = privacy.check_eval({"DEEPEVAL_TELEMETRY_OPT_OUT": "YES"}, [f])
    assert ok["ok"] and tele["ok"]
    f.write_text(json.dumps({"USE_LOCAL_MODEL": "NO", "CONFIDENT_API_KEY": "secret-value-123"}))
    bad, tele = privacy.check_eval({}, [f])
    assert not bad["ok"] and "secret-value" not in bad["detail"] and not tele["ok"] and tele["level"] == "warn"


def test_engine_cloud_features_fail():
    good = "screenpipe record --port 3030 --disable-telemetry --pii-backend local"
    assert privacy.check_engine(good, "", {})["ok"]
    for bad in ("--enable-sync", "--audio-transcription-engine deepgram", "--pii-backend tinfoil", "--enable-mdns"):
        assert not privacy.check_engine(good + " " + bad, "", {})["ok"]
    assert not privacy.check_engine("screenpipe record --port 3030", "", {})["ok"]          # telemetry left on
    assert not privacy.check_engine(good, "", {"SCREENPIPE_SYNC_TOKEN": "x"})["ok"]


def test_listeners_must_be_loopback():
    lsof = ("Python 1 u 5u IPv4 0x1 0t0 TCP 127.0.0.1:8001 (LISTEN)\n"
            "screenpipe 2 u 6u IPv4 0x2 0t0 TCP *:3030 (LISTEN)\nOther 3 u 7u IPv4 0x3 0t0 TCP *:5000 (LISTEN)\n")
    r = privacy.check_listeners(lsof)
    assert not r["ok"] and "capture engine on all interfaces" in r["detail"] and "5000" not in r["detail"]


def test_roster_hashes_skip_common_words_and_sync_is_ignored(tmp_path):
    import hashlib
    hs = privacy.roster_hashes(["Keelam Varsha", "Rose"], {"rose"})
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    assert h("keelam varsha") in hs and h("keelam") in hs and h("rose") not in hs
    repo = tmp_path / "pub"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".guard/\n")
    roster = tmp_path / "names_roster.json"
    roster.write_text("[]")
    assert not privacy.check_guard_sync(repo, [roster])["ok"]
    privacy.sync_guard(repo, ["Keelam Varsha"], set())
    assert privacy.check_guard_sync(repo, [roster])["ok"]
    assert "keelam" not in (repo / ".guard" / "roster.local.sha256").read_text().lower()
