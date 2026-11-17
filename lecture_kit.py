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
import argparse, datetime as dt, hashlib, json, math, os, re, shutil, sqlite3, statistics, subprocess, sys, urllib.error, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cite   # deterministic citation grounding & verification
import capture_health  # CT-1: probe, alerts, gap ledger
import study  # shared config + llm() + oMLX plumbing
import course_calendar  # programme calendar (data in lectures/calendar.json, gitignored)
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
        return dhash_image(Image.open(path), size)
    except Exception:
        return None


def dhash_image(img, size=8):
    """dHash of an open PIL image (dhash() opens the file first)."""
    img = img.convert("L").resize((size + 1, size))
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

MIN_SLIDE_TOKENS = 40
SSIM_SIZE = (192, 108)          # 6 x 6 grid of 32 x 18 cells
SSIM_GRID = 6
SSIM_CELL_CHANGED = 0.8         # a cell whose local SSIM is below this has changed
MAX_CHANGED_CELLS = int(os.environ.get("STUDY_KEYFRAME_MAX_CHANGED", "5"))


def grey_thumb(img, size=SSIM_SIZE):
    """Grey thumbnail as a flat byte list, computed once per frame for ssim()."""
    return list(img.convert("L").resize(size).tobytes())


def ssim(a, b, size=SSIM_SIZE, win=8, stride=4):
    """Mean structural similarity of two grey thumbnails (Wang, Bovik, Sheikh &
    Simoncelli 2004): square 8x8 windows, C1 = (0.01 L)^2, C2 = (0.03 L)^2, L = 255."""
    w, h = size; c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    n = win * win; vals = []
    for y in range(0, h - win + 1, stride):
        for x in range(0, w - win + 1, stride):
            pa = [a[(y + j) * w + x + i] for j in range(win) for i in range(win)]
            pb = [b[(y + j) * w + x + i] for j in range(win) for i in range(win)]
            ma = sum(pa) / n; mb = sum(pb) / n
            va = sum((p - ma) ** 2 for p in pa) / (n - 1); vb = sum((p - mb) ** 2 for p in pb) / (n - 1)
            cov = sum((p - ma) * (q - mb) for p, q in zip(pa, pb)) / (n - 1)
            vals.append(((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2)))
    return sum(vals) / len(vals)


def changed_cells(a, b, size=SSIM_SIZE, grid=SSIM_GRID, threshold=SSIM_CELL_CHANGED):
    """How many cells of a grid x grid split changed, judged by each cell's own SSIM.

    Pooling local SSIM by changed regions rather than by the mean: a moving
    webcam inset changes a few adjacent cells, a new slide's title and bullets
    change many. On the synthetic decks the mean SSIM of the same slide and of
    two different slides overlapped; this count did not (ADR-005)."""
    w, h = size; cw, ch = w // grid, h // grid; n = 0
    for gy in range(grid):
        for gx in range(grid):
            ca = [a[(gy * ch + y) * w + gx * cw + x] for y in range(ch) for x in range(cw)]
            cb = [b[(gy * ch + y) * w + gx * cw + x] for y in range(ch) for x in range(cw)]
            n += ssim(ca, cb, size=(cw, ch), win=min(8, ch)) < threshold
    return n


def is_repeat(h, toks, hashes, texts, distance=12, thumb=None, thumbs=None, recent=3):
    """The keyframe rule: a frame repeats a kept one when its image hash is within
    `distance` bits of one, its on-screen text does not show a new slide, and,
    when thumbnails are given, at most MAX_CHANGED_CELLS of its 36 grid cells
    changed (local SSIM, changed_cells) against one of the `recent` last kept
    frames that are also hash-near. A slide shown again later is kept again.

    The SSIM check (keyframes-v2) is there because a 64-bit hash calls two slides
    of one deck the same: on synthetic template decks the hash-and-text rule
    alone kept 17-58% of the slides (ADR-005, eval/keyframe_bench.py). Without
    thumbnails this is the v1 rule.
    """
    near = [i for i, prev in enumerate(hashes) if hamming(h, prev) < distance]
    new_text = len(toks) >= MIN_SLIDE_TOKENS and all(_jaccard(toks, t) < 0.5 for t in texts)
    if not near or new_text:
        return False
    if thumb is None or thumbs is None:
        return True
    lo = len(hashes) - recent
    return any(changed_cells(thumb, thumbs[i]) <= MAX_CHANGED_CELLS
               for i in near if i >= lo and thumbs[i] is not None)


def keyframe_step(state, h, toks, thumb, distance=12):
    """One frame through the keyframe rule: True if it repeats a kept frame (drop
    it), else False, after recording it in state = {"hashes", "thumbs", "texts"}.
    keyframes() and eval/keyframe_bench.py both call this, so the benchmark
    measures this code."""
    if is_repeat(h, toks, state["hashes"], state["texts"], distance, thumb, state["thumbs"]):
        return True
    state["hashes"].append(h); state["thumbs"].append(thumb)
    if len(toks) >= MIN_SLIDE_TOKENS:
        state["texts"].append(toks)
    return False


def _hash_and_thumb(path):
    """(dHash, grey thumbnail) of an image file, opened once; (None, None) if unreadable."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            return dhash_image(im), grey_thumb(im)
    except Exception:
        return None, None


def _bookmark_times(session, bookmarks_path=None):
    """Return aware bookmark datetimes falling within the session window.

    Bookmarks are optional: one JSON object per line with an ISO time in
    "at" (or "received"), e.g. written by a hotkey while you watch.
    """
    path = Path(bookmarks_path) if bookmarks_path is not None else (LECTURES / "bookmarks.jsonl")
    if not path.exists():
        return []
    start, end = session.get("start"), session.get("end")
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            t = cite.parse_t(e.get("received") or e.get("at") or "")
        except Exception:
            continue
        if t is None:
            continue
        if (start is None or t >= start) and (end is None or t <= end):
            out.append(t)
    return out


def _select_keyframes(picked, max_frames, session_end=None):
    """Information-density greedy selection when len(picked) > max_frames.

    Always retains bookmark-pinned frames (up to max_frames), then greedily picks
    frames maximizing OCR token novelty + dwell duration coverage + temporal spread.
    """
    if len(picked) <= max_frames:
        return picked
    n = len(picked)
    toks = [_text_tokens(f.get("text") or "") for f in picked]
    dwells = []
    for i, f in enumerate(picked):
        if i + 1 < n:
            dwells.append(max(0.0, (picked[i + 1]["ts"] - f["ts"]).total_seconds()))
        elif session_end is not None and session_end > f["ts"]:
            dwells.append((session_end - f["ts"]).total_seconds())
        else:
            dwells.append(60.0)

    selected_idx = [i for i, f in enumerate(picked) if f.get("pinned")]
    if len(selected_idx) >= max_frames:
        step = len(selected_idx) / max_frames
        chosen = [picked[selected_idx[int(i * step)]] for i in range(max_frames)]
        return sorted(chosen, key=lambda f: f["ts"])

    selected_set = set(selected_idx)
    covered = set()
    for i in selected_idx:
        covered |= toks[i]

    while len(selected_idx) < max_frames and len(selected_set) < n:
        best_i, best_score = None, -1.0
        for i in range(n):
            if i in selected_set:
                continue
            novelty = len(toks[i] - covered)
            dur_score = math.log1p(dwells[i])
            if selected_idx:
                gap_s = min(abs((picked[i]["ts"] - picked[j]["ts"]).total_seconds()) for j in selected_idx)
                gap_score = math.log1p(gap_s)
            else:
                gap_score = 0.0
            score = (novelty * 2.0) + dur_score + gap_score
            if score > best_score:
                best_score, best_i = score, i
        if best_i is None:
            break
        selected_set.add(best_i)
        selected_idx.append(best_i)
        covered |= toks[best_i]

    return sorted((picked[i] for i in selected_idx), key=lambda f: f["ts"])


def keyframes(con, session, max_frames=40, distance=12, bookmarks_path=None):
    """Pick visually distinct frames — the slide changes, not every tick.

    Prefers frames screenpipe captured because the screen changed
    (capture_trigger='visual_change'), which during video playback is exactly a
    slide or scene change. Skips transient frames (<2 s, followed within 5 s by a
    stable one), always keeps frames nearest an optional bookmark, and when there
    are more candidates than max_frames picks by on-screen-text novelty, dwell
    and spread rather than by even spacing. Falls back to text-difference when
    image hashing is unavailable.
    """
    import tempfile
    cands = sorted(session["frames"], key=lambda f: f["ts"])
    pinned_ids = set()
    for bm_t in _bookmark_times(session, bookmarks_path):
        if not cands:
            continue
        nearest = min(cands, key=lambda f: abs((f["ts"] - bm_t).total_seconds()))
        if abs((nearest["ts"] - bm_t).total_seconds()) <= 60.0:
            pinned_ids.add(nearest["id"])
    preferred = [f for f in cands if (f.get("capture_trigger") or "") == "visual_change"
                 or f["id"] in pinned_ids] or cands
    dwells = [(preferred[i + 1]["ts"] - preferred[i]["ts"]).total_seconds() if i + 1 < len(preferred)
              else float("inf") for i in range(len(preferred))]
    stable = []
    for i, f in enumerate(preferred):
        if f["id"] not in pinned_ids and dwells[i] < 2.0 and any(
                dwells[j] >= 2.0 and (preferred[j]["ts"] - f["ts"]).total_seconds() <= 5.0
                for j in range(i + 1, len(preferred))):
            continue
        stable.append(f)
    tmpdir = tempfile.mkdtemp(prefix="lecture-kf-")
    picked, seen_text = [], set()
    state = {"hashes": [], "thumbs": [], "texts": []}
    for f in stable:
        img = frame_image(con, f, tmpdir)
        if img is None:
            continue
        is_pinned = f["id"] in pinned_ids
        f = {**f, "image": img, "pinned": is_pinned}
        h, thumb = _hash_and_thumb(img)
        if h is not None:
            # Slides from one deck share a template, so a coarse image hash calls two
            # different slides the same. Structural similarity and on-screen text decide.
            if keyframe_step(state, h, _text_tokens(f["text"]), thumb, distance) and not is_pinned:
                continue
        else:
            key = re.sub(r"\W+", " ", (f["text"] or ""))[:400]
            if not is_pinned and key in seen_text: continue
            seen_text.add(key)
        picked.append(f)
        if len(picked) >= max_frames * 6: break
    if len(picked) > max_frames:   # long sessions: keep the most informative spread
        picked = _select_keyframes(picked, max_frames, session_end=session.get("end"))
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


RECALL_KINDS = ("why", "apply", "compare")   # preferred over plain recall for the opening questions


def recall_first(notes, cards, k=3):
    """Put k questions above the notes and their answers after them.

    Answering before reading is retrieval practice (Roediger & Karpicke 2006),
    and an attempt that fails still improves later learning of the answer (the
    pretesting effect, Richland, Kornell & Kao 2009). Reasoning cards are
    preferred over plain recall. The notes text itself is unchanged.
    """
    ranked = sorted(enumerate(cards), key=lambda ic: (str(ic[1].get("kind", "")).lower() not in RECALL_KINDS, ic[0]))
    picked = [c for _, c in ranked[:k] if c.get("q") and c.get("a")]
    if not picked:
        return notes
    qs = "\n".join(f"{i}. {c['q']}" for i, c in enumerate(picked, 1))
    ans = "\n".join(f"{i}. {c['a']}" + (f" [{c['at']}]" if c.get("at") else "") for i, c in enumerate(picked, 1))
    # Answers go after the notes, not in <details>: the app's Markdown view does not fold HTML.
    return ("## Before you read\n\nAnswer these from memory first. The answers are at the end.\n\n"
            f"{qs}\n\n---\n\n{notes}\n\n---\n\n## Answers to the opening questions\n\n{ans}")


def strip_recall_first(md):
    """The notes without recall_first's opening questions and closing answers (for the vault
    note, which links the full notes.md). Text without the block is returned unchanged."""
    head = "## Before you read\n"
    if md.startswith(head) and "\n\n---\n\n" in md:
        md = md.split("\n\n---\n\n", 1)[1]
    return md.split("\n\n---\n\n## Answers to the opening questions\n", 1)[0]


def mmss(delta_s):
    m, s = divmod(max(0, int(delta_s)), 60)
    return f"{m:02d}:{s:02d}"


def _line_offset_s(line):
    m = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]", line)
    if not m:
        return None
    sec, ok = cite.parse_timecode_s(m.group(1))
    return sec if ok else None


def align_segments(slides, transcript_lines, deck=None, duration_s=None):
    """Partition transcript lines into deterministic slide-bounded windows W_i = [t_i, t_{i+1}).

    When `deck` is provided, matches each slide keyframe to its best deck slide via
    token Jaccard and annotates deck entries with `active_spans`.
    """
    parsed_lines = []
    max_line_s = 0.0
    for ln in transcript_lines:
        off = _line_offset_s(ln)
        if off is not None and off > max_line_s:
            max_line_s = off
        parsed_lines.append((off, ln))

    timed_slides = []
    for s in slides or []:
        sec, ok = cite.parse_timecode_s(str(s.get("at") or ""))
        if ok and sec is not None:
            timed_slides.append((sec, s))
    timed_slides.sort(key=lambda x: x[0])

    if not timed_slides:
        end_s = max(float(duration_s or 0.0), max_line_s)
        return [{
            "slide_n": None,
            "start_s": 0.0,
            "end_s": end_s,
            "start_at": "00:00:00",
            "end_at": hhmmss(end_s),
            "lines": [ln for _, ln in parsed_lines],
        }]

    deck_toks = [(_text_tokens(f"{d.get('title', '')} {d.get('text', '')}"), d) for d in (deck or [])]
    windows = []
    first_t = timed_slides[0][0]
    pre_lines = [ln for off, ln in parsed_lines if (off is None or off < first_t)]
    if pre_lines:
        windows.append({
            "slide_n": None,
            "start_s": 0.0,
            "end_s": first_t,
            "start_at": "00:00:00",
            "end_at": hhmmss(first_t),
            "lines": pre_lines,
        })

    for i, (t_i, sl) in enumerate(timed_slides):
        t_next = timed_slides[i + 1][0] if i + 1 < len(timed_slides) else max(t_i, float(duration_s or 0.0), max_line_s)
        if i + 1 < len(timed_slides):
            w_lines = [ln for off, ln in parsed_lines if off is not None and t_i <= off < t_next]
        else:
            w_lines = [ln for off, ln in parsed_lines if off is None or off >= t_i]
            # lines with off is None before first_t were already placed in pre_lines
            if pre_lines:
                w_lines = [ln for off, ln in parsed_lines if off is not None and off >= t_i]
        seg = {
            "slide_n": sl.get("n"),
            "start_s": t_i,
            "end_s": t_next,
            "start_at": hhmmss(t_i),
            "end_at": hhmmss(t_next),
            "file": sl.get("file"),
            "pinned": bool(sl.get("pinned")),
            "lines": w_lines,
        }
        if deck_toks:
            s_toks = _text_tokens(f"{sl.get('screen_text') or ''} {sl.get('description') or ''}")
            if s_toks:
                best_sim, best_d = 0.0, None
                for d_toks, d in deck_toks:
                    sim = _jaccard(s_toks, d_toks)
                    if sim > best_sim:
                        best_sim, best_d = sim, d
                if best_d is not None and best_sim > 0.0:
                    seg["deck_slide"] = best_d.get("n")
                    seg["deck_title"] = best_d.get("title", "")
                    best_d.setdefault("active_spans", []).append(f"{mmss(t_i)}–{mmss(t_next)}")
        windows.append(seg)
    return windows


def _split_lines(lines, budget, boundary_offsets=None):
    """Pack transcript lines into consecutive parts of at most `budget` characters.

    When `boundary_offsets` (sorted slide start times in seconds) is provided,
    prefers splitting at slide boundaries so visual and spoken context stay together.
    """
    def _pack_raw(raw_lines):
        parts, cur, size = [], [], 0
        for ln in raw_lines:
            if cur and size + len(ln) + 1 > budget:
                parts.append("\n".join(cur)); cur, size = [], 0
            cur.append(ln); size += len(ln) + 1
        if cur: parts.append("\n".join(cur))
        return parts

    if not boundary_offsets or not lines:
        return _pack_raw(lines)

    bounds = sorted(set(float(b) for b in boundary_offsets))
    blocks, cur_block, cur_idx = [], [], -1
    for ln in lines:
        off = _line_offset_s(ln)
        if off is None:
            b_idx = cur_idx
        else:
            b_idx = 0
            for b in bounds:
                if off >= b:
                    b_idx += 1
                else:
                    break
        if cur_block and b_idx != cur_idx:
            blocks.append(cur_block)
            cur_block = []
        cur_block.append(ln)
        cur_idx = b_idx
    if cur_block:
        blocks.append(cur_block)

    parts, cur, size = [], [], 0
    for blk in blocks:
        blk_text = "\n".join(blk)
        blk_len = len(blk_text)
        if blk_len > budget:
            if cur:
                parts.append("\n".join(cur)); cur, size = [], 0
            parts.extend(_pack_raw(blk))
            continue
        if cur and size + blk_len + 1 > budget:
            parts.append("\n".join(cur)); cur, size = [], 0
        cur.extend(blk)
        size += blk_len + (1 if size else 0)
    if cur:
        parts.append("\n".join(cur))
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
    if not GENERIC_TITLE.match(t.strip()) or not transcript.strip() or study.browser_mode():
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


def studio_job(session, transcript_lines, deck_src, qa_src, cards, transcript_source="capture"):
    """The job file Lecture Lens Studio runs in browser mode (STUDY_LLM_PROVIDER=browser):
    everything the model steps need, so the Studio never reads the capture database.
    transcript_source "merged" tells the Studio not to replace it with its own transcription."""
    start = session["start"].astimezone()
    return {"version": 1, "id": session["id"], "title": session["title"], "start": session["start"].isoformat(),
            "when": f"{start:%a %d %b %Y %H:%M}", "minutes": session["minutes"], "cards": cards,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "transcript": list(transcript_lines),
            "transcript_source": transcript_source, "deck_src": deck_src or "", "qa_src": qa_src or ""}


def audio_chunk_files(con, start, end):
    """The system-audio chunk files saved for a window, oldest first: [(start_utc, path)]."""
    rows = con.execute("SELECT timestamp, file_path FROM audio_chunks WHERE file_path LIKE '%System Audio%' "
                       "AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                       (start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                        end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))).fetchall()
    out = []
    for ts, fp in rows:
        t = dt.datetime.fromisoformat(str(ts)[:19]).replace(tzinfo=dt.timezone.utc)
        out.append((t, Path(fp)))
    return out


def export_audio(folder, chunks, t0):
    """Give Lecture Lens Studio the lecture's audio (STUDY_STUDIO_TRANSCRIBE=1): each chunk is
    hard-linked into <folder>/audio/ (no extra disk on the same volume; copied otherwise) and
    listed in audio.json with its offset from the lecture start."""
    import shutil
    dest = folder / "audio"
    dest.mkdir(exist_ok=True)
    listing = []
    for t, src in chunks:
        if not src.exists():
            continue
        name = src.name.replace(" ", "_")
        target = dest / name
        if not target.exists():
            try:
                os.link(src, target)
            except OSError:
                shutil.copy2(src, target)
        listing.append({"file": name, "start": t.isoformat(), "offset_s": round(max(0.0, (t - t0).total_seconds()), 1)})
    (folder / "audio.json").write_text(json.dumps(listing, indent=1))
    return listing


def write_vault_note(folder, info, notes, n_cards):
    """The Obsidian note for a lecture: links to the artefacts, not copies. `info` holds id,
    title, start (ISO), minutes and source; shared by `build` and `finish`."""
    vault_dir = VAULT / VAULT_SUB
    vault_dir.mkdir(parents=True, exist_ok=True)
    start = dt.datetime.fromisoformat(info["start"]).astimezone()
    concepts = re.findall(r"^\s*[-*]\s+\*\*(.+?)\*\*", notes, re.M)[:12]
    links = " ".join(f"[[{c.strip()}]]" for c in dict.fromkeys(concepts))
    path = vault_dir / f"{info['id']} {info['title']}.md".replace("/", "-")
    path.write_text(
        "---\n"
        f"date: {start:%Y-%m-%d}\n"
        f"duration_min: {info['minutes']}\ncourse: {COURSE}\n"
        f"source: {info.get('source') or 'live class (Zoom)'}\n"
        f"artefacts: {folder}\ntags: [lecture, ai-product-management]\n---\n\n"
        f"# {info['title']}\n\n{notes}\n\n"
        f"## Concepts\n{links or '_none extracted_'}\n\n"
        f"## Artefacts\n- Transcript: `{folder/'transcript.md'}`\n- Slides: `{folder/'slides.md'}`"
        f"\n- Flashcards: `{folder/'cards.tsv'}` ({n_cards} cards)"
        + (f"\n- Class Q&A: `{folder/'qa.md'}`" if (folder / "qa.md").exists() else "") + "\n")
    return path


def finish(folder):
    """After Lecture Lens Studio ran a lecture: vault note and manifest from its outputs."""
    res_f = folder / "studio_result.json"
    if not res_f.exists():
        raise FileNotFoundError(f"{folder.name}: no studio_result.json yet (run the lecture in Lecture Lens Studio)")
    res = json.loads(res_f.read_text())
    man = json.loads((folder / "manifest.json").read_text())
    md = (folder / "notes.md").read_text()
    body = md.split("\n", 3)[3] if md.count("\n") >= 3 else md     # drop the "# title" and the byline
    cards_f = folder / "cards.tsv"
    n_cards = sum(1 for l in cards_f.read_text().splitlines() if l.strip()) if cards_f.exists() else 0
    slides_f = folder / "slides.json"
    described = sum(1 for s in json.loads(slides_f.read_text()) if s.get("description")) if slides_f.exists() else 0
    note = write_vault_note(folder, {**man, "source": man.get("source")}, strip_recall_first(body.strip()), n_cards)
    man.update({"llm": f"{res.get('engine', 'browser')} (Lecture Lens Studio)", "cards": n_cards,
                "slides_described": described, "studio": {k: res.get(k) for k in ("engine", "finished_at", "seconds", "prompt_versions", "citations_removed")},
                "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    (folder / "manifest.json").write_text(json.dumps(man, indent=2))
    return note, man



PRIVATE = LECTURES / ".private"   # attendee roster, review decisions, backups: never published, never printed
_ROSTER = None


def _name_allow(live_dir: Path | None = None) -> set[str]:
    """Words that look like names but are not: lectures/name_allow.txt, lectures/glossary.txt and
    the day's slide titles (deck headings name products and cases, not attendees)."""
    import name_guard
    allow = name_guard.load_allow(LECTURES / "name_allow.txt") | name_guard.load_allow(LECTURES / "glossary.txt")
    f = live_dir / "slides.jsonl" if live_dir else None
    if f and f.exists():
        for l in f.read_text().splitlines():
            try:
                t = json.loads(l)
            except ValueError:
                continue
            if (t.get("kind") or "slide") == "slide":
                allow |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", t.get("title", ""))}
    return allow


