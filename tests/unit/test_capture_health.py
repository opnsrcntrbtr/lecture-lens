"""Capture health (CT-1) and regression gates (EP-2): pure functions, invented fixtures."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capture_health as ch  # noqa: E402

UTC = dt.timezone.utc
T0 = dt.datetime(2030, 1, 5, 4, 30, tzinfo=UTC)          # class starts 10:00 IST
s = lambda sec: T0 + dt.timedelta(seconds=sec)


def run(n, **kw):
    """Probe every 30 s for n probes with the given per-probe values."""
    recs = []
    for i in range(n):
        now = s(i * 30)
        recs.append(ch.probe(now, kw.get("running", True), now - dt.timedelta(seconds=kw.get("chunk_age", 10)),
                             kw.get("level", -12.0), now - dt.timedelta(seconds=kw.get("frame_age", 5)),
                             kw.get("expected", True), kw.get("drm", False)))
    return recs


def first_alert_after(recs):
    for i in range(len(recs)):
        a = ch.alert(recs[: i + 1])
        if a:
            return (ch.parse_t(recs[i]["t"]) - ch.parse_t(recs[0]["t"])).total_seconds(), a
    return None, None


def test_healthy_capture_never_alerts():
    assert all(r["status"] == "ok" for r in run(20)) and first_alert_after(run(20)) == (None, None)


def test_all_zero_audio_alerts_silent_within_90_seconds():
    t, a = first_alert_after(run(10, level=-120.0))
    assert a["status"] == "silent_audio" and t <= 90


def test_a_quiet_break_is_not_silence():
    # the meeting app's tap is all zeros but system audio carries room tone at -70 dB
    lvl = ch.combined_level([-120.0, -70.3])
    assert first_alert_after(run(20, level=lvl)) == (None, None)


def test_stalled_audio_and_missing_frames_alert():
    t, a = first_alert_after(run(10, chunk_age=200))
    assert a["status"] == "audio_stalled" and t <= 90
    t, a = first_alert_after(run(10, frame_age=400))
    assert a["status"] == "no_frames" and "visible" in a["message"]


def test_no_frames_is_fine_without_a_meeting_or_under_drm_pause():
    assert first_alert_after(run(10, frame_age=400, expected=False)) == (None, None)
    assert first_alert_after(run(10, frame_age=400, drm=True)) == (None, None)


def test_a_short_blip_does_not_alert():
    recs = run(3, level=-120.0) + run(3)
    assert first_alert_after(recs) == (None, None)


def test_late_start_reminder_within_two_minutes_then_every_minute_for_ten():
    sent, times = 0, []
    for sec in range(0, 900, 30):
        if ch.late_start_due(s(sec), T0, capture_running=False, sent=sent):
            sent += 1
            times.append(sec)
    assert times[0] <= 120 and times[1] - times[0] == 60 and times[-1] <= 600 and len(times) == 10
    assert not ch.late_start_due(s(90), T0, capture_running=True, sent=0)


def test_gap_ledger_late_start_only():
    # capture starts 54 min late, audio chunks every 30 s to the end; a quiet break still has chunks
    chunks = [s(54 * 60 + 30 * i) for i in range(int((180 - 54) * 2))]
    g = ch.gaps(T0, s(180 * 60), chunks)
    assert len(g) == 1 and g[0]["cause"] == "late_start" and g[0]["from"] == T0.isoformat()
    assert g[0]["to"] == s(54 * 60).isoformat() and g[0]["minutes"] == 54.0


def test_gap_ledger_hole_cause_from_health():
    chunks = [s(30 * i) for i in range(40)] + [s(1200 + 300 + 30 * i) for i in range(40)]
    health = [{"t": s(1300).isoformat(), "status": "audio_stalled"}]
    g = ch.gaps(T0, s(2700), chunks, health)
    assert [x["cause"] for x in g] == ["engine_stopped"]
    assert ch.gaps(T0, s(2700), chunks)[0]["cause"] == "unknown"
    filled = [{"from": s(1100).isoformat(), "to": s(1600).isoformat()}]
    assert ch.gaps(T0, s(2700), chunks, filled=filled)[0]["filled"] is True


def test_chunk_names_and_append(tmp_path):
    assert ch.chunk_time("System Audio (output)_2030-01-05_04-31-00.mp4") == s(60)
    for n in ("App Tap (output)_2030-01-05_04-31-00.mp4", "App Tap (output)_2030-01-05_04-31-30.mp4",
              "System Audio (output)_2030-01-05_04-31-10.mp4", "notes.txt"):
        (tmp_path / n).write_bytes(b"")
    newest = ch.newest_chunks(tmp_path)
    assert {k: v[1] for k, v in newest.items()} == {"App Tap (output)": s(90), "System Audio (output)": s(70)}
    rows = ch.append(tmp_path / "h.jsonl", {"t": "x"}, keep=2); rows = ch.append(tmp_path / "h.jsonl", {"t": "y"}, keep=2)
    rows = ch.append(tmp_path / "h.jsonl", {"t": "z"}, keep=2)
    assert [r["t"] for r in rows] == ["y", "z"]


# ------------------------------------------------------------------ EP-2 gates
def test_gates_have_failing_fixtures():
    import lecture_kit as k
    good = "# T\n\n## Key concepts\n\n- **Mulch** keeps soil moist through dry weeks, as shown in class [00:01:00].\n"
    bad = good + "\n- **Compost** feeds the worms that loosen heavy clay soil over a season or two.\n"
    assert k.gate_notes_timecodes(good) == 0 and k.gate_notes_timecodes(bad) == 1
    assert k.gate_notes_timecodes("## Open questions\n\n- Why does compost feed worms that loosen clay soil in winter?") == 0
    assert k.DOES_NOT_SPECIFY.search("The lecture does not specify a threshold.")
    assert not k.DOES_NOT_SPECIFY.search("The lecture specifies a threshold of 0.85.")
    assert k.gate_title("Garden Basics: Soil", ["Garden Basics: Soil and Water", None])
    assert not k.gate_title("Zoom Webinar", ["Garden Basics: Soil and Water"])
    quiz = [{"q": "Which keeps soil moist?", "options": ["Mulch", "Sand", "Gravel"], "answer": "a"},
            {"q": "Broken", "options": ["A", "A", "B"], "answer": 0}]
    assert k.gate_quiz(quiz) == 0.5 and k.gate_quiz([]) is None


def test_regression_gates_on_a_fixture_lecture(tmp_path, monkeypatch):
    import lecture_kit as k
    d = tmp_path / "20300105-1000_Garden-Basics"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"title": "Garden Basics", "start": T0.isoformat(), "minutes": 30, "slides": 1}))
    (d / "notes.md").write_text("# Garden Basics\n\n## Key concepts\n\n- Mulch keeps soil moist through dry weeks, as the lecture showed [00:01:00].\n")
    (d / "transcript.md").write_text("# t\n\n_\n[00:01:00] Mulch keeps soil moist through dry weeks.\n")
    live = tmp_path / "live" / "20300105"
    live.mkdir(parents=True)
    (live / "questions.jsonl").write_text(json.dumps({"q": "x?", "answer": "The lecture does not specify it."}) + "\n")
    monkeypatch.setattr(k, "LECTURES", tmp_path)
    monkeypatch.setattr(k.course_calendar, "session_at", lambda t: {"title": "Garden Basics"})
    g = {x["name"]: x for x in k.regression_gates(d, 0)}
    assert g["names_left"]["ok"] and g["notes_without_timecode"]["ok"] and g["title_from_candidates"]["ok"]
    assert g["notes_citation_warnings"]["ok"]
    assert not g["drafts_does_not_specify"]["ok"] and g["drafts_does_not_specify"]["value"] == 1
    assert not k.regression_gates(d, 2)[0]["ok"]


def test_slow_saves_while_audio_arrives_are_behind_not_stalled():
    # 21 Nov: chunks saved 40-90 s late while the engine heard audio all along
    eng = {"audio_active": True, "frame_ok": True}
    recs = [ch.probe(s(i * 30), True, s(i * 30) - dt.timedelta(seconds=100), -4.0,
                     s(i * 30) - dt.timedelta(seconds=5), True, engine=eng, audio_write_lag_s=70.0)
            for i in range(10)]
    assert {r["status"] for r in recs} == {"audio_behind"} and recs[0]["audio_write_lag_s"] == 70.0
    assert first_alert_after(recs[:6]) == (None, None)            # 150 s: under the 180-s delay
    t, a = first_alert_after(recs)
    assert a["status"] == "audio_behind" and t == 180 and "Keep capturing" in a["message"]
    # the engine hears nothing (or cannot save): a real stall, as before
    t, a = first_alert_after(run(10, chunk_age=200))
    assert a["status"] == "audio_stalled" and "restart" in a["message"]
    stalled = ch.probe(s(0), True, s(-200), -4.0, s(-5), True, engine=ch.engine_signals(
        {"device_status_details": "System Audio (output): active (last activity: 0s ago)",
         "audio_db_write_stalled": True}, s(0)))
    assert stalled["status"] == "audio_stalled"


def test_minutes_of_unsaved_audio_are_behind_even_while_chunks_are_saved():
    # 21 Nov 11:04: a chunk was saved every 30 s, but each was 4 minutes old
    eng = {"audio_active": True, "frame_ok": True}
    r = ch.probe(s(0), True, s(-5), -4.0, s(-5), True, engine=eng, audio_write_lag_s=240.0, audio_unsaved_s=250.0)
    assert r["status"] == "audio_behind" and r["audio_unsaved_s"] == 250.0
    assert ch.probe(s(0), True, s(-5), -4.0, s(-5), True, engine=eng, audio_unsaved_s=40.0)["status"] == "ok"
    newest = {"System Audio (output)": (Path("x.mp4"), s(-310))}
    assert ch.unsaved_s(newest, s(0)) == 280.0


def test_an_unchanged_screen_is_not_no_frames_while_the_frame_loop_is_healthy():
    ok = ch.probe(s(0), True, s(-10), -4.0, s(-300), True, engine={"frame_ok": True})
    assert ok["status"] == "ok" and ok["engine_frame_ok"] is True
    assert ch.probe(s(0), True, s(-10), -4.0, s(-700), True, engine={"frame_ok": True})["status"] == "no_frames"
    assert ch.probe(s(0), True, s(-10), -4.0, s(-300), True, engine={"frame_ok": False})["status"] == "no_frames"


def test_engine_signals_and_save_times(tmp_path):
    now = s(0)
    sig = ch.engine_signals({"device_status_details": "System Audio (output): active (last activity: 3s ago)",
                             "frame_status": "ok"}, now)
    assert sig == {"audio_active": True, "frame_ok": True}
    assert ch.engine_signals({"last_audio_timestamp": "2030-01-05T09:57:00+05:30"}, now) == {"audio_active": False}
    assert ch.engine_signals({}, now) == {}
    import os
    f = tmp_path / "System Audio (output)_2030-01-05_04-28-00.mp4"
    f.write_bytes(b"x")
    saved = s(-60)                                              # saved at 04:29:00, 30 s after its span ended
    os.utime(f, (saved.timestamp(), saved.timestamp()))
    t, lag = ch.newest_saved(ch.newest_chunks(tmp_path))
    assert t == saved and lag == 30.0
