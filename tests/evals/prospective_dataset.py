"""Build .prospective.json: the learner's questions with the answer drafted before staff
replied (qa_prospective.jsonl) and the staff reply as the expected output.

    python prospective_dataset.py <lectures/live/YYYYMMDD>

The staff reply is real ground truth from the class, so this eval needs no hand labels.
retrieval_context = what was said in the 10 minutes before the question + cited evidence.
"""
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    d = Path(sys.argv[1])
    load = lambda f: [json.loads(l) for l in (d / f).read_text().splitlines() if l.strip()] if (d / f).exists() else []
    segs = load("whisper_segments.jsonl")
    bank = {e["id"]: e for e in json.loads((d / "evidence.json").read_text())} if (d / "evidence.json").exists() else {}
    threads = {t["id"]: t for t in json.loads((d / "qa_threads.json").read_text())}
    cases = []
    for p in load("qa_prospective.jsonl"):
        t = threads.get(p["thread"])
        if not t or not t["answers"]:
            continue
        at = dt.datetime.fromisoformat(p["t"])
        lo = (at - dt.timedelta(minutes=10)).isoformat()
        ctx = [" ".join(s["text"] for s in segs if lo <= s["t"] <= at.isoformat())[-6000:]]
        ctx += [f"{bank[i]['title']}: {bank[i]['fact']}" for i in p.get("sources", []) if i in bank]
        cases.append({"input": t["q"], "actual_output": p["answer"], "confidence": p.get("confidence"),
                      "expected_output": " ".join(a["text"] for a in t["answers"]), "retrieval_context": ctx})
    (HERE / ".prospective.json").write_text(json.dumps(cases, indent=1))
    print(json.dumps({"cases": len(cases)}))


if __name__ == "__main__":
    main()
