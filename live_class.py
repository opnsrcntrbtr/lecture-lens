#!/usr/bin/env python3
"""live_class: follow a live class while screenpipe captures it, without touching capture.

One background loop that only reads what screenpipe has already saved:
  - whisper: re-transcribes finished 30 s system-audio files with whisper.cpp in
    ~5 min batches (with 30 s of overlap for context), at background priority,
    and skips a round if screenpipe's health is not ok.
  - slides: reads new on-screen text of the class window from screenpipe's API
    every minute and keeps one entry per distinct slide; Q&A and chat windows go
    to a separate raw file (names are removed later, in the build).
  - outline: every 10 minutes, three bullets from the last 10 minutes of
    transcript and slide titles, using the local model.
  - render: a live note in the Obsidian vault, rewritten every minute.

It never starts, stops or reconfigures screenpipe.

    live_class.py run --start 10:04 --until 13:15 --title "..." [--no-whisper] [--no-outline]
    live_class.py status
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import live_qa
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.path.expanduser(os.environ.get("SCREENPIPE_DATA", "~/.screenpipe/data")))
LECTURES = Path(os.path.expanduser(os.environ.get("STUDY_LECTURE_DIR", str(HERE / "lectures"))))
VAULT = Path(os.path.expanduser(os.environ.get("STUDY_VAULT", "~/vaults/ai-product-course")))
AUDIO_DEVICE = os.environ.get("LIVE_AUDIO_DEVICE", "System Audio (output)")
LIVE_APP = os.environ.get("LIVE_APP", "zoom").lower()
CLASS_WINDOWS = {"zoom webinar", "zoom meeting", "zoom", ""}
SIDE_WINDOWS = {"q&a", "webinar chat", "meeting chat", "chat"}
UI_NOISE = re.compile(r"(audio settings|video & effects|rotate 90|not seeing a|settings,|share screen|"
                      r"you are viewing|view options|leave webinar|raise hand|unmute|recording\.\.\.)", re.I)
LOCAL = dt.datetime.now().astimezone().tzinfo
FNAME_TS = re.compile(r"_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.mp4$")
CHUNK_S = 30.0
# Q&A docked inside the main meeting window: its panel header ("Switch") then author and time lines
QA_DOCKED = re.compile(r"(^|\n)Switch\n[^\n]+\n\d{1,2}:\d{2}\s?[AP]M\n", re.I)
# A shared slide is recognised by a marker line (e.g. "MODULE 1 • SESSION 1") and ends at its
# footer; both are per-course regexes set in .env. Text outside them is the meeting app's chrome.
# Set LIVE_SLIDE_MARKER to your deck's marker regex; without one, any window text that is
# not meeting-app chrome counts as slide text.
SLIDE_MARKER = re.compile(os.environ["LIVE_SLIDE_MARKER"], re.I) if os.environ.get("LIVE_SLIDE_MARKER") else None
SLIDE_FOOTER = re.compile(os.environ.get("LIVE_SLIDE_FOOTER", r"$^"))


# ------------------------------------------------------------------ pure helpers (unit-tested)
def file_start(name: str) -> dt.datetime | None:
    """screenpipe names audio files <device>_<UTC yyyy-mm-dd_HH-MM-SS>.mp4"""
    m = FNAME_TS.search(name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=dt.timezone.utc)


def finished_files(names: list[str], device: str, start: dt.datetime, now: dt.datetime,
                   grace_s: float = 5.0) -> list[tuple[dt.datetime, str]]:
    """Audio files of one device that began at or after `start` and are complete by `now`."""
    out = []
    for n in names:
        if not n.startswith(device + "_"):
            continue
        t = file_start(n)
        if t and t >= start - dt.timedelta(seconds=CHUNK_S) and t + dt.timedelta(seconds=CHUNK_S + grace_s) <= now:
            out.append((t, n))
    return sorted(out)


def batches(files: list[tuple[dt.datetime, str]], size: int = 10, gap_s: float = 45.0) -> list[list[tuple[dt.datetime, str]]]:
    """Consecutive runs of at most `size` files; a time gap starts a new batch."""
    out, cur = [], []
    for f in files:
        if cur and ((f[0] - cur[-1][0]).total_seconds() > gap_s or len(cur) >= size):
            out.append(cur)
            cur = []
        cur.append(f)
    if cur:
        out.append(cur)
    return out


def place_segments(segs: list[dict], durations: list[float], starts: list[dt.datetime], keep_from: dt.datetime) -> list[dict]:
    """Map whisper offsets (ms into the concatenated audio) to clock time using each file's
    own start, and drop segments that begin before `keep_from` (the overlap)."""
    bounds, acc = [], 0.0
    for d in durations:
        bounds.append(acc)
        acc += d
    out = []
    for s in segs:
        off = s["from_ms"] / 1000.0
        i = max(k for k, b in enumerate(bounds) if b <= off) if bounds else 0
        t = starts[i] + dt.timedelta(seconds=off - bounds[i])
        text = s["text"].strip()
        if t < keep_from - dt.timedelta(seconds=0.5) or not text or text.startswith("[") and text.endswith("]"):
            continue
        out.append({"t": t.isoformat(), "text": text})
    return out


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def same_slide(a: set, b: set, threshold: float = 0.5) -> bool:
    """Similar words, or one is mostly contained in the other (a slide partly covered
    by another window, or a build revealing more bullets)."""
    contain = len(a & b) / min(len(a), len(b)) if a and b else 0.0
    return jaccard(a, b) >= threshold or contain >= 0.8


def is_noise(text: str) -> bool:
    """Zoom's own settings panels and toolbars, not class content."""
    if len(tokens(text)) < 6:
        return True
    hits = len(UI_NOISE.findall(text))
    return hits >= 3 or hits * 40 > len(text)


