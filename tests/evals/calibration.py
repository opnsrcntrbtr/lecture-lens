"""Judge calibration: does the local judge agree with a human?

    python calibration.py export <results_dir> [<results_dir> ...] [--out calibration/labels.csv] [--n 50]
    python calibration.py report [calibration/labels.csv]

export: one row per (test case, metric) from DeepEval's saved test runs, with the
judge's score, verdict and reason, and two empty columns for the human: human_pass
(1 or 0) and note. A deterministic sample balanced across metrics and judge verdicts,
so judge passes and fails are both checked.

report: for labelled rows, per metric and overall: agreement, Cohen's kappa, and the
false-pass rate (judge passed, human failed), the error that matters most, since a
lenient judge hides real problems. Writes calibration/report.json.

Labels live in the private toolkit only (they quote lecture content).
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
FIELDS = ["id", "run", "metric", "input", "output", "expected", "context", "judge_score", "judge_pass", "judge_reason",
          "human_pass", "note", "label_source", "panel_claude", "panel_local"]


def g(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def rows_from_run(path: Path) -> list[dict]:
    run = json.loads(path.read_text())
    out = []
    for c in g(run, "testCases", "test_cases", default=[]) or []:
        for m in g(c, "metricsData", "metrics_data", default=[]) or []:
            if g(m, "score") is None:
                continue
            key = f"{path.parent.name}|{g(c, 'name', default='')}|{g(m, 'name')}"
            ctx = g(c, "retrievalContext", "retrieval_context", default=[]) or []
            out.append({"id": hashlib.sha1(key.encode()).hexdigest()[:10], "run": path.parent.name, "metric": g(m, "name"),
                        "input": (g(c, "input", default="") or "")[:600], "output": (g(c, "actualOutput", "actual_output", default="") or "")[:900],
                        "expected": (g(c, "expectedOutput", "expected_output", default="") or "")[:900],
                        "context": " | ".join(ctx)[:1200], "judge_score": round(g(m, "score"), 3),
                        "judge_pass": int(bool(g(m, "success"))), "judge_reason": (g(m, "reason", default="") or "")[:500],
                        "human_pass": "", "note": ""})
    return out


def balanced(rows: list[dict], n: int) -> list[dict]:
    buckets = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["id"]):
        buckets[(r["metric"], r["judge_pass"])].append(r)
    out, i = [], 0
    while len(out) < n and any(i < len(b) for b in buckets.values()):
        for k in sorted(buckets):
            if i < len(buckets[k]) and len(out) < n:
                out.append(buckets[k][i])
        i += 1
    return out


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else (1.0 if po == 1 else 0.0)


def summary(pairs: list[tuple[int, int]]) -> dict:
    """pairs: (judge_pass, human_pass)."""
    n = len(pairs)
    judge_pass = [h for j, h in pairs if j == 1]
    return {"n": n, "agreement": round(sum(a == b for a, b in pairs) / n, 3) if n else None, "kappa": kappa(pairs),
            "false_pass_rate": round(sum(h == 0 for h in judge_pass) / len(judge_pass), 3) if judge_pass else None,
            "false_fail_rate": round(sum(h == 1 for j, h in pairs if j == 0) / max(1, sum(j == 0 for j, _ in pairs)), 3)}


def report(rows: list[dict]) -> dict:
    """Judge vs reference labels. A reference label is the learner's own (label_source human or empty)
    or an adversarial panel's (panel, panel-split); the split keeps the two apart, because only the
    learner's labels calibrate the judge against a person."""
    lab = [r for r in rows if str(r.get("human_pass", "")).strip() in ("0", "1")]
    src = lambda r: "human" if (r.get("label_source") or "human") == "human" else "panel"
    by = defaultdict(list)
    for r in lab:
        by[r["metric"]].append((int(r["judge_pass"]), int(r["human_pass"])))
    pairs = lambda f: [(int(r["judge_pass"]), int(r["human_pass"])) for r in lab if f(r)]
    return {"labelled": len(lab), "of": len(rows), "overall": summary([p for v in by.values() for p in v]),
            "by_metric": {m: summary(v) for m, v in sorted(by.items())},
            "by_source": {"human": summary(pairs(lambda r: src(r) == "human")),
                          "panel": summary(pairs(lambda r: src(r) == "panel"))}}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("dirs", nargs="+")
    e.add_argument("--out", default=str(HERE / "calibration" / "labels.csv")); e.add_argument("--n", type=int, default=50)
    r = sub.add_parser("report"); r.add_argument("path", nargs="?", default=str(HERE / "calibration" / "labels.csv"))
    a = ap.parse_args()
    if a.cmd == "export":
        rows = [x for d in a.dirs for p in sorted(glob.glob(str(Path(d) / "test_run_*.json"))) for x in rows_from_run(Path(p))]
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        old = {}
        if out.exists():                              # keep labels already given
            old = {r["id"]: r for r in csv.DictReader(out.open())}
        pick = balanced(rows, a.n)
        for x in pick:
            if x["id"] in old:
                for k in ("human_pass", "note", "label_source", "panel_claude", "panel_local"):
                    x[k] = old[x["id"]].get(k, "")
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(pick)
        print(json.dumps({"rows": len(pick), "from": len(rows), "file": str(out)}))
    else:
        res = report(list(csv.DictReader(open(a.path))))
        Path(a.path).with_name("report.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
