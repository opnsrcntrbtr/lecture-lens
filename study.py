#!/usr/bin/env python3
"""study — grounded learning assistant on top of screenpipe + a local oMLX model.

Everything stays on this Mac: captures come from screenpipe (localhost:3030),
answers come from oMLX (OMLX_BASE_URL). Nothing is sent to the internet.

Commands
  study ask "question" [--since 7d] [--app Chrome]      grounded Q&A with citations
  study summarize [--since 3h | --date 2026-09-18]      study notes (Markdown)
  study flashcards [--since 3h | --date ...] [-n 20]    Anki TSV + self-test quiz
  study check                                           health of screenpipe + oMLX

Config (env vars, usually set in ~/.zshrc or .env)
  OMLX_BASE_URL   default http://127.0.0.1:8001/v1
  OMLX_MODEL      default Swift-Qwen3.8-27b-oQ4e-mtp
  OMLX_API_KEY    optional
  SCREENPIPE_URL  default http://localhost:3030
  STUDY_NOTES_DIR default ~/lecture-lens/notes
  STUDY_CTX_CHARS max characters of captured text per LLM call (default 60000 ≈ 15k tokens)
"""
import argparse, datetime as dt, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load_dotenv():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), os.path.expandvars(v.strip().strip('"').strip("'")))
load_dotenv()

OMLX = os.environ.get("OMLX_BASE_URL", "http://127.0.0.1:8001/v1").rstrip("/")
MODEL = os.environ.get("OMLX_MODEL", "Swift-Qwen3.8-27b-oQ4e-mtp")
KEY = os.environ.get("OMLX_API_KEY", "")
# "omlx" (default): the model server above. "browser": Lecture Lens Studio runs the model inside
# Chrome (Gemini Nano or WebGPU); the Python side then does only the deterministic work and leaves
# job files for it (see website/docs/guides/studio.md).
PROVIDER = os.environ.get("STUDY_LLM_PROVIDER", "omlx").strip().lower() or "omlx"


def browser_mode() -> bool:
    return PROVIDER == "browser"
SP = os.environ.get("SCREENPIPE_URL", "http://localhost:3030").rstrip("/")
NOTES = Path(os.path.expanduser(os.environ.get("STUDY_NOTES_DIR", str(HERE / "notes"))))
CTX = int(os.environ.get("STUDY_CTX_CHARS", "60000"))

# ---------------------------------------------------------------- http helpers
# Every call is to this Mac, so ignore any HTTP(S)_PROXY settings (they can turn localhost calls into 403s).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SP_KEY = None
def sp_key():
    """Local API key: $SCREENPIPE_API_KEY, else ask the screenpipe CLI (never stored on disk by us)."""
    global _SP_KEY
    if _SP_KEY is None:
        _SP_KEY = os.environ.get("SCREENPIPE_API_KEY", "")
        if not _SP_KEY:
            import shutil, subprocess
            b = os.path.expandvars(os.environ.get("SP_BIN", "")) or shutil.which("screenpipe") or ""
            try: _SP_KEY = subprocess.run([b, "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip().splitlines()[-1]
            except Exception: _SP_KEY = ""
    return _SP_KEY

def _get(url, timeout=30, auth=False):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {sp_key()}"} if auth and sp_key() else {})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read())

def llm(system, user, max_tokens=2048, temperature=0.3, think=False):
    body = {"model": MODEL, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            # Qwen3-family hint; servers that don't know it ignore it
            "chat_template_kwargs": {"enable_thinking": think}}
    req = urllib.request.Request(f"{OMLX}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {KEY}"} if KEY else {})})
    with _OPENER.open(req, timeout=900) as r:
        out = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()

