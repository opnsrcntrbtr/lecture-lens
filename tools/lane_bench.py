"""NG-6 measurements on this Mac (M-6 timings for the lanes):

1. Ask's time to first token (wait + prefill), idle, over n runs.
2. The same with a batch job running (a loop of 'build' calls), where Ask is interactive and
   so goes ahead of the next batch call (it still waits for the call in flight: the server
   cannot be pre-empted). Acceptance: p95 under load at most 1.5 x idle. The first idle call
   is a warm-up and is not counted.
3. Prefix reuse: two calls sharing a long transcript prefix, differing only in the task at
   the end. Acceptance: the second call's prefill at most 30% of the first's.

Streams replies (STUDY_LLM_STREAM=1) so prefill is measured. Writes eval/results/<ts>-lanes.json.
  python3 tools/lane_bench.py [-n 5] [--lecture FOLDER]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
os.environ["STUDY_LLM_STREAM"] = "1"
import study  # noqa: E402

ASK = ("You answer questions about a lecture briefly.", "In one sentence: why does an AI product need a fallback plan?")


def ask_once() -> float:
    """Wall time from the call to the first token: wait for the lane plus prefill."""
    t0 = time.time()
    with study.llm_slot("interactive"):
        t_in = time.time()
        study._llm(ASK[0], ASK[1], 40, 0.0)
        pre = study._LAST.get("prefill_s") or 0.0
    return round((t_in - t0) + pre, 3)


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--lecture", default=None)
    a = ap.parse_args(argv)
    ask_once()                                   # warm-up: the first call fills the prefix cache
    idle = [ask_once() for _ in range(a.n)]
    stop = threading.Event()

    def batch():
        while not stop.is_set():
            try:
                study.llm("You summarise.", "Summarise in 150 words why evaluation sets matter for AI products. " * 3,
                          max_tokens=200, temperature=0.3, slot="build")
            except Exception:
                time.sleep(0.5)

    th = threading.Thread(target=batch, daemon=True)
    th.start()
    time.sleep(2.0)
    loaded = []
    for _ in range(a.n):
        loaded.append(ask_once())
        time.sleep(1.0)
    stop.set()
    th.join(timeout=120)
    # prefix reuse on a real transcript prefix (guarded lines of the newest lecture)
    import lecture_kit as lk
    folder = Path(a.lecture) if a.lecture else sorted(p for p in lk.LECTURES.glob("2*") if (p / "notes.md").exists())[-1]
    text = "\n".join(l["text"] for l in lk._lecture_lines(folder) if "/t/" in l["line_id"])[:24000]
    sysm = f"You study a lecture transcript. Run {os.urandom(4).hex()}."   # a fresh prefix, shared by both calls
    pres = []
    for task in ("TASK: list three key terms.", "TASK: name one example used."):
        with study.llm_slot("build"):
            study._llm(sysm, f"TRANSCRIPT:\n{text}\n\n{task}", 30, 0.0)
            pres.append(study._LAST.get("prefill_s"))
    rep = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": study.MODEL,
           "ask_idle_s": idle, "ask_loaded_s": loaded, "p95_idle": p95(idle), "p95_loaded": p95(loaded),
           "median_idle": sorted(idle)[len(idle) // 2], "median_loaded": sorted(loaded)[len(loaded) // 2],
           "ratio": round(p95(loaded) / max(1e-3, p95(idle)), 2), "prefix_prefill_s": pres,
           "prefix_ratio": round(pres[1] / pres[0], 2) if all(pres) else None, "prefix_chars": len(text)}
    rep["lanes_ok"] = rep["ratio"] <= 1.5
    rep["prefix_ok"] = rep["prefix_ratio"] is not None and rep["prefix_ratio"] <= 0.3
    out = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-lanes.json"
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