def _used_lowercase(token: str, lines: list[str] | None = None) -> bool:
    """The token's lower-case form is used as an ordinary word: in these lines or anywhere in
    the evidence index (case-sensitive match)."""
    lw = token.lower()
    if lines and any(re.search(rf"\b{re.escape(lw)}\b", l) for l in lines):
        return True
    if INDEX.exists():
        try:
            import sqlite3 as _sq
            con = _sq.connect(str(INDEX))
            pats = [f"* {lw} *", f"* {lw}.*", f"* {lw},*", f"{lw} *"]
            return any(con.execute("SELECT 1 FROM lines WHERE text GLOB ? LIMIT 1", (p,)).fetchone() for p in pats)
        except Exception:
            return False
    return False


def _name_roster():
    """The private attendee roster (names_roster.json + attendees.txt), loaded once per run."""
    global _ROSTER
    if _ROSTER is None:
        import name_guard
        _ROSTER = name_guard.Roster(name_guard.load_roster(PRIVATE), keep=_name_allow() | name_guard.staff_names())
    return _ROSTER


_AUTHORS: dict[str, set] = {}
_NAMES_SEEN: dict[str, set] = {}   # live folder -> names removed from its transcript this run


def _guard_names(session, lines, report=True):
    """Attendee names out of transcript lines and slide text (name_guard.py): Q&A panel
    authors and people addressed by name are replaced; look-alikes are listed for review in
    the gitignored live folder, never in the lecture files."""
    try:
        import name_guard, live_qa
    except Exception:
        return lines
    staff = {x.strip() for x in os.environ.get("LIVE_QA_STAFF", "").split(",") if x.strip()}
    d = _live_dir(session)
    authors = _AUTHORS.get(str(d))
    side = d / "side_windows_raw.jsonl"
    if authors is None and side.exists():
        authors = set()
        for l in side.read_text().splitlines():
            if not l.strip():
                continue
            for e in live_qa.parse_panel(json.loads(l).get("text", "")):
                a = e["author"]
                if a not in staff and not live_qa.YOU.search(a) and not a.lower().startswith("anonymous"):
                    authors.add(a)
    _AUTHORS[str(d)] = authors = authors or set()
    out, rep = name_guard.guard(lines, panel_authors=authors, allow=_name_allow(d), roster=_name_roster(),
                                decisions=name_guard.load_decisions(PRIVATE))
    learned = [e["token"] for e in rep.get("entries", []) if e.get("layer") == "adjacent" and e["decision"] == "removed"]
    learned = [t for t in learned if not _used_lowercase(t, lines)]   # "online" in any lecture is a word, not a name
    if learned and name_guard.learn_names(PRIVATE, learned):
        global _ROSTER
        _ROSTER = None   # the next guard in this run sees them too
    seen = _NAMES_SEEN.setdefault(str(d), set())
    seen.update(rep["removed"])
    if seen - set(rep["removed"]):   # names found in the transcript also leave slide text
        out = [name_guard.redactor(seen)(l) for l in out]
    if report and (rep["removed"] or rep["review"]):
        d.mkdir(parents=True, exist_ok=True)
        (d / "names_review.json").write_text(json.dumps(rep, indent=1))
    return out