def slide_body(text: str) -> str | None:
    """The slide's own text: after the marker, before the footer. None if no slide is shown."""
    if SLIDE_MARKER is None:
        if is_noise(text):
            return None
        body = text
    else:
        m = SLIDE_MARKER.search(text)
        if not m:
            return None
        body = text[m.end():]
    f = SLIDE_FOOTER.search(body)
    if f and f.start() > 0:
        body = body[:f.start()]
    return body.strip(" •|-–\n") or None


def slide_title(body: str) -> str:
    """OCR joins a slide's lines without spaces ("agendaUNDERSTAND"), so split at
    lower-to-upper joins and newlines; the first piece that reads like a heading is the title."""
    for piece in re.split(r"\n|(?<=[a-z\)'’])(?=[A-Z])", body):
        piece = piece.strip(" •|-–")
        if 6 <= len(piece) <= 90 and len(piece.split()) >= 2 and not UI_NOISE.search(piece):
            return piece
    return body.strip()[:80]


def new_slides(rows: list[dict], last_tokens: set | None, threshold: float = 0.5) -> list[dict]:
    """Keep a row when its words differ enough from the slide kept before it."""
    out = []
    for r in rows:
        body = slide_body(r["text"])
        if not body or len(tokens(body)) < 3:
            continue
        tk = tokens(body)
        if last_tokens is not None and same_slide(tk, last_tokens, threshold):
            continue
        out.append({"t": r["t"], "title": slide_title(body), "text": body[:1500]})
        last_tokens = tk
    return out


Q_TYPES = ("clarify", "challenge", "practical", "capstone")


def parse_questions(raw: str, evidence_ids: set[str], asked: list[str] | None = None) -> list[dict]:
    """Questions from the model's JSON. Keeps only well-formed items, drops source ids that
    are not in the verified evidence bank (the model may not invent sources), and drops
    near-repeats of questions already generated or asked by attendees."""
    try:
        m = re.search(r"\[.*\]", raw, re.S)
        items = json.loads(m.group(0)) if m else []
    except (json.JSONDecodeError, AttributeError):
        items = []
    seen = [tokens(q) for q in (asked or [])]
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not str(it.get("q", "")).strip() or not str(it.get("answer", "")).strip():
            continue
        tk = tokens(it["q"])
        if any(jaccard(tk, s) >= 0.5 for s in seen):
            continue
        # bracketed numbers point at nothing the reader can see; a source counts only if the
        # answer names it (S#) and it is in the verified bank
        it["answer"] = re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", str(it["answer"]))
        named = set(re.findall(r"\bS\d+\b", it["answer"]))
        src = sorted(named & evidence_ids, key=lambda x: int(x[1:]))
        kind = it.get("type") if it.get("type") in Q_TYPES else "clarify"
        out.append({"type": kind, "q": it["q"].strip(), "answer": it["answer"].strip(), "sources": src})
        seen.append(tk)
    return out


