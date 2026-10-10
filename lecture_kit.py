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
import argparse, datetime as dt, json, os, re, sqlite3, sys, urllib.error, urllib.request, urllib.parse
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
        rows = [r for r in rows if r["device"] == best]
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
    cards = [c for c in _salvage_json_objects(raw) if c.get("q") and c.get("a")]
    if not cards:
        say("  cards: model returned no usable JSON, skipped")
    return cards[:n]


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


def live_qa_source(session, folder, budget=9000):
    """Write qa.md from live_class's Q&A threads and follow-ups; return a compact source block."""
    d = _live_dir(session)
    if not (d / "qa_threads.json").exists():
        return ""
    try:
        import live_qa
    except Exception:
        return ""
    threads = [t for t in json.loads((d / "qa_threads.json").read_text()) if live_qa.thread_kind(t) != "trivial"]
    fus = {}
    if (d / "qa_followups.jsonl").exists():
        for l in (d / "qa_followups.jsonl").read_text().splitlines():
            if l.strip():
                x = json.loads(l); fus.setdefault(x["thread"], []).append(x)
    fus = live_qa.curate_followups(threads, fus) if hasattr(live_qa, "curate_followups") else fus
    md = [f"# Class Q&A — {session['title']}", "",
          "_Attendees are unnamed; staff replies as posted. Follow-ups are local-model drafts, not answers from the class._", ""]
    for t in threads:
        md += [f"## {t['time']} · {t['by']} · {live_qa.thread_kind(t)}", "", t["q"], ""]
        md += [f"> **{a['by']}** ({a['time']}): {a['text']}".replace("\n", "\n> ") for a in t["answers"]] + [""]
        for f in fus.get(t["id"], []):
            md += [f"- Follow-up ({f['type']}): {f['q']}", f"  - Draft: {f['answer']}"]
        md += [""]
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
                "transcript_segments": len(audio), "transcript_parts": len(parts) or 1, "slides": len(slides),
                "slides_described": sum(1 for s in slides if s["description"]),
                "cards": len(card_rows), "vl_model": VL_MODEL or None, "llm": study.MODEL,
                "player_box": list(box) if box else None,
                "built_at": dt.datetime.now(dt.timezone.utc).isoformat()}
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
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
