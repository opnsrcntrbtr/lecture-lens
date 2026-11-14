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
    lines = ["[00:06:00] The Klarna case and the Klarna metric."]
    out, rep = ng.guard(lines, dictionary=DICT, staff=set())
    assert out == lines and rep["review"] == {"Klarna": 2}
    out, rep = ng.guard(lines, dictionary=DICT, allow={"klarna"}, staff=set())
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
