"""One evidence store for every feature (NG-1): lecture lines with stable IDs, grouped into
passages, searchable by words (BM25 over SQLite FTS5) and, when an embedding model is
configured, by meaning; the two are fused by reciprocal rank.

Every line is passed through the caller's name guard before it is stored, so the index
never holds an attendee name. Stdlib only. The dense leg is optional and off unless the
caller supplies an embed function (lecture_kit wires it to STUDY_EMBED_MODEL).

Tables (lectures/index.sqlite):
  lines(line_id, lecture_id, chunk_id, source, idx, t0, t_abs, text)
  chunks(chunk_id, lecture_id, source, line_ids, t0, t1, speaker, context, text, text_sha)
  chunks_fts(chunk_id, context, text)          -- FTS5, porter stemming
  vectors(chunk_id, model, dim, vec)           -- float32 blobs, only with an embedder
  meta(key, value)                             -- embed_model, guard_version, built_at

Line IDs are "<key>/<kind>/<n>", e.g. "20261114-1000.3f2a/t/57": key is the lecture
folder's date-time prefix and a short hash of its name (or "live-<day>"), kind is t (transcript), s (slide) or e (evidence), and
n is the line's position in its guarded source. They stay the same while the source file
is unchanged; text_sha shows when it changed.
"""
from __future__ import annotations

