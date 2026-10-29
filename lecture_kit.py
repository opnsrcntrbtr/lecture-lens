#!/usr/bin/env python3
"""lecture_kit — turn captured lectures (course-portal videos and live classes) into study artefacts.

Reads what screenpipe already captured on this Mac (its SQLite DB, read-only)
and produces, per lecture: a timecoded transcript, slide keyframes described by
a local vision model, extracted on-screen text, study notes, Anki cards and an
Obsidian note. Nothing leaves the machine.

    lecture list [--since 7d]        lecture sessions detected in the captures
    lecture build <id|last> [...]    build artefacts for one session
    lecture watch                    build each session a few minutes after it ends
    lecture doctor                   check DB, keyframes, vision model, vault

Design notes
  * Deterministic work (session segmentation, keyframe selection, file layout)
    is plain Python: same input, same output, no model involved.
  * The model is used only where judgement is needed (describing a slide,
    writing notes, making cards), one bounded call at a time — no agent loop.
  * Every artefact records where it came from, so a claim in notes.md can be
    traced to a timestamp in the transcript or a keyframe image.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, re, shutil, sqlite3, subprocess, sys, urllib.error, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import study  # shared config + llm() + oMLX plumbing
sys.path.insert(0, str(HERE / "eval"))
import vlm_client as vc  # the evaluated vision client
from player_crop import player_box  # crops page chrome away (eval: 9/20 -> 0/20 leaking answers)

DB = Path(os.path.expanduser(os.environ.get("SCREENPIPE_DB", "~/.screenpipe/db.sqlite")))
VAULT = Path(os.path.expanduser(os.environ.get("STUDY_VAULT", "~/vaults/ai-product-course")))
VAULT_SUB = os.environ.get("STUDY_VAULT_SUBDIR", "Lectures")
LECTURES = Path(os.path.expanduser(os.environ.get("STUDY_LECTURE_DIR", str(HERE / "lectures"))))
VL_MODEL = os.environ.get("OMLX_VL_MODEL", "")
SITE = os.environ.get("STUDY_LECTURE_SITE", "learn.example.edu")   # your course portal's host
# The portal's own name as it appears in window titles (stripped from titles; a bare match is a generic title).
PORTAL = os.environ.get("STUDY_PORTAL_NAME", "").strip()
COURSE = os.environ.get("STUDY_COURSE_NAME", "My course")
# Native apps whose windows are live classes (Zoom webinars): no browser URL to match.
LIVE_APPS = [a.strip() for a in os.environ.get("STUDY_LIVE_APPS", "zoom").split(",") if a.strip()]
# A gap longer than this ends a lecture session.
GAP_MIN = int(os.environ.get("STUDY_SESSION_GAP_MIN", "10"))
MIN_SESSION_MIN = float(os.environ.get("STUDY_MIN_SESSION_MIN", "3"))

# ------------------------------------------------------------------ db access
class _Row(dict):
    """dict row that also answers row[0] like sqlite3.Row."""
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else super().__getitem__(k)


class _Rows(list):
    def fetchall(self): return list(self)
    def fetchone(self): return self[0] if self else None


class HttpDB:
    """Read-only access through screenpipe's /raw_sql.

    On macOS a running screenpipe holds a process-exclusive lock on db.sqlite
    (unix-excl VFS, crates/screenpipe-db/src/db/setup.rs), so while capture runs
    no other process can open the file — not even read-only. Same SQL, same
    rows, via the local API instead. /raw_sql accepts only SELECT/WITH and
    caps LIMIT at 10,000, so row queries without a LIMIT are paged."""
    PAGE = 10000

    @staticmethod
    def _inline(sql, params):
        parts = sql.split("?")
        if len(parts) - 1 != len(params):
            raise ValueError("parameter count mismatch")
        def lit(v):
            if v is None: return "NULL"
            if isinstance(v, (int, float)): return repr(v)
            return "'" + str(v).replace("'", "''") + "'"
        return "".join(a + (lit(params[i]) if i < len(params) else "") for i, a in enumerate(parts))

    def _post(self, q):
        req = urllib.request.Request(f"{study.SP}/raw_sql", json.dumps({"query": q}).encode(),
                                     {"Content-Type": "application/json",
                                      **({"Authorization": f"Bearer {study.sp_key()}"} if study.sp_key() else {})})
        try:
            with study._OPENER.open(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise sqlite3.OperationalError(f"screenpipe /raw_sql {e.code}: {e.read()[:300]!r}") from None

    def execute(self, sql, params=()):
        q = " ".join(self._inline(sql, tuple(params)).split())
        up = q.upper()
        aggregate = " GROUP BY " not in up and re.search(r"\b(COUNT|MAX|MIN|SUM|AVG)\(", up)
        if " LIMIT " in up or aggregate:
            return _Rows(_Row(r) for r in self._post(q))
        out, off = _Rows(), 0
        while True:
            page = self._post(f"{q} LIMIT {self.PAGE} OFFSET {off}")
            out.extend(_Row(r) for r in page)
            if len(page) < self.PAGE: return out
            off += self.PAGE


def db():
    if not DB.exists():
        sys.exit(f"screenpipe database not found at {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)  # read-only: never writes to captures
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM frames LIMIT 1").fetchall()
    except sqlite3.OperationalError as e:
        if "locked" not in str(e): raise
        con.close()
        return HttpDB()  # capture is running and owns the file
    return con

def parse_since(since):
    m = re.fullmatch(r"(\d+)([hdw])", since or "7d")
    if not m: sys.exit("--since looks like 12h, 7d or 2w")
    n, u = int(m[1]), m[2]
    delta = {"h": dt.timedelta(hours=n), "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n)}[u]
    return dt.datetime.now(dt.timezone.utc) - delta

def _ts(row_value):
    try:
        return dt.datetime.fromisoformat(str(row_value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None

def frames_since(con, start):
    rows = con.execute("""
        SELECT id, timestamp, window_name, browser_url, capture_trigger, snapshot_path,
               COALESCE(full_text, accessibility_text, '') AS text, content_hash, video_chunk_id, offset_index
        FROM frames
        WHERE datetime(timestamp) >= datetime(?) AND (browser_url LIKE ? OR window_name LIKE ?{live})
        ORDER BY timestamp
    """.format(live="".join(" OR lower(app_name) LIKE ?" for _ in LIVE_APPS)),
        (start.strftime("%Y-%m-%dT%H:%M:%SZ"), f"%{SITE}%", f"%{SITE.split('.')[0]}%",
         *[f"%{a.lower()}%" for a in LIVE_APPS])).fetchall()
    out = []
    for r in rows:
        t = _ts(r["timestamp"])
        if t: out.append({**dict(r), "ts": t})
    out += markers_since(start)
    out.sort(key=lambda f: f["ts"])
    return out

MARKERS = Path(os.path.expanduser(os.environ.get("STUDY_MARKERS", "~/.screenpipe/study-markers.jsonl")))

def markers_since(start):
    """Frame-shaped stand-ins for time spent on DRM video pages.

    While a DRM player page is focused screenpipe captures no frames (they would
    blank the video, ADR-004), so sp-autoswitch logs the page's URL and title
    every poll. They carry no image: sessions and titles come from them, the
    transcript from audio, keyframes simply skip them."""
    if not MARKERS.exists():
        return []
    out = []
    for i, line in enumerate(MARKERS.read_text(errors="replace").splitlines()):
        try:
            m = json.loads(line)
            t = _ts(m["ts"])
        except Exception:
            continue
        if not t or t < start or SITE not in (m.get("url") or ""):
            continue
        out.append({"id": f"m{i}", "timestamp": m["ts"], "window_name": m.get("title") or "",
                    "browser_url": m.get("url"), "capture_trigger": "drm_marker", "snapshot_path": None,
                    "text": "", "content_hash": None, "video_chunk_id": None, "offset_index": None, "ts": t})
    return out

def audio_between(con, start, end, device_filter=None):
    """Transcript lines for a window: one line per ~30 s audio chunk, in spoken order.

    screenpipe stores several short segments per chunk, all stamped with the
    chunk's time, so order by (chunk time, offset in chunk) and join them.
    device_filter: a device-name substring, or "auto"/None = the output (non-
    microphone) device that transcribed the most text in this window. The same
    audio usually reaches two taps ("System Audio" and "App Tap"); taking one
    avoids duplicated lines, and the fuller one avoids a near-empty transcript.
    """
    rows = con.execute("""
        SELECT id, timestamp, start_time, transcription, device, is_input_device
        FROM audio_transcriptions
        WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) <= datetime(?) AND TRIM(COALESCE(transcription,'')) <> ''
        ORDER BY timestamp, start_time, id
    """, (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchall()
    rows = [dict(r) for r in rows]
    want = (device_filter or "auto").lower()
    if want == "auto":
        chars = {}
        for r in rows:
            if not r["is_input_device"]:
                chars[r["device"]] = chars.get(r["device"], 0) + len(r["transcription"])
        if not chars:  # only a microphone recorded
            for r in rows:
                chars[r["device"]] = chars.get(r["device"], 0) + len(r["transcription"])
        best = max(chars, key=chars.get) if chars else None
        chosen = [r for r in rows if r["device"] == best]
        # microphone backup: where the chosen output recorded nothing for 60 s+, use the mic, marked (mic)
        mic = [r for r in rows if r["is_input_device"] and r["device"] != best]
        if mic and chosen and not any(r["is_input_device"] for r in chosen):
            import transcript_clean
            # coverage = every chunk the output device recorded, silent ones included (a quiet break is
            # not missing audio), from audio_chunks; transcribed rows alone would miss silences
            try:
                recorded = con.execute(
                    "SELECT timestamp FROM audio_chunks WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) <= datetime(?) "
                    "AND file_path LIKE ?", (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"), f"%{best}%")).fetchall()
            except Exception:
                recorded = []
            primary = sorted({t for t in [_ts(x[0]) for x in recorded] + [_ts(r["timestamp"]) for r in chosen] if t})
            fill = transcript_clean.gap_fill(primary, [(t, r) for r in mic if (t := _ts(r["timestamp"]))])
            fill_rows = [dict(r, transcription="(mic) " + r["transcription"]) for _, r in fill]
            rows = sorted(chosen + fill_rows, key=lambda r: (r["timestamp"], r["start_time"] or 0, r["id"]))
        else:
            rows = chosen
    else:
        rows = [r for r in rows if want in (r["device"] or "").lower()]
    out = []
    for r in rows:
        t = _ts(r["timestamp"])
        if not t: continue
        text = re.sub(r"\s+", " ", r["transcription"]).strip()
        if out and out[-1]["ts"] == t and out[-1]["device"] == r["device"]:
            out[-1]["text"] += " " + text
        else:
            out.append({"ts": t, "text": text, "device": r["device"]})
    return out

# ------------------------------------------------------- session segmentation
def title_of(frames):
    """Lecture title: the most common browser window title, cleaned of chrome."""
    from collections import Counter
    chrome = "|".join(["Google Chrome"] + ([re.escape(PORTAL)] if PORTAL else []))
    names = [re.sub(rf"\s*[-–|]\s*({chrome}).*$", "", (f["window_name"] or "").strip())
             for f in frames if f["window_name"]]
    names = [n for n in names if len(n) > 3]
    return Counter(names).most_common(1)[0][0] if names else "Untitled lecture"

def sessions(frames, gap_min=GAP_MIN):
    """Split frames into viewing sessions on time gaps."""
    out, cur = [], []
    for f in frames:
        if cur and (f["ts"] - cur[-1]["ts"]).total_seconds() > gap_min * 60:
            out.append(cur); cur = []
        cur.append(f)
    if cur: out.append(cur)
    result = []
    for s in out:
        minutes = (s[-1]["ts"] - s[0]["ts"]).total_seconds() / 60
        if minutes < MIN_SESSION_MIN: continue
        result.append({"start": s[0]["ts"], "end": s[-1]["ts"], "minutes": round(minutes, 1),
                       "frames": s, "title": title_of(s),
                       "id": s[0]["ts"].astimezone().strftime("%Y%m%d-%H%M")})
    return result

# -------------------------------------------------------------- keyframes
def dhash(path, size=8):
    """64-bit difference hash. Pure stdlib + Pillow if present, else None."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(path).convert("L").resize((size + 1, size))
    except Exception:
        return None
    px = list(img.tobytes())  # mode L: one byte per pixel (getdata is deprecated in Pillow 12)
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]; right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | int(left > right)
    return bits