def render(title: str, start: dt.datetime, slides: list[dict], outline: list[dict], status: str, words: int,
           questions: list[dict] | None = None, evidence: dict | None = None) -> str:
    lt = lambda iso: dt.datetime.fromisoformat(iso).astimezone(LOCAL).strftime("%H:%M")
    lines = [f"# LIVE — {title}", "",
             f"_Updated {dt.datetime.now(LOCAL).strftime('%H:%M:%S')} · capture: {status} · "
             f"re-transcribed words: {words} · slides: {len(slides)}. Replaced by the full lecture note after class._", ""]
    if questions:
        lines += ["## Questions to ask (newest first; draft answers, check before relying)", ""]
        for q in list(reversed(questions))[:9]:
            refs = "; ".join(f"[{i}] {evidence[i]['title']}" for i in q["sources"] if evidence and i in evidence)
            lines += [f"- **{lt(q['t'])} · {q['type']}** — {q['q']}",
                      f"  - _Draft:_ {q['answer']}" + (f" ({refs})" if refs else " (lecture only)")]
        lines += [""]
    lines += ["## Running outline", ""]
    for o in outline:
        lines += [f"**{lt(o['from'])}–{lt(o['to'])}**", o["bullets"], ""]
    if not outline:
        lines += ["_First outline after 10 minutes of transcript._", ""]
    lines += ["## Slides", ""]
    prev = None
    for s in slides:
        if s["title"] != prev:  # a slide revisited or rebuilt shows once
            lines += [f"- **{lt(s['t'])}** {s['title']}"]
        prev = s["title"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ IO
def utc_iso(ts: str) -> str:
    """screenpipe timestamps come with Z or a local offset; store all as UTC ISO."""
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat()


def health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:3030/health", timeout=5) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": f"unreachable ({e.__class__.__name__})"}


def healthy(h: dict) -> bool:
    return h.get("frame_status") == "ok" and h.get("audio_status") == "ok"


def whisper_model() -> str | None:
    pat = os.path.expanduser(os.environ.get("LIVE_WHISPER_MODEL",
          "~/.cache/huggingface/hub/models--ggerganov--whisper.cpp/snapshots/*/ggml-large-v3-turbo-q8_0.bin"))
    hits = sorted(glob.glob(pat))
    return hits[-1] if hits else None


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=30)
    return float(r.stdout.strip() or CHUNK_S)


def transcribe(batch: list[tuple[dt.datetime, str]], keep_from: dt.datetime, prompt: str, threads: int) -> list[dict]:
    model = whisper_model()
    if not model or not shutil.which("whisper-cli"):
        raise RuntimeError("whisper-cli or model missing")
    with tempfile.TemporaryDirectory() as td:
        lst = Path(td) / "list.txt"
        lst.write_text("".join(f"file '{(DATA / n).as_posix()}'\n" for _, n in batch))
        wav = Path(td) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True, timeout=120)
        durs = [duration(DATA / n) for _, n in batch]
        base = Path(td) / "out"
        cmd = ["taskpolicy", "-b", "nice", "-n", "19", "whisper-cli", "-m", model, "-f", str(wav), "-l", "en",
               "-t", str(threads), "-oj", "-of", str(base), "-np"]
        if prompt:
            cmd += ["--prompt", prompt]
        subprocess.run(cmd, check=True, timeout=900, capture_output=True)
        data = json.loads((base.with_suffix(".json")).read_text())
    segs = [{"from_ms": s["offsets"]["from"], "text": s["text"]} for s in data.get("transcription", [])]
    return place_segments(segs, durs, [t for t, _ in batch], keep_from)


def screen_rows(since: dt.datetime) -> list[dict]:
    import study
    rows, off = [], 0
    while True:
        b = study.search(start=since, content_type="ocr", limit=100, offset=off)
        rows += b
        off += 100
        if len(b) < 100 or off > 2000:
            break
    out = []
    for x in rows:
        c = x["content"]
        if LIVE_APP not in (c.get("app_name") or "").lower():
            continue
        out.append({"t": utc_iso(c.get("timestamp")), "window": (c.get("window_name") or "").strip().lower(), "text": c.get("text") or ""})
    out.sort(key=lambda r: r["t"])
    return out


def api_transcript(since: dt.datetime, until: dt.datetime) -> str:
    import study
    rows = study.search(start=since, end=until, content_type="audio", limit=100)
    rows = [r["content"] for r in rows if "microphone" not in (r["content"].get("device_name") or "").lower()]
    return " ".join((r.get("transcription") or "").strip() for r in sorted(rows, key=lambda r: r["timestamp"]))


