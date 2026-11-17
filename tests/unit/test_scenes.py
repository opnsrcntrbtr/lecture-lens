"""CT-5 scene classes: the cascade stages on invented frames, OCR region, macro-F1."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scenes  # noqa: E402


def box(text, left, top, width=0.3):
    return {"text": text, "left": left, "top": top, "width": width, "height": 0.03}


def test_ocr_region_drops_toolbars_and_side_panel():
    entries = [box("Mute", 0.03, 0.95), box("Soil and mulch", 0.2, 0.2), box("Chat message from someone", 0.82, 0.3, 0.15),
               box("Mulch keeps water in", 0.2, 0.3)]
    assert scenes.ocr_text(entries) == "Soil and mulch\nMulch keeps water in"


def test_title_stage():
    assert scenes.classify({"app_name": "Lecture Lens", "window_name": "Study"})["class_source"] == "title"
    c = scenes.classify({"app_name": "Google Chrome", "window_name": "Garden plan - Google Docs"})
    assert c == {"class": "shared_document", "class_source": "title", "reason": "document window"}


def test_layout_break_gallery_slide_document_other():
    brk = {"text": "We will be back shortly\nTaking a 10 minute break", "dwell_s": 300}
    assert scenes.classify(brk)["class"] == "break"
    tiles = "\n".join(["Asha Rao", "Ben Ode", "Carla M.", "Dev K", "Eli Moss", "Fay Lin"])
    assert scenes.classify({"text": tiles, "dwell_s": 30})["class"] == "gallery"
    slide = "\n".join(["Watering basics", "Mulch keeps water in the soil", "Drip lines feed the roots directly",
                       "Water early in the morning", "Check soil moisture before watering"])
    assert scenes.classify({"text": slide, "dwell_s": 40}) == {"class": "slide", "class_source": "stability",
                                                               "reason": "5 short lines"}
    doc = "\n".join(["This document describes the garden plan for the season in detail with long sentences " * 2] * 8)
    assert scenes.classify({"text": doc, "dwell_s": 60})["class"] == "shared_document"
    assert scenes.classify({"text": "My Programs\nAcademic Planner\nDiscussions"})["class"] == "other"


def test_vision_only_when_ambiguous():
    calls = []
    vlm = lambda img: calls.append(img) or "gallery"
    slide = "\n".join(["Watering basics", "Mulch keeps water in the soil", "Drip lines feed the roots directly",
                       "Water early in the morning"])
    assert scenes.classify({"text": slide, "dwell_s": 40, "image": "a.jpg"}, vlm)["class_source"] == "stability"
    c = scenes.classify({"text": "", "dwell_s": 40, "image": "b.jpg"}, vlm)
    assert c == {"class": "gallery", "class_source": "vision", "reason": "almost no text"} and calls == ["b.jpg"]
    assert scenes.classify({"text": slide, "dwell_s": 1.0, "diff_prev": 0.8, "image": "c.jpg"}, vlm)["class_source"] == "vision"


def test_parse_label_and_macro_f1():
    assert scenes.parse_label("Slide.") == "slide" and scenes.parse_label("a shared document") == "shared_document"
    assert scenes.parse_label("no idea") is None
    r = scenes.macro_f1(["slide", "slide", "break", "gallery"], ["slide", "break", "break", "gallery"])
    assert r["per_class"]["slide"]["recall"] == 0.5 and r["break_as_slide"] == 0
    assert scenes.macro_f1(["break"], ["slide"])["break_as_slide"] == 1