# ---------------------------------------------------------------- time parsing
def parse_window(since=None, date=None):
    now = dt.datetime.now(dt.timezone.utc)
    if date:
        d = dt.datetime.fromisoformat(date).astimezone()  # local midnight
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(dt.timezone.utc), (start + dt.timedelta(days=1)).astimezone(dt.timezone.utc)
    m = re.fullmatch(r"(\d+)([mhdw])", since or "24h")
    if not m:
        sys.exit("--since must look like 90m, 3h, 7d or 2w")
    n, u = int(m[1]), m[2]
    delta = {"m": dt.timedelta(minutes=n), "h": dt.timedelta(hours=n),
             "d": dt.timedelta(days=n), "w": dt.timedelta(weeks=n)}[u]
    return now - delta, now

def iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")

# ---------------------------------------------------------------- screenpipe
def search(q=None, start=None, end=None, app=None, content_type="all", limit=50, offset=0):
    p = {"content_type": content_type, "limit": limit, "offset": offset}
    if q: p["q"] = q
    if start: p["start_time"] = iso(start)
    if end: p["end_time"] = iso(end)
    if app: p["app_name"] = app
    return _get(f"{SP}/search?{urllib.parse.urlencode(p)}", auth=True).get("data", [])

def normalize(item):
    c, t = item.get("content", {}), item.get("type", "")
    text = (c.get("transcription") or c.get("text") or "").strip()
    if not text:
        return None
    src = "audio" if t == "Audio" else (c.get("text_source") or t.lower())
    where = c.get("browser_url") or c.get("window_name") or c.get("app_name") or c.get("device_name") or ""
    return {"ts": c.get("timestamp", ""), "src": src, "where": where, "app": c.get("app_name", ""),
            "text": re.sub(r"\s+", " ", text)}

def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        if not r: continue
        key = r["text"][:200]
        if key in seen: continue
        seen.add(key); out.append(r)
    return sorted(out, key=lambda r: r["ts"])

def local_time(ts):
    try: return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")
    except Exception: return ts

def fetch_window(start, end, app=None, cap=2000):
    rows, off = [], 0
    while off < cap:
        batch = search(start=start, end=end, app=app, limit=100, offset=off)
        if not batch: break
        rows += [normalize(b) for b in batch]; off += len(batch)
    return dedupe(rows)

def as_sources(rows, per_item=1500):
    return "\n\n".join(f"[{i}] ({local_time(r['ts'])} · {r['src']} · {r['where'][:80]})\n{r['text'][:per_item]}"
                       for i, r in enumerate(rows, 1))

def chunks(rows, budget=CTX, per_item=1500):
    cur, size = [], 0
    for r in rows:
        n = min(len(r["text"]), per_item) + 120
        if cur and size + n > budget:
            yield cur; cur, size = [], 0
        cur.append(r); size += n
    if cur: yield cur

# ---------------------------------------------------------------- commands
SYS_TUTOR = ("You are a rigorous study tutor for an executive AI product management programme. "
             "Use ONLY the numbered sources (the learner's own screen captures and lecture transcripts). "
             "Cite sources inline like [3]. If the sources don't contain the answer, say so plainly and "
             "suggest what to review. Never invent facts.")

ANSWER_INSTRUCTIONS = "Answer clearly, then list 'Sources used' with their timestamps."
PROMPT_VERSION = "ask-v1"


def retrieve(question, start, end, app=None, log=sys.stderr):
    """Keyword retrieval over screenpipe captures. Returns (queries, rows)."""
    kw = llm("Extract search keywords.",
             f"Question: {question}\n\nReturn 3-6 short keyword queries (1-3 words each) that would appear "
             "verbatim in lecture slides or transcripts answering this. One per line, no numbering.",
             max_tokens=120, temperature=0)
    phrases = [q.strip(" -•\"'") for q in kw.splitlines() if q.strip()][:6] or [question]
    # screenpipe full-text search is strict on multi-word input, so also query distinctive single terms
    stop = {"what","when","which","where","does","should","with","from","that","this","have","about","into","over","between","than"}
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", " ".join(phrases) + " " + question) if w.lower() not in stop]
    queries = list(dict.fromkeys(phrases + [w.lower() for w in words]))[:14]
    rows = []
    for q in queries:
        try: rows += [normalize(x) for x in search(q=q, start=start, end=end, app=app, limit=20)]
        except Exception as e: print(f"  search '{q}' failed: {e}", file=log)
    rows = dedupe(rows)
    if len(rows) < 5:  # thin keyword hits: add the most recent captures in the window as context
        rows = dedupe(rows + fetch_window(start, end, app, cap=300)[-60:])
    return queries, rows


