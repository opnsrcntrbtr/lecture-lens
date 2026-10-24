import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import transcript_clean as tc
import live_class as lc


def seg(sec, text):
    m, s = divmod(sec, 60)
    return {"t": f"2026-01-05T05:{m:02d}:{s:02d}+00:00", "text": text}


def test_drops_stock_phrases_and_duplicates():
    rows = [seg(0, "Thank you."), seg(5, "you"), seg(10, "Cacti store water in their stems."),
            seg(10, "Cacti store water in their stems.")]
    kept, dropped = tc.clean_segments(rows)
    assert [s["text"] for s in kept] == ["Cacti store water in their stems."]
    assert dropped == {"stock": 2, "dup": 1}


def test_drops_prompt_echo_but_keeps_real_speech_using_prompt_words():
    prompt = "Dr. Rivera desert botany: succulents, xerophytes, CAM photosynthesis"
    rows = [seg(0, "Dr. Rivera xerophytes: succulents?"),
            seg(30, "Today we look at how succulents survive a dry season.")]
    kept, dropped = tc.clean_segments(rows, prompt)
    assert [s["text"] for s in kept] == ["Today we look at how succulents survive a dry season."]
    assert dropped["echo"] == 1


def test_drops_loops_within_window_only():
    loop = [seg(i * 20, "Roots go deep in sandy soil.") for i in range(4)]       # 4 in 60 s
    spread = [seg(600, "Let's take questions."), seg(1500, "Let's take questions.")]
    kept, dropped = tc.clean_segments(loop + spread)
    assert dropped["repeat"] == 4
    assert [s["text"] for s in kept] == ["Let's take questions.", "Let's take questions."]


def test_slide_kinds_and_ocr_fix():
    slide = {"src": "vision", "title": "Al for dry climates: three adaptations",
             "text": " ".join(["water storage leaf shape root depth"] * 10)}
    board = {"src": "vision", "title": "rcot dpth", "text": "rcot dpth sand ~ water"}
    chrome = {"src": "vision", "title": "Host has started screen sharing.", "text": "Host has started screen sharing."}
    out = tc.label_slides([slide, board, chrome])
    assert [r["kind"] for r in out] == ["slide", "whiteboard", "chrome"]
    assert out[0]["title"].startswith("AI for dry climates")


def test_whisper_runs_on_audio_health_alone():
    assert lc.audio_ok({"frame_status": "disabled", "audio_status": "ok"})
    assert not lc.healthy({"frame_status": "disabled", "audio_status": "ok"})
    assert not lc.audio_ok({"frame_status": "ok", "audio_status": "stale"})
