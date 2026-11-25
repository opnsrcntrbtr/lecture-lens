"""CC-7 / M-12 voice latency on this Mac, without a microphone: a panel on the invented
sample product, each answer spoken by a built-in voice into a WAV and fed through the voice
loop (whisper transcription, the next panel question from the local model, synthesis of
that question). Reports the processing time per answer (p50, p95) and how much of the spoken
answer whisper got back. The app adds playback start and the end-of-speech wait (about 2 s
with auto-stop, none with the Stop button) on top.

  python3 tools/voice_bench.py [--n 6] [--pressure sceptical]
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capstone as cs  # noqa: E402
import capstone_panel as cp  # noqa: E402
import capstone_voice as cv  # noqa: E402

ANSWERS = [
    "We measured a twelve percent drop in repeat contacts during the four week pilot, um, with two hundred users.",
    "Because the golden set has three hundred real inputs, and we block release when the pass rate falls below ninety percent.",
    "The fallback hands the conversation to a person when confidence is low, and we tested that path in the pilot.",
    "We decided not to build voice ordering, you know, because the error cost was too high for the first release.",
    "Our unit economics hold at heavy usage: about four cents per successful task, including retries.",
    "The main risk is drift in the product catalogue, so we re-run the evaluation every week and alert on a drop.",
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6); ap.add_argument("--pressure", default="sceptical")
    a = ap.parse_args(argv)
    import study
    llm = lambda system, user: study.llm(system, user, max_tokens=600, temperature=0.5, slot="interactive")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ws = cs.Workspace(td / "cap").init()
        for f, kind in (("prd.md", "prd"), ("strategy.md", "strategy"), ("system_card.md", "system_card")):
            src = HERE / "capstone_samples" / "A" / f
            if src.exists():
                ws.register(kind, src, kind)
        pn = cp.Panel(ws, 7)
        pn.start("panel", a.pressure, 60)
        pn.next_question(llm)
        vp = cv.VoicePanel(pn)
        speaker = next((v for v, _ in cv.installed_voices() if v.startswith("Samantha")), None)
        rows, heard = [], []
        for i in range(a.n):
            if not pn.view()["awaiting_answer"]:
                break
            vp.say_current()
            text = ANSWERS[i % len(ANSWERS)]
            wav = cv.wav_from_text(text, speaker, td / f"a{i}.wav")
            r = vp.answer_audio(wav, llm)
            if r.get("error"):
                print(json.dumps({"error": r["error"]})); break
            rows.append(r["timing"])
            sm = difflib.SequenceMatcher(None, [cv.norm(w) for w in text.split()], [cv.norm(w) for w in r["transcript"].split()])
            heard.append(round(sm.ratio(), 3))
            print(json.dumps({"answer": i + 1, **r["timing"], "heard": heard[-1], "wpm": r["speech"]["wpm"]}), flush=True)
        s = vp.summary()
    q = lambda xs, p: round(sorted(xs)[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 2) if xs else None
    rep = {"answers": len(rows), "pressure": a.pressure, "voices": s["voices"],
           "processing_p50_s": q([r["processing_s"] for r in rows], .5), "processing_p95_s": q([r["processing_s"] for r in rows], .95),
           "transcribe_p50_s": q([r["transcribe_s"] for r in rows], .5), "model_p50_s": q([r["model_s"] for r in rows], .5),
           "speak_p50_s": q([r["speak_s"] for r in rows], .5), "heard_median": q(heard, .5)}
    p = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-voice-bench.json"
    p.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
