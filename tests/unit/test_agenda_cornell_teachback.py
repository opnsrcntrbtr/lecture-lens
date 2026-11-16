"""NG-4 agenda reconciliation, LS-5 Cornell view and LS-6 teach-back (invented class, fake models)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import agenda      # noqa: E402
import cite        # noqa: E402
import cornell     # noqa: E402
import teachback   # noqa: E402

AGENDA = """Gardening basics, session 3
Part 1 · Soil and compost  (about 40 minutes)
What soil is made of; how compost feeds it.
Part 2 · Watering (~30 min): drip lines, mulch, timing
Part 3 · Pests  (about 30 minutes)
Companion planting and traps.
Close: questions."""


def lines(spec):
    """spec: [(start minute, n minutes, text)] -> transcript lines every 30 s from 10:00."""
    out, k = [], 0
    for m0, n, text in spec:
        for i in range(n * 2):
            s = (m0 * 60) + i * 30
            out.append({"line_id": f"L/t/{k}", "t": f"10:{s // 60:02d}:{s % 60:02d}" if s < 3600 else
                        f"11:{(s - 3600) // 60:02d}:{s % 60:02d}", "text": text})
            k += 1
    return out


def test_parse_agenda_parts():
    ps = agenda.parse_agenda(AGENDA)
    assert [p["title"] for p in ps] == ["Soil and compost", "Watering", "Pests"]
    assert [p["planned_min"] for p in ps] == [40, 30, 30]
    assert "drip lines" in ps[1]["detail"] and "Close" not in ps[2]["detail"]


def test_reconcile_moved_skipped_unplanned():
    # class: 15 min recap, watering 25 min, soil 20 min, no pests
    ls = lines([(0, 15, "last week we planted seeds recap"), (15, 25, "drip lines and mulch keep water"),
                (40, 20, "compost feeds the soil")])
    secs = agenda.windows(ls)
    labels = {s["section_id"]: "none" for s in secs}
    for s in secs:
        m = int(s["t0"][3:5]) + (60 if s["t0"].startswith("11") else 0)
        labels[s["section_id"]] = "none" if m < 15 else ("p2" if m < 40 else "p1")
    m = agenda.reconcile(agenda.parse_agenda(AGENDA), secs, labels)
    st = {r["item_id"]: (r["status"], r["coverage"], r["t0"]) for r in m["parts"]}
    assert st["p3"][0] == "skipped"
    assert st["p2"] == ("covered", "covered", "10:15:00")
    assert st["p1"][0] == "moved" and st["p1"][1] == "covered" and st["p1"][2] == "10:40:00"
    assert len(m["unplanned"]) == 3


def test_a_lone_window_is_a_mention_not_teaching():
    ls = lines([(0, 30, "drip lines and mulch"), (30, 5, "pests in passing"), (35, 20, "drip lines")])
    secs = agenda.windows(ls)
    labels = {s["section_id"]: ("p3" if s["t0"] == "10:30:00" else "p2") for s in secs}
    m = agenda.reconcile(agenda.parse_agenda(AGENDA), secs, labels)
    p3 = next(r for r in m["parts"] if r["item_id"] == "p3")
    assert p3["status"] == "skipped" and p3["mentions_min"] == 0     # smoothed into p2 (flanked)


def test_label_votes_and_verify_with_fake_models():
    ls = lines([(0, 20, "compost feeds the soil"), (20, 20, "drip lines and mulch")])
    secs = agenda.windows(ls)
    parts = agenda.parse_agenda(AGENDA)

    def labeller(prompt):
        ids = [w.split(" ")[0] for w in prompt.split("TRANSCRIPT WINDOWS")[1].splitlines() if w.startswith("w")]
        return json.dumps({"labels": {i: ("p1" if int(i[1:]) < 4 else "p2") for i in ids}})

    voted, agree = agenda.vote(parts, secs, labeller)
    assert voted["w00"] == "p1" and voted["w05"] == "p2" and set(agree.values()) == {1.0}
    reject_p2 = lambda p: json.dumps({"teaches": "Watering" not in p.split("\n")[0]})
    v = agenda.verify(parts, secs, voted, reject_p2)
    assert v["w00"] == "p1" and v["w05"] == "none"


def test_notes_block_lists_delivered_order_and_not_covered():
    m = {"parts": [{"agenda_item": "Part 1: Soil", "item_id": "p1", "status": "moved", "runs": [["10:40:00", "11:00:00"]]},
                   {"agenda_item": "Part 2: Watering", "item_id": "p2", "status": "covered", "runs": [["10:15:00", "10:40:00"]]},
                   {"agenda_item": "Part 3: Pests", "item_id": "p3", "status": "skipped", "runs": []}],
         "sections": [{"section_id": "w00", "t0": "10:00:00"}], "unplanned": ["w00"]}
    b = agenda.notes_block(m)
    assert b.index("Watering") < b.index("Soil") and "not covered" in b and "- Part 3: Pests" in b
    assert "pests" not in b.split("not covered")[0].lower()
    notes = "# Notes\n\nBody.\n"
    once = agenda.apply_to_notes(notes, b)
    assert agenda.apply_to_notes(once, b) == once and once.startswith("# Notes\n\nBody.")


def test_prequestion_on_a_skipped_part_is_not_covered(tmp_path):
    import prequestions as pq
    live = tmp_path / "live"
    live.mkdir()
    pq.save(live, [{"pq_id": "pq1", "agenda_item": "Part 3 Pests", "text": "Which plant keeps aphids away?", "guess": None,
                    "guess_t": None, "late": False, "resolved": None, "got_it": None, "became_card": False}])
    rows = pq.resolve(live, [{"line_id": "L/t/0", "t": "10:00:00", "text": "aphids pests plant keeps away"}],
                      lambda p: json.dumps({"answered": {"P1": [1]}}), skipped=["Pests"])
    assert rows[0]["resolved"]["status"] == "not_covered" and "skipped" in rows[0]["resolved"]["reason"]


# ------------------------------------------------------------------ LS-5
NOTES = """# Gardening
## TL;DR
* Mulch keeps water in the soil and cuts watering by half [10:16:00–10:18:00].
* Compost feeds soil life, which builds structure [10:41:00].
## Action items for the learner
* Buy mulch [10:20:00].
"""


def test_extracts_link_timed_bullets_to_lines():
    ls = lines([(15, 5, "mulch keeps water in"), (40, 3, "compost feeds soil life")])
    ex = cornell.extracts(NOTES, ls)
    assert [e["extract_id"] for e in ex] == ["x1", "x2"] and ex[0]["section_id"] == "tl-dr"
    assert ex[0]["line_ids"] and "[10:" not in ex[0]["quote"] and ex[0]["t0"] == "10:16:00"


def test_cues_need_an_extract_and_must_not_contain_the_answer():
    ex = [{"extract_id": "x1", "section_id": "s", "quote": "Mulch keeps water in the soil and cuts watering by half", "t0": "10:16:00"}]
    reply = json.dumps({"cues": [
        {"kind": "cue", "extracts": ["x1"], "question": "What does mulch do for soil moisture?"},
        {"kind": "cue", "extracts": ["x9"], "question": "Unknown extract?"},
        {"kind": "cue", "extracts": ["x1"], "question": "Does mulch keep water in the soil and cut watering by half?"},
        {"kind": "apply", "extracts": ["x1"], "question": "How would you use mulch in your own garden plan?"}]})
    cues = cornell.make_cues(ex, {"s": "Watering"}, lambda p: reply)
    assert [c["question"][:9] for c in cues] == ["What does", "How would"]
    tsv = cornell.cards_tsv(cues, ex)
    assert tsv.count("\n") == 1 and "[10:16:00]" in tsv           # only 'cue' kind becomes a card


def test_summary_is_never_prefilled(tmp_path):
    folder = tmp_path / "L"
    folder.mkdir()
    (folder / "notes.md").write_text(NOTES)
    v = cornell.view(folder, tmp_path / "summaries")
    assert v["summary"] == "" and v["summary_prompt"] and [s["heading"] for s in v["sections"]][0] == "TL;DR"
    cornell.save_summary(tmp_path / "summaries", "L", "Mulch saves water.")
    assert cornell.view(folder, tmp_path / "summaries")["summary"] == "Mulch saves water."


# ------------------------------------------------------------------ LS-6
EX = [{"extract_id": f"x{i}", "quote": q, "line_ids": [f"L/t/{i}"], "t0": "10:00:00"} for i, q in enumerate([
    "mulch keeps water in", "compost feeds soil life", "drip lines water roots", "traps catch slugs",
    "marigolds deter aphids", "water early in the morning"], 1)]


def checker():
    """Supported when every content word of the claim is in the passage (a strict fake)."""
    return cite.Checker("fake", "1", 0.5, lambda s, ps: [1.0 if cite.norm_words(s) <= cite.norm_words(p) else 0.0 for p in ps])


def writer(planted_ok=True):
    st = [{"text": "mulch keeps water in", "extract": "x1"}, {"text": "compost feeds soil life", "extract": "x2"},
          {"text": "drip lines water roots", "extract": "x3"}, {"text": "traps catch slugs", "extract": "x4"},
          {"text": "marigolds attract aphids", "extract": "x5", "planted": True, "correct": "marigolds deter aphids",
           "why": "they deter, not attract"},
          {"text": "water late in the evening", "extract": "x6", "planted": True,
           "correct": "water early in the morning" if planted_ok else "water whenever", "why": "morning"},
          {"text": "compost feeds soil life always", "extract": "x2"}]
    return lambda p: json.dumps({"statements": st})


def test_teachback_key_is_checked_and_hidden_until_scored(tmp_path):
    gen = teachback.generate("Garden care", EX, lambda e: [e["quote"]], writer(), checker(), seed=1)
    assert gen["ok"] and sum(s["planted"] for s in gen["statements"]) == 2
    assert "near-duplicate" in gen["dropped"] or "true statement not supported" in gen["dropped"]
    store = teachback.Store(tmp_path)
    d = store.new("L", gen)
    pub = teachback.public(d)
    assert all(set(s) == {"s_id", "text"} for s in pub["statements"])
    planted = [s["s_id"] for s in d["statements"] if s["planted"]]
    true = [s["s_id"] for s in d["statements"] if not s["planted"]]
    r = teachback.score(store, d["id"], [planted[0], true[0]], {planted[0]: "they deter"})
    assert (r["caught"], r["missed"], r["false_alarms"]) == (1, 1, 1)
    assert any(s.get("correct") for s in r["statements"])
    assert teachback.score(store, d["id"], [])["caught"] == 1        # scoring happens once


def test_teachback_refuses_when_planted_errors_are_not_shown_wrong():
    gen = teachback.generate("Garden care", EX, lambda e: [e["quote"]], writer(planted_ok=False), checker(), seed=1)
    assert not gen["ok"] and "planted" in gen["error"]
