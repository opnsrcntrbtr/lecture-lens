"""CC-7 voice mode for the panel defence: hear each reviewer ask, answer out loud.

A cascade, all on this Mac: the app records your answer (push to talk; it also stops after
about 2 s of quiet once you have spoken), whisper.cpp transcribes it, the transcript goes
through the same panel turn rules as a typed answer (capstone_panel), and the next question
is spoken in that reviewer's own built-in macOS voice (`say`). Nothing is downloaded and
nothing leaves the Mac. Recordings are deleted once transcribed unless
STUDY_PANEL_KEEP_AUDIO=1.

Pace (words a minute) and fillers per 100 words come from whisper's word timestamps and are
shown as information only: whether they predict panel scores was not researched, and
whisper drops some fillers even when prompted to keep them, so the filler rate is a floor.

Latency per answer (voice_metrics.jsonl in the panel session): transcribe_s, model_s (next
question), speak_s (synthesis), and from the app the time from the end of your speech to the
first sound of the reply.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import capstone_panel as cp

# whisper keeps more disfluencies when its prompt contains some
FILLER_PROMPT = "Um, so, uh, I think, you know, the, um, main point is, like, this."
FILLERS_1 = {"um", "uh", "er", "erm", "uhm", "hmm", "ah", "mm"}
FILLERS_2 = {("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")}
HEDGES = {"basically", "actually", "literally"}
PREFERRED = ["Daniel", "Samantha", "Karen", "Moira", "Rishi", "Aman", "Tessa", "Fred", "Albert", "Ralph", "Kathy"]
NOVELTY = {"Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News", "Jester", "Organ", "Superstar",
           "Trinoids", "Whisper", "Wobble", "Zarvox", "Junior"}
SAY_LINE = re.compile(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#")
norm = lambda w: re.sub(r"[^a-z']", "", w.lower())


# ------------------------------------------------------------------ voices
def installed_voices(listing: str | None = None) -> list[tuple[str, str]]:
    """(name, locale) of installed English voices, novelty voices left out."""
    if listing is None:
        try:
            listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
    out = []
    for line in listing.splitlines():
        m = SAY_LINE.match(line)
        if m and m.group(2).startswith("en_"):
            name = m.group(1).strip()
            if name.split(" (")[0] not in NOVELTY:
                out.append((name, m.group(2)))
    return out


def persona_voices(persona_ids: list[str], voices: list[tuple[str, str]], override: str = "") -> dict[str, str]:
    """One distinct voice per reviewer: STUDY_PANEL_VOICES ("methodologist=Daniel,...") first,
    then the preferred list, then any other installed English voice."""
    names = [v for v, _ in voices]
    base = lambda n: n.split(" (")[0]
    chosen: dict[str, str] = {}
    for pair in filter(None, (x.strip() for x in override.split(","))):
        pid, _, v = pair.partition("=")
        hit = next((n for n in names if base(n) == v.strip() or n == v.strip()), None)
        if pid.strip() in persona_ids and hit:
            chosen[pid.strip()] = hit
    pool = [n for p in PREFERRED for n in names if base(n) == p] + [n for n in names if base(n) not in PREFERRED]
    pool = [n for n in dict.fromkeys(pool) if n not in chosen.values()]
    for pid in persona_ids:
        if pid not in chosen and pool:
            chosen[pid] = pool.pop(0)
    return chosen


def speak(text: str, voice: str | None, out: Path) -> float:
    """Synthesise `text` to an AIFF file with macOS `say`; returns the seconds it took."""
    t0 = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["say", "-o", str(out)] + (["-v", voice] if voice else []) + [text]
    subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    return round(time.time() - t0, 2)


# ------------------------------------------------------------------ speech
def transcribe(audio: Path, transcribe_fn=None) -> list[dict]:
    """Word-level transcript of a recorded answer: [{w, t, t1}] in seconds."""
    if transcribe_fn:
        return transcribe_fn(audio)
    import live_asr
    words, _ = live_asr.whisper_words(Path(audio), None, 0.0, prompt=FILLER_PROMPT, timeout=300)
    return words


def speech_metrics(words: list[dict], pause_s: float = 2.0) -> dict:
    """Words a minute over the speaking span, fillers per 100 words, long pauses. Information only."""
    toks = [norm(w["w"]) for w in words]
    toks_ok = [t for t in toks if t]
    n = len(toks_ok)
    span = (words[-1].get("t1", words[-1]["t"]) - words[0]["t"]) if words else 0.0
    fillers = sum(1 for t in toks_ok if t in FILLERS_1 or t in HEDGES)
    fillers += sum(1 for a, b in zip(toks_ok, toks_ok[1:]) if (a, b) in FILLERS_2)
    pauses = sum(1 for a, b in zip(words, words[1:]) if b["t"] - a.get("t1", a["t"]) >= pause_s)
    return {"words": n, "speaking_s": round(span, 1), "wpm": round(60 * n / span) if span >= 5 else None,
            "fillers": fillers, "fillers_per_100": round(100 * fillers / n, 1) if n else None, "long_pauses": pauses,
            "note": "information only; whisper drops some fillers, so this is a floor"}


def clean_text(words: list[dict]) -> str:
    """The transcript as the panel sees it: the words in order, fillers kept (they are yours)."""
    text = " ".join(w["w"].strip() for w in words).strip()
    return re.sub(r"\s+([,.?!;:])", r"\1", text)


# ------------------------------------------------------------------ session
class VoicePanel:
    """Voice turns on top of an existing panel session (capstone_panel.Panel)."""

    def __init__(self, panel: cp.Panel, voices: list[tuple[str, str]] | None = None, speak_fn=speak,
                 transcribe_fn=None, keep_audio: bool | None = None):
        self.p = panel
        self.dir = panel.dir / "voice"
        self.dir.mkdir(parents=True, exist_ok=True)
        pids = [x["persona_id"] for x in panel.cfg()["personas"]]
        self.voices = persona_voices(pids, installed_voices() if voices is None else voices,
                                     os.environ.get("STUDY_PANEL_VOICES", ""))
        self.speak_fn, self.transcribe_fn = speak_fn, transcribe_fn
        self.keep = (os.environ.get("STUDY_PANEL_KEEP_AUDIO") == "1") if keep_audio is None else keep_audio

    def _metrics(self, row: dict) -> None:
        with (self.dir / "voice_metrics.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")

    def say_current(self) -> dict:
        """Audio for the question now waiting (made once per turn)."""
        v = self.p.view()
        q = next((t for t in reversed(v["turns"]) if t["speaker"] != "learner"), None)
        if not q or not v["awaiting_answer"]:
            return {**v, "audio": None}
        out = self.dir / f"q{q['turn']}.aiff"
        secs = 0.0
        if not out.exists():
            secs = self.speak_fn(q["text"], self.voices.get(q["speaker"]), out)
        return {**v, "audio": str(out), "voice": self.voices.get(q["speaker"]), "speak_s": secs}

    def answer_audio(self, audio: Path, llm, now: dt.datetime | None = None) -> dict:
        """Transcribe a recorded answer, submit it like a typed one, and voice the next question."""
        v = self.p.view()
        if not v["awaiting_answer"]:
            return {**v, "error": "no question is waiting"}
        t0 = time.time()
        words = transcribe(Path(audio), self.transcribe_fn)
        t_stt = time.time() - t0
        text = clean_text(words)
        if len(text) < 2:
            return {**v, "error": "nothing was heard: check the microphone and try again", "transcribe_s": round(t_stt, 2)}
        if not self.keep:
            try:
                Path(audio).unlink()
            except OSError:
                pass
        self.p.answer(text, now)
        t1 = time.time()
        nxt = self.p.next_question(llm, now)
        t_model = time.time() - t1
        said = self.say_current() if nxt.get("awaiting_answer") else {**nxt, "audio": None, "speak_s": 0.0}
        m = speech_metrics(words)
        row = {"t": (now or cp.now_utc()).isoformat(timespec="seconds"), "turn": len(self.p.turns()) - 1,
               "transcribe_s": round(t_stt, 2), "model_s": round(t_model, 2), "speak_s": said.get("speak_s", 0.0),
               "processing_s": round(t_stt + t_model + (said.get("speak_s") or 0.0), 2), **{k: m[k] for k in
               ("words", "speaking_s", "wpm", "fillers", "fillers_per_100", "long_pauses")}}
        self._metrics(row)
        return {**said, "transcript": text, "speech": m, "timing": {k: row[k] for k in
                ("transcribe_s", "model_s", "speak_s", "processing_s")}}

    def log_latency(self, end_to_audio_ms: int) -> dict:
        """From the app: end of speech to the first sound of the reply."""
        row = {"t": cp.now_utc().isoformat(timespec="seconds"), "end_to_audio_ms": int(end_to_audio_ms)}
        self._metrics(row)
        return row

    def summary(self) -> dict:
        f = self.dir / "voice_metrics.jsonl"
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
        lat = sorted(r["end_to_audio_ms"] / 1000 for r in rows if "end_to_audio_ms" in r)
        proc = sorted(r["processing_s"] for r in rows if "processing_s" in r)
        wpm = [r["wpm"] for r in rows if r.get("wpm")]
        fil = [r["fillers_per_100"] for r in rows if r.get("fillers_per_100") is not None]
        q = lambda xs, p: round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 1) if xs else None
        return {"answers": len(proc), "end_to_audio_p50_s": q(lat, .5), "end_to_audio_p95_s": q(lat, .95),
                "processing_p50_s": q(proc, .5), "processing_p95_s": q(proc, .95),
                "wpm_median": round(statistics.median(wpm)) if wpm else None,
                "fillers_per_100_median": round(statistics.median(fil), 1) if fil else None,
                "voices": self.voices, "note": "pace and fillers are information only"}


def wav_from_text(text: str, voice: str | None, out: Path) -> Path:
    """A spoken stand-in answer for the bench: `say` to AIFF, then 16 kHz mono WAV."""
    aiff = out.with_suffix(".aiff")
    speak(text, voice, aiff)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(out)],
                   check=True, timeout=60)
    aiff.unlink()
    return out