def hamming(a, b): return bin(a ^ b).count("1")

def extract_from_chunk(con, frame, dest):
    """Pull one frame out of the compacted MP4 (screenpipe turns JPEGs into
    video chunks in the background, so snapshot_path disappears after a while).
    """
    import subprocess
    if not frame["video_chunk_id"]:
        return False
    row = con.execute("SELECT file_path FROM video_chunks WHERE id = ?", (frame["video_chunk_id"],)).fetchone()
    if not row or not row["file_path"] or not Path(row["file_path"]).exists():
        return False
    idx = int(frame["offset_index"] or 0)
    # ffmpeg 7+ replaced -vsync with -fps_mode and newer builds reject the old flag,
    # so try the current spelling first. A failure here must be visible: it once
    # silently reduced an 83-minute class to 5 slides.
    global _EXTRACT_WARNED
    err = ""
    for sync in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", row["file_path"],
               "-vf", f"select=eq(n\\,{idx})", *sync, "-frames:v", "1", "-q:v", "3", "-y", str(dest)]
        try:
            r = subprocess.run(cmd, timeout=60, capture_output=True)
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return True
            err = r.stderr.decode(errors="replace").strip()[:200]
        except Exception as e:
            err = str(e)[:200]
    if not _EXTRACT_WARNED:
        _EXTRACT_WARNED = True
        print(f"  warning: could not extract frames from video chunks ({err or 'ffmpeg missing?'})", file=sys.stderr)
    return False

_EXTRACT_WARNED = False


def frame_image(con, frame, tmpdir):
    """Path to this frame's image: the live JPEG if still on disk, else one
    extracted from the compacted MP4. Returns None when neither is available."""
    p = frame["snapshot_path"]
    if p and Path(p).exists():
        return Path(p)
    dest = Path(tmpdir) / f"f{frame['id']}.jpg"
    if dest.exists():
        return dest
    return dest if extract_from_chunk(con, frame, dest) else None


_UI_NOISE = re.compile(r"(Audio (settings|options)|Open chat panel|\d+ new messages?|Leave)\s*", re.I)

def _text_tokens(text):
    """Word set of a frame's on-screen text, without repeated meeting-app chrome."""
    return set(re.findall(r"[a-z0-9]{3,}", _UI_NOISE.sub(" ", text or "").lower()))

def _jaccard(a, b):
    return len(a & b) / max(1, len(a | b))

def keyframes(con, session, max_frames=40, distance=12):
    """Pick visually distinct frames — the slide changes, not every tick.

    Prefers frames screenpipe captured because the screen changed
    (capture_trigger='visual_change'), which during video playback is exactly a
    slide or scene change. Falls back to text-difference when image hashing is
    unavailable.
    """
    import tempfile
    cands = session["frames"]
    preferred = [f for f in cands if (f["capture_trigger"] or "") == "visual_change"] or cands
    tmpdir = tempfile.mkdtemp(prefix="lecture-kf-")
    picked, hashes, seen_text, texts = [], [], set(), []
    for f in preferred:
        img = frame_image(con, f, tmpdir)
        if img is None:
            continue
        f = {**f, "image": img}
        h = dhash(img)
        if h is not None:
            # Slides from one deck share a template, so a coarse image hash calls two
            # different slides the same. On-screen text decides in that case.
            toks = _text_tokens(f["text"])
            looks_same = any(hamming(h, prev) < distance for prev in hashes)
            new_text = len(toks) >= 40 and all(_jaccard(toks, t) < 0.5 for t in texts)
            if looks_same and not new_text:
                continue
            hashes.append(h)
            if len(toks) >= 40: texts.append(toks)
        else:
            key = re.sub(r"\W+", " ", (f["text"] or ""))[:400]
            if key in seen_text: continue
            seen_text.add(key)
        picked.append(f)
        if len(picked) >= max_frames * 6: break
    if len(picked) > max_frames:   # long sessions: spread the picks over the whole session
        step = len(picked) / max_frames
        picked = [picked[int(i * step)] for i in range(max_frames)]
    return picked