RETRIEVAL_VERSION = "bm25-v1"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by for from has have how in is it its of on or that the this "
                  "to was were what when where which who why will with does do did not".split())


def _terms(text):
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def bm25_rank(question, rows, k1=1.5, b=0.75):
    """Order rows by Okapi BM25 relevance to the question, best first.

    BM25 as in Robertson & Zaragoza (2009), "The Probabilistic Relevance
    Framework: BM25 and Beyond", with the non-negative idf
    log(1 + (N - n + 0.5) / (n + 0.5)). The collection is the candidate rows
    themselves, so the score needs no index. Ties keep time order. A row with no
    query term scores 0 and goes last, but is kept: the model may still need it.
    """
    import math
    docs = [_terms(r["text"]) for r in rows]
    if not docs:
        return []
    q = list(dict.fromkeys(_terms(question)))
    N = len(docs); avg = sum(map(len, docs)) / N or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q}
    def score(d):
        s = 0.0
        for t in q:
            f = d.count(t)
            if f:
                idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        return s
    scored = sorted(enumerate(rows), key=lambda ir: (-score(docs[ir[0]]), ir[0]))
    return [r for _, r in scored]


def answer_from(question, rows, think=False):
    """Generation step, separated from retrieval so evals can run it on fixed
    context. Returns (answer, ctx_rows_used, source_block_sent_to_model).

    Rows are ranked by BM25 before packing (bm25-v1). Before this, rows were
    packed in time order, so with many hits the earliest captures filled the
    budget and a relevant late one was dropped. Best source first: models use
    the start of a long context best (Liu et al. 2024, "Lost in the Middle").
    """
    ctx = next(chunks(bm25_rank(question, rows)), [])  # best-effort single window within budget
    src = as_sources(ctx)
    ans = llm(SYS_TUTOR, f"Sources:\n{src}\n\nQuestion: {question}\n\n{ANSWER_INSTRUCTIONS}",
              max_tokens=1500, think=think)
    return ans, ctx, src


_SOURCES_TAIL = re.compile(r"\n[ \t>*#_-]*sources used\b.*\Z", re.I | re.S)
_CITE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")


def answer_body(answer):
    """The answer without the trailing 'Sources used' list the model is asked for."""
    return _SOURCES_TAIL.sub("", answer).rstrip()


def _cited(group):
    out = set()
    for part in re.split(r"\s*,\s*", group):
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part.strip())
        if m:
            lo, hi = int(m[1]), int(m[2])
            out.update(range(lo, hi + 1) if 0 < hi - lo < 50 else (lo, hi))
        else:
            out.add(int(part))
    return out


def check_citations(answer, n_sources):
    """Deterministic checks on an answer's [n] citations; no model.

    After ALCE (Gao et al. 2023, "Enabling Large Language Models to Generate Text
    with Citations"), a citation is part of the answer's correctness: it must
    point at a source the model was given, and a substantive statement should
    carry one. Returns (warnings, coverage) where coverage is the share of
    substantive sentences (6+ words) in the answer body that cite something.
    A plain "the sources don't say" answer is not flagged for having no citation.
    """
    body = answer_body(answer)
    cited = set()
    for g in _CITE.findall(body):
        cited |= _cited(g)
    warnings = []
    bad = sorted(n for n in cited if not 1 <= n <= n_sources)
    if bad:
        warnings.append(f"cites source(s) {', '.join(map(str, bad))} but only {n_sources} were given")
    sentences = [x for x in re.split(r"(?<=[.!?])\s+|\n+", body) if len(re.findall(r"\w+", _CITE.sub("", x))) >= 6]
    with_cite = sum(1 for x in sentences if _CITE.search(x))
    coverage = round(with_cite / len(sentences), 3) if sentences else None
    refusal = re.search(r"(sources|captures|lecture|material)s? (do not|don't|does not|doesn't) (contain|say|cover|mention)", body, re.I)
    if sentences and not with_cite and not refusal:
        warnings.append("the answer cites no source")
    return warnings, coverage


