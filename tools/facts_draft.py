"""M-6 checklists: draft 30 checkable facts per class for the learner to edit.

Facts are drawn from ten-minute windows across the whole class (so a pipeline that loses the
middle of a class is caught), each with a verbatim quote from its cited transcript lines
(checked) and its time. Written to eval/golden/facts_<lecture>.json with status "draft": edit
the facts, delete weak ones, add missed ones, then set status to "confirmed".

  python3 tools/facts_draft.py LECTURE_FOLDER [-n 30]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

PROMPT = """CLASS TRANSCRIPT, {t0} to {t1} (numbered lines):
{lines}

TASK: Write up to 3 facts taught here that a good set of study notes should contain: a
definition, a number, a named framework or example, a decision rule or a caveat. Each fact is
one plain sentence a reader could check against notes. Give the exact words it comes from.
Skip greetings, logistics and audio checks; if nothing is taught, return an empty list.
Reply with JSON only: {{"facts": [{{"fact": "...", "quote": "<exact words from the lines>", "lines": [3]}}]}}"""


def draft(lines: list[dict], llm, n: int = 30) -> list[dict]:
    import agenda, capstone
    said = [l for l in lines if "/t/" in l["line_id"] and l.get("t")]
    wins = agenda.windows(said, 10)
    per = []
    for w in wins:
        mine = [l for l in said if l["line_id"] in set(w["line_ids"])]
        if len(mine) < 5:
            continue
        raw = llm(PROMPT.format(t0=w["t0"][:5], t1=w["t1"][:5],
                                lines="\n".join(f"{i}. {l['text']}" for i, l in enumerate(mine, 1))))
        m = re.search(r"\{.*\}", raw or "", re.S)
        try:
            facts = json.loads(m.group(0)).get("facts", []) if m else []
        except ValueError:
            facts = []
        got = []
        for f in facts if isinstance(facts, list) else []:
            idx = [int(x) for x in (f.get("lines") or []) if str(x).isdigit() and 1 <= int(x) <= len(mine)]
            if not idx or not (f.get("fact") or "").strip():
                continue
            src = " ".join(mine[i - 1]["text"] for i in idx)
            span = capstone.exact_span(f.get("quote") or "", src)
            if span is None or len(span.split()) < 3:
                continue
            got.append({"fact": f["fact"].strip(), "quote": span, "line_ids": [mine[i - 1]["line_id"] for i in idx],
                        "t": mine[idx[0] - 1]["t"]})
        per.append(got)
    out, k = [], 0
    while len(out) < n and any(per):      # round-robin across windows: spread over the class
        for g in per:
            if g and len(out) < n:
                out.append(g.pop(0))
        k += 1
    out.sort(key=lambda f: f["t"])
    for i, f in enumerate(out, 1):
        f["fact_id"] = f"f{i:02d}"
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("lecture")
    ap.add_argument("-n", type=int, default=30)
    a = ap.parse_args(argv)
    import lecture_kit as lk, study
    folder = lk.find_folder(a.lecture)
    llm = lambda p: study.llm("You draft checklists of facts taught in a class. JSON only.", p, max_tokens=900,
                              temperature=0.2, slot="build")
    facts = draft(lk._lecture_lines(folder), llm, a.n)
    out = HERE / "eval" / "golden" / f"facts_{folder.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"lecture": folder.name, "status": "draft: edit, then set to confirmed",
                               "made": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                               "facts": facts}, indent=1, ensure_ascii=False))
    print(json.dumps({"lecture": folder.name, "facts": len(facts), "path": str(out)}))


if __name__ == "__main__":
    main()
