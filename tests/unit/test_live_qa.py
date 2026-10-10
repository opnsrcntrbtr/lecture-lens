import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import live_qa as q

SNAP1 = "\n".join([
    "Switch",
    "Asha Rao", "10:43 AM", "Do ferns need shade?", "Do ferns need shade?",
    "Tutor Tom", "10:45 AM", "Mostly, yes", "Mostly, yes",
    "Sam Example (You)", "10:46 AM", "Why do cacti store water?", "Why do cacti store water?",
    "Tutor Tom", "10:47 AM", "Rain is rare\n\nso they save it", "Rain is rare\n\nso they save it",
    "Ravi K", "10:48 AM", "Is the soil test recorded?", "Is the soil test recorded?",
    "Tutor Tom", "10:49 AM", "Yes", "Yes", "Type your question here…", "Who can see your questions?",
])
SNAP2 = "\n".join([
    "Ravi K", "10:50 AM", "Is the soil test recorded?", "Is the soil test recorded?",
    "Tutor Tom", "10:49 AM", "Yes", "Yes",
    "Mia", "10:51 AM", "Can moss grow on glass?", "Can moss grow on glass?",
])


def test_parse_panel_undoubles_and_drops_footer():
    e = q.parse_panel(SNAP1)
    assert [x["text"] for x in e][3] == "Rain is rare\n\nso they save it"
    assert e[-1] == {"author": "Tutor Tom", "time": "10:49 AM", "text": "Yes"}


def test_threads_label_people_and_merge_snapshots():
    th = q.build_threads([SNAP1, SNAP2])
    assert [t["by"] for t in th] == ["Attendee", "You", "Attendee", "Attendee"]
    assert "Asha" not in str(th) and "Ravi" not in str(th) and "Mia" not in str(th)
    soil = [t for t in th if "soil" in t["q"]][0]
    assert soil["answers"] == [{"by": "Tutor Tom", "time": "10:49 AM", "text": "Yes"}]
    assert th[-1]["answers"] == [] and not q.needs_followup(th[-1], {})
    assert q.needs_followup(th[0], {}) and not q.needs_followup(th[0], {th[0]["id"]: 1})


def test_render_section():
    th = q.build_threads([SNAP1])
    md = "\n".join(q.render_section(th, {th[1]["id"]: [{"type": "clarify", "q": "Which cacti?", "answer": "Not covered yet.", "sources": []}]}, {}))
    assert "You:** Why do cacti" in md and "Which cacti?" in md and "Tutor Tom" in md


def test_configured_staff_overrides_inference():
    th = q.build_threads([SNAP1, SNAP2], {"Tutor Tom"})
    assert {a["by"] for t in th for a in t["answers"]} == {"Tutor Tom"}
    th2 = q.build_threads([SNAP1], {"Someone Else"})
    assert all(t["answers"] == [] for t in th2) and "Tutor" not in str([t["by"] for t in th2])


def test_attendee_names_quoted_in_text_are_redacted():
    snap = SNAP1 + "\nMia\n10:52\u202fAM\nI agree with Asha about shade?\nI agree with Asha about shade?"
    th = q.build_threads([snap], {"Tutor Tom"})
    assert "Asha" not in str(th) and "another attendee" in th[-1]["q"]


def test_thread_kind_and_followup_skip():
    assert q.thread_kind({"q": "Insightful, thank you"}) == "trivial"
    assert q.thread_kind({"q": "Will the recording be on the LMS?"}) == "admin"
    assert q.thread_kind({"q": "Why do cacti store water in their stems during drought?"}) == "content"
    assert not q.needs_followup({"id": "x", "q": "Thanks!", "answers": [{"by": "T", "time": "1:00 PM", "text": "Welcome"}]}, {})


def test_relevant_sources_and_clean_answer():
    bank = {"S1": {"title": "Desert plant survey", "fact": "cacti store water in stems during drought"},
            "S2": {"title": "Ocean tides", "fact": "the moon drives tides"}}
    assert q.relevant_sources(["S1", "S2"], "Why do cacti store water?", bank) == ["S1"]
    assert q.clean_answer("Stems hold water [Lecture]. Roots are shallow [Q&A reply].") == "Stems hold water. Roots are shallow."


def test_rank_ask_now_prefers_current_topic_and_drops_asked():
    c = [{"q": "How do cacti store water in drought?", "answer": "", "t": "2026-01-05T04:40:00+00:00", "sources": []},
         {"q": "Do ferns need shade in winter?", "answer": "", "t": "2026-01-05T04:44:00+00:00", "sources": []},
         {"q": "Can moss grow on glass windows?", "answer": "", "t": "2026-01-05T04:44:00+00:00", "sources": []}]
    top = q.rank_ask_now(c, "today cacti store water in thick stems through drought", ["Can moss grow on glass?"],
                         "2026-01-05T04:45:00+00:00", k=2)
    assert top[0]["q"].startswith("How do cacti") and all("moss" not in t["q"] for t in top)


def test_curate_followups_drops_off_topic_repeats_and_caps_logistics():
    th = [
        {"id": "a", "by": "Attendee", "time": "10:10 AM", "q": "How do farmers pick drought tolerant seed varieties?",
         "answers": [{"by": "Tutor Tom", "time": "10:11 AM", "text": "Seed trials over three seasons."}]},
        {"id": "b", "by": "Attendee", "time": "10:20 AM", "q": "Where can I see the poll?",
         "answers": [{"by": "Tutor Tom", "time": "10:21 AM", "text": "In the poll window."}]},
        {"id": "c", "by": "Attendee", "time": "10:30 AM", "q": "Thanks!", "answers": []},
    ]
    seed = {"q": "How many seasons of seed trials before a drought variety is trusted?"}
    fus = {
        "a": [seed, {"q": "Which irrigation schedule suits clay soils?"}],          # second is off-topic
        "b": [{"q": "Will the poll results be shared after class?"},
              {"q": "When does the poll window close?"},
              dict(seed)],                                                       # repeat of thread a
        "c": [{"q": "Anything else?"}],
    }
    out = q.curate_followups(th, fus)
    assert [f["q"] for f in out["a"]] == [seed["q"]]
    assert len(out["b"]) == 1 and "poll" in out["b"][0]["q"]
    assert "c" not in out


def test_followup_prompt_for_logistics_asks_one_logistics_question_and_avoids_repeats():
    t = {"id": "b", "by": "Attendee", "time": "10:20 AM", "q": "Where can I see the poll?",
         "answers": [{"by": "Tutor Tom", "time": "10:21 AM", "text": "In the poll window."}]}
    p = q.followup_prompt("Soils", t, "talk", [], "", avoid=["How deep do roots grow?"])
    assert "LOGISTICS" in p and "1 follow-up" in p and "How deep do roots grow?" in p


def test_undouble_handles_cut_off_copy_and_leaked_next_entry():
    x = "Vertical means one industry.\n\nAgents act on their own"
    assert q._undouble(x + "\n" + x[:20]) == x
    assert q._undouble(x + "\n" + x + "\nNext question text that leaked") == x
    assert q._undouble("Short reply") == "Short reply"