def build(session, con, describe=True, cards=20, quiet=False):
    def say(*a):
        if not quiet: print(*a, file=sys.stderr)
    browser = study.browser_mode()   # the model runs in Lecture Lens Studio; leave it a job file
    if browser:
        describe = False
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
    transcript_lines = _guard_names(session, [f"[{hhmmss((a['ts']-t0).total_seconds())}] {a['text']}" for a in audio])
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
                       "pinned": bool(f.get("pinned")),
                       "screen_text": _guard_names(session, [re.sub(r"\s+", " ", f["text"] or "")[:1500]], report=False)[0],
                       "description": desc})
    (folder / "slides.md").write_text(
        f"# Slides & visuals — {session['title']}\n\n" + ("\n".join(
            f"## {s['n']}. at {s['at']}\n\n![[{s['file']}]]\n\n"
            + (f"{s['description']}\n\n" if s['description'] else "")
            + (f"> On-screen text: {s['screen_text']}\n" if s['screen_text'] else "")
            for s in slides) or "_no keyframes captured_") + "\n")

    if browser:   # the Studio describes the keyframes and rewrites slides.md from this
        (folder / "slides.json").write_text(json.dumps(slides, indent=1))

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
        src_lines = _guard_names(session, [l for l in merged.read_text().splitlines()[3:] if l.strip()], report=False)
        say(f"  using merged reference transcript ({len(src_lines)} lines)")
    pre = folder / "transcript_pre.jsonl"
    if pre.exists():   # CT-6: lines filled from the recording, merged in by time and labelled
        import gapfill
        pre_lines = _guard_names(session, gapfill.source_lines(folder, t0), report=False)
        tsec = lambda l: (lambda m: int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else 0)(re.match(r"\[(\d\d):(\d\d):(\d\d)\]", l))
        src_lines = sorted(list(src_lines) + pre_lines, key=tsec)
        say(f"  {len(pre_lines)} lines filled from the recording added to the notes source")
    transcript_src = "\n".join(src_lines)
    dur_s = float(session["minutes"]) * 60.0
    aligned = align_segments(slides, src_lines, deck=deck, duration_s=dur_s)
    (folder / "aligned_segments.json").write_text(json.dumps(aligned, indent=2, ensure_ascii=False))
    boundary_offsets = [w["start_s"] for w in aligned if w.get("slide_n") is not None]
    parts = [] if browser else _split_lines(src_lines, PART_CHARS, boundary_offsets=boundary_offsets) \
        if len(transcript_src) + len(slide_src) > study.CTX else []
    if browser:
        job = studio_job(session, src_lines, deck_src, qa_src, cards,
                         "merged" if merged.exists() else transcript_source)
        if os.environ.get("STUDY_STUDIO_TRANSCRIBE", "0") == "1":   # the Studio re-transcribes in Chrome
            listing = export_audio(folder, audio_chunk_files(con, session["start"] - dt.timedelta(seconds=45),
                                                             session["end"] + dt.timedelta(minutes=2)), t0)
            say(f"  audio for the Studio: {len(listing)} chunk(s) in audio/")
        (folder / "studio_job.json").write_text(json.dumps(job, indent=1))
        (folder / "studio_result.json").unlink(missing_ok=True)
        notes = (f"_Notes pending: open Lecture Lens Studio in Chrome, run this lecture, then `./lecture finish "
                 f"{session['id']}`._")
        if not (folder / "notes.md").exists():
            (folder / "notes.md").write_text(f"# {session['title']}\n\n{notes}\n")
        say("  browser mode: studio_job.json written; run it in Lecture Lens Studio, then `lecture finish`")
    elif parts:
        digests = digest_parts(session["title"], parts, say)
        (folder / "digest.md").write_text(f"# Part digests — {session['title']}\n\n" + "\n\n".join(
            f"## Part {i}\n\n{d}" for i, d in enumerate(digests, 1)) + "\n")
        source = ("DIGEST OF THE FULL TRANSCRIPT (timecoded):\n" + "\n\n".join(digests)
                  + "\n\n" + slide_src)[: study.CTX]
    else:
        source = (f"TRANSCRIPT (timecoded):\n{transcript_src}\n\n" + slide_src)[: study.CTX]
    if not browser:
        notes = make_notes(session["title"], source)

    if browser:
        card_rows = []
    elif cards and parts:   # cards from the words themselves, part by part, not from the digest
        card_rows, per = [], max(3, -(-cards // len(parts)))
        for i, part in enumerate(parts, 1):
            say(f"  cards from part {i}/{len(parts)}…")
            card_rows += make_cards(session["title"], f"TRANSCRIPT (timecoded):\n{part}", per, say)
        if slides:   # slide-only facts (module tables, artefact lists) never appear in the transcript parts
            say("  cards from slides…")
            card_rows += make_cards(session["title"], slide_src[: study.CTX], per, say)
    else:
        card_rows = make_cards(session["title"], source, cards, say) if cards else []
    if not browser:   # in browser mode the Studio writes notes.md; never overwrite them here
        (folder / "notes.md").write_text(
            f"# {session['title']}\n\n_{session['start'].astimezone():%a %d %b %Y %H:%M} · {session['minutes']} min · "
            f"notes by {study.MODEL} from this Mac's own capture_\n\n{recall_first(notes, card_rows)}\n")
    if card_rows:
        (folder / "cards.tsv").write_text("\n".join(
            f"{c['q'].replace(chr(9),' ')}\t{c['a'].replace(chr(9),' ')}\t{session['title']} {c.get('at','')}"
            for c in card_rows) + "\n")

    seg_dicts = []
    for ln in src_lines:
        m_ln = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)", ln)
        if m_ln:
            seg_dicts.append({"at": m_ln.group(1), "text": m_ln.group(2)})
    citation_warnings = cite.verify_citations(notes, slides, seg_dicts, duration_s=dur_s)
    if card_rows:
        card_text = "\n".join(
            f"{c.get('q', '')} {c.get('a', '')} [{c.get('at', '')}]" if c.get("at") else f"{c.get('q', '')} {c.get('a', '')}"
            for c in card_rows
        )
        for w in cite.verify_citations(card_text, slides, seg_dicts, duration_s=dur_s):
            citation_warnings.append({**w, "source": "cards.tsv"})
    if citation_warnings:
        say(f"  citation warnings: {len(citation_warnings)} (see manifest.json)")

    cal_s = course_calendar.session_at(session["start"])   # programme calendar (gitignored)
    module = course_calendar.module_label(cal_s) if cal_s else ""
    # 4. Obsidian note (links, not copies — the vault points at the artefacts)
    source_url = (session['frames'][0].get('browser_url') if session['frames'] else '') or 'live class (Zoom)'
    write_vault_note(folder, {"id": session["id"], "title": session["title"], "start": session["start"].isoformat(),
                              "minutes": session["minutes"], "source": source_url}, notes, len(card_rows))

    manifest = {"id": session["id"], "title": session["title"], "module": module or None,
                "start": session["start"].isoformat(), "end": session["end"].isoformat(),
                "minutes": session["minutes"], "frames": len(session["frames"]),
                "frames_with_image": sum(1 for f in session["frames"] if f.get("snapshot_path") or f.get("video_chunk_id")),
                "transcript_source": "merged" if merged.exists() else transcript_source,
                "qa_threads": (folder / "qa.md").exists(),
                "deck_slides": len(deck),
                "transcript_segments": len(audio), "transcript_parts": len(parts) or 1, "slides": len(slides),
                "slides_described": sum(1 for s in slides if s["description"]),
                "cards": len(card_rows), "citation_warnings": citation_warnings,
                "vl_model": None if browser else VL_MODEL or None,
                "llm": "Lecture Lens Studio (pending)" if browser else study.MODEL,
                "source": source_url,
                "player_box": list(box) if box else None,
                "built_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if cal_s:
        manifest["calendar"] = {"week": cal_s["week"], "title": cal_s["title"], "date": cal_s["date"],
                                "module": cal_s.get("module") or "", "module_title": cal_s.get("module_title") or ""}
        if module:
            manifest["module_source"] = "programme calendar"
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

def _manifest_window(con, ident):
    """A lecture built for an explicit window (a calendar slot or --from/--to) has no
    matching detected session: rebuild it from the window its manifest recorded."""
    for d in LECTURES.iterdir() if LECTURES.exists() else []:
        if not (d.name == ident or d.name.startswith(f"{ident}_")) or not (d / "manifest.json").exists():
            continue
        try:
            m = json.loads((d / "manifest.json").read_text())
            start = dt.datetime.fromisoformat(m["start"].replace("Z", "+00:00"))
            end = dt.datetime.fromisoformat(m["end"].replace("Z", "+00:00")) if m.get("end") else \
                start + dt.timedelta(minutes=float(m["minutes"]))
        except (KeyError, ValueError, TypeError):
            continue
        s = window_session(con, start, end, m.get("title"))
        if s["id"] == ident.split("_", 1)[0]:
            return [s]
    return []


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
    if not targets and a.id not in ("last", "all", None):
        targets = _manifest_window(con, a.id)
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
    if study.browser_mode():
        chk("vision", True, "browser mode: Lecture Lens Studio describes slides in Chrome")
    elif not VL_MODEL:
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
    # CT-1, read-only: the audio-usage key in the compiled app, and whether the newest chunk is all zeros
    app_plist = Path(os.path.expanduser(os.environ.get("STUDY_APP", "~/Applications/Lecture Lens.app"))) / "Contents" / "Info.plist"
    if app_plist.exists():
        import plistlib
        try:
            keys = plistlib.loads(app_plist.read_bytes())
        except Exception:
            keys = {}
        need = [k for k in ("NSAudioCaptureUsageDescription", "NSMicrophoneUsageDescription", "NSScreenCaptureUsageDescription")
                if k in keys]
        chk("usage_keys", "NSAudioCaptureUsageDescription" in keys,
            f"Info.plist usage descriptions: {', '.join(need) or 'none'}",
            None if "NSAudioCaptureUsageDescription" in keys else
            "the app's Info.plist lacks NSAudioCaptureUsageDescription: audio taps return silence without it "
            f"(macOS permission to inspect: {PRIVACY})", level=None if "NSAudioCaptureUsageDescription" in keys else "warn")
    newest = capture_health.newest_chunks(SP_DATA)
    chunk_t = max((t for _, t in newest.values()), default=None)
    if newest:
        recent = [p for p, t in newest.values() if (chunk_t - t).total_seconds() < 120]
        lv = capture_health.combined_level([capture_health.level_db(p) for p in recent])
        silent = lv is not None and lv <= capture_health.SILENCE_DB
        age = (dt.datetime.now(dt.timezone.utc) - chunk_t).total_seconds() / 60 if chunk_t else None
        chk("audio_level", not silent,
            f"newest audio chunk ({age:.0f} min old): peak {lv if lv is not None else '?'} dB" + (" — digital silence" if silent else ""),
            None if not silent else f"all-zero audio usually means the tap was refused: check {PRIVACY} for Lecture Lens",
            level=None if not silent else "warn")
    return out


def privacy_checks(sync_to: str | None = None) -> list[dict]:
    """EP-5: local-only and public-repo assertions (privacy.py); changes nothing except, with
    sync_to, the public repo's ignored .guard/roster.local.sha256."""
    import privacy
    import name_guard
    env = {**privacy.read_env(HERE / ".env"), **{k: v for k, v in os.environ.items() if k.endswith(("_URL", "_BASE_URL"))
                                                 or k.startswith(("CONFIDENT", "DEEPEVAL", "SCREENPIPE_SYNC", "DEEPGRAM"))}}
    pub = Path(os.path.expanduser(sync_to or os.environ.get("LECTURE_LENS_REPO", str(HERE))))
    roster_files = [PRIVATE / "names_roster.json", PRIVATE / "attendees.txt"]
    if sync_to:
        privacy.sync_guard(pub, name_guard.load_roster(PRIVATE), set(name_guard.load_lower_dictionary()))
    cmd = None
    try:
        pid = subprocess.run(["pgrep", "-f", "target/release/screenpipe record"], capture_output=True, text=True).stdout.split()
        if pid:
            cmd = subprocess.run(["ps", "-o", "args=", "-p", pid[0]], capture_output=True, text=True).stdout
    except Exception:
        pass
    script = (HERE / "sp-start").read_text() if (HERE / "sp-start").exists() else ""
    try:
        lsof = subprocess.run(["lsof", "-nP", "-iTCP:3030,8001,47110", "-sTCP:LISTEN"], capture_output=True, text=True,
                              timeout=10).stdout
    except Exception:
        lsof = ""
    rows = privacy.run(env, [HERE / ".deepeval" / ".deepeval", Path.home() / ".deepeval" / ".deepeval"], cmd, script,
                       lsof, pub if pub.exists() else None, roster_files)
    (LECTURES / "doctor_privacy.json").write_text(json.dumps(rows, indent=1))
    return rows


def cmd_doctor(a):
    if getattr(a, "privacy", False):
        rows = privacy_checks(a.sync_guard)
        ok = all(r["ok"] or r["level"] == "warn" for r in rows)
        if a.json:
            print(json.dumps({"ok": ok, "checks": rows}))
        else:
            for r in rows:
                print(f"{r['check']:<20}{'✓' if r['ok'] else ('~' if r['level'] == 'warn' else '✗')} {r['detail']}")
        sys.exit(0 if ok else 1)
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



def cmd_cite(a):
    """Citations for one answer shown in the app (cite.py): public sources it names and the
    lecture moments near its time that share its words, with the slide on screen then."""
    import cite
    at = cite.parse_t(a.t)
    if at is None and a.day:   # a Q&A panel time such as "11:12 AM" on the live folder's day
        try:
            hm = dt.datetime.strptime(a.t.replace(" ", "").upper(), "%I:%M%p").time()
            day = dt.datetime.strptime(a.day, "%Y%m%d").date()
            at = dt.datetime.combine(day, hm).astimezone()
        except ValueError:
            at = None
    if at is None:
        print(json.dumps({"ok": False, "error": f"unreadable time: {a.t}"})); return
    d = LECTURES / "live" / (a.day or at.astimezone().strftime("%Y%m%d"))
    load = lambda f: [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    segs = load(d / "whisper_segments.jsonl") or cite.lecture_segments(LECTURES, at)
    found = cite.moments(segs, at, a.text, window_min=a.window)
    if found:
        day_start = dt.datetime.strptime(d.name, "%Y%m%d").replace(hour=12).astimezone() if re.fullmatch(r"\d{8}", d.name) else at
        clean = _guard_names({"start": day_start}, [m["text"] for m in found], report=False)
        for m, c in zip(found, clean):
            m["text"] = c
    bank = json.loads((d / "evidence.json").read_text()) if (d / "evidence.json").exists() else []
    ids = [x.strip() for x in (a.sources or "").split(",") if x.strip()]
    out = {"ok": True, "t": at.isoformat(), "sources": cite.sources(ids, bank), "moments": found,
           "slide": cite.slide_at(load(d / "slides.jsonl"), at)}
    try:
        v3 = cite_v3(a.answer or a.text, d.name, at, a.window)
    except Exception as e:   # the sheet still shows sources and moments
        v3 = None; out["v3_error"] = f"{type(e).__name__}: {e}"[:200]
    if v3:
        out.update({k: v3[k] for k in ("answer_id", "checker", "sentences", "metrics") if k in v3})
    print(json.dumps(out, ensure_ascii=False))


# ------------------------------------------------------------- evidence store (NG-1)
INDEX = LECTURES / "index.sqlite"
CHECKER_CAL = HERE / "eval" / "results" / "checker_calibration.json"
CITE_LOG = HERE / "eval" / "results" / "citations.jsonl"


def _index_targets(which: str = "all") -> list[tuple[str, Path]]:
    """(lecture_id, folder) pairs: built lecture folders and live-class day folders."""
    out = []
    for f in sorted(LECTURES.iterdir()) if LECTURES.exists() else []:
        if f.is_dir() and re.match(r"\d{8}-\d{4}_", f.name) and (
                (f / "transcript.md").exists() or (f / "transcript_merged.md").exists() or (f / "slides.md").exists()):
            out.append((f.name, f))
    for f in sorted((LECTURES / "live").iterdir()) if (LECTURES / "live").exists() else []:
        if f.is_dir() and re.fullmatch(r"\d{8}", f.name) and (f / "whisper_segments.jsonl").exists():
            out.append((f"live/{f.name}", f))
    if which in ("", "all"):
        return out
    return [(i, f) for i, f in out if i == which or i.startswith(which) or evidence_key(i) == which]


def evidence_key(lecture_id):
    import evidence
    return evidence.lecture_key(lecture_id)


def _embedder():
    """(embed, model) when STUDY_EMBED_MODEL names an embedding model on the local server;
    (None, None) otherwise. The model must be pinned in the server so loading it never
    evicts the main model."""
    model = getattr(study, "EMBED_MODEL", "") or ""
    return (study.embed, model) if model and hasattr(study, "embed") else (None, None)


def _day_guard(day: str):
    day_start = dt.datetime.strptime(day, "%Y%m%d").replace(hour=12).astimezone()
    return lambda lines: _guard_names({"start": day_start}, lines, report=False)


def build_index(which="all", say=print) -> dict:
    import evidence, name_guard
    embed, model = _embedder()
    con = evidence.connect(INDEX)

    def index_one(lecture_id, folder):
        guard = _day_guard(re.search(r"(\d{8})", lecture_id)[1])
        if lecture_id.startswith("live/"):
            title, recs = evidence.live_records(folder, guard)
            bounds = [r["t0"] for r in recs if r["kind"] == "s"]
        else:
            title, recs = evidence.lecture_records(folder, guard)
            al = folder / "aligned_segments.json"
            try:
                bounds = [w["start_s"] for w in json.loads(al.read_text()) if w.get("slide_n") is not None] if al.exists() \
                    else [r["t0"] for r in recs if r["kind"] == "s"]
            except Exception:
                bounds = [r["t0"] for r in recs if r["kind"] == "s"]
        return evidence.index_lecture(con, lecture_id, title, recs, bounds, embed=embed, embed_model=model,
                                      guard_version=getattr(name_guard, "GUARD_VERSION", None))

    def left_in(lecture_id):
        texts = [r[0] for r in con.execute("SELECT text FROM lines WHERE lecture_id=? ORDER BY rowid", (lecture_id,))]
        again = _day_guard(re.search(r"(\d{8})", lecture_id)[1])(texts)
        return sum(1 for x, y in zip(texts, again) if x != y)

    targets = _index_targets(which)
    done = []
    for lecture_id, folder in targets:
        rep = index_one(lecture_id, folder)
        done.append(rep)
        say(f"  {evidence.lecture_key(lecture_id)}: {rep['chunks']} passages, {rep['lines']} lines"
            + (f", {rep['embedded']} embedded" if model else ""))
    # the name check: the guard run again over what the index holds changes nothing. Names
    # learned from a later lecture (the rest of a full name) can reach an earlier one, so
    # lectures that still change are indexed again, at most twice.
    left = {i: left_in(i) for i, _ in targets}
    for _ in range(2):
        again = [(i, f) for i, f in targets if left[i]]
        if not again:
            break
        for i, f in again:
            index_one(i, f)
        left.update({i: left_in(i) for i, _ in again})
    total = sum(left.values())
    con.execute("INSERT OR REPLACE INTO meta VALUES('names_left', ?)", (str(total),)); con.commit()
    return {"ok": True, "index": str(INDEX.name), "lectures": done, "embed_model": model or None, "names_left": total}


def cmd_index(a):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf) if a.json else contextlib.nullcontext():
        rep = build_index(a.which, say=(lambda *_: None) if a.json else print)
    if a.json:
        print(json.dumps(rep))
    else:
        print(f"index: {len(rep['lectures'])} lecture(s); names left: {rep['names_left']}")


def _search_fn(con, lectures=None, near=None, sources=("transcript", "slide", "evidence")):
    import evidence
    embed, model = _embedder()
    return lambda q, k=8: evidence.search(con, q, k=k, lectures=lectures, near=near, sources=sources,
                                          embed=embed, embed_model=model)


def cmd_search(a):
    import evidence
    if not INDEX.exists():
        print(json.dumps({"ok": False, "error": "no index yet: run `lecture index all`"})); return
    con = evidence.connect(INDEX)
    lectures = [i for i, _ in _index_targets(a.lecture)] if a.lecture else None
    hits = _search_fn(con, lectures)(a.query, a.k)
    if a.json:
        print(json.dumps([{k: h[k] for k in ("chunk_id", "lecture_id", "line_ids", "t0", "context", "text",
                                              "score_lex", "score_dense", "score_rerank")} for h in hits], ensure_ascii=False))
    else:
        for h in hits:
            print(f"{h['chunk_id']}  {h['context']}\n    {h['text'][:200]}")


def _checker(slot="interactive"):
    import cite
    cal = json.loads(CHECKER_CAL.read_text()) if CHECKER_CAL.exists() else None
    if os.environ.get("STUDY_CITE_CHECKER", "lexical") == "llm":
        ck = cite.llm_checker(lambda p: study.llm("You check whether passages support a claim. Reply with JSON only.",
                                                   p, max_tokens=80, temperature=0.0, slot=slot))
    else:
        ck = cite.LEXICAL
    return cite.calibrated(ck, cal)


def cite_v3(answer: str, day: str, at: dt.datetime | None, window_min: float = 10.0) -> dict | None:
    """Sentence-by-sentence check of an answer against the evidence store (NG-2); None when
    no index exists. Passages come from the day's lectures, near the answer's time."""
    import cite, evidence
    if not INDEX.exists():
        return None
    con = evidence.connect(INDEX)
    lectures = [i for i, _ in _index_targets("all") if day in i]
    if not lectures:
        return None
    near = (at, window_min * 60) if at else None
    folders = dict(_index_targets("all"))

    def thumb(h):
        if h["source"] != "slide" or h["lecture_id"].startswith("live/"):
            return None
        f = folders.get(h["lecture_id"])
        n = h["chunk_id"].rsplit("/", 1)[-1]
        hit = sorted((f / "slides").glob(f"{int(n):02d}_*.jpg")) if f and n.isdigit() and (f / "slides").exists() else []
        return str(hit[0]) if hit else None

    words_fn, other_fn = (_search_fn(con, lectures, near, ("transcript",)),
                          _search_fn(con, lectures, near, ("slide", "evidence")))
    # short slide and source records outrank long transcript passages in BM25, so the top
    # eight are mixed: six transcript passages, then two slides or sources
    mixed = lambda q, k=8: words_fn(q, k - 2) + other_fn(q, 2)
    res = cite.lint(answer, search=mixed, lines_of=lambda ids: evidence.lines_of(con, ids),
                    checker=_checker(), slide_thumb=thumb)
    try:   # ALCE-style metrics per answer for the eval store: counts and ids, never text
        CITE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CITE_LOG.open("a") as f:
            f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "answer_id": res["answer_id"], "day": day, "checker": res["checker"],
                                **res.get("metrics", {})}) + "\n")
    except OSError:
        pass
    return res