def load_side(path: Path, frm: dt.datetime, to: dt.datetime) -> str:
    """Attendee Q&A text seen in this window, so generated questions do not repeat them."""
    if not path.exists():
        return ""
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return " / ".join(r["text"][:600] for r in rows if frm.isoformat() <= r["t"] < to.isoformat())[-3000:]


def make_questions(a, frm, to, text, titles, outline, prior, side_text, ev_f, q_f):
    import study
    bank = json.loads(ev_f.read_text()) if ev_f.exists() else []
    bank_txt = "\n".join(f"{e['id']}: {e['title']} ({e.get('date', '')}): {e['fact']}" for e in bank)
    earlier = re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", " | ".join(o["bullets"].replace("\n", " ") for o in outline[-4:]))[:3000]
    idea = os.environ.get("LIVE_CAPSTONE_IDEA", "").strip()
    raw = study.llm(study.SYS_TUTOR,
        f"LIVE CLASS: {a.title}\nSLIDES IN THIS WINDOW (quoted): {titles}\nEARLIER OUTLINE (quoted): {earlier}\n\n"
        f"LAST 10 MINUTES OF TRANSCRIPT (quoted; speech-to-text, may have errors):\n{text[:8000]}\n\n"
        f"ATTENDEE Q&A ALREADY VISIBLE (quoted; do not repeat these):\n{side_text or '(none)'}\n\n"
        f"VERIFIED EVIDENCE BANK (the only outside sources you may cite, by id):\n{bank_txt or '(empty)'}\n\n"
        + (f"LEARNER'S CAPSTONE IDEA: {idea}\n\n" if idea else "")
        + "Write 3 questions the learner could type into the Zoom Q&A right now, about what was just taught. Mix types: "
        "clarify (how a framework works, its criteria or cut-offs), challenge (contrast the lecture with an evidence-bank fact), "
        "practical (tools, costs, build-buy-prompt, evals)" + (", capstone (apply the framework to the learner's idea)" if idea else "") + ".\n"
        "Rules: each question under 40 words and specific to this lecture. Each draft answer under 80 words: first what the lecture said, "
        "then outside evidence written as (S#) if one truly applies; never use bracketed numbers like [3]. Use only numbers that appear in the transcript or the evidence bank. If unsure, say so.\n"
        'Return ONLY a JSON array: [{"type": "clarify", "q": "...", "answer": "...", "sources": ["S3"]}]',
        max_tokens=900, temperature=0.3)
    prior_q = [q["q"] for q in prior] + [l for l in side_text.split(" / ") if "?" in l]
    qs = parse_questions(raw, {e["id"] for e in bank}, prior_q)
    with q_f.open("a") as f:
        f.writelines(json.dumps({"t": to.isoformat(), **q}) + "\n" for q in qs)
    return qs


def qa_time(hhmm: str, now: dt.datetime) -> dt.datetime:
    """The panel's '11:21 AM' as an aware UTC datetime on today's date."""
    t = dt.datetime.strptime(hhmm.replace(" ", ""), "%I:%M%p").time()
    return dt.datetime.combine(now.astimezone(LOCAL).date(), t, LOCAL).astimezone(dt.timezone.utc)


