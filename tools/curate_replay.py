"""Replay a past live class through curated-question rounds (NG-5 acceptance).

Copies the day's live inputs to a temporary folder (the real folder is never written), runs
a round every --every minutes from --from to --to with the local model, and reports: drafts
matching the "does not specify" pattern (target 0), the duplicate rate across all rounds'
questions at the dedupe threshold (target <= 2%), ask vs self-check counts, and seconds per
round. Nothing in the report quotes the class.

  python3 tools/curate_replay.py --day 20261114 --from 05:50 --to 08:20 [--every 25] [--version 2]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
DNS = re.compile(r"\b(?:does not|doesn't|did not|didn't) (?:specify|say|mention|cover)\b|\bnot specified\b", re.I)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True); ap.add_argument("--from", dest="frm", required=True, help="UTC HH:MM")
    ap.add_argument("--to", required=True, help="UTC HH:MM"); ap.add_argument("--every", type=int, default=25)
    ap.add_argument("--version", type=int, default=2); ap.add_argument("-k", type=int, default=5)
    a = ap.parse_args(argv)
    import curate, study
    import lecture_kit as kit
    src = kit.LECTURES / "live" / a.day
    day = dt.datetime.strptime(a.day, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
    hm = lambda s: day.replace(hour=int(s[:2]), minute=int(s[3:5]))
    sys_q = ("You prepare questions for a learner in a live class from quoted class lines. "
             "Follow the requested JSON format exactly. Never answer the questions.")
    llm = (lambda p: study.llm(sys_q, p, max_tokens=1600, temperature=0.3, slot="build")) if a.version == 2 else \
        (lambda p: study.llm(study.SYS_TUTOR, p, max_tokens=1600, temperature=0.3, slot="build"))
    day_start = day.replace(hour=12).astimezone()
    guard = lambda lines: kit._guard_names({"start": day_start}, lines, report=False)
    title = (kit.course_calendar.session_on(day.date()) or {}).get("title") or "Live class"
    rounds = []
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp) / a.day
        live.mkdir()
        for f in ("whisper_segments.jsonl", "slides.jsonl", "evidence.json", "agenda.txt", "qa_threads.json"):
            if (src / f).exists():
                shutil.copy(src / f, live / f)
        t = hm(a.frm)
        while t <= hm(a.to):
            t0 = time.monotonic()
            run = curate.run_v2 if a.version == 2 else curate.run
            r = run(live, llm, t, title, guard=guard, k=a.k)
            rounds.append({"at": t.isoformat(), "added": r.get("added", 0), "note": r.get("note"),
                           "seconds": round(time.monotonic() - t0, 1)})
            print(rounds[-1], flush=True)
            t += dt.timedelta(minutes=a.every)
        rows = [r for r in curate.curated(curate.load_jsonl(live / "questions.jsonl"))]
    qs = [r["q"] for r in rows]
    dups = sum(1 for i, q in enumerate(qs) if any(curate.repeats(q, p) for p in qs[:i]))
    rep = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "day": a.day, "version": a.version,
           "model": study.MODEL, "rounds": rounds, "questions": len(rows),
           "drafts_does_not_specify": sum(1 for r in rows if DNS.search(str(r.get("answer") or ""))),
           "duplicate_rate": round(dups / len(qs), 3) if qs else None,
           "ask": sum(1 for r in rows if r.get("pile") == "ask"),
           "self_check": sum(1 for r in rows if r.get("pile") == "self_check"),
           "levels": {lv: sum(1 for r in rows if r.get("requested_level") == lv) for lv in curate.LEVELS},
           "median_round_s": sorted(x["seconds"] for x in rounds)[len(rounds) // 2] if rounds else None}
    out = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-curate-replay-v{a.version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if k != "rounds"}))
    print("report:", out)


if __name__ == "__main__":
    main()