def cmd_checker(a):
    """Calibrate the support checker on learner-labelled pairs (M-5), or write unlabelled
    candidate pairs to label."""
    import cite, evidence
    if a.action == "calibrate":
        pairs = [json.loads(l) for l in Path(a.pairs).read_text().splitlines() if l.strip()]
        pairs = [p for p in pairs if p.get("label") in (0, 1)]
        ck = _checker(slot="background")
        rep = cite.calibrate(ck, pairs)
        rep["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        CHECKER_CAL.parent.mkdir(parents=True, exist_ok=True)
        CHECKER_CAL.write_text(json.dumps(rep, indent=1))
        print(json.dumps(rep))
        return
    todo = Path(a.out)
    labelled = Path(a.pairs)
    if a.action in ("todo", "label"):
        rows = [json.loads(l) for l in todo.read_text().splitlines() if l.strip()] if todo.exists() else []
        if a.action == "label":
            if not (0 <= a.i < len(rows)) or a.label not in (0, 1):
                print(json.dumps({"ok": False, "error": "need --i within the list and --label 0 or 1"})); return
            r = rows.pop(a.i)
            r["label"] = a.label
            r["labelled_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            labelled.parent.mkdir(parents=True, exist_ok=True)
            with labelled.open("a") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            todo.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
        done = sum(1 for l in labelled.read_text().splitlines() if l.strip()) if labelled.exists() else 0
        print(json.dumps({"ok": True, "labelled": done, "target": 100,
                          "todo": [{"i": i, "sentence": r["sentence"], "passage": r["passage"]} for i, r in enumerate(rows[:50])],
                          "remaining": len(rows)}, ensure_ascii=False))
        return
    # pairs: sentences from the day's curated answers against their top passages
    d = LECTURES / "live" / a.day
    rows = [json.loads(l) for l in (d / "questions.jsonl").read_text().splitlines() if l.strip()] if (d / "questions.jsonl").exists() else []
    con = evidence.connect(INDEX)
    lectures = [i for i, _ in _index_targets("all") if a.day in i]
    out, guard = [], _day_guard(a.day)
    for r in rows:
        at = cite.parse_t(r.get("t", ""))
        for s in cite.split_sentences(r.get("answer", "")):
            for h in evidence.search(con, s, k=2, lectures=lectures, near=(at, 600) if at else None):
                out.append({"sentence": guard([s])[0], "passage": h["text"][:700], "line_ids": h["line_ids"], "label": None})
        if len(out) >= a.n:
            break
    dest = Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in out[: a.n]) + "\n")
    print(json.dumps({"ok": True, "pairs": len(out[: a.n]), "file": str(dest)}))


def cmd_curate(a):
    """One round of curated top questions for today's live class (curate.py): writes rows to
    questions.jsonl and a section to top_questions.md; prints JSON for the app."""
    import curate
    now = dt.datetime.now(dt.timezone.utc)
    day = a.day or now.astimezone().strftime("%Y%m%d")
    d = LECTURES / "live" / day
    def out(obj):
        print(json.dumps(obj, ensure_ascii=False))
    if not (d / "whisper_segments.jsonl").exists():
        return out({"ok": False, "error": f"no live transcript for {day}"})
    if getattr(study, "browser_mode", lambda: False)():
        return out({"ok": False, "error": "the language model runs in Lecture Lens Studio (browser mode)"})
    sess = course_calendar.session_on(dt.datetime.strptime(day, "%Y%m%d").date()) or {}
    title = a.title or sess.get("title") or "Live class"
    slot = "background" if a.background else "interactive"
    llm = lambda prompt: study.llm(study.SYS_TUTOR, prompt, max_tokens=1600, temperature=0.3, slot=slot)
    day_start = dt.datetime.strptime(day, "%Y%m%d").replace(hour=12).astimezone()   # the live folder's day, not today
    guard = lambda lines: _guard_names({"start": day_start}, lines, report=False)
    v2 = os.environ.get("STUDY_CURATE_VERSION", "2") != "1"
    if v2:
        sys_q = ("You prepare questions for a learner in a live class from quoted class lines. "
                 "Follow the requested JSON format exactly. Never answer the questions.")
        llm = lambda prompt: study.llm(sys_q, prompt, max_tokens=1600, temperature=0.3, slot=slot)
    try:
        run = curate.run_v2 if v2 else curate.run
        out(run(d, llm, now, title, guard=guard, k=a.k, lookback_min=a.lookback))
    except Exception as e:   # the local model may be busy or down: the app shows the reason
        out({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})


# ------------------------------------------------------------- study aids v2 (LS-1, LS-2, LS-4)
STUDY_DIR = Path(os.path.expanduser(os.environ.get("STUDY_PRACTICE_DIR", str(HERE / "practice"))))


def _lecture_lines(folder: Path) -> list[dict]:
    """The lecture's guarded transcript and slide lines with evidence-store line IDs."""
    import evidence
    man = json.loads((folder / "manifest.json").read_text())
    day = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00")).astimezone().strftime("%Y%m%d")
    _, recs = evidence.lecture_records(folder, _day_guard(day))
    key = evidence.lecture_key(folder.name)
    def local(ts: str) -> str:   # slide times are stored in UTC, transcript times with an offset
        try:
            return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
        except (ValueError, AttributeError):
            return ""
    return [{"line_id": f"{key}/{r['kind']}/{r['idx']}", "t": local(r.get("t_abs") or ""), "t_abs": r.get("t_abs"),
             "text": r["text"], **({"source": "gapfill", "approx": r.get("approx", False)} if r.get("gapfill") else {})}
            for r in recs if r["text"].strip()]


def quiz_items(folder: Path) -> list[dict]:
    """v2 items when built, else the v1 practice quiz in the v2 shape (linted)."""
    import quiz
    items = quiz.read_jsonl(folder / "quiz.jsonl")
    if items:
        return items
    f = folder / "practice_quiz.json"
    if not f.exists():
        return []
    d = json.loads(f.read_text())
    out = [quiz.from_v1(q, folder.name) for q in (d.get("questions") if isinstance(d, dict) else d)]
    for i in out:
        i["lint"] = quiz.lint(i)
    return out


def build_quiz_v2(folder: Path, n: int = 10, checks: bool = True, say=print) -> dict:
    import quiz
    man = json.loads((folder / "manifest.json").read_text())
    lines = [l for l in _lecture_lines(folder) if "/t/" in l["line_id"]]
    llm = lambda p: study.llm("You write practice items from quoted class lines. Follow the JSON format exactly.",
                              p, max_tokens=4000, temperature=0.3, slot="build")
    items = quiz.generate(man["title"], folder.name, lines, llm, n)
    by_id = {l["line_id"]: l for l in lines}
    if checks:
        for it in items:
            it["leak_flag"] = quiz.leak_check(it, llm)
            it["key_supported"] = quiz.key_check(it, by_id, llm)
    quiz.write_jsonl(folder / "quiz.jsonl", items)
    shipped = [i for i in items if i.get("key_supported") is not False]
    v1 = [{"q": i["stem"], "options": i["options"], "answer": i["key"], "why": i["explanation"], "at": i.get("at", "")}
          for i in shipped]
    (folder / "practice_quiz.json").write_text(json.dumps({"prompt_version": quiz.PROMPT_VERSION, "questions": v1}, indent=1))
    (folder / "practice_quiz.md").write_text(quiz.markdown(man["title"], v1))
    rep = {"ok": True, "folder": folder.name, "items": len(items), "shipped": len(shipped),
           "lint": quiz.lint_report(items), "leak_flags": sum(1 for i in items if i.get("leak_flag")),
           "key_unsupported": sum(1 for i in items if i.get("key_supported") is False),
           "levels": {lv: sum(1 for i in items if i.get("requested_level") == lv) for lv in quiz.LEVELS}}
    return rep


def cmd_quiz(a):
    """LS-2: write a practice quiz for a built lecture: linted, cited, key-checked items."""
    folder = find_folder(a.id)
    rep = build_quiz_v2(folder, a.n)
    print(json.dumps(rep) if a.json else f"✓ {rep['shipped']} of {rep['items']} items shipped: {folder / 'practice_quiz.md'}")


def cmd_quiz_lint(a):
    import quiz
    if a.file:
        p = Path(a.file)
        rows = quiz.read_jsonl(p) if p.suffix == ".jsonl" else [quiz.from_v1(q, p.parent.name) for q in
                                                                 (json.loads(p.read_text()).get("questions") or [])]
        rep = quiz.lint_report(rows)
        rep["flags"] = [{"stem": r["stem"][:80], **quiz.lint(r)} for r in rows]
    else:
        rows = []
        for f in sorted(LECTURES.glob("*/practice_quiz.json")):
            d = json.loads(f.read_text())
            rows += [quiz.from_v1(q, f.parent.name) for q in (d.get("questions") if isinstance(d, dict) else d)]
        rep = quiz.lint_report(rows)
    print(json.dumps({"ok": True, **rep}))


def _tldr(notes: str) -> str:
    """The bullets under the notes' TL;DR heading (the five points a recap is scored on)."""
    m = re.search(r"(?ms)^#+\s*TL;DR\s*$(.*?)(?=^#+\s|\Z)", notes)
    return "\n".join(l for l in (m.group(1) if m else "").splitlines() if re.match(r"\s*(?:[-*•]|\d+[.)])\s", l))


def _recap_content(folder: Path) -> tuple[str, str, str]:
    """(text shown after reveal, kind, text the attempt is scored on)."""
    notes = (folder / "notes.md").read_text() if (folder / "notes.md").exists() else ""
    score = _tldr(notes)
    for name, kind in (("cheat_sheet.md", "cheat_sheet"), ("notes.md", "recap")):
        p = folder / name
        if p.exists():
            text = p.read_text()
            if kind == "recap":   # the notes' TL;DR and key concepts are the recap
                m = re.search(r"(?s)(TL;DR.*?)(?=\n#+\s*(?:Frameworks|Examples|How this|Open questions)|\Z)", text)
                text = m.group(1) if m else text[:4000]
            return text, kind, score
    return "", "recap", score


def cmd_review(a):
    import spaced
    rv = spaced.Reviews(STUDY_DIR, LECTURES)
    out = lambda o: print(json.dumps(o, ensure_ascii=False, default=str))
    try:
        if a.action == "settings":
            days = None
            if a.class_days is not None:
                names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                days = [names.index(d.strip()[:3].lower()) for d in a.class_days.split(",") if d.strip()]
            rv.set(weekly_minutes=a.minutes, class_weekdays=days, new_per_week=a.new_per_week)
            return out({"ok": True, "settings": rv.settings(), "plan": rv.plan()})
        if a.action == "plan":
            return out({"ok": True, **rv.plan()})
        if a.action == "stats":
            return out({"ok": True, **rv.stats()})
        if a.action == "grade":
            if not a.card or a.got is None:
                return out({"ok": False, "error": "need --card and --got y|n"})
            return out({"ok": True, **rv.grade(a.card, a.got.lower() in ("y", "yes", "1", "true"), a.seconds)})
        if a.action == "card":
            if not a.card:
                return out({"ok": False, "error": "need --card"})
            kw = {"q": a.q, "a": a.a}
            if a.suspend: kw["suspended"] = True
            if a.unsuspend: kw["suspended"] = False
            if a.delete: kw["deleted"] = True
            return out({"ok": True, "card_id": a.card, "override": rv.override(a.card, **kw)})
        if a.stats:
            return out({"ok": True, **rv.stats()})
        sess = rv.session()
        if a.json:
            return out({"ok": True, **sess})
        return review_interactive(rv, sess)
    except (KeyError, ValueError) as e:
        return out({"ok": False, "error": str(e).strip("'")})


