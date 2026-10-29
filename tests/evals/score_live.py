"""Judge every drafted question and follow-up a live class produced, after class, so the
Live tab and qa.md can drop the weak ones.

    python score_live.py <lectures/live/YYYYMMDD> [--runs 3]

Items: questions.jsonl and the curated follow-ups (what the Live tab and qa.md show).
Metrics (rubric.py, default rules): Question relevance and Answerability decide pruning;
Faithfulness of the drafted answer only tags it 'unsupported'. Each G-Eval verdict is the
majority of up to `runs` judge calls (two, and a third only when they split); faithfulness
runs once. Resumable: items already scored are skipped.

Writes <live>/live_scores.json and adds "pruned" / "unsupported" (question texts) to
qa_view.json. Needs the judge model loaded (run_live_scores.sh does the swap).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1]))

from live_questions_dataset import window  # noqa: E402


def items(d: Path) -> list[dict]:
    import live_qa
    load = lambda f: [json.loads(l) for l in (d / f).read_text().splitlines() if l.strip()] if (d / f).exists() else []
    segs = load("whisper_segments.jsonl")
    bank = {e["id"]: e for e in json.loads((d / "evidence.json").read_text())} if (d / "evidence.json").exists() else {}
    threads = json.loads((d / "qa_threads.json").read_text()) if (d / "qa_threads.json").exists() else []
    by_id = {t["id"]: t for t in threads}
    out = []
    for q in load("questions.jsonl"):
        ctx = [window(segs, q["t"], 10, 0)] + [f"{bank[i]['title']}: {bank[i]['fact']}" for i in q.get("sources", []) if i in bank]
        out.append({"kind": "question", "q": q["q"], "input": q["q"], "answer": q.get("answer", ""), "ctx": ctx})
    fus = {}
    for f in load("qa_followups.jsonl"):
        fus.setdefault(f["thread"], []).append(f)
    for tid, fl in live_qa.curate_followups(threads, fus).items():
        t = by_id.get(tid)
        if not t:
            continue
        reply = " / ".join(x["text"] for x in t["answers"])
        for f in fl:
            ctx = [window(segs, f["t"]), f"Thread question: {t['q']}", f"Staff reply: {reply}"] + \
                  [f"{bank[i]['title']}: {bank[i]['fact']}" for i in f.get("sources", []) if i in bank]
            out.append({"kind": "followup", "q": f["q"], "input": f"Thread: {t['q']}\nFollow-up: {f['q']}",
                        "answer": f.get("answer", ""), "ctx": ctx})
    return out


def verdict(metric, case, runs: int) -> dict | None:
    votes = []
    for i in range(runs):
        try:
            metric.measure(case)
            votes.append(bool(metric.success))
        except Exception:
            continue
        if i == 1 and len(votes) == 2 and votes[0] == votes[1]:
            break                                   # two agreeing calls decide; a split gets a third
    if not votes:
        return None
    return {"pass": sum(votes) > len(votes) / 2, "votes": f"{sum(votes)}/{len(votes)}", "reason": (metric.reason or "")[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live_dir")
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    import rubric
    from deepeval.test_case import LLMTestCase
    d = Path(a.live_dir)
    out_f = d / "live_scores.json"
    res = json.loads(out_f.read_text()) if out_f.exists() else {"rubric": rubric.VERSION, "items": {}}
    for it in items(d):
        if it["q"] in res["items"]:
            continue
        case = LLMTestCase(input=it["input"], actual_output=it["answer"] or "(no draft)", retrieval_context=it["ctx"])
        rel = verdict(rubric.geval("relevance"), case, a.runs)
        ans = verdict(rubric.geval("answerability"), case, a.runs)
        fai = verdict(rubric.faithfulness(0.7)[0], case, 1) if it["answer"] else None
        if rel is None or ans is None:
            continue
        res["items"][it["q"]] = {"kind": it["kind"], "relevance": rel, "answerability": ans, "faithfulness": fai,
                                 "pruned": not (rel["pass"] and ans["pass"]),
                                 "unsupported": bool(fai) and not fai["pass"]}
        out_f.write_text(json.dumps(res, indent=1, ensure_ascii=False))
        print(it["kind"], res["items"][it["q"]]["pruned"], it["q"][:70], flush=True)
    # let the Live tab hide them
    view_f = d / "qa_view.json"
    view = json.loads(view_f.read_text()) if view_f.exists() else {"threads": [], "followups": {}}
    view["pruned"] = [q for q, v in res["items"].items() if v["pruned"]]
    view["unsupported"] = [q for q, v in res["items"].items() if v["unsupported"]]
    view_f.write_text(json.dumps(view))
    n = len(res["items"])
    print(json.dumps({"scored": n, "pruned": len(view["pruned"]), "unsupported": len(view["unsupported"]), "rubric": res["rubric"]}))


if __name__ == "__main__":
    main()