def ask_now_round(seg_f, q_f, fu_f, qa_f, out_f, now):
    """Rank every generated question and follow-up against the last 5 minutes of lecture."""
    load = lambda f: [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    since = (now - dt.timedelta(minutes=5)).isoformat()
    recent = " ".join(s["text"] for s in load(seg_f) if s["t"] >= since)
    threads = json.loads(qa_f.read_text()) if qa_f.exists() else []
    asked = [t["q"] for t in threads]
    cands = [dict(c, origin="question") for c in load(q_f)] + [dict(c, origin="follow-up") for c in load(fu_f)]
    top = live_qa.rank_ask_now(cands, recent, asked, now.isoformat())
    out_f.write_text(json.dumps({"t": now.isoformat(), "top": top}, indent=1))
    return top


def qa_round(a, side_f, seg_f, slide_f, ev_f, qa_f, fu_f, qa_done, ok, now, log):
    """Rebuild Q&A threads from every panel snapshot so far; draft follow-ups for up to
    `a.qa_per_round` answered threads, the learner's own first, then the newest."""
    if not side_f.exists():
        return qa_done
    snaps = [json.loads(l)["text"] for l in side_f.read_text().splitlines() if l.strip() and '"q&a"' in l]
    staff = {x.strip() for x in os.environ.get("LIVE_QA_STAFF", "").split(",") if x.strip()}
    threads = live_qa.build_threads(snaps, staff)
    qa_f.write_text(json.dumps(threads, indent=1))
    todo = [t for t in threads if live_qa.needs_followup(t, qa_done)]
    todo.sort(key=lambda t: (t["by"] != "You", -threads.index(t)))
    if not todo or not ok:
        return qa_done
    import study
    bank = json.loads(ev_f.read_text()) if ev_f.exists() else []
    bank_txt = "\n".join(f"{e['id']}: {e['title']} ({e.get('date', '')}): {e['fact']}" for e in bank)
    segs = [json.loads(l) for l in seg_f.read_text().splitlines() if l.strip()] if seg_f.exists() else []
    since = (now - dt.timedelta(minutes=10)).isoformat()
    transcript = " ".join(s["text"] for s in segs if s["t"] >= since)
    titles = [json.loads(l)["title"] for l in slide_f.read_text().splitlines()[-4:]] if slide_f.exists() else []
    prior = [json.loads(l)["q"] for l in fu_f.read_text().splitlines() if l.strip()] if fu_f.exists() else []
    bank_by_id = {e["id"]: e for e in bank}
    for t in todo[:a.qa_per_round]:
        at = qa_time(t["time"], now)  # what the professor said around the time of the question
        ctx = " ".join(s["text"] for s in segs if (at - dt.timedelta(minutes=8)).isoformat() <= s["t"] <= (at + dt.timedelta(minutes=4)).isoformat()) or transcript
        raw = study.llm(study.SYS_TUTOR, live_qa.followup_prompt(a.title, t, ctx, titles, bank_txt, avoid=prior[-12:]),
                        max_tokens=700, temperature=0.3)
        hide = live_qa.redactor(snaps, staff or {a["by"] for th in threads for a in th["answers"]})
        keep = 1 if live_qa.thread_kind(t) == "admin" else 2
        fus = []
        for x in parse_questions(raw, {e["id"] for e in bank}, prior + [t["q"]])[:keep]:
            ans = live_qa.clean_answer(x["answer"])
            fus.append(dict(x, q=hide(x["q"]), answer=hide(ans),
                            sources=live_qa.relevant_sources(x["sources"], x["q"] + " " + ans, bank_by_id)))
        with fu_f.open("a") as f:
            f.writelines(json.dumps({"thread": t["id"], "t": now.isoformat(), **x}) + "\n" for x in fus)
        prior += [x["q"] for x in fus]
        qa_done[t["id"]] = len(t["answers"])
        log(f"qa follow-ups {len(fus)} for {t['by']} {t['time']}")
    return qa_done


# ------------------------------------------------------------------ loop
def run(a):
    today = dt.datetime.now(LOCAL).date()
    hh, mm = map(int, a.start.split(":"))
    start = dt.datetime.combine(today, dt.time(hh, mm), LOCAL).astimezone(dt.timezone.utc)
    uh, um = map(int, a.until.split(":"))
    until = dt.datetime.combine(today, dt.time(uh, um), LOCAL).astimezone(dt.timezone.utc)
    out = LECTURES / "live" / today.strftime("%Y%m%d")
    out.mkdir(parents=True, exist_ok=True)
    seg_f, slide_f, side_f, outl_f, state_f, q_f = (out / n for n in
        ("whisper_segments.jsonl", "slides.jsonl", "side_windows_raw.jsonl", "outline.jsonl", "state.json", "questions.jsonl"))
    ev_f = out / "evidence.json"  # verified sources, written by a person or an agent with web access
    qa_f, fu_f = out / "qa_threads.json", out / "qa_followups.jsonl"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    done = set(state.get("done_files", []))
    last_slide_t = state.get("last_slide_t") or start.isoformat()
    last_tokens = set(state.get("last_tokens", [])) or None
    last_outline = dt.datetime.fromisoformat(state.get("last_outline") or start.isoformat())
    qa_done = dict(state.get("qa_done", {}))
    last_ask = start
    safe = re.sub(r'[\\/:*?"<>|]', " ", a.title)
    note = VAULT / "Lectures" / f"{today.strftime('%Y%m%d')} LIVE - {safe}.md"
    load = lambda f: [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    next_whisper = time.time()
    log = lambda *m: print(dt.datetime.now(LOCAL).strftime("%H:%M:%S"), *m, flush=True)
    log("start", start.isoformat(), "until", until.isoformat(), "out", out)

    while dt.datetime.now(dt.timezone.utc) < until:
        h = health()
        ok = healthy(h)
        status = "ok" if ok else f"CHECK ({h.get('frame_status')}/{h.get('audio_status')})"
        now = dt.datetime.now(dt.timezone.utc)

        # slides and side windows
        try:
            rows = [r for r in screen_rows(dt.datetime.fromisoformat(last_slide_t)) if r["t"] > last_slide_t]
            side = [dict(r, window="q&a") if r["window"] not in SIDE_WINDOWS else r
                    for r in rows if r["window"] in SIDE_WINDOWS or QA_DOCKED.search(r["text"])]
            cls = [r for r in rows if r["window"] in CLASS_WINDOWS and not is_noise(r["text"])]
            if side:
                with side_f.open("a") as f:
                    f.writelines(json.dumps(r) + "\n" for r in side)
            fresh = new_slides(cls, last_tokens)
            if fresh:
                with slide_f.open("a") as f:
                    f.writelines(json.dumps(s) + "\n" for s in fresh)
                last_tokens = tokens(fresh[-1]["text"])  # text is the slide body
                log("slides +", len(fresh), "|", fresh[-1]["title"][:60])
            if rows:
                last_slide_t = rows[-1]["t"]
        except Exception as e:
            log("slides error", e)

        # whisper, at most once per interval, only when capture is healthy
        if not a.no_whisper and time.time() >= next_whisper:
            next_whisper = time.time() + a.whisper_every
            if ok:
                try:
                    names = os.listdir(DATA)
                    files = [f for f in finished_files(names, AUDIO_DEVICE, start, now) if f[1] not in done]
                    titles = [s["title"] for s in load(slide_f)[-3:]]
                    prompt = (a.prompt + " " + " ".join(titles)).strip()[:600]
                    for b in batches(files):
                        prev = [f for f in finished_files(names, AUDIO_DEVICE, start, now)
                                if f[0] < b[0][0] and (b[0][0] - f[0]).total_seconds() <= CHUNK_S + 15][-1:]
                        t0 = time.time()
                        segs = transcribe(prev + b, b[0][0], prompt, a.threads)
                        with seg_f.open("a") as f:
                            f.writelines(json.dumps(s) + "\n" for s in segs)
                        done.update(n for _, n in b)
                        log(f"whisper {len(b)} files -> {len(segs)} segments in {time.time() - t0:.0f}s")
                        if not healthy(health()):
                            log("capture health dipped; pausing whisper this round")
                            break
                except Exception as e:
                    log("whisper error", e)
            else:
                log("capture not healthy; whisper skipped", status)

        # Zoom Q&A: threads every round, follow-ups for newly answered threads
        if not a.no_qa:
            try:
                qa_done = qa_round(a, side_f, seg_f, slide_f, ev_f, qa_f, fu_f, qa_done, ok, now, log)
            except Exception as e:
                log("qa error", e)
        if (now - last_ask).total_seconds() >= 300:
            try:
                top = ask_now_round(seg_f, q_f, fu_f, qa_f, out / "ask_now.json", now)
                last_ask = now
                log("ask-now", len(top))
            except Exception as e:
                log("ask-now error", e)

        # outline every 10 minutes
        if not a.no_outline and (now - last_outline).total_seconds() >= 600:
            frm, to = last_outline, last_outline + dt.timedelta(minutes=10)
            segs = [s for s in load(seg_f) if frm.isoformat() <= s["t"] < to.isoformat()]
            text = " ".join(s["text"] for s in segs) or api_transcript(frm, to)
            titles = [s["title"] for s in load(slide_f) if frm.isoformat() <= s["t"] < to.isoformat()]
            if len(text.split()) > 40 and ok:
                try:
                    import study
                    bullets = study.llm(study.SYS_TUTOR,
                        f"SLIDE TITLES (quoted): {titles}\n\nTRANSCRIPT, 10 minutes of a live class (quoted; speech-to-text, may have errors):\n{text[:9000]}\n\n"
                        "Write exactly 3 short bullets ('- ') on what was taught: concepts, frameworks, numbers. Only what the material says. No citations, no brackets, no preamble.",
                        max_tokens=260, temperature=0.2)
                    with outl_f.open("a") as f:
                        f.write(json.dumps({"from": frm.isoformat(), "to": to.isoformat(), "bullets": bullets.strip()}) + "\n")
                    log("outline", frm.astimezone(LOCAL).strftime("%H:%M"))
                except Exception as e:
                    log("outline error", e)
                if not a.no_questions:
                    try:
                        make_questions(a, frm, to, text, titles, load(outl_f), load(q_f), load_side(side_f, frm, to), ev_f, q_f)
                        log("questions", frm.astimezone(LOCAL).strftime("%H:%M"))
                    except Exception as e:
                        log("questions error", e)
            last_outline = to if (now - to).total_seconds() < 1200 else now - dt.timedelta(minutes=10)

        words = sum(len(s["text"].split()) for s in load(seg_f))
        try:
            note.parent.mkdir(parents=True, exist_ok=True)
            ev = {e["id"]: e for e in json.loads(ev_f.read_text())} if ev_f.exists() else {}
            md = render(a.title, start, load(slide_f), load(outl_f), status, words, load(q_f), ev)
            an_f = out / "ask_now.json"
            if an_f.exists():
                top = json.loads(an_f.read_text()).get("top", [])
                if top:
                    an = ["## Ask now (top 3, refreshed every 5 min)", ""]
                    for i, c in enumerate(top, 1):
                        refs = "; ".join(ev[x]["title"] for x in c.get("sources", []) if x in ev)
                        an += [f"{i}. **{c['q']}**", f"   - _Draft:_ {c['answer']}" + (f" ({refs})" if refs else "")]
                    j = md.find("\n## ")
                    md = md[:j + 1] + "\n".join(an) + "\n\n" + md[j + 1:] if j >= 0 else md
            if qa_f.exists():
                fus: dict = {}
                for f in load(fu_f):
                    fus.setdefault(f["thread"], []).append(f)
                threads = json.loads(qa_f.read_text())
                fus = live_qa.curate_followups(threads, fus)
                (out / "qa_view.json").write_text(json.dumps(  # what the app's Live tab shows
                    {"threads": [t["id"] for t in threads if live_qa.thread_kind(t) != "trivial"], "followups": fus}))
                qa_md = "\n".join(live_qa.render_section(threads, fus, ev))
                if qa_md:
                    i = md.find("\n## ")
                    md = md[:i + 1] + qa_md + "\n" + md[i + 1:] if i >= 0 else md + qa_md
            note.write_text(md)
        except Exception as e:
            log("render error", e)
        state_f.write_text(json.dumps({"done_files": sorted(done), "last_slide_t": last_slide_t, "qa_done": qa_done,
                                       "last_tokens": sorted(last_tokens or []), "last_outline": last_outline.isoformat(),
                                       "status": status, "words": words, "updated": now.isoformat()}))
        time.sleep(a.every)
    log("until reached; stopping")


def status(a):
    today = dt.datetime.now(LOCAL).strftime("%Y%m%d")
    f = LECTURES / "live" / today / "state.json"
    s = json.loads(f.read_text()) if f.exists() else {}
    print(json.dumps({k: s.get(k) for k in ("status", "words", "updated", "last_outline")} | {"files": len(s.get("done_files", []))}))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--start", required=True, help="HH:MM local, when the class began")
    r.add_argument("--until", required=True, help="HH:MM local, when to stop following")
    r.add_argument("--title", required=True)
    r.add_argument("--every", type=int, default=60, help="seconds between rounds")
    r.add_argument("--whisper-every", type=int, default=300)
    r.add_argument("--threads", type=int, default=4)
    r.add_argument("--prompt", default="", help="vocabulary to bias whisper (course terms, names)")
    r.add_argument("--no-whisper", action="store_true")
    r.add_argument("--no-outline", action="store_true")
    r.add_argument("--no-questions", action="store_true", help="skip the 10-minute questions to ask")
    r.add_argument("--no-qa", action="store_true", help="skip Zoom Q&A threads and follow-ups")
    r.add_argument("--qa-per-round", type=int, default=2, help="follow-ups generated per round (model calls)")
    r.set_defaults(fn=run)
    sub.add_parser("status").set_defaults(fn=status)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