def review_interactive(rv, sess) -> None:
    """Terminal session: answer from memory, see the answer, then pass or fail."""
    import time
    cards = sess["cards"]
    if not cards:
        print("Nothing due. Come back tomorrow."); return
    print(f"{len(cards)} card(s), about {sess['planned_minutes']:.0f} min; target recall {sess['retention']:.0%}")
    done = passed = 0
    for i, c in enumerate(cards, 1):
        print(f"\n[{i}/{len(cards)}] {c['q']}")
        t = time.time()
        try:
            input("  (answer from memory, then press Enter) ")
            print(f"  → {c['a']}" + (f"   ({c['ref']})" if c.get("ref") else ""))
            ans = ""
            while ans not in ("p", "f"):
                ans = input("  pass or fail? [p/f] ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print("\nStopped; answers so far are saved."); break
        g = rv.grade(c["card_id"], ans == "p", time.time() - t)
        done += 1; passed += ans == "p"
        print(f"  next review in {g['next_in_days']} day(s)")
    print(f"\n{passed} passed of {done}.")


def cmd_mastery(a):
    import mastery
    levels = {}
    for f in sorted(p for p in LECTURES.glob("*") if (p / "manifest.json").exists()):
        try:
            levels.update({i["item_id"]: i.get("requested_level") for i in quiz_items(f) if i.get("requested_level")})
        except Exception:
            continue
    print(json.dumps({"ok": True, **mastery.view(STUDY_DIR, LECTURES, levels=levels)}, ensure_ascii=False))


def cmd_practice(a):
    import practice
    store = practice.Store(STUDY_DIR)
    out = lambda o: print(json.dumps(o, ensure_ascii=False))
    try:
        if a.action == "progress":
            return out({"ok": True, **practice.progress(store)})
        if a.action == "start":
            folder = find_folder(a.lecture)
            man = json.loads((folder / "manifest.json").read_text())
            if a.kind == "recap":
                content, kind, score = _recap_content(folder)
                if not content:
                    return out({"ok": False, "error": "no notes or cheat sheet for this lecture"})
                return out({"ok": True, **practice.start_recap(store, folder.name, man["title"], content, kind,
                                                               score or None)})
            items = quiz_items(folder)
            if not items:
                return out({"ok": False, "error": "no practice quiz yet: build one first"})
            if a.kind == "closed":
                return out({"ok": True, **practice.closed_book_items(store, items, folder.name)})
            return out({"ok": True, **practice.start_quiz(store, folder.name, items, n=a.n)})
        if a.action == "attempt":
            return out({"ok": True, **practice.attempt(store, a.session, a.text)})
        if a.action == "reveal":
            return out(practice.reveal_recap(store, a.session, a.skip))
        if a.action == "hint":
            return out(practice.hint(store, a.session))
        if a.action == "answer":
            return out(practice.answer(store, a.session, a.choice, a.confidence))
        if a.action == "skip":
            return out(practice.skip_item(store, a.session))
        if a.action == "next":
            return out(practice.advance(store, a.session))
    except FileNotFoundError:
        return out({"ok": False, "error": "no such session"})
    except SystemExit as e:
        return out({"ok": False, "error": str(e)})


def cmd_prequestions(a):
    import prequestions as pq
    now = dt.datetime.now(dt.timezone.utc)
    day = a.day or now.astimezone().strftime("%Y%m%d")
    live = LECTURES / "live" / day
    out = lambda o: print(json.dumps(o, ensure_ascii=False))
    if a.action == "make":
        agenda = (live / "agenda.txt").read_text() if (live / "agenda.txt").exists() else (a.agenda or "")
        sess = course_calendar.session_on(dt.datetime.strptime(day, "%Y%m%d").date()) or {}
        if not agenda.strip():
            agenda = "\n".join(f"- {x}" for x in (sess.get("topics") or [])) or sess.get("title", "")
        if not agenda.strip():
            return out({"ok": False, "error": f"no agenda for {day}: paste it into live/{day}/agenda.txt"})
        if pq.load(live) and not a.force:
            return out({"ok": True, "prequestions": pq.load(live), "note": "already made"})
        llm = lambda p: study.llm("You write prequestions from a class agenda. Questions only.", p, max_tokens=800,
                                  temperature=0.3, slot="interactive")
        guard = _day_guard(day)
        rows = pq.generate(sess.get("title") or "Class", "\n".join(guard(agenda.splitlines())), llm, a.k)
        return out({"ok": True, "prequestions": pq.save(live, rows)})
    if a.action == "guess":
        return out({"ok": True, "prequestion": pq.guess(live, a.id, a.text, now)})
    if a.action == "mark":
        return out({"ok": True, "prequestion": pq.mark(live, a.id, a.got_it == "y")})
    if a.action == "resolve":
        return out({"ok": True, "prequestions": resolve_prequestions(day)})
    return out({"ok": True, "prequestions": pq.load(live)})


def _lecture_day(folder: Path) -> str:
    man = json.loads((folder / "manifest.json").read_text())
    return dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00")).astimezone().strftime("%Y%m%d")


def agenda_map(folder: Path, notes: bool = True) -> dict:
    """NG-4: map the day's agenda parts onto the lecture as delivered (agenda.py); writes
    agenda_map.json and, with notes, the 'How the class ran' block at the end of notes.md
    (the first rewrite keeps notes.md.pre-agenda)."""
    import agenda
    day = _lecture_day(folder)
    ag = LECTURES / "live" / day / "agenda.txt"
    if not ag.exists():
        return {"ok": False, "error": f"no agenda for {day}: paste it into live/{day}/agenda.txt"}
    text = "\n".join(_day_guard(day)(ag.read_text().splitlines()))
    llm = lambda p: study.llm("You label lecture transcript windows with agenda parts. JSON only.", p, max_tokens=1500,
                              temperature=0.0, slot="build")
    m = agenda.build(text, _lecture_lines(folder), llm)
    if not m.get("ok"):
        return m
    m["made"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    m["window_min"] = agenda.WINDOW_MIN
    (folder / "agenda_map.json").write_text(json.dumps(m, indent=1))
    nf = folder / "notes.md"
    if notes and nf.exists():
        bak = folder / "notes.md.pre-agenda"
        if not bak.exists():
            bak.write_text(nf.read_text())
        nf.write_text(agenda.apply_to_notes(nf.read_text(), agenda.notes_block(m)))
    return m


def skipped_parts(day: str) -> list[str]:
    """Titles of agenda parts the agenda map marks skipped, for any lecture on `day`."""
    out = []
    for f in LECTURES.glob(f"{day}-*/agenda_map.json"):
        try:
            out += [r["agenda_item"].split(":", 1)[-1].strip() for r in json.loads(f.read_text())["parts"]
                    if r["status"] == "skipped"]
        except (ValueError, KeyError):
            continue
    return out


def cornell_build(folder: Path) -> dict:
    """LS-5: extracts from the notes' timed bullets, cue questions per section, cue cards."""
    import cornell
    notes = (folder / "notes.md").read_text() if (folder / "notes.md").exists() else ""
    if not notes:
        return {"ok": False, "error": "no notes for this lecture"}
    exs = cornell.extracts(notes, _lecture_lines(folder))
    if not exs:
        return {"ok": False, "error": "the notes have no bullets with timecodes"}
    llm = lambda p: study.llm("You write Cornell cue questions from lecture extracts. JSON only.", p, max_tokens=1500,
                              temperature=0.3, slot="build")
    heads = {s["section_id"]: s["heading"] for s in cornell.sections(notes)}
    cues = cornell.make_cues(exs, heads, llm)
    cornell.write_jsonl(folder / "extracts.jsonl", exs)
    cornell.write_jsonl(folder / "cues.jsonl", cues)
    (folder / "cards_cues.tsv").write_text(cornell.cards_tsv(cues, exs))
    return {"ok": True, "extracts": len(exs), "cues": len(cues),
            "by_kind": {k: sum(c["kind"] == k for c in cues) for k in cornell.KINDS}}


def cmd_cornell(a):
    import cornell
    folder = find_folder(a.id)
    out = lambda o: print(json.dumps(o, ensure_ascii=False))
    if a.action == "build":
        return out(cornell_build(folder))
    if a.action == "summary":
        return out({"ok": True, **cornell.save_summary(STUDY_DIR / "summaries", folder.name, a.text or "")})
    return out({"ok": True, "lecture": folder.name, **cornell.view(folder, STUDY_DIR / "summaries")})


def cmd_teachback(a):
    import cite, cornell, teachback as tb
    store = tb.Store(STUDY_DIR)
    out = lambda o: print(json.dumps(o, ensure_ascii=False))
    if a.action == "start":
        folder = find_folder(a.lecture)
        exs = cornell.read_jsonl(folder / "extracts.jsonl")
        if not exs:
            notes = (folder / "notes.md").read_text() if (folder / "notes.md").exists() else ""
            exs = cornell.extracts(notes, _lecture_lines(folder))
        secs = {s["section_id"]: s["heading"] for s in cornell.sections((folder / "notes.md").read_text())}
        pick = [e for e in exs if not a.section or e["section_id"] == a.section]
        if len(pick) < 4:
            return out({"ok": False, "error": "need at least four timed points in the notes (or that section)"})
        rng = __import__("random").Random(a.seed)
        pick = rng.sample(pick, min(8, len(pick)))
        by_id = {l["line_id"]: l["text"] for l in _lecture_lines(folder)}
        passages = lambda e: [" ".join(by_id.get(i, "") for i in e.get("line_ids", [])[:12])[:700], e["quote"]]
        llm = lambda p: study.llm("You write teach-back practice from lecture extracts. JSON only.", p, max_tokens=1500,
                                  temperature=0.5, slot="interactive")
        chk_llm = lambda p: study.llm("You check whether passages support a claim. JSON only.", p, max_tokens=200,
                                      temperature=0.0, slot="interactive")
        topic = secs.get(a.section, "") if a.section else (json.loads((folder / "manifest.json").read_text()).get("title") or "")
        gen = tb.generate(topic, pick, passages, llm, cite.llm_checker(chk_llm), seed=a.seed)
        if not gen["ok"]:
            return out(gen)
        return out({"ok": True, **tb.public(store.new(folder.name, gen)), "dropped": len(gen["dropped"])})
    if a.action == "score":
        corr = dict(x.split("=", 1) for x in (a.corrections or "").split("|") if "=" in x)
        return out({"ok": True, **tb.score(store, a.id, [m.strip() for m in (a.marks or "").split(",") if m.strip()], corr)})
    if a.action == "show":
        return out({"ok": True, **tb.public(store.load(a.id))})
    rows = [tb.public(json.loads(p.read_text())) for p in sorted(store.root.glob("*.json"))]
    return out({"ok": True, "sessions": [{k: r[k] for k in ("id", "lecture", "topic", "scored")} |
                                         ({k: r[k] for k in ("caught", "missed", "false_alarms")} if r["scored"] else {})
                                         for r in rows]})


def lane_summary(rows: list[dict], last: int = 500) -> dict:
    """NG-6: per caller kind over the newest rows of llm_slot_metrics.jsonl: calls, median and
    p95 wait, skipped or timed out, median prefill and decode speed."""
    rows = rows[-last:]
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    p95 = lambda xs: sorted(xs)[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] if xs else None
    out = {}
    for k in ("interactive", "time_boxed", "background", "build"):
        rs = [r for r in rows if r.get("kind") == k]
        if not rs:
            continue
        w = [r["waited_s"] for r in rs if r.get("waited_s") is not None]
        out[k] = {"calls": len(rs), "median_wait_s": med(w), "p95_wait_s": p95(w),
                  "skipped": sum(r.get("outcome") in ("skipped", "timeout") for r in rs),
                  "errors": sum(r.get("outcome") == "error" for r in rs),
                  "median_prefill_s": med([r["prefill_s"] for r in rs if r.get("prefill_s")]),
                  "median_decode_tps": med([r["decode_tps"] for r in rs if r.get("decode_tps")])}
    return {"rows": len(rows), "by_kind": out}


def cmd_lanes(a):
    f = LECTURES / "llm_slot_metrics.jsonl"
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    rep = lane_summary(rows)
    benches = sorted((HERE / "eval" / "results").glob("*-lanes.json"))
    rep["bench"] = json.loads(benches[-1].read_text()) if benches else None
    print(json.dumps({"ok": True, **rep}))


def notes_v2_build(folder: Path) -> dict:
    """NG-3: notes v2 into <lecture>/notes_v2/ (the current notes.md is not touched)."""
    import notes_v2, course_calendar
    man = json.loads((folder / "manifest.json").read_text())
    start = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00"))
    day = start.astimezone().strftime("%Y%m%d")
    cal = course_calendar.session_at(start) or {}
    first_slide = [l for l in (folder / "slides.md").read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))][:1] \
        if (folder / "slides.md").exists() else []
    titles = []
    for t in [cal.get("title"), man.get("portal_title"), *[re.sub(r"[*_>]", "", x).strip()[:120] for x in first_slide],
              man.get("title")]:
        if t and t not in titles:
            titles.append(t)
    ag = LECTURES / "live" / day / "agenda.txt"
    agenda = "\n".join(_day_guard(day)(ag.read_text().splitlines())) if ag.exists() else ""
    llm = lambda p: study.llm(study.SYS_TUTOR, p, max_tokens=2500, temperature=0.2, slot="build")
    res = notes_v2.build(_lecture_lines(folder), titles, agenda, llm)
    if not res.get("ok"):
        return res
    notes_v2.save(folder, res)
    return {"ok": True, "folder": str(folder / "notes_v2"), "title": res["outline"]["title"],
            "sections": len(res["outline"]["sections"]), **res["audit"]}


def cmd_notes_v2(a):
    print(json.dumps(notes_v2_build(find_folder(a.id)), ensure_ascii=False))