import array
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Callable, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines(line_id TEXT PRIMARY KEY, lecture_id TEXT, chunk_id TEXT, source TEXT,
  idx INTEGER, t0 REAL, t_abs TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS lines_lecture ON lines(lecture_id);
CREATE INDEX IF NOT EXISTS lines_chunk ON lines(chunk_id);
CREATE TABLE IF NOT EXISTS chunks(chunk_id TEXT PRIMARY KEY, lecture_id TEXT, source TEXT, line_ids TEXT,
  t0 REAL, t1 REAL, t_abs0 TEXT, t_abs1 TEXT, speaker TEXT, context TEXT, text TEXT, text_sha TEXT);
CREATE INDEX IF NOT EXISTS chunks_lecture ON chunks(lecture_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, context, text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS vectors(chunk_id TEXT, model TEXT, dim INTEGER, vec BLOB, PRIMARY KEY(chunk_id, model));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

TARGET_WORDS = 220        # about 300 tokens per passage
MAX_WORDS = 300
GAP_S = 120.0             # a silence this long starts a new passage
RRF_K = 60
TIMED = re.compile(r"^\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(.*)")
SLIDE_HEAD = re.compile(r"^##\s+(\d+)\.\s+at\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")
SPEAKER = re.compile(r"^\(([^)]{1,20})\)\s*")
QWORD = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}|\d+(?:\.\d+)?")
STOP = {"the", "and", "for", "are", "but", "not", "you", "your", "with", "that", "this", "from", "have", "what",
        "when", "which", "should", "would", "could", "about", "there", "their", "they", "them", "then", "than",
        "into", "will", "were", "been", "does", "also", "only", "more", "most", "some", "such", "each", "very",
        "just", "like", "right", "okay", "yeah", "know", "think", "going", "want", "need", "make", "here",
        "where", "other", "these", "those", "said", "says", "how", "why", "who", "can", "its", "our", "was",
        "has", "had", "any", "all", "one", "two", "use", "used", "get", "got", "let", "say", "see", "way"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def connect(path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def lecture_key(lecture_id: str) -> str:
    """'20261114-1000_Title' -> '20261114-1000.3f2a' (the folder's date-time prefix plus a
    short hash of its full name, since two captures can start in the same minute);
    'live/20261114' -> 'live-20261114'."""
    if lecture_id.startswith("live/"):
        return "live-" + lecture_id.split("/", 1)[1]
    return lecture_id.split("_", 1)[0] + "." + sha(lecture_id)[:4]


def _clock(s: float | None) -> str:
    if s is None:
        return ""
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------------ sources
def lecture_records(folder: Path, guard: Callable[[list[str]], list[str]]) -> tuple[str, list[dict]]:
    """Guarded line records of one built lecture folder: transcript (merged when present),
    then slide descriptions. Returns (title, records)."""
    title = folder.name.split("_", 1)[-1].replace("-", " ")
    start, minutes = None, 0.0
    man = folder / "manifest.json"
    if man.exists():
        try:
            m = json.loads(man.read_text())
            title = m.get("title") or title
            start = dt.datetime.fromisoformat(str(m.get("start")).replace("Z", "+00:00"))
            minutes = float(m.get("minutes") or 0)
        except Exception:
            pass
    recs: list[dict] = []
    tr = folder / ("transcript_merged.md" if (folder / "transcript_merged.md").exists() else "transcript.md")
    if tr.exists():
        raw = [l for l in tr.read_text().splitlines() if TIMED.match(l)]
        lines = [(TIMED.match(l), l) for l in guard(raw)]
        offs = [int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) for m, _ in lines if m]
        # a merged reference transcript can carry wall-clock times (10:19:24) instead of offsets
        clock = bool(start and offs and max(offs) > minutes * 60 + 900 and min(offs) >= start.astimezone().hour * 3600 - 3600)
        for i, (m, line) in enumerate(lines):
            if not m:
                continue
            off = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if clock:
                local = start.astimezone()
                t_abs = local.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(seconds=off)
                off = (t_abs - start).total_seconds()
            else:
                t_abs = start + dt.timedelta(seconds=off) if start else None
            recs.append({"kind": "t", "idx": i, "t0": float(off), "text": m[4].strip(),
                         "t_abs": t_abs.isoformat() if t_abs else None})
    sm = folder / "slides.md"
    if sm.exists():
        blocks, cur = [], None
        for line in sm.read_text().splitlines():
            h = SLIDE_HEAD.match(line)
            if h:
                cur = {"n": int(h[1]), "t0": float(int(h[2]) * 3600 + int(h[3]) * 60 + int(h[4])), "body": [], "file": None}
                blocks.append(cur)
            elif cur is not None:
                f = re.match(r"^!\[\[([^\]]+)\]\]", line.strip())
                if f:
                    cur["file"] = f[1]
                elif line.strip():
                    cur["body"].append(line.strip().lstrip("*-# ").strip())
        texts = guard([" ".join(b["body"])[:1500] for b in blocks])
        for b, text in zip(blocks, texts):
            if text.strip():
                recs.append({"kind": "s", "idx": b["n"], "t0": b["t0"], "text": text, "slide_file": b["file"],
                             "t_abs": (start + dt.timedelta(seconds=b["t0"])).isoformat() if start else None})
    return title, recs


def live_records(folder: Path, guard: Callable[[list[str]], list[str]]) -> tuple[str, list[dict]]:
    """Guarded records of one live-class folder: whisper lines, on-screen slides, and the
    day's evidence bank (public facts)."""
    load = lambda f: [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
    segs = [s for s in load(folder / "whisper_segments.jsonl") if s.get("text")]
    t_first = None
    recs: list[dict] = []
    if segs:
        times = [_parse(s.get("t")) for s in segs]
        t_first = min((t for t in times if t), default=None)
        texts = guard([s["text"].strip() for s in segs])
        for i, (s, t, text) in enumerate(zip(segs, times, texts)):
            recs.append({"kind": "t", "idx": i, "t0": (t - t_first).total_seconds() if t and t_first else None,
                         "t_abs": t.isoformat() if t else None, "text": text})
    slides = load(folder / "slides.jsonl")
    if slides:
        texts = guard([f"{s.get('title', '')} :: {s.get('text', '')}"[:1500] for s in slides])
        for i, (s, text) in enumerate(zip(slides, texts)):
            t = _parse(s.get("t"))
            if (s.get("kind") or "slide") != "slide":
                continue
            recs.append({"kind": "s", "idx": i, "t0": (t - t_first).total_seconds() if t and t_first else None,
                         "t_abs": t.isoformat() if t else None, "text": text})
    ev = folder / "evidence.json"
    if ev.exists():
        try:
            bank = json.loads(ev.read_text())
            texts = guard([f"{e.get('title', '')}: {e.get('fact', '')}" for e in bank])
            for e, text in zip(bank, texts):
                recs.append({"kind": "e", "idx": e["id"], "t0": None, "t_abs": None, "text": text, "url": e.get("url")})
        except Exception:
            pass
    return f"Live class {folder.name}", recs


def _parse(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ------------------------------------------------------------------ chunking
def chunk(records: list[dict], key: str, title: str, boundaries: Iterable[float] = ()) -> list[dict]:
    """Passages of consecutive transcript lines (about TARGET_WORDS words), split at slide
    boundaries and long silences; each slide and evidence record is its own passage."""
    bounds = sorted(b for b in boundaries if b is not None)
    slides = sorted((r for r in records if r["kind"] == "s" and r["t0"] is not None), key=lambda r: r["t0"])

    def slide_title(t):
        cur = None
        for s in slides:
            if s["t0"] <= t:
                cur = s
        return (cur["text"].split("::")[0].split(".")[0][:60].strip() if cur else "")

    out: list[dict] = []
    cur: list[dict] = []

    def flush():
        if not cur:
            return
        t0, t1 = cur[0]["t0"], cur[-1]["t0"]
        st = slide_title(t0) if t0 is not None else ""
        ctx = " · ".join(x for x in (title, st, _clock(t0)) if x)
        sp = {SPEAKER.match(r["text"])[1] for r in cur if SPEAKER.match(r["text"])}
        out.append({"chunk_id": f"{key}/t/{cur[0]['idx']}-{cur[-1]['idx']}", "source": "transcript",
                    "lines": list(cur), "t0": t0, "t1": t1, "t_abs0": cur[0].get("t_abs"),
                    "t_abs1": cur[-1].get("t_abs"), "speaker": ",".join(sorted(sp)) or None, "context": ctx})
        cur.clear()

    words = 0
    for r in [r for r in records if r["kind"] == "t"]:
        n = len(r["text"].split())
        crosses = cur and r["t0"] is not None and cur[-1]["t0"] is not None and (
            r["t0"] - cur[-1]["t0"] > GAP_S or any(cur[-1]["t0"] < b <= r["t0"] for b in bounds))
        if cur and (crosses or words >= TARGET_WORDS or words + n > MAX_WORDS):
            flush(); words = 0
        cur.append(r); words += n
    flush()
    for r in records:
        if r["kind"] in ("s", "e"):
            label = "slide" if r["kind"] == "s" else "source"
            ctx = " · ".join(x for x in (title, f"{label} {r['idx']}", _clock(r["t0"])) if x)
            out.append({"chunk_id": f"{key}/{r['kind']}/{r['idx']}", "source": "slide" if r["kind"] == "s" else "evidence",
                        "lines": [r], "t0": r["t0"], "t1": r["t0"], "t_abs0": r.get("t_abs"), "t_abs1": r.get("t_abs"),
                        "speaker": None, "context": ctx})
    return out


# ------------------------------------------------------------------ building
def index_lecture(con: sqlite3.Connection, lecture_id: str, title: str, records: list[dict],
                  boundaries: Iterable[float] = (), embed: Callable[[list[str]], list[list[float]]] | None = None,
                  embed_model: str | None = None, guard_version: str | None = None) -> dict:
    """Replace one lecture's rows. Chunks whose text_sha is unchanged keep their vectors,
    so a rebuild re-embeds nothing that did not change."""
    key = lecture_key(lecture_id)
    seen: dict[tuple, int] = {}
    for r in records:   # a repeated slide number or evidence id still gets its own line ID
        k = (r["kind"], r["idx"])
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            r["idx"] = f"{r['idx']}.{seen[k]}"
    chunks = chunk(records, key, title, boundaries)
    old = {r["chunk_id"]: r["text_sha"] for r in con.execute("SELECT chunk_id, text_sha FROM chunks WHERE lecture_id=?", (lecture_id,))}
    con.execute("DELETE FROM lines WHERE lecture_id=?", (lecture_id,))
    con.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE lecture_id=?)", (lecture_id,))
    con.execute("DELETE FROM chunks WHERE lecture_id=?", (lecture_id,))
    to_embed, kept = [], set()
    for c in chunks:
        text = "\n".join(r["text"] for r in c["lines"])
        h = sha(c["context"] + "\n" + text)
        ids = []
        for r in c["lines"]:
            lid = f"{key}/{r['kind']}/{r['idx']}"
            ids.append(lid)
            con.execute("INSERT OR REPLACE INTO lines VALUES(?,?,?,?,?,?,?,?)",
                        (lid, lecture_id, c["chunk_id"], c["source"], r["idx"] if isinstance(r["idx"], int) else None,
                         r["t0"], r.get("t_abs"), r["text"]))
        con.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (c["chunk_id"], lecture_id, c["source"], json.dumps(ids), c["t0"], c["t1"], c["t_abs0"],
                     c["t_abs1"], c["speaker"], c["context"], text, h))
        con.execute("INSERT INTO chunks_fts(chunk_id, context, text) VALUES(?,?,?)", (c["chunk_id"], c["context"], text))
        if old.get(c["chunk_id"]) == h:
            kept.add(c["chunk_id"])
        else:
            to_embed.append((c["chunk_id"], c["context"] + "\n" + text))
    live_ids = {c["chunk_id"] for c in chunks}
    for cid in set(old) - live_ids:
        con.execute("DELETE FROM vectors WHERE chunk_id=?", (cid,))
    embedded = 0
    if embed and embed_model:
        have = {r[0] for r in con.execute("SELECT chunk_id FROM vectors WHERE model=?", (embed_model,))}
        to_embed += [(cid, None) for cid in kept if cid not in have]
        pending = []
        for cid, txt in to_embed:
            if txt is None:
                row = con.execute("SELECT context, text FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
                txt = row["context"] + "\n" + row["text"]
            pending.append((cid, txt))
        for i in range(0, len(pending), 16):
            batch = pending[i:i + 16]
            vecs = embed([t for _, t in batch])
            for (cid, _), v in zip(batch, vecs):
                con.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)",
                            (cid, embed_model, len(v), array.array("f", _unit(v)).tobytes()))
                embedded += 1
    meta = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    if guard_version:
        meta["guard_version"] = guard_version
    if embed_model:
        meta["embed_model"] = embed_model
    for k, v in meta.items():
        con.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
    con.commit()
    return {"lecture_id": lecture_id, "chunks": len(chunks), "lines": sum(len(c["lines"]) for c in chunks),
            "embedded": embedded, "unchanged": len(kept)}


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ------------------------------------------------------------------ search
def fts_query(text: str) -> str:
    toks = []
    for w in QWORD.findall(text or ""):
        lw = w.lower()
        if lw in STOP or (len(lw) < 3 and not lw[0].isdigit()):
            continue
        if lw not in toks:
            toks.append(lw)
    return " OR ".join(f'"{t}"' for t in toks[:40])


def search(con: sqlite3.Connection, query: str, *, k: int = 8, lectures: list[str] | None = None,
           near: tuple[dt.datetime, float] | None = None, sources: tuple[str, ...] = ("transcript", "slide", "evidence"),
           embed: Callable[[list[str]], list[list[float]]] | None = None, embed_model: str | None = None,
           pool: int = 30) -> list[dict]:
    """Top k passages: BM25 rank fused with cosine rank (when an embedder is given) by RRF.
    `near` keeps timed passages within ±seconds of a time (untimed evidence always passes)."""
    where, args = ["chunks_fts MATCH ?"], [fts_query(query)]
    if args[0] == "":
        return []
    if lectures:
        where.append(f"c.lecture_id IN ({','.join('?' * len(lectures))})"); args += lectures
    where.append(f"c.source IN ({','.join('?' * len(sources))})"); args += list(sources)
    rows = con.execute(
        f"SELECT c.*, bm25(chunks_fts, 0.0, 0.5, 1.0) AS bm FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id "
        f"WHERE {' AND '.join(where)} ORDER BY bm LIMIT ?", args + [pool * 4]).fetchall()
    rows = [r for r in rows if _near_ok(r, near)][:pool]
    lex = {r["chunk_id"]: (i, -r["bm"]) for i, r in enumerate(rows)}
    by = {r["chunk_id"]: r for r in rows}
    dense: dict[str, tuple[int, float]] = {}
    if embed and embed_model:
        qv = _unit(embed([query])[0])
        q = "SELECT v.chunk_id, v.vec FROM vectors v JOIN chunks c ON c.chunk_id = v.chunk_id WHERE v.model=?"
        qa = [embed_model]
        if lectures:
            q += f" AND c.lecture_id IN ({','.join('?' * len(lectures))})"; qa += lectures
        scored = []
        for r in con.execute(q, qa):
            v = array.array("f"); v.frombytes(r["vec"])
            scored.append((sum(a * b for a, b in zip(qv, v)), r["chunk_id"]))
        scored.sort(reverse=True)
        top = []
        for s, cid in scored:
            if cid not in by:
                row = con.execute("SELECT *, 0.0 AS bm FROM chunks WHERE chunk_id=?", (cid,)).fetchone()
                if row is None or row["source"] not in sources or not _near_ok(row, near):
                    continue
                by[cid] = row
            top.append((cid, s))
            if len(top) >= pool:
                break
        dense = {cid: (i, s) for i, (cid, s) in enumerate(top)}
    fused = []
    for cid in by:
        f = 0.0
        if cid in lex: f += 1.0 / (RRF_K + lex[cid][0] + 1)
        if cid in dense: f += 1.0 / (RRF_K + dense[cid][0] + 1)
        fused.append((f, cid))
    fused.sort(reverse=True)
    out = []
    for f, cid in fused[:k]:
        r = by[cid]
        out.append({"chunk_id": cid, "lecture_id": r["lecture_id"], "source": r["source"],
                    "line_ids": json.loads(r["line_ids"]), "t0": r["t0"], "t_abs": r["t_abs0"],
                    "context": r["context"], "text": r["text"],
                    "score_lex": round(lex[cid][1], 4) if cid in lex else None,
                    "score_dense": round(dense[cid][1], 4) if cid in dense else None,
                    "score_rerank": None, "score": round(f, 6)})
    return out


def _near_ok(row, near) -> bool:
    if not near:
        return True
    at, window_s = near
    if row["t_abs0"] is None:
        return row["source"] == "evidence"
    a0, a1 = _parse(row["t_abs0"]), _parse(row["t_abs1"] or row["t_abs0"])
    if a0 is None:
        return False
    return a0 - dt.timedelta(seconds=window_s) <= at <= (a1 or a0) + dt.timedelta(seconds=window_s)


def lines_of(con: sqlite3.Connection, line_ids: list[str]) -> list[dict]:
    if not line_ids:
        return []
    rows = con.execute(f"SELECT * FROM lines WHERE line_id IN ({','.join('?' * len(line_ids))})", line_ids).fetchall()
    by = {r["line_id"]: dict(r) for r in rows}
    return [by[i] for i in line_ids if i in by]
