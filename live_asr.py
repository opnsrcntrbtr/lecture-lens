"""CT-2 event-driven second pass: each closed audio chunk is transcribed as soon as it is
complete, instead of on a timer.

- A chunk counts as closed when its file has not changed for `settle_s` and either the next
  chunk exists or its 30 s have passed.
- Each pass decodes the last `overlap_s` (6 s) of the previous chunk plus the chunk itself,
  with word-level timestamps. Words in the final `hold_s` (2 s) of a chunk are held back and
  committed by the next pass, which hears them again in its overlap, so a word split by a
  chunk edge is not lost and none is written twice.
- If the watcher falls more than `max_behind` chunks behind, it drops the overlap
  re-processing for those chunks (and commits held words directly). It never drops audio.
- A chunk is skipped only when it is digitally silent (peak at or below -90 dB). Whisper's
  guards stay on: no conditioning on earlier text (-mc 0), its no-speech and entropy
  thresholds, and transcript_clean's repeated-phrase filter.
- It never takes the model lock (whisper.cpp is separate from the model server), and runs
  at normal priority: background QoS doubled the time per chunk (M-3).

Rows go to whisper_segments.jsonl (same {t, text} as before, plus line_id, t1, pass,
chunk_id, engine, wt: each word's offset in seconds from t) and to transcript.jsonl. Line IDs count up and are never reused.
Per-chunk timings go to asr_metrics.jsonl: {chunk_id, chunk_end, done_at, lag_s,
transcribe_s, words, overlap}.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

CHUNK_S = 30.0
SILENT_DB = -90.0
FNAME_TS = re.compile(r"_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.(?:mp4|m4a|wav)$")
ENGINE = "whisper.cpp large-v3-turbo q8 (event)"


def file_start(name: str) -> dt.datetime | None:
    m = FNAME_TS.search(name)
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=dt.timezone.utc) if m else None


def lines_from_words(words: list[dict], gap_s: float = 0.8, max_words: int = 25) -> list[list[dict]]:
    """Group committed words into transcript lines: a pause, a sentence end or a length cap
    starts a new line."""
    out, cur = [], []
    for w in words:
        if cur and (w["t"] - cur[-1]["t1"] > gap_s or len(cur) >= max_words or re.search(r"[.?!]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


_n = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())
JITTER_S = 0.6


def commit(words: list[dict], chunk_start: float, chunk_end: float, hold_s: float, final: bool,
           tail: list[dict] | None = None) -> tuple[list, list]:
    """(committed, held) words of one pass, by absolute time in seconds.

    With an overlap, the new pass re-hears the end of the previous chunk. Its words are lined
    up with the last words already committed (`tail`) by their order, not their timestamps,
    which move a little between passes: everything after the last matched word is new. When
    nothing lines up (a pause, or a very different hearing), the time boundary decides:
    words from `hold_s` before the chunk start on."""
    hi = chunk_end if final else chunk_end - hold_s
    start_i = None
    if tail:
        import difflib
        head = [w for w in words if w["t"] < chunk_start + 3.0]          # the overlap and a little after
        a, b = [_n(x["w"]) for x in tail], [_n(x["w"]) for x in head]
        blocks = [m for m in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
                  if m.size >= 2 and m.a + m.size >= len(a) - 2]          # a run that reaches the tail's end
        if blocks:
            m = max(blocks, key=lambda m: m.b + m.size)
            start_i = m.b + m.size + (len(a) - (m.a + m.size))         # skip any tail words after the run
            start_i = min(start_i, len(head))
    if start_i is not None:
        mine = words[start_i:]
    else:
        mine = [w for w in words if w["t"] >= chunk_start - hold_s]
    return [w for w in mine if w["t"] < hi], [w for w in mine if w["t"] >= hi]


class EventASR:
    def __init__(self, data_dir: Path, device: str, start: dt.datetime, out_dir: Path, line_prefix: str,
                 transcribe=None, prompt_fn=lambda: "", settle_s: float = 2.0, overlap_s: float = 6.0,
                 hold_s: float = 2.0, max_behind: int = 3, clean=None, log=print, now=time.time, done: set | None = None):
        self.data, self.device, self.start = Path(data_dir), device, start
        self.out, self.prefix = Path(out_dir), line_prefix
        self.transcribe = transcribe or whisper_words
        self.prompt_fn, self.settle_s, self.overlap_s, self.hold_s = prompt_fn, settle_s, overlap_s, hold_s
        self.max_behind, self.clean, self.log, self.now = max_behind, clean, log, now
        self.done = done if done is not None else set()
        self.state_f = self.out / "asr_state.json"
        st = json.loads(self.state_f.read_text()) if self.state_f.exists() else {}
        self.next_line = st.get("next_line", 0)
        self.held = st.get("held", [])
        self.tail = st.get("tail", [])        # the last words committed, for the soft boundary
        self.last = st.get("last")            # previous chunk name, for the overlap
        self.done.update(st.get("done", []))

    def _save(self):
        self.state_f.write_text(json.dumps({"next_line": self.next_line, "held": self.held, "last": self.last,
                                            "tail": self.tail,
                                            "done": sorted(self.done)[-2000:]}))

    def closed(self) -> list[tuple[dt.datetime, str]]:
        names = sorted(n for n in os.listdir(self.data) if n.startswith(self.device + "_"))
        now = self.now()
        out = []
        for i, n in enumerate(names):
            t = file_start(n)
            if not t or t < self.start - dt.timedelta(seconds=CHUNK_S) or n in self.done:
                continue
            try:
                age = now - (self.data / n).stat().st_mtime
            except OSError:
                continue
            nxt = i + 1 < len(names)
            if age >= self.settle_s and (nxt or now >= t.timestamp() + CHUNK_S + self.settle_s):
                out.append((t, n))
        return out

    def poll(self, final: bool = False) -> int:
        """Transcribe every closed chunk, oldest first; returns how many were done."""
        todo = self.closed()
        behind = len(todo) > self.max_behind
        if behind:
            self.log(f"asr: {len(todo)} chunks waiting; skipping the overlap until caught up")
        for k, (t, n) in enumerate(todo):
            self.process(t, n, overlap=not behind, final=final and k == len(todo) - 1)
        return len(todo)

    def process(self, t: dt.datetime, name: str, overlap: bool = True, final: bool = False) -> list[dict]:
        t_start = time.time()
        path = self.data / name
        prev = self.data / self.last if self.last and overlap else None
        cs = t.timestamp()
        if peak_db(path) <= SILENT_DB:
            words, ov = [], 0.0
        else:
            words, ov = self.transcribe(path, prev if prev and prev.exists() else None, self.overlap_s, self.prompt_fn())
        dur = duration(path)
        absw = [{"w": w["w"], "t": cs - ov + w["t"], "t1": cs - ov + w.get("t1", w["t"])} for w in words]
        if ov:   # held words were heard again in the overlap: the new pass decides them
            self.held = []
        committed, held = commit(absw, cs, cs + dur, self.hold_s, final, self.tail if ov else None)
        committed = sorted(self.held + committed, key=lambda w: w["t"]) if not ov else committed
        self.held = held
        if committed:
            self.tail = (self.tail + committed)[-12:]
        rows = []
        for ln in lines_from_words(committed):
            text = " ".join(w["w"] for w in ln).strip()
            if not text:
                continue
            rows.append({"t": dt.datetime.fromtimestamp(ln[0]["t"], dt.timezone.utc).isoformat(),
                         "t1": dt.datetime.fromtimestamp(ln[-1]["t1"], dt.timezone.utc).isoformat(),
                         "text": text, "line_id": f"{self.prefix}/t/{self.next_line}", "pass": "final",
                         "chunk_id": name, "engine": ENGINE, "speaker": None,
                         "wt": [round(w["t"] - ln[0]["t"], 1) for w in ln]})   # each word's offset from t
            self.next_line += 1
        if self.clean and rows:
            kept = self.clean(rows)
            rows = [r for r in rows if r in kept]
        for f in ("whisper_segments.jsonl", "transcript.jsonl"):
            with (self.out / f).open("a") as fh:
                fh.writelines(json.dumps(r) + "\n" for r in rows)
        self.done.add(name)
        self.last = name
        self._save()
        done_at = self.now()
        with (self.out / "asr_metrics.jsonl").open("a") as fh:
            fh.write(json.dumps({"chunk_id": name, "chunk_end": cs + dur, "done_at": done_at,
                                 "lag_s": round(done_at - (cs + dur), 2), "transcribe_s": round(time.time() - t_start, 2),
                                 "words": len(committed), "overlap": bool(ov)}) + "\n")
        return rows

    def flush(self):
        """End of class: commit anything still held."""
        if self.held:
            text = " ".join(w["w"] for w in self.held)
            row = {"t": dt.datetime.fromtimestamp(self.held[0]["t"], dt.timezone.utc).isoformat(),
                   "t1": dt.datetime.fromtimestamp(self.held[-1]["t1"], dt.timezone.utc).isoformat(), "text": text,
                   "line_id": f"{self.prefix}/t/{self.next_line}", "pass": "final", "chunk_id": self.last,
                   "engine": ENGINE, "speaker": None, "wt": [round(w["t"] - self.held[0]["t"], 1) for w in self.held]}
            self.next_line += 1
            for f in ("whisper_segments.jsonl", "transcript.jsonl"):
                with (self.out / f).open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
            self.held = []
            self._save()


# ------------------------------------------------------------------ audio helpers
def duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip() or CHUNK_S)
    except (ValueError, subprocess.SubprocessError):
        return CHUNK_S


def peak_db(path: Path) -> float:
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
        return -999.0 if not m or m.group(1) == "-inf" else float(m.group(1))
    except subprocess.SubprocessError:
        return 0.0      # unknown: transcribe rather than skip


def whisper_model() -> str | None:
    import glob
    pat = os.path.expanduser(os.environ.get("LIVE_WHISPER_MODEL",
          "~/.cache/huggingface/hub/models--ggerganov--whisper.cpp/snapshots/*/ggml-large-v3-turbo-q8_0.bin"))
    hits = sorted(glob.glob(pat))
    return hits[-1] if hits else None


def whisper_words(path: Path, prev: Path | None, overlap_s: float, prompt: str = "", threads: int = 4) -> tuple[list[dict], float]:
    """Word-level transcription of [last overlap_s of prev] + path. Returns ([{w, t, t1}] with
    t in seconds from the start of the decoded audio, overlap seconds actually prepended)."""
    model = whisper_model()
    if not model:
        raise RuntimeError("whisper model missing")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wav = td / "a.wav"
        ov = 0.0
        if prev is not None:
            pd = duration(prev)
            ov = min(overlap_s, pd)
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{pd - ov:.3f}", "-i", str(prev), "-i", str(path),
                            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1", "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(wav)], check=True, timeout=60)
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                            str(wav)], check=True, timeout=60)
        cmd = ["whisper-cli", "-m", model, "-f", str(wav), "-l", "en", "-t", str(threads), "-oj", "-of", str(td / "o"),
               "-np", "-ml", "1", "-sow", "-mc", "0"]
        if prompt:
            cmd += ["--prompt", prompt[:400]]
        subprocess.run(cmd, check=True, timeout=300, capture_output=True)
        data = json.loads((td / "o.json").read_text())
    words = []
    for s in data.get("transcription", []):
        w = (s.get("text") or "").strip()
        if not w or (w.startswith("[") and w.endswith("]")):
            continue
        words.append({"w": w, "t": s["offsets"]["from"] / 1000.0, "t1": s["offsets"]["to"] / 1000.0})
    return words, ov