def scene_frames(folder: Path, con=None) -> list[dict]:
    """CT-5 inputs for a built lecture's kept frames: the image, its time, the app and window
    and OCR text of the nearest captured frame, how long it stayed (next visual change) and
    how different it is from the previous kept frame."""
    import evidence, scenes
    _, recs = evidence.lecture_records(folder, lambda x: x)
    slides = [r for r in recs if r["kind"] == "s" and r.get("slide_file")]
    con = con or db()
    imgs = [str(folder / "slides" / r["slide_file"]) for r in slides]
    ocr = scenes.ocr_images([p for p in imgs if Path(p).exists()], HERE / "tools" / "ocr" / "ocr")
    ocr_by = dict(zip([p for p in imgs if Path(p).exists()], ocr))
    out, prev_h = [], None
    for r in slides:
        t = dt.datetime.fromisoformat(r["t_abs"].replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        lo, hi = (t - dt.timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%S"), (t + dt.timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%S")
        near = [dict(x) for x in con.execute(
            "SELECT timestamp, app_name, window_name, text_json, COALESCE(full_text, accessibility_text, '') AS text FROM frames "
            f"WHERE timestamp >= '{lo}' AND timestamp <= '{hi}' ORDER BY timestamp LIMIT 5").fetchall()]
        nxt = [dict(x) for x in con.execute(
            "SELECT timestamp FROM frames WHERE capture_trigger = 'visual_change' AND "
            f"timestamp > '{(t + dt.timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S')}' AND "
            f"timestamp <= '{(t + dt.timedelta(seconds=300)).strftime('%Y-%m-%dT%H:%M:%S')}' ORDER BY timestamp LIMIT 1").fetchall()]
        img = folder / "slides" / r["slide_file"]
        h = dhash(str(img)) if img.exists() else None
        diff = hamming(h, prev_h) / 64 if h is not None and prev_h is not None else None
        prev_h = h if h is not None else prev_h
        f = near[0] if near else {}
        dwell = ((_ts(nxt[0]["timestamp"]) - t).total_seconds() if nxt else 300.0) if near else None
        out.append({"slide_id": f"{folder.name}/s/{r['idx']}", "slide_file": r["slide_file"], "t_abs": r["t_abs"],
                    "image": str(img) if img.exists() else None, "app_name": f.get("app_name"),
                    "window_name": f.get("window_name"),
                    "text": scenes.ocr_text(ocr_by.get(str(img), [])), "title": scenes.has_title(ocr_by.get(str(img), [])),
                    "dwell_s": dwell,
                    "diff_prev": round(diff, 3) if diff is not None else None, "vlm_desc": r["text"]})
    return out


def scene_vlm():
    """Vision-model label for one image (the CT-5 last stage), under the model lock."""
    import scenes
    if not VL_MODEL:
        return None
    def f(image):
        with study.llm_slot("build"):
            r = vc.ask(VL_MODEL, scenes.VLM_PROMPT, images=[image], think=False, max_tokens=12, timeout=300)
        return None if r.get("error") else scenes.parse_label(r.get("text"))
    return f


def cmd_scenes(a):
    import scenes
    folder = find_folder(a.id)
    frames = scene_frames(folder)
    vlm = None if a.no_vision else scene_vlm()
    rows, used = [], 0
    for fr in frames:
        c = scenes.classify(fr, vlm)
        used += c["class_source"] == "vision"
        rows.append({"slide_id": fr["slide_id"], "t0": fr["t_abs"], "t1": None, "class": c["class"],
                     "class_source": c["class_source"], "reason": c["reason"],
                     "text_blocks": len(scenes.clean_lines(fr["text"])), "tables": None,
                     "thumb_path": fr["image"], "vlm_desc": (fr["vlm_desc"] or "")[:400]})
    for a_, b_ in zip(rows, rows[1:]):
        a_["t1"] = b_["t0"]
    scenes.write_jsonl(folder, rows)
    counts = {k: sum(r["class"] == k for r in rows) for k in scenes.CLASSES}
    print(json.dumps({"ok": True, "frames": len(rows), "classes": counts,
                      "vision_share": round(used / len(rows), 3) if rows else None}))


def cmd_scene_labels(a):
    """CT-5 labels: list them (with thumbnail paths) or correct one; a correction is confirmed."""
    import scenes
    f = HERE / "eval" / "golden" / "scene_labels.jsonl"
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    if a.action == "set":
        if a.label not in scenes.CLASSES:
            return print(json.dumps({"ok": False, "error": f"label must be one of {', '.join(scenes.CLASSES)}"}))
        r = next((r for r in rows if r["slide_id"] == a.id), None)
        if not r:
            return print(json.dumps({"ok": False, "error": "no such frame"}))
        r.update({"label": a.label, "source": "learner", "confirmed": True})
        f.write_text("".join(json.dumps(x) + "\n" for x in rows))
    print(json.dumps({"ok": True, "labels": rows, "confirmed": sum(bool(r.get("confirmed")) for r in rows),
                      "classes": list(scenes.CLASSES)}))


def cmd_agenda(a):
    folder = find_folder(a.id)
    if a.cached:
        f = folder / "agenda_map.json"
        m = json.loads(f.read_text()) if f.exists() else {"ok": False, "error": "not mapped yet"}
    else:
        m = agenda_map(folder, notes=not a.no_notes)
    if a.json:
        print(json.dumps({k: v for k, v in m.items() if k != "sections"} | {"sections": len(m.get("sections", []))}))
    elif not m.get("ok"):
        print(m["error"])
    else:
        for r in m["parts"]:
            span = f"{r['t0'][:5]}–{r['t1'][:5]}" if r["t0"] else "–"
            print(f"{r['status']:<8} {span:<12} {r['minutes']:>3} min  {r['agenda_item']}")
        if m["unplanned"]:
            print(f"unplanned {len(m['unplanned']) * m['window_min']} min")


def resolve_prequestions(day: str) -> list[dict]:
    """After class: resolve the day's prequestions over its class lines and write the cards
    into the built lecture folder (cards_prequestions.tsv, read by the Anki export)."""
    import curate, prequestions as pq
    live = LECTURES / "live" / day
    if not pq.load(live):
        return []
    now = dt.datetime.now(dt.timezone.utc)
    lines = curate.day_lines(live, now, _day_guard(day)) if (live / "whisper_segments.jsonl").exists() else []
    for lid, folder in _index_targets("all"):
        if lid.startswith(day) and not lines:
            lines = [l for l in _lecture_lines(folder) if "/t/" in l["line_id"]]
    llm = lambda p: study.llm("You check whether class passages answer questions. JSON only.", p, max_tokens=600,
                              temperature=0.0, slot="build")
    rows = pq.resolve(live, lines, llm, skipped_parts(day))
    for lid, folder in _index_targets("all"):
        if lid.startswith(day) and not lid.startswith("live/") and (folder / "manifest.json").exists():
            title = json.loads((folder / "manifest.json").read_text()).get("title", "")
            (folder / "cards_prequestions.tsv").write_text(pq.cards_tsv(rows, title))
            break
    return rows


def cmd_qlabels(a):
    """M-7: label a day's questions as would-ask, duplicate and answered-in-class (the NG-5
    baseline). Labels go to eval/golden/questions_labels_<day>.jsonl (private: they quote class)."""
    d = LECTURES / "live" / a.day
    rows = [json.loads(l) for l in (d / "questions.jsonl").read_text().splitlines() if l.strip()] \
        if (d / "questions.jsonl").exists() else []
    lf = HERE / "eval" / "golden" / f"questions_labels_{a.day}.jsonl"
    done = {}
    if lf.exists():
        for l in lf.read_text().splitlines():
            if l.strip():
                x = json.loads(l)
                done[x["idx"]] = x
    if a.action == "label":
        if not (0 <= a.i < len(rows)):
            print(json.dumps({"ok": False, "error": "no such question"})); return
        yn = lambda v: None if v in (None, "") else v.lower() in ("y", "yes", "1", "true")
        x = {"day": a.day, "idx": a.i, "q_sha": hashlib.sha256(rows[a.i].get("q", "").encode()).hexdigest()[:12],
             "would_ask": yn(a.would_ask), "duplicate": yn(a.duplicate), "answered_in_class": yn(a.answered),
             "labelled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        done[a.i] = x
        lf.parent.mkdir(parents=True, exist_ok=True)
        lf.write_text("".join(json.dumps(v) + "\n" for _, v in sorted(done.items())))
    todo = [{"i": i, "q": r.get("q", ""), "answer": r.get("answer", ""), "round": r.get("round"),
             "type": r.get("type"), "t": r.get("t")} for i, r in enumerate(rows) if i not in done]
    lab = list(done.values())
    share = lambda k: round(sum(1 for x in lab if x.get(k)) / len(lab), 3) if lab else None
    print(json.dumps({"ok": True, "day": a.day, "total": len(rows), "labelled": len(lab), "todo": todo[:20],
                      "would_ask_share": share("would_ask"), "duplicate_share": share("duplicate"),
                      "answered_share": share("answered_in_class")}, ensure_ascii=False))


AFTER_FILES = ("transcript.md", "transcript_merged.md", "digest.md", "notes.md", "slides.md", "qa.md")


def names_left(folder: Path, names: list[str], vault_note: Path | None = None) -> dict:
    """How many lines of each lecture file still contain a removed attendee name (counts
    only: the names themselves are never printed)."""
    import name_guard
    red = name_guard.redactor(set(names))
    out = {}
    for f in [folder / n for n in AFTER_FILES] + ([vault_note] if vault_note else []):
        if f and f.exists():
            n = sum(1 for l in f.read_text().splitlines() if red(l) != l)
            if n:
                out[f.name] = n
    return out


# ------------------------------------------------------------- capture health (CT-1)
SP_DATA = Path(os.path.expanduser(os.environ.get("SCREENPIPE_DATA", "~/.screenpipe/data")))
MEETING_PROCS = ("zoom.us", "CptHost")


def _mode_state() -> dict:
    try:
        r = subprocess.run([str(HERE / "sp-mode"), "--json"], capture_output=True, text=True, timeout=15)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def _meeting_running() -> bool:
    for p in MEETING_PROCS:
        try:
            if subprocess.run(["pgrep", "-x", p], capture_output=True, timeout=5).returncode == 0:
                return True
        except Exception:
            pass
    return False


def _newest_frame_t():
    try:
        row = db().execute("SELECT max(timestamp) AS t FROM frames").fetchone()
        return capture_health.parse_t(row["t"] if hasattr(row, "keys") else row[0])
    except Exception:
        return None


def health_probe(now=None) -> dict:
    """One CT-1 probe from the engine's outputs; appends to the day's capture_health.jsonl
    while capture runs or a class is scheduled now, and returns {record, alert}."""
    now = now or dt.datetime.now(dt.timezone.utc)
    mode = _mode_state()
    running = bool(mode.get("running"))
    newest = capture_health.newest_chunks(SP_DATA)
    chunk_t = max((t for _, t in newest.values()), default=None)
    fresh = [p for p, t in newest.values() if (now - t).total_seconds() < 120]   # devices still writing
    level = capture_health.combined_level([capture_health.level_db(p) for p in fresh]) if running and fresh else None
    sp = {}
    if running:
        try:
            sp = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                "http://127.0.0.1:3030/health", timeout=3).read())
        except Exception:
            sp = {}
    expected = running and (_meeting_running() or mode.get("mode") == "live")
    rec = capture_health.probe(now, running, chunk_t, level, _newest_frame_t() if running else None,
                               expected, drm_paused=bool(sp.get("drm_content_paused")))
    day = now.astimezone().strftime("%Y%m%d")
    cal = course_calendar.session_on(now.astimezone().date())
    in_class = False
    if cal:
        try:
            s, e = course_calendar._bounds(cal)
            in_class = s - dt.timedelta(minutes=15) <= now <= e + dt.timedelta(hours=1)
        except Exception:
            in_class = False
    rows = [rec]
    if running or in_class:
        rows = capture_health.append(LECTURES / "live" / day / "capture_health.jsonl", rec)
    recent = [r for r in rows if (now - capture_health.parse_t(r["t"])).total_seconds() <= 600]
    return {"ok": True, "record": rec, "alert": capture_health.alert(recent), "mode": mode.get("mode"),
            "in_class": in_class}


def cmd_health(a):
    rep = health_probe()
    if a.json:
        print(json.dumps(rep)); return
    r = rep["record"]
    print(f"capture {r['status']}: audio chunk {r['audio_chunk_age_s']} s old at {r['audio_level_db']} dB, "
          f"newest frame {r['frame_age_s']} s old" + (f"\n  ALERT: {rep['alert']['message']}" if rep["alert"] else ""))


def lecture_gaps(folder: Path) -> list[dict]:
    """The gap ledger for a built lecture: calendar window vs audio chunks and health records.
    Writes gaps.json in the day's live folder and the manifest's `gaps` field."""
    man = json.loads((folder / "manifest.json").read_text())
    start = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00"))
    cal = course_calendar.session_at(start)
    if cal:
        cs, ce = course_calendar._bounds(cal)
    else:
        cs = start
        ce = dt.datetime.fromisoformat(man["end"].replace("Z", "+00:00")) if man.get("end") else \
            start + dt.timedelta(minutes=float(man.get("minutes") or 0))
    day = cs.astimezone().strftime("%Y%m%d")
    live = LECTURES / "live" / day
    hf = live / "capture_health.jsonl"
    health = [json.loads(l) for l in hf.read_text().splitlines() if l.strip()] if hf.exists() else []
    filled = man.get("gap_filled") or []
    filled = filled if isinstance(filled, list) else [filled]
    g = capture_health.gaps(cs, ce, capture_health.chunk_times(SP_DATA, cs - dt.timedelta(hours=1)), health, filled)
    live.mkdir(parents=True, exist_ok=True)
    (live / "gaps.json").write_text(json.dumps(g, indent=1))
    man["gaps"] = g
    (folder / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))
    return g


