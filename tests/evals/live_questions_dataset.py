"""Build .live_questions.json: a sample of the questions and Q&A follow-ups that
live_class.py drafted during a class, each with the context it should rest on.

    python live_questions_dataset.py <lectures/live/YYYYMMDD> [--n 24]

Each case: input = the question (with the thread for follow-ups), actual_output = the
draft answer, retrieval_context = transcript around the question time + the thread's
replies + the evidence facts the draft cites. Deterministic sample (every k-th item).
"""
import argparse
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).parent


def window(segs, t, before=8, after=4):
    at = dt.datetime.fromisoformat(t)
    lo, hi = (at - dt.timedelta(minutes=before)).isoformat(), (at + dt.timedelta(minutes=after)).isoformat()
    return " ".join(s["text"] for s in segs if lo <= s["t"] <= hi)[-6000:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live_dir")
    ap.add_argument("--n", type=int, default=24)
    a = ap.parse_args()
    d = Path(a.live_dir)
    load = lambda f: [json.loads(l) for l in (d / f).read_text().splitlines() if l.strip()] if (d / f).exists() else []
    segs = load("whisper_segments.jsonl")
    bank = {e["id"]: e for e in json.loads((d / "evidence.json").read_text())} if (d / "evidence.json").exists() else {}
    threads = {t["id"]: t for t in json.loads((d / "qa_threads.json").read_text())} if (d / "qa_threads.json").exists() else {}
    cases = []
    for q in load("questions.jsonl"):
        ctx = [window(segs, q["t"], 10, 0)] + [f"{bank[i]['title']}: {bank[i]['fact']}" for i in q.get("sources", []) if i in bank]
        cases.append({"kind": "question", "type": q["type"], "input": q["q"], "actual_output": q["answer"], "retrieval_context": ctx,
                      "context": "Live lecture; question for the Q&A box."})
    for f in load("qa_followups.jsonl"):
        t = threads.get(f["thread"])
        if not t:
            continue
        reply = " / ".join(x["text"] for x in t["answers"])
        ctx = [window(segs, f["t"]), f"Thread question: {t['q']}", f"Staff reply: {reply}"] + \
              [f"{bank[i]['title']}: {bank[i]['fact']}" for i in f.get("sources", []) if i in bank]
        cases.append({"kind": "followup", "type": f["type"], "input": f"Thread: {t['q']}\nFollow-up: {f['q']}",
                      "actual_output": f["answer"], "retrieval_context": ctx, "context": t["q"]})
    qs = [c for c in cases if c["kind"] == "question"]
    fs = [c for c in cases if c["kind"] == "followup"]
    pick = lambda xs, k: xs[:: max(1, len(xs) // max(1, k))][:k]
    sample = pick(qs, a.n // 3) + pick(fs, a.n - a.n // 3)
    (HERE / ".live_questions.json").write_text(json.dumps(sample, indent=1))
    print(json.dumps({"questions": len(qs), "followups": len(fs), "sampled": len(sample)}))


if __name__ == "__main__":
    main()