# ------------------------------------------------------------- vision model
def vl_describe(image_path, context=""):
    """Describe one slide with the local vision model.

    Uses the same client and prompt the eval harness measures (eval/vlm_client.py).
    Refuses to return a description unless the server provably received the
    image: a model loaded text-only answers confidently about images it never
    saw, and that must never end up in study notes.
    """
    if not VL_MODEL:
        return None
    prompt = vc.slide_prompt(context)
    base = _VL_BASELINE.get(prompt)
    if base is None:
        base = _VL_BASELINE[prompt] = vc.text_baseline(VL_MODEL, prompt)
    think = os.environ.get("OMLX_VL_THINK", "false").lower() == "true"
    r = vc.ask(VL_MODEL, prompt, images=[image_path], think=think,
               max_tokens=4000 if think else 1200, timeout=600)
    if r["error"]:
        print(f"  vision model call failed: {r['error']}", file=sys.stderr)
        return None
    if not vc.image_received(r["prompt_tokens"], base):
        global _VL_DROP_WARNED
        if not _VL_DROP_WARNED:
            print("  ✗ the server did not pass the image to the model (loaded text-only?). "
                  "Reload it in VLM mode; slide descriptions skipped.", file=sys.stderr)
            _VL_DROP_WARNED = True
        return None
    out = r["text"].strip()
    return None if out.upper().startswith("SKIP") else out


_VL_BASELINE = {}
_VL_DROP_WARNED = False

# ------------------------------------------------------------------ building
def hhmmss(delta_s):
    delta_s = max(0, int(delta_s)); return f"{delta_s//3600:02d}:{delta_s%3600//60:02d}:{delta_s%60:02d}"

def slugify(s):
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")[:70] or "lecture"

NOTES_PROMPT_VERSION = "notes-v3"
CARDS_PROMPT_VERSION = "cards-v3"


def make_notes(title, source):
    """Study notes from a lecture's transcript + slide text. Shared with the eval."""
    return study.llm(study.SYS_TUTOR,
        f"Lecture: {title}\n\n{source}\n\n"
        "Write study notes with these sections: ## TL;DR (5 bullets), ## Key concepts, "
        "## Frameworks & models, ## Worked examples and numbers, ## How this applies to building AI products, "
        "## Action items for the learner, ## Open questions. "
        "Cite timecodes like [00:12:30] and slides like [slide 4]. "
        "Ignore player UI and navigation text. Speech-to-text often misspells names and numbers: "
        "use the spelling shown on slides when a slide shows it.\n"
        "Rules for accuracy (notes-v3, from a live-class comparison):\n"
        "- If the sources give two different values for the same fact, report both and who gave each; "
        "do not pick one.\n"
        "- If a question was deferred ('details will be shared'), say it was not answered. Never infer the answer.\n"
        "- A statistic shown on a slide without a source is a claim: write 'the slide claims'.\n"
        "- Under Open questions list only what the session left unanswered or contradictory.", max_tokens=3200)


def _salvage_json_objects(raw):
    """Every complete {...} object in the text — survives a truncated or chatty reply."""
    out, dec = [], json.JSONDecoder()
    i = raw.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(raw[i:])
            if isinstance(obj, dict): out.append(obj)
            i = raw.find("{", i + end)
        except json.JSONDecodeError:
            i = raw.find("{", i + 1)
    return out


def make_cards(title, source, n=20, say=lambda *a: None):
    """Flashcards as [{q, a, at, kind}] from the same source. Shared with the eval.

    cards-v2 (baseline eval: v1 cards were mostly recall and overlapped, G-Eval 0.4;
    and asking 20 cards of a 7-minute lecture overran the token budget into broken
    JSON): the count scales with the material, each card tests a different idea,
    at most a third may be plain recall, and partial replies are salvaged.
    """
    words = len(source.split())
    n = max(4, min(n, round(words / 120)))
    raw = study.llm(study.SYS_TUTOR, f"Lecture: {title}\n\n{source}\n\n"
        f"Create {n} flashcards from this lecture only. Rules:\n"
        "- Each card tests a DIFFERENT idea; no two cards may share an answer.\n"
        "- Mix the kinds: 'why' (reasoning), 'compare' (distinguish two things), 'apply' "
        "(a short new scenario to decide), 'recall' (a definition or number). "
        f"At most {max(1, n // 3)} 'recall' cards.\n"
        "- A question must be answerable without seeing the lecture's wording; the answer is "
        "under 40 words and states only what the lecture supports.\n"
        "- Write each question as a plain question. Do not begin with 'Recall', 'Apply the lecture's' "
        "or 'Compare the'; do not mention 'the lecture' in the question.\n"
        'Return ONLY a JSON array: [{"q":"...","a":"...","at":"00:10:00","kind":"why"}].',
        max_tokens=400 + 220 * n, temperature=0.2)
    cards = dedupe_cards([c for c in _salvage_json_objects(raw) if c.get("q") and c.get("a")])
    if not cards:
        say("  cards: model returned no usable JSON, skipped")
    return cards[:n]


def dedupe_cards(cards, threshold=0.6):
    """Drop a card that asks for what an earlier card already asks for: same question
    words, or the same answer under a different question (the eval's most common card fault)."""
    tok = lambda t: set(re.findall(r"[a-z0-9]{3,}", str(t).lower()))
    sim = lambda a, b: len(a & b) / len(a | b) if a | b else 1.0
    kept, seen = [], []
    for c in cards:
        q, a = tok(c["q"]), tok(c["a"])
        if any(sim(q, q2) >= threshold or sim(a, a2) >= threshold for q2, a2 in seen):
            continue
        kept.append(c); seen.append((q, a))
    return kept


def _split_lines(lines, budget):
    """Pack transcript lines into consecutive parts of at most `budget` characters."""
    parts, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > budget:
            parts.append("\n".join(cur)); cur, size = [], 0
        cur.append(ln); size += len(ln) + 1
    if cur: parts.append("\n".join(cur))
    return parts


PART_CHARS = int(os.environ.get("STUDY_PART_CHARS", "24000"))

def digest_parts(title, parts, say=lambda *a: None):
    """Map step for sessions longer than one context window: each part of the
    transcript becomes a dense, timecoded digest, so the notes cover the whole
    session instead of only the first CTX characters."""
    out = []
    for i, part in enumerate(parts, 1):
        say(f"  digesting transcript part {i}/{len(parts)}…")
        out.append(study.llm(study.SYS_TUTOR,
            f"Lecture: {title} (part {i} of {len(parts)})\n\nTRANSCRIPT (timecoded):\n{part}\n\n"
            "List every substantive point made in this part as bullets: facts, names, numbers, dates, "
            "definitions, frameworks, examples, commitments and answers to audience questions. "
            "Keep the timecode of each point like [00:12:30]. Use only what is said; if a name is "
            "unclear in the transcript, mark it (unclear). No preamble.", max_tokens=1400))
    return out


GENERIC_TITLE = re.compile(r"^(" + (re.escape(PORTAL) + r"\b.*|" if PORTAL else "")
                           + r"learning experience platform|untitled lecture|zoom\b.*)$", re.I)