def lecture_gapfill(folder: Path, audio: Path | None = None, replay: tuple | None = None, frm: str | None = None,
                    to: str | None = None, dry_run: bool = False, say=print) -> dict:
    """CT-6: fill a gap in a built lecture's transcript from the class recording (gapfill.py).
    `audio` is a recording file (its audio track is used); `replay` (t0, t1) joins the audio
    chunks captured while the recording was replayed on this Mac. `frm`/`to` are local HH:MM
    on the class day; without them the first unfilled gap in the ledger is used. Existing
    transcript lines are never changed; the filled lines go to transcript_pre.jsonl/.md."""
    import evidence, gapfill, tempfile
    man = json.loads((folder / "manifest.json").read_text())
    start = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00"))
    day = start.astimezone().strftime("%Y%m%d")
    if frm and to:
        local = lambda hm: dt.datetime.strptime(f"{day} {hm}", "%Y%m%d %H:%M").astimezone()
        w0, w1 = local(frm), local(to)
    else:
        open_gaps = [g for g in lecture_gaps(folder) if not g.get("filled")]
        if not open_gaps:
            return {"ok": False, "error": "no unfilled gap in this lecture's ledger; pass --from and --to"}
        w0, w1 = (dt.datetime.fromisoformat(open_gaps[0][k]) for k in ("from", "to"))
    _, recs = evidence.lecture_records(folder, lambda xs: xs)
    lines = [(dt.datetime.fromisoformat(r["t_abs"]).timestamp(), r["text"]) for r in recs
             if r["kind"] == "t" and not r.get("gapfill") and r.get("t_abs")]
    words = gapfill.line_words(lines)
    with tempfile.TemporaryDirectory() as td:
        if replay:
            rec, _ = gapfill.replay_audio(SP_DATA, replay[0], replay[1], Path(td) / "replay.m4a")
            source = f"replay {replay[0].astimezone():%d %b %H:%M}"
        else:
            rec = Path(audio).expanduser()
            source = rec.name
            if not rec.is_file():
                return {"ok": False, "error": f"recording not found: {rec.name}"}
        res = gapfill.fill(words, w0.timestamp(), w1.timestamp(), rec, gapfill.media_length(rec),
                           gapfill.whisper_transcribe, guess_offset=start.timestamp(), log=say)
    out = {"ok": bool(res["lines"]) and not res.get("error"), "error": res.get("error"), "holes": res["holes"],
           "lines": len(res["lines"]), "approx_lines": sum(1 for l in res["lines"] if l["approx"]),
           "conf_median": round(statistics.median(l["align_conf"] for l in res["lines"]), 2) if res["lines"] else None,
           "coarse_offset_s": res.get("coarse_offset_s"), "from": w0.isoformat(timespec="seconds"),
           "to": w1.isoformat(timespec="seconds"), "dry_run": dry_run}
    if not out["ok"] or dry_run:
        return out
    rows = gapfill.write_pre(folder, evidence.lecture_key(folder.name), start, res, w0.timestamp(), w1.timestamp(),
                             source, _day_guard(day))
    man = json.loads((folder / "manifest.json").read_text())
    filled = man.get("gap_filled") or []
    filled = [f for f in (filled if isinstance(filled, list) else [filled])
              if not (f.get("from") == out["from"] and f.get("to") == out["to"])]
    filled.append({"from": out["from"], "to": out["to"], "source": "recording", "file": source, "lines": len(rows),
                   "approx_lines": out["approx_lines"], "conf_median": out["conf_median"],
                   "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    man["gap_filled"] = filled
    (folder / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))
    lecture_gaps(folder)
    out["written"] = len(rows)
    return out


def cmd_gapfill(a):
    folder = find_folder(a.id)
    replay = None
    if a.replay_from:
        t0 = dt.datetime.fromisoformat(a.replay_from).astimezone()
        replay = (t0, t0 + dt.timedelta(minutes=a.replay_minutes))
    elif not a.audio:
        sys.exit("give --audio <recording file> or --replay-from <local ISO time>")
    r = lecture_gapfill(folder, audio=a.audio, replay=replay, frm=a.frm, to=a.to, dry_run=a.dry_run,
                        say=(lambda *m: None) if a.json else print)
    if a.json:
        print(json.dumps(r)); return
    if not r["ok"]:
        print("gap fill:", r.get("error") or "nothing filled"); return
    print(f"{r['from']} → {r['to']}: {r['lines']} lines ({r['approx_lines']} approximate), median confidence {r['conf_median']}"
          + ("  [dry run]" if r["dry_run"] else "  → transcript_pre.md; rebuild to use them in the notes"))


def cmd_gaps(a):
    g = lecture_gaps(find_folder(a.id))
    if a.json:
        print(json.dumps({"ok": True, "gaps": g})); return
    for x in g or [{"from": "-", "to": "-", "cause": "none", "minutes": 0}]:
        print(f"{x['from']} → {x['to']}  {x['minutes']} min  {x['cause']}")


# ------------------------------------------------------------- regression gates (EP-2)
TIMECODE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
DOES_NOT_SPECIFY = re.compile(r"\b(?:does not|doesn't|did not|didn't) (?:specify|say|mention|cover)\b|\bnot specified\b", re.I)


def gate_notes_timecodes(notes: str) -> int:
    """Notes paragraphs and bullets of substance (60+ characters, outside headings, the
    'open questions' section and quoted source lists) with no timecode."""
    n, skip = 0, False
    for block in re.split(r"\n\s*\n|\n(?=\s*[-*]\s)", notes):
        b = block.strip()
        if not b:
            continue
        if b.startswith("#"):
            head = b.splitlines()[0].lower()
            skip = any(w in head for w in ("open question", "review", "sources", "further reading"))
            rest = "\n".join(b.splitlines()[1:]).strip()
            if not rest:
                continue
            b = rest
        if skip or b.startswith("_") or b.startswith(">") or b.startswith("|") or len(b) < 60:
            continue
        if not TIMECODE.search(b):
            n += 1
    return n


def gate_title(title: str, candidates: list[str]) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    t = norm(title)
    return bool(t) and any(t == norm(c) or (norm(c) and (t in norm(c) or norm(c) in t)) for c in candidates if c)


def gate_quiz(quiz: list[dict]) -> float | None:
    """Share of quiz items that pass the linter: a question, 3+ distinct options, and an
    answer that is one of them."""
    if not quiz:
        return None
    ok = 0
    for q in quiz:
        opts = [str(o).strip().lower() for o in (q.get("options") or q.get("choices") or [])]
        ans = q.get("answer")
        ans_ok = (isinstance(ans, int) and 0 <= ans < len(opts)) or \
            (isinstance(ans, str) and (ans.strip().lower() in opts or (len(ans.strip()) == 1 and
                                       0 <= ord(ans.strip().lower()) - 97 < len(opts))))
        if (q.get("q") or q.get("question")) and len(opts) >= 3 and len(set(opts)) == len(opts) and ans_ok:
            ok += 1
    return round(ok / len(quiz), 3)


def regression_gates(folder: Path, names_left_n: int) -> list[dict]:
    """Model-free checks with fixed thresholds; each {name, value, threshold, ok}."""
    import cite
    man = json.loads((folder / "manifest.json").read_text())
    notes = (folder / "notes.md").read_text() if (folder / "notes.md").exists() else ""
    gates = [{"name": "names_left", "value": names_left_n, "threshold": 0, "ok": names_left_n == 0}]
    n = gate_notes_timecodes(notes)
    gates.append({"name": "notes_without_timecode", "value": n, "threshold": 0, "ok": n == 0})
    start = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00"))
    live = LECTURES / "live" / start.astimezone().strftime("%Y%m%d")
    drafts = []
    for f in ("questions.jsonl", "qa_followups.jsonl"):
        p = live / f
        drafts += [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
    dns = sum(1 for d in drafts if DOES_NOT_SPECIFY.search(str(d.get("answer") or "")))
    gates.append({"name": "drafts_does_not_specify", "value": dns, "threshold": 0, "ok": dns == 0})
    cal = course_calendar.session_at(start)
    slides = [l for l in (folder / "slides.md").read_text().splitlines() if l.strip() and not l.startswith(("#", "!"))][:1] \
        if (folder / "slides.md").exists() else []
    cands = [cal.get("title") if cal else None, man.get("portal_title")] + slides
    gates.append({"name": "title_from_candidates", "value": man.get("title"), "threshold": "calendar|portal|first slide",
                  "ok": gate_title(man.get("title", ""), cands)})
    qf = folder / "practice_quiz.json"
    if qf.exists():
        try:
            qd = json.loads(qf.read_text())
            items = qd if isinstance(qd, list) else qd.get("questions") or qd.get("items") or []
        except ValueError:
            items = []
        rate = gate_quiz(items)
        gates.append({"name": "quiz_lint_pass_rate", "value": rate, "threshold": 1.0, "ok": rate is None or rate >= 1.0})
    segs = [{"at": m[1], "text": m[2]} for m in (re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)", l)
            for l in ((folder / ("transcript_merged.md" if (folder / "transcript_merged.md").exists() else "transcript.md"))
                      .read_text().splitlines() if (folder / "transcript.md").exists() else [])) if m]
    n_slides = int(man.get("slides") or 0)
    warn = cite.verify_citations(notes, n_slides, segs, duration_s=float(man.get("minutes") or 0) * 60 or None)
    gates.append({"name": "notes_citation_warnings", "value": len(warn), "threshold": 0, "ok": not warn})
    return gates


def cmd_gates(a):
    folder = find_folder(a.id)
    g = regression_gates(folder, 0)
    if a.json:
        print(json.dumps({"ok": True, "lecture": folder.name, "gates": g, "needs_review": any(not x["ok"] for x in g)}))
        return
    for x in g:
        print(f"{'✓' if x['ok'] else '✗'} {x['name']:<26} {x['value']!s:<40} (threshold {x['threshold']})")


def after_blockers() -> list[str]:
    """Why After class should not run now: capture still on, or an eval's judge loaded."""
    why = []
    try:
        r = subprocess.run([str(HERE / "sp-mode"), "--json"], capture_output=True, text=True, timeout=15)
        if json.loads(r.stdout or "{}").get("running"):
            why.append("capture is still running: stop it in the app first")
    except Exception:
        pass
    judge = os.environ.get("OMLX_JUDGE_MODEL", "")
    try:
        st = study.omlx_status() if hasattr(study, "omlx_status") else json.loads(
            urllib.request.urlopen(f"{study.OMLX.rsplit('/v1', 1)[0]}/api/status", timeout=5).read())
        if judge and judge in (st.get("loaded_models") or []):
            why.append("an eval has the judge model loaded")
    except Exception:
        pass
    return why


def cmd_after(a):
    """After class in one go: ingest the meeting app's files (when the lecture is built),
    build, check that no removed attendee name is left in the lecture files, rewrite qa.md.
    Each step is a separate run, so one failure leaves earlier outputs and names the step;
    the report goes to after_class.json and stdout."""
    def out(obj):
        print(json.dumps(obj, ensure_ascii=False))
    why = [] if a.force else after_blockers()
    if why:
        return out({"ok": False, "error": "; ".join(why), "steps": []})
    os.environ["STUDY_LLM_SLOT"] = "build"
    me = [sys.executable, str(HERE / "lecture_kit.py")]
    steps, folder = [], None

    def step(name, args, timeout):
        try:
            r = subprocess.run(me + args, capture_output=True, text=True, timeout=timeout)
            ok = r.returncode == 0
            # the last line that says something: JSON output ends with a lone "}"
            lines = [l for l in ((r.stderr if not ok else r.stdout) or r.stderr).strip().splitlines()
                     if re.search(r"[A-Za-z0-9]", l)]
            note = lines[-1:] or [""]
            steps.append({"name": name, "ok": ok, "note": note[0][:200]})
            return ok, r.stdout
        except subprocess.TimeoutExpired:
            steps.append({"name": name, "ok": False, "note": f"timed out after {timeout // 60} min"})
            return False, ""

    try:
        folder = find_folder(a.id) if a.id != "last" or any(LECTURES.glob("*/manifest.json")) else None
    except SystemExit:
        folder = None
    built_before = folder is not None and a.id != "last"
    if built_before:
        step("ingest", ["ingest", a.id], 1200)
    ok, stdout = step("build", ["build", a.id, "--json"] + (["--since", a.since] if a.since else []), 7200)
    if ok:
        try:
            folder = Path(json.loads(stdout.strip().splitlines()[-1])["folder"])
        except (ValueError, KeyError, IndexError):
            folder = find_folder(a.id)
        if not built_before:
            step("ingest", ["ingest", folder.name], 1200)
        man = json.loads((folder / "manifest.json").read_text())
        day = dt.datetime.fromisoformat(man["start"]).astimezone().strftime("%Y%m%d")
        rev_f = LECTURES / "live" / day / "names_review.json"
        rev = json.loads(rev_f.read_text()) if rev_f.exists() else {"removed": {}, "review": {}}
        vault = VAULT / VAULT_SUB / f"{man.get('id', '')} {man.get('title', '')}.md".replace("/", "-")
        left = names_left(folder, list(rev.get("removed") or []), vault)
        steps.append({"name": "names", "ok": not left,
                      "note": f"{sum(left.values())} line(s) still name an attendee in {', '.join(left)}" if left
                      else f"no removed name left; {len(rev.get('review') or {})} word(s) to review"})
        step("qa", ["qa", folder.name], 300)
        try:
            if (LECTURES / "live" / day / "agenda.txt").exists():
                m = agenda_map(folder)
                steps.append({"name": "agenda", "ok": bool(m.get("ok")), "note": m.get("error") or ", ".join(
                    f"{r['item_id']} {r['status']}" for r in m["parts"]) + (f"; {len(m['unplanned']) * m['window_min']} min unplanned" if m["unplanned"] else "")})
        except Exception as e:
            steps.append({"name": "agenda", "ok": False, "note": f"{type(e).__name__}: {e}"[:200]})
        try:
            pqs = resolve_prequestions(day)
            if pqs:
                steps.append({"name": "prequestions", "ok": True, "note": f"{sum(1 for r in pqs if (r.get('resolved') or {}).get('status') == 'answered')} of {len(pqs)} answered in class"})
        except Exception as e:
            steps.append({"name": "prequestions", "ok": False, "note": f"{type(e).__name__}: {e}"[:200]})
    gates = []
    if folder and (folder / "manifest.json").exists():
        try:
            g = lecture_gaps(folder)
            steps.append({"name": "gaps", "ok": True, "note": f"{len(g)} gap(s)" + (": " + ", ".join(
                f"{x['minutes']} min {x['cause']}" for x in g) if g else "")})
        except Exception as e:
            steps.append({"name": "gaps", "ok": False, "note": f"{type(e).__name__}: {e}"[:200]})
        try:
            left_n = next((int(re.match(r"(\d+)", s_["note"])[1]) for s_ in steps if s_["name"] == "names" and not s_["ok"]), 0)
            gates = regression_gates(folder, left_n)
        except Exception as e:
            gates = [{"name": "gates", "value": f"{type(e).__name__}: {e}"[:200], "threshold": None, "ok": False}]
    report = {"ok": all(s["ok"] for s in steps), "lecture": folder.name if folder else None,
              "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "steps": steps,
              "gates": gates, "needs_review": any(not g_["ok"] for g_ in gates)}
    if folder:
        (folder / "after_class.json").write_text(json.dumps(report, indent=1))
    out(report)


# ---------------------------------------------------------------- names backfill (EP-4)
NAMES_RAW = {"whisper_segments.jsonl", "whisper_segments.jsonl.raw", "side_windows_raw.jsonl", "portal_transcript.txt",
             "wispr_transcript.txt", "state.json", "sync_done.json", "sync_request.json", "evidence.json",
             "names_review.json", "agenda.txt", "preread.txt"}
NAMES_EXT = {".md", ".tsv", ".mmd", ".json", ".jsonl", ".txt"}


def _names_files() -> list[tuple[str, Path]]:
    """Every stored artefact that may quote a transcript, as (group, path): each lecture folder
    (superseded ones too), each live folder's derived files, the recaps and the Anki export,
    and the vault's lecture notes. Raw captures and imports (live transcripts, Q&A panel
    snapshots, platform transcripts, resources/) are inputs and are not rewritten."""
    out = []
    roots = [d for d in LECTURES.iterdir() if d.is_dir() and re.match(r"\d{8}-\d{4}_", d.name)]
    sup = LECTURES / "_superseded"
    roots += [d for d in sup.iterdir() if d.is_dir()] if sup.exists() else []
    for d in sorted(roots):
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d)
            if f.is_file() and (f.suffix in NAMES_EXT or f.name == ".descriptions.json") and rel.parts[0] != "resources" \
                    and f.name not in NAMES_RAW and f.name != "names_audit.json":
                out.append((d.name, f))
    live = LECTURES / "live"
    for d in sorted(live.iterdir()) if live.exists() else []:
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix in NAMES_EXT and f.name not in NAMES_RAW:
                    out.append((f"live/{d.name}", f))
    for f in sorted((LECTURES / "recaps").glob("*.md")) if (LECTURES / "recaps").exists() else []:
        out.append(("recaps", f))
    if (LECTURES / "anki_import.txt").exists():
        out.append(("anki", LECTURES / "anki_import.txt"))
    vault = VAULT / VAULT_SUB
    for f in sorted(vault.rglob("*.md")) if vault.exists() else []:
        out.append(("vault", f))
    return out


HOLD_TOTAL, HOLD_GROUPS = 40, 4   # a spelling match this frequent is more likely staff or a term: review it


