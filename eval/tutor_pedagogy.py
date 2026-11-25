"""Judge-free pedagogy checks for the guided tutor (tutor_session.py), on your real lectures.

Scripted students run real episodes in a throwaway practice folder (your record is untouched):
  pressure       asks for the answer instead of trying ("just tell me the answer")
  misconception  gives wrong answers and climbs all three hint rungs
  quick          answers correctly first time
Checks (no model judge; small local judges disagree with people, MRBench NAACL 2025):
  key_hidden      the committed answer never appears in what the app sees before a reveal is allowed
  leak_free       no hint repeats most of the key's terms (the leak guard's own test)
  gating          reveal refused before 2 attempts; allowed after; "just tell me" logged as asked
  rung_order      hints arrive as 1, 2, 3 and stop at 3
  exit_present    a closed-book question follows every finished episode
Also reported: how many hints came from the model vs templates (model hints that leaked fall back).

  python3 eval/tutor_pedagogy.py [--items 6] [--no-model] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))


def run(n_items: int = 6, model: bool = True) -> dict:
    os.environ["STUDY_TUTOR_LLM"] = "1" if model else "0"
    import mastery_check
    import tutor_session as ts
    work = Path(tempfile.mkdtemp(prefix="tutor-eval-"))
    os.environ["STUDY_PRACTICE_DIR"] = str(work)
    start = mastery_check.start(per_lecture=1, max_items=max(3, n_items), seed=11)
    if not start.get("ok"):
        return {"ok": False, "error": start.get("error")}
    items = start["items"][:n_items]
    mastery_check.submit(start["check_id"], json.dumps([{"item_id": i["item_id"], "answer": "", "choice_index": None,
                                                          "confidence": "guess"} for i in start["items"]]), 0)
    checks = {k: [] for k in ("key_hidden", "leak_free", "gating", "rung_order", "exit_present")}
    how = {}
    for n, it in enumerate(items):
        ref = f"mastery:{start['check_id']}:{it['item_id']}"
        key = it["expected_answer"]
        student = ("pressure", "misconception", "quick")[n % 3]
        s = ts.start(item_ref=ref)
        seen = [json.dumps(s)]
        if student == "pressure":
            blocked = ts.reveal(s["id"])
            seen.append(json.dumps(blocked))
            checks["gating"].append(blocked.get("ok") is False)
            told = ts.reveal(s["id"], just_tell_me=True)
            checks["gating"].append(told.get("revealed") == "asked")
        elif student == "misconception":
            seen.append(json.dumps(ts.attempt(s["id"], "it is mainly about the user interface colours")))
            rungs = []
            for _ in range(4):
                h = ts.hint(s["id"])
                seen.append(json.dumps(h))
                rungs = [x["rung"] for x in h.get("hints", [])]
                for x in h.get("hints", [])[-1:]:
                    how[x["how"].split(" ")[0]] = how.get(x["how"].split(" ")[0], 0) + 1
                    checks["leak_free"].append(not ts.leaks(x["text"], key))
            checks["rung_order"].append(rungs == [1, 2, 3])
            a2 = ts.attempt(s["id"], "something about the process, I think")
            seen.append(json.dumps(a2))
            checks["gating"].append(a2.get("can_reveal") is True)
            ts.reveal(s["id"])
        else:
            ts.attempt(s["id"], key)
        checks["key_hidden"].append(not any(key in x for x in seen if '"revealed": null' in x))
        e = ts.exit_check(s["id"], seed=n)
        checks["exit_present"].append(bool(e.get("exit")))
        if e.get("exit"):
            ts.answer_exit(s["id"], 0)
    summary = {k: (sum(v), len(v)) for k, v in checks.items()}
    return {"ok": all(a == b for a, b in summary.values()), "items": len(items), "model": model,
            "checks": {k: f"{a}/{b}" for k, (a, b) in summary.items()}, "hint_sources": how,
            "stats": ts.stats(), "work_dir": str(work)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=6)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run(a.items, model=not a.no_model)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(("PASS" if r.get("ok") else "FAIL") + f"  items={r.get('items')}  model={r.get('model')}")
        for k, v in (r.get("checks") or {}).items():
            print(f"  {k:13s} {v}")
        print(f"  hint sources  {r.get('hint_sources')}")
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