def lecture_title(session, transcript):
    """A portal player's page title is often generic ('<Portal> - Learning Experience
    Platform', so name the lecture from what was said (one short model call)."""
    t = session["title"]
    if not GENERIC_TITLE.match(t.strip()) or not transcript.strip():
        return t
    try:
        name = study.llm("You name lecture recordings.", "Give a 3-8 word title for this lecture, "
                         "no quotes, no trailing period.\n\n" + transcript[:4000], max_tokens=30).strip()
        name = re.sub(r"[\"'`*#]+", "", name.splitlines()[0]).strip().rstrip(".")
        return name[:90] or t
    except Exception:
        return t

def _live_dir(session):
    return LECTURES / "live" / session["start"].astimezone().strftime("%Y%m%d")


def live_transcript(session, bucket_s=30, min_coverage=0.6):
    """The whisper re-transcription live_class.py wrote during the class, grouped into
    30 s parts shaped like audio_between's rows. None when absent, disabled
    (STUDY_TRANSCRIPT_SOURCE=screenpipe) or covering less than 60% of the session."""
    if os.environ.get("STUDY_TRANSCRIPT_SOURCE", "auto") == "screenpipe":
        return None
    f = _live_dir(session) / "whisper_segments.jsonl"
    if not f.exists():
        return None
    lo, hi = session["start"] - dt.timedelta(seconds=45), session["end"] + dt.timedelta(minutes=2)
    out = []
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        t = dt.datetime.fromisoformat(s["t"])
        if not (lo <= t <= hi) or not s["text"].strip():
            continue
        if out and (t - out[-1]["ts"]).total_seconds() < bucket_s:
            out[-1]["text"] += " " + s["text"].strip()
        else:
            out.append({"ts": t, "text": s["text"].strip(), "device": "whisper_live"})
    minutes = max(1, session.get("minutes") or int((session["end"] - session["start"]).total_seconds() // 60))
    covered = len({o["ts"].replace(second=0, microsecond=0) for o in out})
    return out if covered >= min_coverage * minutes else None


def deck_source(folder, budget=36000, notes_chars=260):
    """The class's own deck, when one was put in the lecture folder (deck.json from
    tools/deck_text.py, or a .pptx that is converted here). Slide text is exact, unlike
    keyframe descriptions; speaker notes are the instructor's, trimmed per slide."""
    dj = folder / "deck.json"
    if not dj.exists():
        decks = sorted(folder.glob("*.pptx"))
        if not decks:
            return "", []
        sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
        import deck_text
        dj.write_text(json.dumps(deck_text.read_deck(str(decks[0])), ensure_ascii=False, indent=1))
    deck = json.loads(dj.read_text())
    lines = []
    for s in deck:
        body = re.sub(r"\s+", " ", s["text"]).strip()
        note = re.sub(r"\s+", " ", s.get("notes", "")).strip()[:notes_chars]
        lines.append(f"[deck {s['n']}] {s['title']} :: {body}" + (f" || speaker notes: {note}" if note else ""))
    text = "CLASS DECK (the instructor's own slides, exact text; cite as [deck N]):\n" + "\n".join(lines)
    return text[:budget], deck


def live_qa_source(session, folder, budget=9000):
    """Write qa.md from live_class's Q&A threads and follow-ups; return a compact source block."""
    d = _live_dir(session)
    if not (d / "qa_threads.json").exists():
        return ""
    try:
        import live_qa
    except Exception:
        return ""
    live = json.loads((d / "qa_threads.json").read_text())
    threads = [t for t in live if live_qa.thread_kind(t) != "trivial"]
    fus = {}
    if (d / "qa_followups.jsonl").exists():
        for l in (d / "qa_followups.jsonl").read_text().splitlines():
            if l.strip():
                x = json.loads(l); fus.setdefault(x["thread"], []).append(x)
    fus = live_qa.curate_followups(threads, fus) if hasattr(live_qa, "curate_followups") else fus
    # Post-class judge scores (tests/evals/score_live.py): follow-ups failing relevance or
    # answerability move to qa_pruned.md; drafts failing faithfulness are tagged.
    scores = json.loads((d / "live_scores.json").read_text()).get("items", {}) if (d / "live_scores.json").exists() else {}
    pruned = {}
    if scores:
        for tid in list(fus):
            keep = []
            for f in fus[tid]:
                sc = scores.get(f["q"])
                (pruned.setdefault(tid, []) if sc and sc["pruned"] else keep).append(f)
            fus[tid] = keep
    # The meeting app's own Q&A export, anonymised by qa_export.py, is complete where the
    # live capture may have missed a thread: use its threads, keep the live follow-ups.
    ref = folder / "qa_threads_export.json"
    if ref.exists():
        import qa_export
        by_norm = {qa_export.norm(t["q"]): t["id"] for t in live}
        exp = []
        for t in json.loads(ref.read_text()):
            k = qa_export.norm(t["q"])
            tid = by_norm.get(k) or next((v for c, v in by_norm.items() if min(len(c), len(k)) >= 12 and (c.startswith(k) or k.startswith(c))), None)
            exp.append(dict(t, id=tid or live_qa.thread_id({"time": t["time"], "text": t["q"]})))
        # An attendee's export may hold only their own questions ("My questions"): the export
        # wins where it has the thread, the live capture keeps every thread it lacks.
        have = {t["id"] for t in exp}
        rest = [t for t in threads if t["id"] not in have]
        threads = sorted([t for t in exp if live_qa.thread_kind(t) != "trivial"] + rest, key=lambda t: live_qa._minutes(t["time"]))
        say_src = "the meeting app's Q&A export" + (" and the live capture" if rest else "")
    else:
        say_src = "the live capture"
    md = [f"# Class Q&A — {session['title']}", "",
          f"_From {say_src}. Attendees are unnamed; staff replies as posted. Follow-ups are local-model drafts, not answers from the class._", ""]
    for t in threads:
        md += [f"## {t['time']} · {t['by']} · {live_qa.thread_kind(t)}", "", t["q"], ""]
        md += [f"> **{a['by']}** ({a['time']}): {a['text']}".replace("\n", "\n> ") for a in t["answers"]] + [""]
        for f in fus.get(t["id"], []):
            tag = " _(unsupported by the class context)_" if (scores.get(f["q"]) or {}).get("unsupported") else ""
            md += [f"- Follow-up ({f['type']}): {f['q']}", f"  - Draft: {f['answer']}{tag}"]
        md += [""]
    if scores:
        n_p = sum(len(v) for v in pruned.values())
        md.insert(3, f"_Judged after class ({len(scores)} drafted items, rules {json.loads((d / 'live_scores.json').read_text()).get('rubric', '?')}): "
                     f"{n_p} follow-ups that failed relevance or answerability are in qa_pruned.md._\n")
        pm = [f"# Pruned follow-ups — {session['title']}", "",
              "_Failed Question relevance or Answerability in the post-class judge run (majority of up to 3 calls)._", ""]
        by_id = {t["id"]: t for t in threads}
        for tid, fl in pruned.items():
            pm += [f"## Thread: {(by_id.get(tid) or {}).get('q', tid)[:200]}", ""]
            for f in fl:
                sc = scores[f["q"]]
                why = ", ".join(m for m in ("relevance", "answerability") if not sc[m]["pass"])
                pm += [f"- {f['q']}", f"  - failed: {why} ({sc['relevance']['votes']} / {sc['answerability']['votes']} pass votes)",
                       f"  - Draft: {f['answer']}"]
            pm += [""]
        (folder / "qa_pruned.md").write_text("\n".join(pm) + "\n")
    (folder / "qa.md").write_text("\n".join(md) + "\n")
    lines = [f"Q ({t['time']}): {t['q'][:300]} | A: " + " / ".join(a["text"][:500] for a in t["answers"])
             for t in threads if live_qa.thread_kind(t) == "content" and t["answers"]]
    return ("CLASS Q&A (quoted; attendee questions with staff replies, not the lecturer's words):\n" + "\n".join(lines))[:budget]


def build(session, con, describe=True, cards=20, quiet=False):
    def say(*a):
        if not quiet: print(*a, file=sys.stderr)
    folder = LECTURES / f"{session['id']}_{slugify(session['title'])}"
    (folder / "slides").mkdir(parents=True, exist_ok=True)

    # 1. transcript (deterministic)
    # audio chunks are stamped at their start, up to ~30 s before the first frame/marker
    audio = audio_between(con, session["start"] - dt.timedelta(seconds=45), session["end"] + dt.timedelta(minutes=2),
                          os.environ.get("STUDY_AUDIO_DEVICE") or None)
    transcript_source = "capture"
    live = live_transcript(session)
    if live:
        say(f"  using the live whisper re-transcription ({len(live)} parts) instead of screenpipe's ({len(audio)})")
        audio, transcript_source = live, "whisper_live"
    t0 = session["start"]
    transcript_lines = [f"[{hhmmss((a['ts']-t0).total_seconds())}] {a['text']}" for a in audio]
    transcript = "\n".join(transcript_lines)
    prior = None  # keep the title from an earlier build: the model words it differently each time
    try: prior = json.loads((folder / "manifest.json").read_text()).get("title")
    except Exception: pass
    session = {**session, "title": prior or lecture_title(session, transcript)}  # folder keeps the stable id+page slug
    (folder / "transcript.md").write_text(
        f"# Transcript — {session['title']}\n\n_{session['start'].astimezone():%a %d %b %Y %H:%M} · "
        f"{session['minutes']} min · {len(audio)} segments_\n\n" + (transcript or "_no audio captured_") + "\n")

    # 2. keyframes + descriptions
    kfs = keyframes(con, session)
    say(f"  {len(kfs)} keyframe(s), {len(audio)} transcript segment(s)")
    # Crop every keyframe to the lecture player (the region that changes across
    # the session) and cap the long edge; both measured in eval/ — fewer image
    # tokens, faster, and no browser/sidebar text in the descriptions.
    from PIL import Image
    box = player_box([f["image"] for f in kfs]) if len(kfs) >= 3 else None
    max_edge = int(os.environ.get("STUDY_VL_MAX_EDGE", "1600"))
    say(f"  player region: {box if box else 'not detected — using full frames'}")
    cache_file = folder / "slides" / ".descriptions.json"
    try: desc_cache = json.loads(cache_file.read_text())
    except Exception: desc_cache = {}
    stale = folder / "slides" / "_stale"   # images from an earlier build of this session
    for old in (folder / "slides").glob("*.jpg"):
        stale.mkdir(exist_ok=True); old.replace(stale / old.name)
    slides = []
    for i, f in enumerate(kfs, 1):
        stamp = hhmmss((f["ts"] - t0).total_seconds())
        dest = folder / "slides" / f"{i:02d}_{stamp.replace(':','')}.jpg"
        try:
            im = Image.open(f["image"]).convert("RGB")
            if box: im = im.crop(box)
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            im.save(dest, quality=88)
        except Exception as e:
            say(f"  keyframe {i} unreadable: {e}"); continue
        desc = None
        if describe:
            key = str(f["id"])   # a rebuild must not pay for the same 30 s vision call twice
            if key in desc_cache:
                desc = desc_cache[key]
            else:
                say(f"  describing slide {i}/{len(kfs)}…")
                desc = desc_cache[key] = vl_describe(dest, session["title"])
                cache_file.write_text(json.dumps(desc_cache))
        slides.append({"n": i, "at": stamp, "file": dest.name,
                       "screen_text": re.sub(r"\s+", " ", f["text"] or "")[:1500], "description": desc})
    (folder / "slides.md").write_text(
        f"# Slides & visuals — {session['title']}\n\n" + ("\n".join(
            f"## {s['n']}. at {s['at']}\n\n![[{s['file']}]]\n\n"
            + (f"{s['description']}\n\n" if s['description'] else "")
            + (f"> On-screen text: {s['screen_text']}\n" if s['screen_text'] else "")
            for s in slides) or "_no keyframes captured_") + "\n")

    # 3. notes + cards (model, grounded in the two files above)
    slide_src = "SLIDES:\n" + "\n".join(f"[slide {s['n']} @ {s['at']}] {s['description'] or s['screen_text']}"
                                         for s in slides)
    deck_src, deck = deck_source(folder)
    if deck_src:   # the deck replaces keyframe descriptions as the slide source (slides.md stays)
        slide_src = deck_src + "\n\nKEYFRAME TIMES (when each captured slide was on screen):\n" + "\n".join(
            f"[slide {s['n']} @ {s['at']}] {(s['description'] or s['screen_text'])[:90]}" for s in slides)
        say(f"  class deck used as the slide source ({len(deck)} slides)")
    qa_src = live_qa_source(session, folder)
    if qa_src:   # class Q&A: attendee questions and staff replies, names already removed
        slide_src = slide_src + "\n\n" + qa_src
        say(f"  class Q&A added to the notes source ({qa_src.count(chr(10)) + 1} lines)")
    # A reference transcript merged in by transcript_ref.py (e.g. Zoom's own export) is more
    # complete than ours; notes and cards use it when present. transcript.md stays our own.
    src_lines, merged = transcript_lines, folder / "transcript_merged.md"
    if merged.exists():
        src_lines = [l for l in merged.read_text().splitlines()[3:] if l.strip()]
        say(f"  using merged reference transcript ({len(src_lines)} lines)")
    transcript_src = "\n".join(src_lines)
    parts = _split_lines(src_lines, PART_CHARS) if len(transcript_src) + len(slide_src) > study.CTX else []
    if parts:
        digests = digest_parts(session["title"], parts, say)
        (folder / "digest.md").write_text(f"# Part digests — {session['title']}\n\n" + "\n\n".join(
            f"## Part {i}\n\n{d}" for i, d in enumerate(digests, 1)) + "\n")
        source = ("DIGEST OF THE FULL TRANSCRIPT (timecoded):\n" + "\n\n".join(digests)
                  + "\n\n" + slide_src)[: study.CTX]
    else:
        source = (f"TRANSCRIPT (timecoded):\n{transcript_src}\n\n" + slide_src)[: study.CTX]
    notes = make_notes(session["title"], source)
    (folder / "notes.md").write_text(
        f"# {session['title']}\n\n_{session['start'].astimezone():%a %d %b %Y %H:%M} · {session['minutes']} min · "
        f"notes by {study.MODEL} from this Mac's own capture_\n\n{notes}\n")

    if cards and parts:   # cards from the words themselves, part by part, not from the digest
        card_rows, per = [], max(3, -(-cards // len(parts)))
        for i, part in enumerate(parts, 1):
            say(f"  cards from part {i}/{len(parts)}…")
            card_rows += make_cards(session["title"], f"TRANSCRIPT (timecoded):\n{part}", per, say)
        if slides:   # slide-only facts (module tables, artefact lists) never appear in the transcript parts
            say("  cards from slides…")
            card_rows += make_cards(session["title"], slide_src[: study.CTX], per, say)
    else:
        card_rows = make_cards(session["title"], source, cards, say) if cards else []
    if card_rows:
        (folder / "cards.tsv").write_text("\n".join(
            f"{c['q'].replace(chr(9),' ')}\t{c['a'].replace(chr(9),' ')}\t{session['title']} {c.get('at','')}"
            for c in card_rows) + "\n")

    # 4. Obsidian note (links, not copies — the vault points at the artefacts)
    vault_dir = VAULT / VAULT_SUB
    vault_dir.mkdir(parents=True, exist_ok=True)
    concepts = re.findall(r"^\s*[-*]\s+\*\*(.+?)\*\*", notes, re.M)[:12]
    links = " ".join(f"[[{c.strip()}]]" for c in dict.fromkeys(concepts))
    (vault_dir / f"{session['id']} {session['title']}.md".replace("/", "-")).write_text(
        "---\n"
        f"date: {session['start'].astimezone():%Y-%m-%d}\n"
        f"duration_min: {session['minutes']}\ncourse: {COURSE}\n"
        f"source: {((session['frames'][0].get('browser_url') if session['frames'] else '') or 'live class (Zoom)')}\n"
        f"artefacts: {folder}\ntags: [lecture, ai-product-management]\n---\n\n"
        f"# {session['title']}\n\n{notes}\n\n"
        f"## Concepts\n{links or '_none extracted_'}\n\n"
        f"## Artefacts\n- Transcript: `{folder/'transcript.md'}`\n- Slides: `{folder/'slides.md'}`"
        f"\n- Flashcards: `{folder/'cards.tsv'}` ({len(card_rows)} cards)"
        + (f"\n- Class Q&A: `{folder/'qa.md'}`" if (folder / "qa.md").exists() else "") + "\n")

    manifest = {"id": session["id"], "title": session["title"],
                "start": session["start"].isoformat(), "end": session["end"].isoformat(),
                "minutes": session["minutes"], "frames": len(session["frames"]),
                "frames_with_image": sum(1 for f in session["frames"] if f.get("snapshot_path") or f.get("video_chunk_id")),
                "transcript_source": "merged" if merged.exists() else transcript_source,
                "qa_threads": (folder / "qa.md").exists(),
                "deck_slides": len(deck),
                "transcript_segments": len(audio), "transcript_parts": len(parts) or 1, "slides": len(slides),
                "slides_described": sum(1 for s in slides if s["description"]),
                "cards": len(card_rows), "vl_model": VL_MODEL or None, "llm": study.MODEL,
                "player_box": list(box) if box else None,
                "built_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:   # a module set by hand (or by an earlier build) survives a rebuild
        old = json.loads((folder / "manifest.json").read_text())
        for k in ("module", "module_source"):
            if old.get(k) and not manifest.get(k):
                manifest[k] = old[k]
    except Exception:
        pass
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return folder, manifest

# ------------------------------------------------------------------ commands
def cmd_list(a):
    con = db(); ss = sessions(frames_since(con, parse_since(a.since)))
    if a.json:
        out = []
        for s in ss:
            folder = LECTURES / f"{s['id']}_{slugify(s['title'])}"
            man = folder / "manifest.json"
            title = s["title"]
            if man.exists():
                try: title = json.loads(man.read_text()).get("title") or title
                except Exception: pass
            out.append({"id": s["id"], "title": title, "start": s["start"].isoformat(),
                        "end": s["end"].isoformat(), "minutes": s["minutes"], "frames": len(s["frames"]),
                        "built": (folder / "manifest.json").exists(), "folder": str(folder)})
        # built lectures whose frames aged out of the window still belong in the library
        seen = {o["folder"] for o in out}
        for m in sorted(LECTURES.glob("*/manifest.json")) if LECTURES.exists() else []:
            if str(m.parent) in seen: continue
            try: j = json.loads(m.read_text())
            except Exception: continue
            out.append({"id": j.get("id"), "title": j.get("title"), "start": j.get("start"), "end": j.get("end"),
                        "minutes": j.get("minutes"), "frames": j.get("frames"), "built": True, "folder": str(m.parent)})
        print(json.dumps({"sessions": sorted(out, key=lambda o: o.get("start") or "", reverse=True)})); return
    if not ss: print("no lecture sessions found (is screenpipe capturing the portal?)"); return
    for s in ss:
        built = (LECTURES / f"{s['id']}_{slugify(s['title'])}" / "manifest.json").exists()
        print(f"{s['id']}  {s['start'].astimezone():%a %d %b %H:%M}  {s['minutes']:>5} min  "
              f"{len(s['frames']):>4} frames  {'[built]' if built else '       '}  {s['title'][:60]}")

def _local_time(v, day=None):
    """'10:06' (today, local) or an ISO datetime -> aware UTC datetime."""
    if re.fullmatch(r"\d{1,2}:\d{2}", v):
        h, m = map(int, v.split(":"))
        t = (day or dt.datetime.now()).astimezone().replace(hour=h, minute=m, second=0, microsecond=0)
    else:
        t = dt.datetime.fromisoformat(v)
        if t.tzinfo is None: t = t.astimezone()
    return t.astimezone(dt.timezone.utc)

def window_session(con, start, end, title=None):
    """One session for an explicit time window (live classes: audio may start
    before the first saved frame, and a static slide must not split the session)."""
    fr = [f for f in frames_since(con, start) if f["ts"] <= end]
    return {"start": start, "end": end, "minutes": round((end - start).total_seconds() / 60, 1),
            "frames": fr, "title": title or (title_of(fr) if fr else "Untitled lecture"),
            "id": start.astimezone().strftime("%Y%m%d-%H%M")}

def cmd_build(a):
    con = db()
    if a.start:
        start = _local_time(a.start)
        end = _local_time(a.end) if a.end else dt.datetime.now(dt.timezone.utc)
        ss = [window_session(con, start, end, a.title)]; a.id = "last"
        if not ss[0]["frames"] and not audio_between(con, start, end):
            # "--from 10:04" means today; a past class needs the date (2026-10-10T10:04)
            sys.exit(f"nothing was captured between {start.astimezone():%d %b %H:%M} and {end.astimezone():%H:%M}; "
                     "for another day pass ISO times, e.g. --from 2026-01-12T10:00 --to 2026-01-12T11:30")
    else:
        ss = sessions(frames_since(con, parse_since(a.since)))
        if a.title: ss = [{**s, "title": a.title} for s in ss]
    if not ss: sys.exit("no lecture sessions in that window")
    targets = ss[-1:] if a.id in ("last", None) else [s for s in ss if s["id"] == a.id]
    if a.id == "all": targets = ss
    if not targets: sys.exit(f"no session with id {a.id} (try: lecture list)")
    for s in targets:
        print(f"building {s['id']} — {s['title']} ({s['minutes']} min)", file=sys.stderr)
        folder, man = build(s, con, describe=not a.no_vision, cards=a.cards)
        if a.json:
            print(json.dumps({"ok": True, "folder": str(folder), "manifest": man})); continue
        print(f"✓ {folder}\n  {man['transcript_segments']} transcript segments · "
              f"{man['slides']} slides ({man['slides_described']} described) · {man['cards']} cards")

def cmd_watch(a):
    import time
    con = db(); seen = set()
    print(f"watching for finished lectures (idle {a.idle} min) — ctrl-c to stop", file=sys.stderr)
    while True:
        for s in sessions(frames_since(con, parse_since("2d"))):
            age_min = (dt.datetime.now(dt.timezone.utc) - s["end"]).total_seconds() / 60
            done = (LECTURES / f"{s['id']}_{slugify(s['title'])}" / "manifest.json").exists()
            if s["id"] in seen or done or age_min < a.idle: continue
            print(f"building {s['id']} — {s['title']}", file=sys.stderr)
            try: build(s, con, describe=not a.no_vision, cards=a.cards); seen.add(s["id"])
            except Exception as e: print(f"  build failed: {e}", file=sys.stderr); seen.add(s["id"])
        time.sleep(a.interval)

def run_checks(vision_probe=True):
    """Health checks as data: [{id, ok, level, message, fix}]. level: ok|warn|fail."""
    import shutil as _sh
    out = []
    def chk(id, ok, message, fix=None, level=None):
        out.append({"id": id, "ok": bool(ok), "level": level or ("ok" if ok else "fail"),
                    "message": message, "fix": fix})
    PRIVACY = "System Settings → Privacy & Security → Screen & System Audio Recording"
    chk("database", DB.exists(), str(DB), None if DB.exists() else "start screenpipe once to create it")
    if DB.exists():
        con = db()
        n_f = con.execute("SELECT count(*) FROM frames").fetchone()[0]
        n_s = con.execute("SELECT count(*) FROM frames WHERE snapshot_path IS NOT NULL").fetchone()[0]
        n_v = con.execute("SELECT count(*) FROM video_chunks").fetchone()[0]
        n_a = con.execute("SELECT count(*) FROM audio_transcriptions").fetchone()[0]
        devs = [r[0] for r in con.execute("SELECT DISTINCT device FROM audio_transcriptions LIMIT 8")]
        chk("frames", n_f > 0, f"{n_f} frames ({n_s} live JPEGs, {n_v} video chunks)",
            None if n_f else f"grant screen recording to the app that launches screenpipe: {PRIVACY}. If Lecture Lens is already ON there but macOS keeps asking, the app was rebuilt (new ad-hoc signature): remove it with (−), start capture again and allow")
        want = os.environ.get("STUDY_AUDIO_DEVICE", "") or "auto"
        if want.lower() == "auto":  # any output (non-microphone) device counts as lecture audio
            n_tap = con.execute("SELECT count(*) FROM audio_transcriptions WHERE is_input_device = 0").fetchone()[0]
            label = "app/system audio"
        else:
            n_tap = con.execute("SELECT count(*) FROM audio_transcriptions WHERE lower(device) LIKE ?",
                                (f"%{want.lower()}%",)).fetchone()[0]
            label = f"'{want}'"
        chk("app_audio", n_tap > 0, f"{n_tap} transcript segments from {label}",
            None if n_tap else f"allow audio capture for the app that launches screenpipe: {PRIVACY}")
        chk("transcripts", n_a > 0, f"{n_a} segments; devices: {', '.join(devs) or '—'}", level="ok" if n_a else "warn")
    chk("ffmpeg", _sh.which("ffmpeg"), "ffmpeg " + ("found" if _sh.which("ffmpeg") else "missing"),
        None if _sh.which("ffmpeg") else "brew install ffmpeg")
    try:
        from PIL import Image  # noqa: F401
        chk("pillow", True, "Pillow available")
    except ImportError:
        chk("pillow", False, "Pillow missing", "pip install pillow", level="warn")
    if not VL_MODEL:
        chk("vision", False, "OMLX_VL_MODEL not set — slides keep OCR text only", "set OMLX_VL_MODEL in .env", level="warn")
    elif vision_probe:
        try:
            from PIL import Image, ImageDraw
            import tempfile
            probe = Path(tempfile.mkdtemp()) / "probe.jpg"
            im = Image.new("RGB", (640, 360), "white"); ImageDraw.Draw(im).text((40, 150), "PROBE 4721", fill="black")
            im.save(probe)
            q = "What number is written in this image? Reply with the number only."
            base = vc.text_baseline(VL_MODEL, q)
            r = vc.ask(VL_MODEL, q, images=[probe], max_tokens=20)
            got = vc.image_received(r["prompt_tokens"], base)
            read = "4721" in (r["text"] or "")
            chk("vision", got and read, f"{VL_MODEL} — " + ("receives images and reads them" if got and read else
                "image NOT received (model loaded text-only)" if not got else f"misread the probe: {r['text']!r}"),
                None if got and read else "unload and load the model in oMLX so it runs as VLM")
        except Exception as e:
            chk("vision", False, f"cannot reach oMLX: {e}", "start oMLX")
    else:
        chk("vision", True, f"{VL_MODEL} (probe skipped)", level="warn")
    chk("vault", True, str(VAULT) + ("" if VAULT.exists() else " (will be created)"), level="ok" if VAULT.exists() else "warn")
    return out


def cmd_doctor(a):
    checks = run_checks(vision_probe=not a.quick)
    ok = all(c["ok"] or c["level"] == "warn" for c in checks)
    if a.json:
        print(json.dumps({"ok": ok, "checks": checks, "artefacts": str(LECTURES)}))
    else:
        for c in checks:
            mark = {"ok": "✓", "warn": "~", "fail": "✗"}[c["level"] if not c["ok"] else "ok"]
            print(f"{c['id']:<13}{mark} {c['message']}")
            if c["fix"] and not c["ok"]: print(f"{'':<15}→ {c['fix']}")
        print(f"{'artefacts':<13}  {LECTURES}")
    sys.exit(0 if ok else 1)

def find_folder(ident):
    """A built lecture folder by session id, 'last', or folder name."""
    folders = sorted((d for d in LECTURES.iterdir() if (d / "manifest.json").exists()),
                     key=lambda d: json.loads((d / "manifest.json").read_text()).get("built_at", ""))
    if not folders:
        sys.exit("no built lectures yet (lecture build first)")
    if ident in (None, "last"):
        return folders[-1]
    for d in folders:
        if d.name == ident or d.name.startswith(f"{ident}_"):
            return d
    sys.exit(f"no built lecture {ident} (see: lecture list)")


def cmd_qa_import(a):
    """Use the meeting app's own Q&A export for a built lecture: score the live capture
    against it and save an anonymised copy that the next build uses for qa.md."""
    import qa_export
    folder = find_folder(a.id)
    man = json.loads((folder / "manifest.json").read_text())
    staff = {x.strip() for x in os.environ.get("LIVE_QA_STAFF", "").split(",") if x.strip()}
    if not staff:
        sys.exit("set LIVE_QA_STAFF (comma-separated staff names) first: everyone else is anonymised by it")
    ref = qa_export.export_threads(qa_export.parse_export(Path(a.export).read_text(encoding="utf-8-sig")), staff)
    (folder / "qa_threads_export.json").write_text(json.dumps(qa_export.anonymise(ref, staff), indent=1))
    live = LECTURES / "live" / dt.datetime.fromisoformat(man["start"]).astimezone().strftime("%Y%m%d") / "qa_threads.json"
    msg = f"✓ {len(ref)} threads from the export, anonymised: {folder / 'qa_threads_export.json'}"
    if live.exists():
        r = qa_export.capture_recall(ref, json.loads(live.read_text()))
        (folder / "qa_capture_eval.json").write_text(json.dumps(r, indent=1))
        msg += (f"\n  live capture found {r['questions_found']}/{r['questions']} questions and "
                f"{r['replies_found']}/{r['replies']} staff replies")
    print(msg + "\n  rebuild the lecture to use it for qa.md; keep the raw export out of git (it names attendees)")


BRIEF_PROMPT_VERSION = "brief-v1"

def brief_prompt(title, agenda, prior, preread):
    return (f"NEXT CLASS: {title}\nAGENDA (quoted):\n{agenda}\n\nPRE-READ / HOMEWORK (quoted):\n{preread or '(none given)'}\n\n"
            f"NOTES FROM EARLIER CLASSES (quoted, newest last):\n{prior}\n\n"
            "Write a pre-class brief in markdown: (1) '## What to recall' - 5 bullets from the earlier notes that the agenda "
            "builds on, each naming the framework or example; (2) '## Questions to ask' - 10 questions grouped under the agenda "
            "items, each short enough for a Q&A box, one question mark each, each followed by '  - Draft:' with a 1-2 sentence "
            "answer from the earlier notes, or 'not covered yet'; (3) '## To verify' - claims or numbers a learner should check "
            "against a primary source before class. Never invent sources or numbers; quote the notes' own terms.")


def cmd_brief(a):
    """Before class: a brief from the agenda, pre-read and the notes of earlier classes, with
    the local model. Sources are not added here; the evidence bank is verified separately."""
    agenda = Path(a.agenda).read_text() if a.agenda else ""
    preread = Path(a.preread).read_text()[:6000] if a.preread else ""
    built = []
    for d in sorted(LECTURES.iterdir()):
        if (d / "notes.md").exists() and (d / "manifest.json").exists():
            built.append((json.loads((d / "manifest.json").read_text()).get("start", ""), d))
    prior = "\n\n".join(f"### {d.name}\n{(d / 'notes.md').read_text()[:a.budget // max(1, min(3, len(built)))]}"
                         for _, d in sorted(built)[-3:])
    md = study.llm(study.SYS_TUTOR, brief_prompt(a.title, agenda, prior, preread), max_tokens=2500, temperature=0.3)
    day = a.date or dt.datetime.now().astimezone().strftime("%Y%m%d")
    out = LECTURES / "live" / day
    out.mkdir(parents=True, exist_ok=True)
    head = f"---\ndate: {day}\ntype: class-brief\nlecture: {a.title}\nprompt: {BRIEF_PROMPT_VERSION}\n---\n\n# Brief: {a.title}\n\n"
    (out / "brief.md").write_text(head + md.strip() + "\n")
    (VAULT / VAULT_SUB).mkdir(parents=True, exist_ok=True)
    (VAULT / VAULT_SUB / f"{day} Brief - {slugify(a.title)}.md").write_text(head + md.strip() + "\n")
    print(f"✓ {out / 'brief.md'}")


def classify_drop(path: Path) -> str | None:
    """What a file dropped beside a class is: zoom_transcript, qa_export, deck or None.
    Decided by content, not name, so Zoom's own file names work as saved."""
    if path.suffix.lower() == ".pptx":
        return "deck"
    if path.suffix.lower() not in (".txt", ".md", ".vtt") or path.stat().st_size > 5_000_000:
        return None
    head = path.read_text(encoding="utf-8-sig", errors="ignore")[:20000]
    if re.search(r"^\[\d{1,3}:\d{2}\]\s*[^:\n]{1,40}:", head, re.M) or re.search(r"\d\d:\d\d:\d\d(\.\d+)? --> ", head):
        return "zoom_transcript"
    if len(re.findall(r"^.+?\s{2,}\d{1,2}:\d{2}\s?[AP]M\s*$", head, re.M)) >= 2:
        return "qa_export"
    return None


def cmd_qa(a):
    """Rewrite qa.md (and qa_pruned.md) for a built lecture from the live folder; no model."""
    folder = find_folder(a.id)
    man = json.loads((folder / "manifest.json").read_text())
    session = {"title": man["title"], "start": dt.datetime.fromisoformat(man["start"])}
    live_qa_source(session, folder)
    print(f"✓ {folder / 'qa.md'}" + (f" · pruned: {folder / 'qa_pruned.md'}" if (folder / "qa_pruned.md").exists() else ""))


def cmd_ingest(a):
    """After class: find the meeting app's files beside the class (live folder or the built
    lecture folder), then clean the live transcript, import the Q&A export, merge and score
    the reference transcript and score prospective answers. Idempotent; prints a report."""
    import qa_export, transcript_clean, live_qa
    folder = find_folder(a.id)
    man = json.loads((folder / "manifest.json").read_text())
    day = dt.datetime.fromisoformat(man["start"]).astimezone().strftime("%Y%m%d")
    live = LECTURES / "live" / day
    report = {"lecture": folder.name, "found": {}}
    for d in (live, folder):
        for f in sorted(d.glob("*")) if d.exists() else []:
            k = classify_drop(f) if f.is_file() else None
            if k and k not in report["found"]:
                report["found"][k] = str(f)
    seg = live / "whisper_segments.jsonl"
    if seg.exists() and not seg.with_suffix(".jsonl.raw").exists():
        rows = [json.loads(l) for l in seg.read_text().splitlines() if l.strip()]
        kept, dropped = transcript_clean.clean_segments(rows, os.environ.get("STUDY_LIVE_PROMPT", ""))
        transcript_clean._rewrite(seg, kept)
        report["whisper_clean"] = {"in": len(rows), "kept": len(kept), "dropped": dict(dropped)}
    if "deck" in report["found"] and not (folder / Path(report["found"]["deck"]).name).exists():
        shutil.copy2(report["found"]["deck"], folder)
        report["deck"] = "copied into the lecture folder; rebuild to use it"
    if "qa_export" in report["found"]:
        cmd_qa_import(argparse.Namespace(id=folder.name, export=report["found"]["qa_export"]))
        staff = {x.strip() for x in os.environ.get("LIVE_QA_STAFF", "").split(",") if x.strip()}
        ref = qa_export.export_threads(qa_export.parse_export(Path(report["found"]["qa_export"]).read_text(encoding="utf-8-sig")), staff)
        pro = live / "qa_prospective.jsonl"
        if pro.exists():                          # prospective drafts vs the authoritative replies
            drafts = [json.loads(l) for l in pro.read_text().splitlines() if l.strip()]
            rows = []
            for d in drafts:
                t = next((t for t in ref if qa_export.norm(t["q"]) == qa_export.norm(d["q"])), None)
                if t and t["answers"]:
                    rows.append({"time": t["time"], "confidence": d.get("confidence"),
                                 **live_qa.agreement(d["answer"], " ".join(x["text"] for x in t["answers"]))})
            if rows:
                report["prospective"] = {"n": len(rows), "mean_recall": round(sum(r["recall"] for r in rows) / len(rows), 3)}
                (folder / "qa_prospective_eval.json").write_text(json.dumps({"rows": rows, **report["prospective"]}, indent=1))
        for name in ("qa_capture_eval.json",):
            if (folder / name).exists():
                report["qa_capture"] = json.loads((folder / name).read_text())
    if "zoom_transcript" in report["found"] and (folder / "transcript.md").exists():
        r = subprocess.run([sys.executable, str(HERE / "transcript_ref.py"), str(folder), report["found"]["zoom_transcript"]],
                           capture_output=True, text=True, timeout=600)
        report["transcript_ref"] = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else {"error": r.stderr[-400:]}
    # judge calibration: 15 fresh rows from this class for blind labelling (Evals > Holdout check);
    # scoring them needs the judge model and runs separately (tests/evals/run_holdout_check.sh)
    cal = HERE / "tests" / "evals" / "calibration.py"
    if cal.exists() and (live / "questions.jsonl").exists():
        r = subprocess.run([sys.executable, str(cal), "holdout", "--day", day, "--n", "15"],
                           capture_output=True, text=True, timeout=120, cwd=cal.parent)
        try:
            report["holdout"] = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            report["holdout"] = {"error": (r.stderr or r.stdout)[-300:]}
    (folder / "ingest_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="lecture", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--since", default="7d")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("build"); p.add_argument("id", nargs="?", default="last",
                                                help="session id from `lecture list`, 'last' or 'all'")
    p.add_argument("--since", default="7d"); p.add_argument("--cards", type=int, default=20)
    p.add_argument("--no-vision", action="store_true", help="skip slide descriptions")
    p.add_argument("--from", dest="start", help="build one session for a time window: HH:MM today or ISO")
    p.add_argument("--to", dest="end", help="end of the window (default: now)")
    p.add_argument("--title", help="set the title instead of deriving it")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("watch"); p.add_argument("--idle", type=int, default=5,
                                                help="minutes of inactivity before a session counts as finished")
    p.add_argument("--interval", type=int, default=120); p.add_argument("--cards", type=int, default=20)
    p.add_argument("--no-vision", action="store_true"); p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("doctor"); p.add_argument("--json", action="store_true")
    p.add_argument("--quick", action="store_true", help="skip the vision-model image probe")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("qa-import", help="use the meeting app's Q&A export: score the live capture, anonymise, feed the build")
    p.add_argument("id"); p.add_argument("export"); p.set_defaults(fn=cmd_qa_import)
    p = sub.add_parser("brief", help="before class: recall points and questions from the agenda and earlier notes")
    p.add_argument("--title", required=True); p.add_argument("--agenda", help="text file with the agenda")
    p.add_argument("--preread", help="text file: pre-read or homework instructions"); p.add_argument("--date", help="YYYYMMDD (default today)")
    p.add_argument("--budget", type=int, default=24000); p.set_defaults(fn=cmd_brief)
    p = sub.add_parser("qa", help="rewrite qa.md from the live folder (uses post-class judge scores when present)")
    p.add_argument("id", nargs="?", default="last"); p.set_defaults(fn=cmd_qa)
    p = sub.add_parser("ingest", help="after class: use the meeting app's transcript/Q&A export/deck found beside the class")
    p.add_argument("id", nargs="?", default="last"); p.set_defaults(fn=cmd_ingest)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