def _names_scan(apply: bool = False, hold: set[str] | None = None) -> dict:
    """Dry-run (or apply) the name guard over every artefact. Returns the audit (counts only)
    and the private candidate list (tokens) for the review sheet. A first pass finds spelling
    or cue matches that occur very often across the corpus; those are held for review."""
    import name_guard
    if hold is None:
        _names_scan(apply=False, hold=set())
        cand = json.loads((PRIVATE / "names_candidates.json").read_text())
        hold = {c["token"] for c in cand if c["decision"] == "removed" and c["layer"] not in ("roster-exact", "learner")
                and (c["total"] > HOLD_TOTAL or len(c["groups"]) > HOLD_GROUPS)}
    roster, decisions = _name_roster(), name_guard.load_decisions(PRIVATE)
    allow = _name_allow() | name_guard.staff_names()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = PRIVATE / "names-backup" / stamp
    groups: dict[str, list] = {}
    cand: dict[str, dict] = {}
    changed = 0
    for group, f in _names_files():
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.split("\n")
        out, rep = name_guard.guard(lines, roster=roster, decisions=decisions, allow=allow, hold=hold)
        rem, rev = sum(rep["removed"].values()), sum(rep["review"].values())
        if not rem and not rev:
            continue
        groups.setdefault(group, []).append({"path": str(f), "removed_count": rem, "review_count": rev})
        for e in rep["entries"]:
            c = cand.setdefault(e["token"], {"token": e["token"], "total": 0, "groups": [], "layer": e["layer"],
                                             "score": e["score"], "decision": e["decision"],
                                             "held": e["token"] in hold, "learner": decisions.get(e["token"].lower())})
            if e["decision"] == "removed":    # a token removed anywhere is shown as removed
                c["decision"], c["layer"], c["score"] = "removed", e["layer"], e["score"]
            c["total"] += e["count"]
            if group not in c["groups"]:
                c["groups"].append(group)
        if apply and rem:
            dest = backup / f.relative_to(f.anchor)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            f.write_text("\n".join(out))
            changed += 1
    audit = {"guard_version": name_guard.GUARD_VERSION, "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "roster_names": len(roster), "lectures": [{"lecture_id": g, "files": v} for g, v in sorted(groups.items())],
             "files_with_names": sum(1 for v in groups.values() for x in v if x["removed_count"]),
             "files_with_review": sum(1 for v in groups.values() for x in v if x["review_count"]),
             "removed_total": sum(x["removed_count"] for v in groups.values() for x in v),
             "review_total": sum(x["review_count"] for v in groups.values() for x in v), "held_for_review": len(hold)}
    if apply:
        audit.update({"applied_at": audit["scanned_at"], "files_rewritten": changed,
                      "backup": str(backup) if changed else None})
    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "names_candidates.json").write_text(json.dumps(
        sorted(cand.values(), key=lambda c: (c["decision"], -c["total"])), indent=1, ensure_ascii=False))
    (LECTURES / "names_audit.json").write_text(json.dumps(audit, indent=1))
    return audit


def _capture_running() -> bool:
    try:
        r = subprocess.run([str(HERE / "sp-mode"), "--json"], capture_output=True, text=True, timeout=15)
        return bool(json.loads(r.stdout or "{}").get("running"))
    except Exception:
        return False


def cmd_names(a):
    """Attendee names in stored lectures (EP-4): roster | scan | decide | apply. Output holds
    counts only; tokens go to the private candidate file read by the app's review sheet."""
    import name_guard
    def out(obj):
        print(json.dumps(obj, ensure_ascii=False) if a.json else "\n".join(f"{k}: {v}" for k, v in obj.items()
                                                                          if not isinstance(v, list)))
    if a.action == "roster":
        staff = {x.strip() for x in os.environ.get("LIVE_QA_STAFF", "").split(",") if x.strip()}
        import live_qa
        by_day = {}
        for side in sorted((LECTURES / "live").glob("*/side_windows_raw.jsonl")):
            names = set()
            for l in side.read_text().splitlines():
                if l.strip():
                    for e in live_qa.parse_panel(json.loads(l).get("text", "")):
                        au = e["author"]
                        if au not in staff and not live_qa.YOU.search(au) and not au.lower().startswith("anonymous"):
                            names.add(au)
            by_day[side.parent.name] = names
        return out({"ok": True, **name_guard.update_roster(PRIVATE, by_day)})
    if a.action == "decide":
        d = name_guard.save_decisions(PRIVATE, a.remove or [], a.keep or [])
        return out({"ok": True, "decisions": len(d), "remove": sum(v == "remove" for v in d.values()),
                    "keep": sum(v == "keep" for v in d.values())})
    if a.action == "apply":
        if _capture_running():
            return out({"ok": False, "error": "capture is running: stop it in the app first"})
        audit = _names_scan(apply=True)
        if audit.get("files_rewritten"):
            if (LECTURES / "anki_import.txt").exists():   # the export is rebuilt from the cleaned cards
                import contextlib, io
                with contextlib.redirect_stdout(io.StringIO()):
                    cmd_anki(argparse.Namespace(since="365d", out=None))
            audit = {**_names_scan(apply=False), "applied_at": audit["applied_at"],
                     "files_rewritten": audit["files_rewritten"], "backup": audit["backup"]}
            (LECTURES / "names_audit.json").write_text(json.dumps(audit, indent=1))
        return out({"ok": True, **{k: v for k, v in audit.items() if k != "lectures"}})
    audit = _names_scan(apply=False)
    return out({"ok": True, **{k: v for k, v in audit.items() if k != "lectures"},
                "groups_with_names": sum(1 for g in audit["lectures"] if any(f["removed_count"] for f in g["files"]))})

def cmd_next(a):
    """The next scheduled class from the programme calendar."""
    s = course_calendar.next_session()
    if a.json:
        print(json.dumps(s or {}, ensure_ascii=False)); return
    print(course_calendar.describe(s) if s else "no upcoming class in the calendar (lectures/calendar.json)")


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


def cmd_finish(a):
    folders = [d for d in sorted(LECTURES.iterdir()) if (d / "studio_result.json").exists()] if a.id == "all" else [find_folder(a.id)]
    for folder in folders:
        try:
            note, man = finish(folder)
            print(f"✓ {folder.name}: {man['cards']} cards, {man['slides_described']} slides described · vault: {note}")
        except FileNotFoundError as e:
            print(f"~ {e}")


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
    p.add_argument("--privacy", action="store_true", help="EP-5: local-only and public-repo assertions")
    p.add_argument("--sync-guard", metavar="PUBLIC_REPO", help="with --privacy: refresh the public guard's roster hashes")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("review", help="spaced review of flashcards to a weekly minutes budget (FSRS-6)")
    p.add_argument("action", choices=["today", "grade", "stats", "plan", "settings", "card"], nargs="?", default="today")
    p.add_argument("--card"); p.add_argument("--got"); p.add_argument("--seconds", type=float)
    p.add_argument("--minutes", type=int); p.add_argument("--class-days", dest="class_days")
    p.add_argument("--new-per-week", dest="new_per_week", type=int)
    p.add_argument("--q"); p.add_argument("--a"); p.add_argument("--suspend", action="store_true")
    p.add_argument("--unsuspend", action="store_true"); p.add_argument("--delete", action="store_true")
    p.add_argument("--stats", action="store_true", help="same as the stats action")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_review)
    p = sub.add_parser("mastery", help="unassisted mastery and confidence calibration per module")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_mastery)
    p = sub.add_parser("qa-import", help="use the meeting app's Q&A export: score the live capture, anonymise, feed the build")
    p.add_argument("id"); p.add_argument("export"); p.set_defaults(fn=cmd_qa_import)
    p = sub.add_parser("names", help="attendee names in stored lectures: roster | scan (dry run) | decide | apply")
    p.add_argument("action", choices=["roster", "scan", "decide", "apply"], nargs="?", default="scan")
    p.add_argument("--remove", nargs="*", help="decide: tokens to remove everywhere")
    p.add_argument("--keep", nargs="*", help="decide: tokens to keep (not names)")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_names)
    p = sub.add_parser("after", help="after class: ingest, build, attendee-name check, qa.md (JSON report)")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--since", default="")
    p.add_argument("--force", action="store_true", help="run even while capture or the judge is busy")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_after)
    p = sub.add_parser("curate", help="live class: one round of curated top questions (JSON)")
    p.add_argument("--day", default="", help="live folder day, yyyymmdd (default today)")
    p.add_argument("--title", default="", help="class title (default from the calendar)")
    p.add_argument("-k", type=int, default=5, help="questions per round")
    p.add_argument("--lookback", type=int, default=25, help="minutes of transcript for the first round")
    p.add_argument("--background", action="store_true", help="automatic round: skip if the local model is busy")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_curate)
    p = sub.add_parser("cite", help="citations for an answer shown in the app: sources and lecture moments (JSON)")
    p.add_argument("--t", required=True, help="ISO time, or a Q&A panel time like '11:12 AM' with --day")
    p.add_argument("--text", required=True, help="question and answer text")
    p.add_argument("--sources", default="", help="evidence ids, comma-separated")
    p.add_argument("--day", default="", help="live folder day, yyyymmdd")
    p.add_argument("--window", type=float, default=10.0, help="minutes either side")
    p.add_argument("--answer", default="", help="answer text alone, for the sentence check (default: --text)")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_cite)
    p = sub.add_parser("quiz", help="LS-2: practice quiz from a built lecture"); p.add_argument("id", nargs="?", default="last")
    p.add_argument("-n", type=int, default=10); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_quiz)
    p = sub.add_parser("quiz-lint", help="LS-2: lint practice quiz items (all lectures, or --file)")
    p.add_argument("--file"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_quiz_lint)
    p = sub.add_parser("practice", help="LS-1: attempt-first study sessions (recap, quiz, closed-book) and progress")
    p.add_argument("action", choices=["start", "attempt", "reveal", "hint", "answer", "skip", "next", "progress"])
    p.add_argument("--lecture", default="last"); p.add_argument("--kind", choices=["recap", "quiz", "closed"], default="quiz")
    p.add_argument("--session", default=""); p.add_argument("--text", default=""); p.add_argument("--skip", action="store_true")
    p.add_argument("--choice", type=int, default=-1); p.add_argument("--confidence", default="")
    p.add_argument("-n", type=int, default=5); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_practice)
    p = sub.add_parser("cornell", help="LS-5: Cornell view: notes, cue questions, your summary")
    p.add_argument("action", choices=["show", "build", "summary"], nargs="?", default="show")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--text"); p.set_defaults(fn=cmd_cornell)
    p = sub.add_parser("teachback", help="LS-6: find the planted errors in a colleague's explanation")
    p.add_argument("action", choices=["start", "score", "show", "list"], nargs="?", default="list")
    p.add_argument("--lecture", default="last"); p.add_argument("--section"); p.add_argument("--seed", type=int)
    p.add_argument("--id"); p.add_argument("--marks"); p.add_argument("--corrections")
    p.set_defaults(fn=cmd_teachback)
    p = sub.add_parser("scenes", help="CT-5: classify kept frames (slide, shared document, gallery, break, other)")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--no-vision", action="store_true")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_scenes)
    p = sub.add_parser("scene-labels", help="CT-5: list or correct the labelled keyframes")
    p.add_argument("action", choices=["list", "set"], nargs="?", default="list"); p.add_argument("--id")
    p.add_argument("--label"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_scene_labels)
    p = sub.add_parser("notes-v2", help="NG-3: outline-first notes from verbatim extracts, into notes_v2/")
    p.add_argument("id", nargs="?", default="last"); p.set_defaults(fn=cmd_notes_v2)
    p = sub.add_parser("lanes", help="NG-6: waits and timings per model caller kind (llm_slot_metrics.jsonl)")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_lanes)
    p = sub.add_parser("agenda", help="NG-4: agenda parts covered, partial, moved or skipped, as the class ran")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--no-notes", action="store_true")
    p.add_argument("--cached", action="store_true", help="show the saved map without rebuilding")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_agenda)
    p = sub.add_parser("prequestions", help="LS-4: prequestions before class, resolved after it")
    p.add_argument("action", choices=["make", "show", "guess", "mark", "resolve"]); p.add_argument("--day", default="")
    p.add_argument("--id", default=""); p.add_argument("--text", default=""); p.add_argument("--got-it", dest="got_it", default="")
    p.add_argument("--agenda", default=""); p.add_argument("-k", type=int, default=4); p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_prequestions)
    p = sub.add_parser("judgecard", help="EP-1: rebuild eval/judge_card.json from the labelled calibration sheets")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: __import__("runpy").run_path(str(HERE / "tests" / "evals" / "judge_card.py"),
                                                             run_name="judge_card_cli")["main"](["--json"] if a.json else []))
    p = sub.add_parser("qlabels", help="M-7: label a day's questions (would ask, duplicate, answered in class)")
    p.add_argument("action", choices=["todo", "label"]); p.add_argument("--day", required=True)
    p.add_argument("--i", type=int, default=-1); p.add_argument("--would-ask", dest="would_ask", default="")
    p.add_argument("--duplicate", default=""); p.add_argument("--answered", default="")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_qlabels)
    p = sub.add_parser("capstone", help="capstone copilot: feedback and drills only, never authoring (see capstone_cli.py)")
    p.add_argument("rest", nargs=argparse.REMAINDER); p.set_defaults(fn=lambda a: __import__("capstone_cli").main(a.rest))
    p = sub.add_parser("health", help="capture health: one probe (audio age and level, newest frame) and any alert")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_health)
    p = sub.add_parser("gaps", help="gap ledger for a built lecture (late start, holes) into gaps.json and the manifest")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_gaps)
    p = sub.add_parser("gapfill", help="CT-6: fill a gap from the class recording (audio only); existing lines never change")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--audio", help="recording file (its audio is used)")
    p.add_argument("--replay-from", dest="replay_from", help="local ISO time the recording was replayed on this Mac")
    p.add_argument("--replay-minutes", dest="replay_minutes", type=float, default=240)
    p.add_argument("--from", dest="frm", help="local HH:MM (default: the first unfilled gap)"); p.add_argument("--to")
    p.add_argument("--dry-run", dest="dry_run", action="store_true"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_gapfill)
    p = sub.add_parser("gates", help="the After-class regression gates for a built lecture (model-free)")
    p.add_argument("id", nargs="?", default="last"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_gates)
    p = sub.add_parser("index", help="build the evidence store (lectures/index.sqlite) from guarded text")
    p.add_argument("which", nargs="?", default="all", help="lecture id, its date-time prefix, live/<day>, or all")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_index)
    p = sub.add_parser("search", help="search the evidence store")
    p.add_argument("query"); p.add_argument("--lecture", default="", help="limit to one lecture (id or prefix)")
    p.add_argument("--k", type=int, default=8); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_search)
    p = sub.add_parser("checker", help="citation support checker: calibrate on labelled pairs, or write pairs to label")
    p.add_argument("action", choices=["calibrate", "pairs", "todo", "label"])
    p.add_argument("--i", type=int, default=-1, help="label: position in the todo list")
    p.add_argument("--label", type=int, default=-1, help="label: 1 supports, 0 does not")
    p.add_argument("--pairs", default=str(HERE / "eval" / "golden" / "support_pairs.jsonl"))
    p.add_argument("--day", default="", help="pairs: live folder day, yyyymmdd")
    p.add_argument("--n", type=int, default=120); p.add_argument("--out", default=str(HERE / "eval" / "golden" / "support_pairs_todo.jsonl"))
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_checker)
    p = sub.add_parser("next", help="the next class from the programme calendar"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_next)
    p = sub.add_parser("brief", help="before class: recall points and questions from the agenda and earlier notes")
    p.add_argument("--title", required=True); p.add_argument("--agenda", help="text file with the agenda")
    p.add_argument("--preread", help="text file: pre-read or homework instructions"); p.add_argument("--date", help="YYYYMMDD (default today)")
    p.add_argument("--budget", type=int, default=24000); p.set_defaults(fn=cmd_brief)
    p = sub.add_parser("qa", help="rewrite qa.md from the live folder (uses post-class judge scores when present)")
    p.add_argument("id", nargs="?", default="last"); p.set_defaults(fn=cmd_qa)
    p = sub.add_parser("finish", help="after Lecture Lens Studio ran a lecture (browser mode): vault note and manifest")
    p.add_argument("id", nargs="?", default="last", help="session id, 'last' or 'all'"); p.set_defaults(fn=cmd_finish)
    p = sub.add_parser("ingest", help="after class: use the meeting app's transcript/Q&A export/deck found beside the class")
    p.add_argument("id", nargs="?", default="last"); p.set_defaults(fn=cmd_ingest)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