def cmd_ask(a):
    import time
    t0 = time.time()
    start, end = parse_window(a.since, a.date)
    try:
        queries, rows = retrieve(a.question, start, end, a.app)
    except (urllib.error.URLError, ConnectionError) as e:
        msg = f"screenpipe is not reachable at {SP} ({getattr(e, 'reason', e)}) — start capture first"
        if a.json:
            print(json.dumps({"ok": False, "error": msg})); sys.exit(1)
        sys.exit(msg)
    if not rows:
        if a.json:
            print(json.dumps({"ok": False, "error": "no captures matched", "queries": queries})); sys.exit(1)
        sys.exit(f"No captures matched {queries} in that window. Try --since 30d or check `study check`.")
    ans, ctx, _ = answer_from(a.question, rows, a.think)
    warns, coverage = check_citations(ans, len(ctx))
    if a.json:
        print(json.dumps({"ok": True, "question": a.question, "answer": ans, "queries": queries,
                          "sources": [{"n": i, "ts": r["ts"], "src": r["src"], "where": r["where"],
                                       "text": r["text"][:1500]} for i, r in enumerate(ctx, 1)],
                          "citation_warnings": warns, "citation_coverage": coverage,
                          "model": MODEL, "prompt_version": PROMPT_VERSION,
                          "latency_s": round(time.time() - t0, 2)}))
        return
    print(f"  (searched {len(queries)} terms → {len(rows)} captures, using {len(ctx)})\n", file=sys.stderr)
    print(ans)
    for w in warns: print(f"  ⚠ citation check: {w}", file=sys.stderr)
    legend = "\n".join(f"[{i}] {local_time(r['ts'])} · {r['where'][:90]}" for i, r in enumerate(ctx, 1))
    if a.show_sources: print("\n---\n" + legend)

def _notes_path(kind, start, end):
    NOTES.mkdir(parents=True, exist_ok=True)
    s = start.astimezone().strftime("%Y-%m-%d_%H%M"); e = end.astimezone().strftime("%H%M")
    return NOTES / f"{s}-{e}_{kind}.md"

def cmd_summarize(a):
    start, end = parse_window(a.since, a.date)
    rows = fetch_window(start, end, a.app)
    if not rows: sys.exit("Nothing captured in that window.")
    parts = list(chunks(rows))
    print(f"  {len(rows)} captures → {len(parts)} chunk(s)", file=sys.stderr)
    partials = []
    for i, part in enumerate(parts, 1):
        print(f"  summarising chunk {i}/{len(parts)}…", file=sys.stderr)
        partials.append(llm(SYS_TUTOR, f"Sources:\n{as_sources(part)}\n\nWrite dense study notes for this "
            "segment: topics covered, key concepts with definitions, frameworks/models, examples & numbers "
            "mentioned, instructor emphasis, open questions. Cite sources [n]. Ignore UI chrome/noise.",
            max_tokens=2000))
    final = partials[0] if len(partials) == 1 else llm(
        "You merge partial study notes into one clean document. Keep facts, drop duplication.",
        "\n\n---\n\n".join(partials) + "\n\nMerge into final notes with sections: ## TL;DR (5 bullets), "
        "## Key concepts, ## Frameworks & models, ## Examples & numbers, ## How this applies to building AI "
        "products, ## Open questions to review.", max_tokens=3000)
    hdr = f"# Study notes · {start.astimezone():%a %d %b %Y %H:%M} – {end.astimezone():%H:%M}\n\n_Generated locally by {MODEL} from {len(rows)} screenpipe captures._\n\n"
    p = _notes_path("notes", start, end); p.write_text(hdr + final + "\n")
    print(final); print(f"\n✓ saved {p}", file=sys.stderr)

