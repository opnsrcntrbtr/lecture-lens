"""CC-7 voice mode: voices per reviewer, speech metrics, and the voice turn on top of the
panel's own rules, with fake speech and a fake model (no audio, no network)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone as cs          # noqa: E402
import capstone_panel as cp    # noqa: E402
import capstone_voice as cv    # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "capstone_samples"
LISTING = """Albert              en_US    # Hello! My name is Albert.
Bad News            en_US    # Hello! My name is Bad News.
Daniel (English (UK)) en_GB    # Hello! My name is Daniel.
Karen               en_AU    # Hello! My name is Karen.
Amelie              fr_CA    # Bonjour! Je m'appelle Amelie.
Samantha            en_US    # Hello! My name is Samantha.
Rishi               en_IN    # Hello! My name is Rishi.
"""


def test_voices_are_english_distinct_and_overridable():
    v = cv.installed_voices(LISTING)
    names = [n for n, _ in v]
    assert "Amelie" not in names and "Bad News" not in names and "Daniel (English (UK))" in names
    m = cv.persona_voices(["methodologist", "product_leader", "risk_examiner"], v)
    assert m == {"methodologist": "Daniel (English (UK))", "product_leader": "Samantha", "risk_examiner": "Karen"}
    m = cv.persona_voices(["methodologist", "product_leader"], v, "product_leader=Rishi, nobody=Karen")
    assert m["product_leader"] == "Rishi" and m["methodologist"] == "Daniel (English (UK))"
    assert len(set(m.values())) == len(m)


def words(text, start=0.0, step=0.4, gaps=()):
    out, t = [], start
    for i, w in enumerate(text.split()):
        if i in gaps:
            t += 2.5
        out.append({"w": w, "t": t, "t1": t + 0.3})
        t += step
    return out


def test_speech_metrics_count_fillers_and_pace():
    ws = words("Um so we measured, you know, a twelve percent drop basically in repeat contacts uh last month",
               gaps=(9,))
    m = cv.speech_metrics(ws)
    assert m["words"] == 17 and m["fillers"] == 4          # um, you know, basically, uh
    assert m["long_pauses"] == 1 and m["wpm"] == round(60 * 17 / m["speaking_s"])
    assert cv.speech_metrics(words("short answer"))["wpm"] is None      # under 5 s: no pace
    assert cv.clean_text([{"w": "We", "t": 0}, {"w": "measured", "t": 1}, {"w": ".", "t": 2}]) == "We measured."


def panel(tmp_path):
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("prd", SAMPLES / "A" / "prd.md", "prd")
    pn = cp.Panel(ws, 3, day="2030-01-05")
    pn.start("panel", "neutral", 30)
    return pn


def fake_llm(system, user):
    return json.dumps({"question": "What evidence shows the reorder suggestions save time for store managers?"})


def test_voice_turn_uses_the_panel_rules_speaks_the_next_question_and_deletes_audio(tmp_path):
    pn = panel(tmp_path)
    pn.next_question(fake_llm)
    spoken = []
    def fake_speak(text, voice, out):
        spoken.append((text, voice)); Path(out).write_bytes(b"AIFF"); return 0.2
    said = "We measured a twelve percent drop in repeat contacts during the four week pilot with two hundred users"
    vp = cv.VoicePanel(pn, voices=cv.installed_voices(LISTING), speak_fn=fake_speak,
                       transcribe_fn=lambda p: words(said), keep_audio=False)
    first = vp.say_current()
    assert first["audio"].endswith(".aiff") and spoken[0][1] == vp.voices[pn.view()["turns"][-1]["speaker"]]
    rec = tmp_path / "answer.wav"; rec.write_bytes(b"RIFF")
    r = vp.answer_audio(rec, fake_llm)
    assert r["transcript"] == said and not rec.exists()                   # recording deleted once transcribed
    turns = pn.turns()
    assert [t["speaker"] for t in turns][-2] == "learner" and turns[-2]["text"] == said
    assert turns[-1]["speaker"] != "learner" and r["audio"] and len(spoken) == 2
    assert set(r["timing"]) == {"transcribe_s", "model_s", "speak_s", "processing_s"}
    assert r["speech"]["words"] == len(said.split())
    vp.log_latency(4200)
    s = vp.summary()
    assert s["answers"] == 1 and s["end_to_audio_p50_s"] == 4.2


def test_nothing_heard_is_not_submitted(tmp_path):
    pn = panel(tmp_path)
    pn.next_question(fake_llm)
    vp = cv.VoicePanel(pn, voices=[], speak_fn=lambda t, v, o: 0.0, transcribe_fn=lambda p: [], keep_audio=True)
    rec = tmp_path / "silence.wav"; rec.write_bytes(b"RIFF")
    r = vp.answer_audio(rec, fake_llm)
    assert "nothing was heard" in r["error"] and rec.exists()
    assert pn.view()["awaiting_answer"] is True and pn.turns()[-1]["speaker"] != "learner"
