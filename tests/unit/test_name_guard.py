"""Attendee names are removed from transcripts when there is evidence; staff stay. Invented fixtures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import name_guard as ng  # noqa: E402

DICT = {"thanks", "yes", "so", "the", "mulch", "garden", "water", "go", "ahead", "early", "beds", "pat"}


def test_addressed_names_are_removed_and_staff_kept():
    lines = ["[00:01:00] Thanks, Zorvan. Mulch keeps soil moist.",
             "[00:02:00] Quillana B. I think beds should be raised.",
             "[00:03:00] Dr Sam Sample says water early.",
             "[00:04:00] Vexley, go ahead with your question."]
    out, rep = ng.guard(lines, dictionary=DICT, staff=ng.staff_names("Dr Sam Sample"))
    text = "\n".join(out)
    for n in ("Zorvan", "Quillana", "Vexley"):
        assert n not in text
    assert "Quillana B." not in text and "an attendee I think" in text
    assert "Sam Sample" in text
    assert set(rep["removed"]) >= {"Zorvan", "Quillana", "Vexley"}


def test_panel_authors_removed_everywhere():
    out, _ = ng.guard(["[00:05:00] As Orrin asked, mulch helps."], panel_authors={"Orrin Vale to Everyone"}, dictionary=DICT, staff=set())
    assert out == ["[00:05:00] As an attendee asked, mulch helps."]


def test_unknown_words_are_reviewed_not_removed():
    lines = ["[00:06:00] The Plaxo case and the Plaxo metric."]
    out, rep = ng.guard(lines, dictionary=DICT, staff=set())
    assert out == lines and rep["review"] == {"Plaxo": 2}
    out, rep = ng.guard(lines, dictionary=DICT, allow={"plaxo"}, staff=set())
    assert rep["review"] == {}


def test_dictionary_words_after_cues_are_kept():
    out, rep = ng.guard(["[00:07:00] So, Garden beds need water."], dictionary=DICT, staff=set())
    assert "Garden" in out[0] and rep["removed"] == {}


def test_products_at_line_start_and_chat_suffix_are_not_names():
    out, rep = ng.guard(["[00:08:00] Waymo, I think, uses remote help.", "[00:09:00] Okay. Labeling matters."],
                        panel_authors={"Everyone"}, dictionary=DICT, staff=set())
    assert "Waymo" in out[0] and "Okay" in out[1] and rep["removed"] == {}


def test_lowercase_after_a_cue_and_acronym_authors_are_kept():
    lines = ["[00:10:00] So, dependencies matter here.", "[00:11:00] Right, timestamp every decision."]
    out, rep = ng.guard(lines, panel_authors={"QRX", "What does the QRX need to say?"}, dictionary=DICT, staff=set())
    assert out == lines and rep["removed"] == {}


def test_frequent_capitalised_term_is_reviewed_not_removed():
    lines = ["[00:00:%02d] Thanks, Zorvan. The Plaxo spec matters." % i for i in range(1)] + \
            ["[00:01:%02d] The Plaxo spec lists owners." % i for i in range(8)]
    out, rep = ng.guard(lines, panel_authors={"Plaxo"}, dictionary=DICT, staff=set())
    assert "Zorvan" in rep["removed"] and "Plaxo" not in rep["removed"] and rep["review"]["Plaxo"] == 9
    assert all("Plaxo" in l for l in out)


def test_names_with_initials_still_count():
    assert ng.looks_like_name("Quillana B.") and ng.looks_like_name("Orrin Vale")
    assert not ng.looks_like_name("PRD") and not ng.looks_like_name("Can we share the deck?")


# ---------------------------------------------------------------- v2: roster layer (invented names)
ROSTER = ["Zorvana Quillamurthy", "Pradyot Vexanta", "Seelan Brahmavar"]


def test_roster_catches_asr_spellings_and_keeps_domain_terms():
    r = ng.Roster(ROSTER, dictionary=DICT)
    lines = ["[00:01:00] As Zorvanah said, the PRD needs a timestamp.",
             "[00:02:00] Pradyoth asked about dependencies and the panelists.",
             "[00:03:00] Shelan mentioned the Klarna case.",
             "[00:04:00] quilamurti had a follow-up."]
    out, rep = ng.guard(lines, dictionary=DICT, staff=set(), roster=r)
    text = "\n".join(out)
    for w in ("Zorvanah", "Pradyoth", "Shelan"):
        assert w not in text, w
    assert "quilamurti" in rep["review"]          # a lower-case spelling match is reviewed, not removed
    for w in ("PRD", "timestamp", "dependencies", "panelists", "Klarna"):
        assert w in text, w
    layers = {e["token"]: e["layer"] for e in rep["entries"] if e["decision"] == "removed"}
    assert layers["Pradyoth"].startswith("roster-") and rep["guard_version"] == "2"


def test_full_roster_name_and_exact_parts_skip_the_frequency_gate():
    r = ng.Roster(ROSTER, dictionary=DICT)
    lines = [f"[00:{i:02d}:00] Pradyot Vexanta raised a hand again." for i in range(12)]
    out, rep = ng.guard(lines, dictionary=DICT, staff=set(), roster=r)
    assert all("Pradyot" not in l and "Vexanta" not in l for l in out)
    assert "Pradyot" not in rep["review"]


def test_learner_decisions_win_both_ways():
    r = ng.Roster(ROSTER, dictionary=DICT)
    lines = ["[00:01:00] Brahmavar and Ombrix were both mentioned twice.", "[00:02:00] Ombrix again, and Brahmavar."]
    out, rep = ng.guard(lines, dictionary=DICT, staff=set(), roster=r, decisions={"brahmavar": "keep", "ombrix": "remove"})
    assert "Brahmavar" in out[0] and "Ombrix" not in out[0] and "Ombrix" not in out[1]
    assert {e["layer"] for e in rep["entries"] if e["token"] == "Ombrix"} == {"learner"}


def test_staff_and_dictionary_parts_are_not_matched_alone():
    r = ng.Roster(["Rose Zentaro"], keep={"zentaro"}, dictionary=DICT | {"rose"})
    assert len(r) == 0 and r.match("Rose") is None
    out, _ = ng.guard(["[00:01:00] A rose garden."], dictionary=DICT | {"rose", "a", "garden"}, staff=set(), roster=r)
    assert out == ["[00:01:00] A rose garden."]


def test_close_but_uncertain_spelling_goes_to_review():
    r = ng.Roster(ROSTER, dictionary=DICT)
    hit = r.match("Prado")
    assert hit and hit[0] == "review"


def test_roster_and_decisions_files_round_trip(tmp_path):
    info = ng.update_roster(tmp_path, {"20300101": {"Orrin Vale", "What is a PRD?", "Everyone"}})
    assert info == {"names": 1, "added": 1}
    (tmp_path / "attendees.txt").write_text("Quessa Lorimar  # added by hand\n")
    assert set(ng.load_roster(tmp_path)) == {"Orrin Vale", "Quessa Lorimar"}
    d = ng.save_decisions(tmp_path, remove=["Ombrix"], keep=["Klarna"])
    assert d == {"klarna": "keep", "ombrix": "remove"} and ng.load_decisions(tmp_path) == d


def test_inflections_places_caps_and_json_escapes():
    r = ng.Roster(["Meanor Kallad", "Pradyot Vexanta"], dictionary=DICT | {"mean", "call"})
    lines = ['{"q": "It means a lot.\\nPradyot asked; NVIDIA and Mumbai came up."}', "Called it. Means well."]
    out, rep = ng.guard(lines, dictionary=DICT | {"mean", "call", "it", "a", "lot", "well"}, staff=set(), roster=r)
    assert "means" in out[0] and "Means" in out[1] and "Called" in out[1]
    assert "\\nan attendee asked" in out[0] and "NVIDIA" in out[0] and "Mumbai" in out[0]


def test_held_tokens_are_reviewed_not_removed():
    r = ng.Roster(ROSTER, dictionary=DICT)
    out, rep = ng.guard(["[00:01:00] Pradyoth spoke."], dictionary=DICT, staff=set(), roster=r, hold={"Pradyoth"})
    assert "Pradyoth" in out[0] and "Pradyoth" in rep["review"]
