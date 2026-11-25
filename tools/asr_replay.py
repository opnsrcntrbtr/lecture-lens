"""M-3 / CT-2 replay: feed a stored class's audio chunks at real-time pace into the
event-driven second pass, with a model build job running, then measure:
  lag            chunk close to its lines written (median, p95); acceptance <= 40 s / 60 s
  edge words     of the words within 1 s of a chunk edge in a whole-file offline transcript,
                 the share missing from the streamed lines (same word within 1.5 s), beside the
                 same share for words far from any edge (the two decodes differ everywhere);
                 acceptance: at most 2 points more at the edges
  duplicates     the same word written twice within 0.5 s; acceptance 0
It also reports what the timer design would have given for the same chunks (chunk length,
grace, half the interval, batch time), from the class's own log when present.

  python3 tools/asr_replay.py --day 20261031 --from 05:20 --minutes 15 [--speed 1] [--no-load]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import live_asr  # noqa: E402

DEV = "System Audio (output)"
norm = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())


def same(a: str, b: str) -> bool:
    """One word, allowing for split or joined spellings ("chat GPT" / "ChatGPT")."""
    a, b = norm(a), norm(b)
    return bool(a) and (a == b or (min(len(a), len(b)) >= 3 and (a in b or b in a)))


def pick(data: Path, day: str, frm: str, minutes: int) -> list[Path]:
    d = dt.datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%d")
    t0 = dt.datetime.strptime(f"{d} {frm}", "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(minutes=minutes)
    out = []
    for p in sorted(data.glob(f"{DEV}_{d}_*.mp4")):
        t = live_asr.file_start(p.name)
        if t and t0 <= t < t1:
            out.append(p)
    return out


def offline_words(chunks: list[Path]) -> list[dict]:
    """Whole-slice transcript in one pass (the reference for chunk-edge words)."""
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        lst = Path(td) / "l.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in chunks))
        big = Path(td) / "all.m4a"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(big)],
                       check=True, timeout=300)
        words, _ = live_asr.whisper_words(big, None, 0.0)
    base = live_asr.file_start(chunks[0].name).timestamp()
    return [{"w": w["w"], "t": base + w["t"]} for w in words]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20261031"); ap.add_argument("--from", dest="frm", default="05:20")
    ap.add_argument("--minutes", type=int, default=15); ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--overlap", type=float, default=8.0); ap.add_argument("--hold", type=float, default=3.0)
    ap.add_argument("--pause", type=float, default=0.0, help="hold back from the last pause this long (s)")
    ap.add_argument("--edge-only", action="store_true",
                    help="no pacing: one chunk at a time, as fast as it decodes (edge quality only, no lag)")
    a = ap.parse_args(argv)
    data = Path(os.path.expanduser("~/.screenpipe/data"))
    chunks = pick(data, a.day, a.frm, a.minutes)
    if len(chunks) < 4:
        sys.exit("not enough chunks in that window")
    if a.edge_only:
        a.no_load = True
    stop = threading.Event()
    if not a.no_load:
        import study
        def load():
            while not stop.is_set():
                try:
                    study.llm("You summarise.", "Summarise why evaluation sets matter for AI products. " * 20,
                              max_tokens=300, temperature=0.3, slot="build")
                except Exception:
                    time.sleep(1)
        threading.Thread(target=load, daemon=True).start()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        feed, out = td / "data", td / "out"
        feed.mkdir(); out.mkdir()
        start = live_asr.file_start(chunks[0].name)
        asr = live_asr.EventASR(feed, DEV, start, out, "replay", overlap_s=a.overlap, hold_s=a.hold,
                                pause_s=a.pause, log=lambda *m: print(*m, flush=True))
        closes = {}
        t_wall = time.time()
        k = 0
        if a.edge_only:      # copy2 keeps the old mtime, so each chunk counts as closed at once
            for k, c in enumerate(chunks):
                shutil.copy2(c, feed / c.name)
                closes[c.name] = time.time()
                if k + 1 < len(chunks):
                    shutil.copy2(chunks[k + 1], feed / chunks[k + 1].name)
                asr.poll(final=k == len(chunks) - 1)
        while len(asr.done) < len(chunks):
            el = (time.time() - t_wall) * a.speed
            while k < len(chunks) and el >= (k + 1) * 30.0:       # a chunk appears when it would have closed
                shutil.copy(chunks[k], feed / chunks[k].name)
                closes[chunks[k].name] = time.time()
                k += 1
            asr.poll(final=k == len(chunks))
            time.sleep(1.0)
        asr.flush()
        stop.set()
        m = [json.loads(l) for l in (out / "asr_metrics.jsonl").read_text().splitlines()]
        lags = sorted(x["done_at"] - closes[x["chunk_id"]] for x in m if x["chunk_id"] in closes)
        rows = [json.loads(l) for l in (out / "transcript.jsonl").read_text().splitlines()]
    streamed = []
    for r in rows:   # word times from the line's word offsets
        ws = r["text"].split()
        t0 = dt.datetime.fromisoformat(r["t"]).timestamp()
        offs = r.get("wt") or [0.0] * len(ws)
        streamed += [{"w": w, "t": t0 + o} for w, o in zip(ws, offs)]
    ref = offline_words(chunks)
    edges = [live_asr.file_start(c.name).timestamp() for c in chunks[1:]]
    edge_words = [w for w in ref if any(abs(w["t"] - e) <= 1.0 for e in edges) and norm(w["w"])]
    # align the two word sequences (timestamps drift in a long one-pass decode, so match by
    # order, not by time); a reference word is found when it lies in a matching block
    import difflib
    rn, sn = [norm(w["w"]) for w in ref], [norm(w["w"]) for w in streamed]
    found = set()
    for b in difflib.SequenceMatcher(None, rn, sn, autojunk=False).get_matching_blocks():
        found.update(range(b.a, b.a + b.size))
    idx = {id(w): i for i, w in enumerate(ref)}
    missing = [w for w in edge_words if idx[id(w)] not in found]
    before = sum(1 for w in missing if any(e - 1.0 <= w["t"] < e for e in edges))
    inner = [w for w in ref if all(abs(w["t"] - e) > 3.0 for e in edges) and norm(w["w"])]
    inner_missing = [w for w in inner if idx[id(w)] not in found]
    def rep_count(ws, win):   # the same word again within the next few words (speech repeats count too)
        return sum(1 for i, s in enumerate(ws) for z in ws[i + 1:i + 3] if norm(s["w"]) and norm(z["w"]) == norm(s["w"])
                   and abs(z["t"] - s["t"]) <= win)
    dups = max(0, rep_count(streamed, 1.0) - rep_count(ref, 1.0))     # beyond what the speaker said twice
    near = lambda ws: [w for w in ws if any(abs(w["t"] - e) <= 2.0 for e in edges)]
    edge_dups = max(0, rep_count(near(streamed), 1.0) - rep_count(near(ref), 1.0))   # the ones stitching could cause
    q = lambda xs, p: round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 1) if xs else None
    rep = {"day": a.day, "from": a.frm, "minutes": a.minutes, "chunks": len(chunks), "speed": a.speed, "load": not a.no_load,
           "overlap_s": a.overlap, "hold_s": a.hold, "pause_s": a.pause, "edge_only": a.edge_only,
           "pass_modes": asr.modes, "edge_missing_before_edge": before, "edge_missing_after_edge": len(missing) - before,
           "lag_median_s": q(lags, .5), "lag_p95_s": q(lags, .95),
           "transcribe_median_s": q(sorted(x["transcribe_s"] for x in m), .5),
           "words_streamed": len(streamed), "words_offline": len(ref), "edge_words": len(edge_words),
           "edge_missing_share": round(len(missing) / len(edge_words), 3) if edge_words else None,
           "interior_missing_share": round(len(inner_missing) / len(inner), 3) if inner else None,
           "duplicates": dups, "duplicates_at_edges": edge_dups, "edge_missing": len(missing),
           "interior_words": len(inner), "interior_missing": len(inner_missing), "timer_design_expected_median_s": round(15 + 5 + 120 / 2 + 26)}
    # words the two decodes simply hear differently are missed everywhere; the edge cost is the excess
    rep["edge_excess_points"] = round(100 * ((rep["edge_missing_share"] or 0) - (rep["interior_missing_share"] or 0)), 1)
    rep["ok"] = bool((a.edge_only or rep["lag_median_s"] is not None) and (a.edge_only or (rep["lag_median_s"] <= 40 and rep["lag_p95_s"] <= 60))
                     and rep["edge_excess_points"] <= 2 and dups == 0)
    p = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-asr-replay.json"
    p.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