def cmd_flashcards(a):
    start, end = parse_window(a.since, a.date)
    rows = fetch_window(start, end, a.app)
    if not rows: sys.exit("Nothing captured in that window.")
    parts = list(chunks(rows)); per = max(3, a.n // len(parts))
    cards = []
    for i, part in enumerate(parts, 1):
        print(f"  generating cards {i}/{len(parts)}…", file=sys.stderr)
        raw = llm(SYS_TUTOR, f"Sources:\n{as_sources(part)}\n\nCreate {per} high-quality flashcards testing "
            "understanding (not trivia): definitions, why/when, trade-offs, application to AI products. "
            'Return ONLY a JSON array: [{"q": "...", "a": "...", "src": [n]}].', max_tokens=2500, temperature=0.2)
        m = re.search(r"\[.*\]", raw, re.S)
        try: cards += json.loads(m[0]) if m else []
        except json.JSONDecodeError: print("  (skipped malformed chunk output)", file=sys.stderr)
    if not cards: sys.exit("Model returned no cards.")
    cards = cards[: a.n]
    tsv = _notes_path("anki", start, end).with_suffix(".tsv")
    tsv.write_text("\n".join(f"{c['q'].replace(chr(9),' ')}\t{c['a'].replace(chr(9),' ')}" for c in cards) + "\n")
    quiz = _notes_path("quiz", start, end)
    quiz.write_text("# Self-test\n\n" + "\n".join(
        f"{i}. **{c['q']}**\n   <details><summary>Answer</summary>\n\n   {c['a']}\n   </details>\n"
        for i, c in enumerate(cards, 1)))
    for i, c in enumerate(cards, 1): print(f"{i}. {c['q']}\n   → {c['a']}\n")
    print(f"✓ Anki import file: {tsv}\n✓ Quiz: {quiz}", file=sys.stderr)

def cmd_check(a):
    ok = True
    try:
        h = _get(f"{SP}/health", timeout=5); print(f"screenpipe  ✓ {SP}  status={h.get('status')}  vision={h.get('frame_status')}  audio={h.get('audio_status')}")
        _get(f"{SP}/search?limit=1", auth=True); print("            ✓ search API authorised")
    except Exception as e: ok = False; print(f"screenpipe  ✗ {SP}  ({e}) — is `sp-start` running?")
    if browser_mode():
        print("model       ~ STUDY_LLM_PROVIDER=browser: notes, cards and answers run in Lecture Lens Studio (Chrome)")
        sys.exit(0 if ok else 1)
    try:
        ids = [m["id"] for m in _get(f"{OMLX}/models", timeout=5)["data"]]
        print(f"oMLX        ✓ {OMLX}  model={MODEL} {'(loaded)' if MODEL in ids else '(NOT in /models!)'}")
        ok &= MODEL in ids
    except Exception as e: ok = False; print(f"oMLX        ✗ {OMLX}  ({e})")
    sys.exit(0 if ok else 1)

def main():
    ap = argparse.ArgumentParser(prog="study", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def win(p, default="24h"):
        p.add_argument("--since", default=default, help="lookback e.g. 90m, 3h, 7d (default %(default)s)")
        p.add_argument("--date", help="a whole local day, YYYY-MM-DD (overrides --since)")
        p.add_argument("--app", help="only this app, e.g. 'Google Chrome'")
    p = sub.add_parser("ask"); p.add_argument("question"); win(p, "30d")
    p.add_argument("--think", action="store_true", help="let the model reason first (slower)")
    p.add_argument("--show-sources", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable output for the app")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("summarize"); win(p, "3h"); p.set_defaults(fn=cmd_summarize)
    p = sub.add_parser("flashcards"); win(p, "3h"); p.add_argument("-n", type=int, default=20); p.set_defaults(fn=cmd_flashcards)
    p = sub.add_parser("check"); p.set_defaults(fn=cmd_check)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
