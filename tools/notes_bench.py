"""M-6: does notes v2 keep more of the class than the current notes?

For each lecture with a checklist (eval/golden/facts_<lecture>.json) and both notes:
  fact recall          share of checklist facts the notes state (model yes/no, notes first so
                       the prefill is shared across facts)
  unsupported rate     share of sampled note sentences the support checker (NG-2, main model)
                       finds no support for in the three best-matching transcript passages
  timecodes in range   share of v2 bullets whose time falls inside their section (deterministic)
Decision rule (research spec): adopt v2 if recall rises by 15 points or more and the
unsupported rate does not rise. Checklists still marked draft make the result provisional.

  python3 tools/notes_bench.py [--sample 20]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

JUDGE = """NOTES:
{notes}

FACT: {fact}
Do the notes state this fact (the same meaning; any number must match)? Reply with JSON only: {{"present": true or false}}"""


def recall(notes: str, facts: list[dict], llm) -> tuple[float, list[bool]]:
    got = []
    for f in facts:
        m = re.search(r"\{.*\}", llm(JUDGE.format(notes=notes[:24000], fact=f["fact"])) or "", re.S)
        try:
            got.append(bool(json.loads(m.group(0)).get("present")) if m else False)
        except ValueError:
            got.append(False)
    return (round(sum(got) / len(got), 3) if got else None), got


def sentences(notes: str) -> list[str]:
    out = []
    for line in notes.splitlines():
        t = line.strip()
        if not t or t.startswith(("#", "_[", "<!--")):
            continue
        t = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", t)
        t = re.sub(r"\s*\[[^\]]*\d[^\]]*\]", "", t).strip()
        out += [s for s in re.split(r"(?<=[.!?])\s+", t) if len(s.split()) >= 6]
    return out


def unsupported(notes: str, lines: list[dict], checker, k: int, seed: int = 7) -> float | None:
    import cite
    said = [l["text"] for l in lines if "/t/" in l["line_id"]]
    passages = [" ".join(said[i:i + 3]) for i in range(0, len(said), 2)]
    ss = sentences(notes)
    if not ss or not passages:
        return None
    rng = random.Random(seed)
    pick = rng.sample(ss, min(k, len(ss)))
    bad = 0
    for s in pick:
        sc = cite.lexical_support(s, passages)
        top = [passages[i] for i in sorted(range(len(passages)), key=lambda i: -sc[i])[:3]]
        bad += not any(p >= checker.threshold for p in checker.fn(s, top))
    return round(bad / len(pick), 3)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20)
    a = ap.parse_args(argv)
    import cite, lecture_kit as lk, study
    judge = lambda p: study.llm("You check study notes against a fact. JSON only.", p, max_tokens=60, temperature=0.0, slot="build")
    chk = cite.llm_checker(lambda p: study.llm("You check whether passages support a claim. JSON only.", p,
                                               max_tokens=80, temperature=0.0, slot="build"))
    out = []
    for ff in sorted((HERE / "eval" / "golden").glob("facts_*.json")):
        d = json.loads(ff.read_text())
        folder = lk.LECTURES / d["lecture"]
        v1, v2 = folder / "notes.md", folder / "notes_v2" / "notes.md"
        if not (v1.exists() and v2.exists()):
            continue
        lines = lk._lecture_lines(folder)
        n1, n2 = v1.read_text(), v2.read_text()
        r1, g1 = recall(n1, d["facts"], judge)
        r2, g2 = recall(n2, d["facts"], judge)
        au = json.loads((folder / "notes_v2" / "notes_audit.json").read_text())
        row = {"lecture": d["lecture"], "checklist": d["status"], "facts": len(d["facts"]),
               "recall_v1": r1, "recall_v2": r2, "gain_points": round((r2 - r1) * 100, 1) if r1 is not None else None,
               "unsupported_v1": unsupported(n1, lines, chk, a.sample), "unsupported_v2": unsupported(n2, lines, chk, a.sample),
               "words_v1": len(n1.split()), "words_v2": len(n2.split()), "v2_audit": au,
               "missed_by_both": [f["fact_id"] for f, x, y in zip(d["facts"], g1, g2) if not x and not y],
               "only_v2": [f["fact_id"] for f, x, y in zip(d["facts"], g1, g2) if y and not x],
               "only_v1": [f["fact_id"] for f, x, y in zip(d["facts"], g1, g2) if x and not y]}
        out.append(row)
        print(json.dumps({k: row[k] for k in ("lecture", "recall_v1", "recall_v2", "unsupported_v1", "unsupported_v2",
                                               "words_v1", "words_v2")}), flush=True)
    if out:
        g = [r["gain_points"] for r in out if r["gain_points"] is not None]
        u1 = [r["unsupported_v1"] for r in out if r["unsupported_v1"] is not None]
        u2 = [r["unsupported_v2"] for r in out if r["unsupported_v2"] is not None]
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
        summary = {"mean_gain_points": mean(g), "mean_unsupported_v1": mean(u1), "mean_unsupported_v2": mean(u2),
                   "provisional": any("draft" in r["checklist"] for r in out)}
        summary["adopt_v2"] = bool(summary["mean_gain_points"] is not None and summary["mean_gain_points"] >= 15
                                   and (summary["mean_unsupported_v2"] or 0) <= (summary["mean_unsupported_v1"] or 0))
        rep = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": study.MODEL,
               "summary": summary, "lectures": out}
        p = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-notes-m6.json"
        p.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
