"""Slide reader (OCR fallback) filters, from the kinds of text that leaked into a real slide log.
Fixtures are generic: no course content and no real names."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import live_class as lc  # noqa: E402


def lines(*rows, title_idx=0):
    out = []
    for i, t in enumerate(rows):
        out.append({"text": t, "x": 0.1, "y": 0.15 + i * 0.03, "w": 0.4, "h": 0.06 if i == title_idx else 0.02})
    return out


BODY = ["Three ways to keep a vegetable patch watered", "Start with a small bed near the tap",
        "Mulch the soil to hold moisture through summer", "Check the drip line every few weeks"]


def test_toolbar_and_qa_panel_lines_are_dropped(monkeypatch):
    monkeypatch.setenv("LIVE_QA_STAFF", "")
    body, title = lc.slide_from_lines(lines("Watering the garden", *BODY, "Chat", "React", "Q&A", "Settings",
                                            "Leave", "All questions (3)", "Send anonymously"))
    assert title == "Watering the garden"
    for junk in ("Chat", "React", "Q&A", "Settings", "Leave", "All questions", "Send anonymously"):
        assert junk not in body.splitlines()


def test_app_licence_dialog_is_dropped():
    body, _ = lc.slide_from_lines(lines("Watering the garden", "PRODUCT NOTICE There was a problem checking this "
                                        "device's license status.", "Sign In", "SIGN IN TO OFFICE it looks like your "
                                        "stored credentials are out of date.", *BODY))
    assert "PRODUCT NOTICE" not in body and "Sign In" not in body.splitlines() and "credentials" not in body
    assert lc.is_ui_line("Garden deck - We... • Saved to this PC v")


def test_taskbar_and_fragment_titles_are_rejected():
    assert not lc.plausible_title("ENG C9 )) 01-01-2030")
    assert not lc.plausible_title("[9 0)) 01-01-2030")
    assert not lc.plausible_title("cu collectica")
    assert lc.plausible_title("Part 5: drip irrigation")
    assert lc.plausible_title("Garden Layout")
    # a junk tallest line falls back to the next real title
    _, title = lc.slide_from_lines(lines("ENG C9 )) 01-01-2030", "Watering the garden", *BODY))
    assert title == "Watering the garden"


def test_name_tiles_and_meeting_window_are_not_slides(monkeypatch):
    monkeypatch.setenv("LIVE_QA_STAFF", "Pat Example,Prof. Pat Example")
    assert lc.is_person_label("Sam Sample - Program Coordinator")
    assert lc.is_person_label("Sam Sample - Program Co...")
    assert lc.is_person_label("Pat Example")
    assert not lc.is_person_label("Raised Beds")
    # a staff-name tile with toolbar text only
    assert lc.slide_from_lines(lines("Pat Example", "Chat", "React", "Q&A", "Settings", "Leave")) == ("", None)
    # the meeting app itself: share banner + meeting title with times, and an attendee tile
    frame = lines("Alex Attendee", "Workshop title, 1 Jan, 2030 10:00:00 - 1 Jan, 2030 13:00:00",
                  "You're screen sharing", "Stop Share", *BODY)
    assert lc.slide_from_lines(frame) == ("", None)
    # a presenter label above a real slide is removed, the slide is kept
    body, title = lc.slide_from_lines(lines("Watering the garden", "Pat Example", *BODY))
    assert title == "Watering the garden" and "Pat Example" not in body


def test_chat_toast_nav_items_and_browser_editor(monkeypatch):
    monkeypatch.setenv("LIVE_QA_STAFF", "")
    assert lc.is_ui_line("From Sam Sample - Program")
    assert not lc.plausible_title("• Pick the plot")
    assert not lc.plausible_title("harvest stage?")
    browser = lines("Watering the garden", "docs.google.com/presentation/d/abc123/edit?slide=id.p9", "Ask Google",
                    "Slideshow", *BODY)
    assert lc.slide_from_lines(browser) == ("", None)
    # a reference URL on a slide is fine
    body, title = lc.slide_from_lines(lines("Watering the garden", "Source: example.org/report-2025", *BODY))
    assert title == "Watering the garden"


def test_insert_ribbon_line_is_chrome():
    assert lc.is_editor_chrome("Pictures l) Photo Album")
    assert lc.is_editor_chrome("WordArt Symbols")
    assert lc.is_editor_chrome("DD English (India)")
    assert not lc.is_editor_chrome("Pictures of the field team")
