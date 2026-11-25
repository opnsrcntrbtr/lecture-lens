"""CT-6 acceptance on a fully captured class: remove a ten-minute window of transcript lines,
fill it back from a "recording" (the class audio joined into one file, trimmed to an odd
start and re-encoded, so its offset is unknown), and measure:
  time error   filled words lined up by order with a per-chunk word-level decode of the same
               window (times from the chunk files); median <= 3 s, 95% within 10 s
  coverage     share of the reference words in the window that came back
  duplicates   word triples at either end of the fill that are already in the neighbouring lines
  unchanged    hash of every pre-existing line (evidence records) before and after the fill
Nothing in the lecture folder is touched: the fill runs on a temporary copy.

  python3 tools/gapfill_bench.py --day 20261031 --holes 05:05,06:20,07:30 [--tempo 1.0]
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import evidence  # noqa: E402
import gapfill  # noqa: E402
import live_asr  # noqa: E402

DEV = "System Audio (output)"
TIMED = evidence.TIMED


def lecture_folder(day: str) -> Path:
    hits = sorted(p for p in (HERE / "lectures").glob(f"{day}-*") if (p / "transcript.md").exists()
                  and "Zoom-Webinar" not in p.name)
    if not hits:
        sys.exit(f"no built lecture for {day}")
    return hits[0]


def chunks_between(data: Path, t0: float, t1: float) -> list[Path]:
    out = []
    for p in sorted(data.glob(f"{DEV}_*.mp4")):
        t = live_asr.file_start(p.name)
        if t and t0 <= t.timestamp() < t1:
            out.append(p)
    return out


def make_recording(chunks: list[Path], out: Path, trim_s: float, tempo: float) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in chunks))
    af = f"atrim=start={trim_s}" + (f",atempo={tempo}" if tempo != 1.0 else "")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-af", af,
                    "-ar", "22050", "-ac", "1", "-c:a", "aac", "-b:a", "48k", str(out)], check=True, timeout=1200)


def reference_words(chunks: list[Path]) -> list[dict]:
    ref = []
    for c in chunks:
        base = live_asr.file_start(c.name).timestamp()
        ws, _ = live_asr.whisper_words(c, None, 0.0)
        ref += [{"w": w["w"], "t": base + w["t"]} for w in ws]
    return ref


def records_hash(folder: Path) -> str:
    recs = [(r["idx"], r["text"], r["t0"]) for r in evidence.lecture_records(folder, lambda x: x)[1]
            if r["kind"] == "t" and not r.get("gapfill")]
    return hashlib.sha256(json.dumps(recs).encode()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="20261031"); ap.add_argument("--holes", default="05:05,06:20,07:30")
    ap.add_argument("--minutes", type=float, default=10); ap.add_argument("--tempo", type=float, default=1.0)
    ap.add_argument("--trim", type=float, default=17.3)
    a = ap.parse_args(argv)
    data = Path(os.path.expanduser("~/.screenpipe/data"))
    src = lecture_folder(a.day)
    man = json.loads((src / "manifest.json").read_text())
    start = dt.datetime.fromisoformat(man["start"].replace("Z", "+00:00"))
    end = start + dt.timedelta(minutes=float(man["minutes"]))
    raw = (src / "transcript.md").read_text().splitlines()
    head, body = raw[:4], [l for l in raw[4:] if TIMED.match(l)]
    line_t = lambda l: start.timestamp() + (lambda m: int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]))(TIMED.match(l))
    results = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        rec = td / "recording.m4a"
        all_chunks = chunks_between(data, start.timestamp() - 60, end.timestamp() + 120)
        t_make = time.time()
        make_recording(all_chunks, rec, a.trim, a.tempo)
        print(f"recording: {len(all_chunks)} chunks, {gapfill.media_length(rec) / 60:.1f} min, "
              f"made in {time.time() - t_make:.0f} s", flush=True)
        for hm in a.holes.split(","):
            h0 = dt.datetime.strptime(f"{start:%Y-%m-%d} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp()
            h1 = h0 + a.minutes * 60
            folder = td / src.name
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir()
            shutil.copy(src / "manifest.json", folder / "manifest.json")
            kept = [l for l in body if not (h0 <= line_t(l) < h1)]
            gone = [l for l in body if h0 <= line_t(l) < h1]
            (folder / "transcript.md").write_text("\n".join(head + kept) + "\n")
            before_hash, md_hash = records_hash(folder), hashlib.sha256((folder / "transcript.md").read_bytes()).hexdigest()
            lines = [(line_t(l), TIMED.match(l)[4]) for l in kept]
            words = gapfill.line_words(lines)
            t_fill = time.time()
            res = gapfill.fill(words, h0, h1, rec, gapfill.media_length(rec), gapfill.whisper_transcribe,
                               guess_offset=start.timestamp(), log=lambda *m: print(*m, flush=True))
            fill_s = time.time() - t_fill
            if res.get("error"):
                results.append({"hole": hm, "error": res["error"]}); continue
            gapfill.write_pre(folder, "bench", start, res, h0, h1, rec.name, lambda xs: xs)
            unchanged = records_hash(folder) == before_hash and \
                hashlib.sha256((folder / "transcript.md").read_bytes()).hexdigest() == md_hash
            # reference: the removed lines' span, decoded chunk by chunk
            g0 = min(line_t(l) for l in gone); g1 = min((line_t(l) for l in kept if line_t(l) >= h1), default=h1)
            ref = [w for w in reference_words(chunks_between(data, g0 - 30, g1 + 30))]
            filled = []
            for ln in res["lines"]:
                ws = ln["text"].split()
                filled += [{"w": w, "t": ln["t"] + o} for w, o in zip(ws, ln["wt"])]
            rn, fn = [gapfill.norm(w["w"]) for w in ref], [gapfill.norm(w["w"]) for w in filled]
            errs, signed, matched = [], [], set()
            for b in difflib.SequenceMatcher(None, rn, fn, autojunk=False).get_matching_blocks():
                for k in range(b.size):
                    d = filled[b.b + k]["t"] - ref[b.a + k]["t"]
                    errs.append(abs(d)); signed.append((ref[b.a + k]["t"], d))
                    matched.add(b.a + k)
            signed.sort()
            thirds = [signed[i * len(signed) // 3:(i + 1) * len(signed) // 3] for i in range(3)]
            bias = [round(sorted(d for _, d in th)[len(th) // 2], 2) if th else None for th in thirds]
            span = [i for i, w in enumerate(ref) if g0 + 2 <= w["t"] < g1 - 2]
            errs.sort()
            q = lambda p: round(errs[min(len(errs) - 1, int(p * (len(errs) - 1)))], 2) if errs else None
            # duplicates: triples at the fill's ends that the neighbouring kept lines already hold
            grams = lambda ws: {tuple(ws[i:i + 3]) for i in range(len(ws) - 2)}
            prev_l = [gapfill.norm(x) for l in kept if line_t(l) < h0 for x in TIMED.match(l)[4].split()][-60:]
            next_l = [gapfill.norm(x) for l in kept if line_t(l) >= h1 for x in TIMED.match(l)[4].split()][:60]
            dup = len(grams(fn[:15]) & grams(prev_l)) + len(grams(fn[-15:]) & grams(next_l))
            r = {"hole": hm, "removed_lines": len(gone), "filled_lines": len(res["lines"]),
                 "approx_lines": sum(1 for l in res["lines"] if l["approx"]),
                 "conf_median": sorted(l["align_conf"] for l in res["lines"])[len(res["lines"]) // 2],
                 "coarse_offset_s": round(res["coarse_offset_s"] - start.timestamp(), 2), "holes": [
                     {k: v for k, v in h.items() if k not in ("from", "to")} for h in res["holes"]],
                 "time_err_median_s": q(.5), "time_err_p95_s": q(.95), "signed_median_by_third_s": bias,
                 "within_10s": round(sum(1 for e in errs if e <= 10) / len(errs), 3) if errs else None,
                 "coverage": round(len(matched & set(span)) / len(span), 3) if span else None,
                 "duplicate_triples": dup, "existing_unchanged": unchanged, "fill_s": round(fill_s, 1)}
            r["ok"] = bool(r["time_err_median_s"] is not None and r["time_err_median_s"] <= 3 and r["within_10s"] >= 0.95
                           and unchanged and dup == 0)
            print(json.dumps(r), flush=True)
            results.append(r)
    rep = {"day": a.day, "tempo": a.tempo, "trim_s": a.trim, "minutes": a.minutes, "results": results,
           "ok": all(r.get("ok") for r in results)}
    p = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-gapfill-bench.json"
    p.write_text(json.dumps(rep, indent=1))
    print(json.dumps({"ok": rep["ok"], "file": p.name}))


if __name__ == "__main__":
    main()
