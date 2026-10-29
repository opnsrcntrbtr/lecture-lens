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

    python calibration.py holdout [--day YYYYMMDD] [--n 15]   (fresh rows nobody has labelled; needs no model)
    python calibration.py rejudge --rubric v2 [--sheet labels.csv|holdout.csv]   (judge model loaded; see run_calibration.sh)

rejudge scores every row of a sheet with one rubric version (rubric.py) and writes
judge_<v>_score / _pass / _reason columns; report then compares each version with the labels.
The holdout sheet is the honest test: rows from a class neither the learner nor the panel
labelled, judged by both rubrics, labelled blind in the app.

Labels live in the private toolkit only (they quote lecture content).
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import subprocess
import sys
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
    if not n:
        return {"n": 0, "agreement": None, "kappa": None, "false_pass_rate": None, "false_fail_rate": None}
    judge_pass = [h for j, h in pairs if j == 1]
    return {"n": n, "agreement": round(sum(a == b for a, b in pairs) / n, 3) if n else None, "kappa": kappa(pairs),
            "false_pass_rate": round(sum(h == 0 for h in judge_pass) / len(judge_pass), 3) if judge_pass else None,
            "false_fail_rate": round(sum(h == 1 for j, h in pairs if j == 0) / max(1, sum(j == 0 for j, _ in pairs)), 3)}


def _versions(rows: list[dict]) -> list[str]:
    """Rubric versions whose verdicts the sheet holds: judge_pass is the original run (v1)."""
    vs = ["v1"] if rows and rows[0].get("judge_pass", "") != "" else []
    return vs + sorted({k.split("_")[1] for k in (rows[0] if rows else {}) if k.startswith("judge_v") and k.endswith("_pass")
                        and k != "judge_v1_pass" and any(r.get(k, "") != "" for r in rows)})


def _jp(r: dict, v: str) -> str:
    return r.get("judge_v1_pass") or r.get("judge_pass", "") if v == "v1" else r.get(f"judge_{v}_pass", "")


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
    out = {"labelled": len(lab), "of": len(rows), "overall": summary([p for v in by.values() for p in v]),
           "by_metric": {m: summary(v) for m, v in sorted(by.items())},
           "by_source": {"human": summary(pairs(lambda r: src(r) == "human")),
                         "panel": summary(pairs(lambda r: src(r) == "panel"))}}
    # each rubric version against the same labels (rows it scored)
    vers = {}
    for v in _versions(rows):
        got = [r for r in lab if _jp(r, v) in ("0", "1")]
        pv = lambda f: [(int(_jp(r, v)), int(r["human_pass"])) for r in got if f(r)]
        bm = defaultdict(list)
        for r in got:
            bm[r["metric"]].append((int(_jp(r, v)), int(r["human_pass"])))
        vers[v] = {"overall": summary(pv(lambda r: True)),
                   "by_source": {"human": summary(pv(lambda r: src(r) == "human")),
                                 "panel": summary(pv(lambda r: src(r) == "panel"))},
                   "by_metric": {m: summary(x) for m, x in sorted(bm.items())}}
    if vers:
        out["by_rubric"] = vers
    return out


METRIC_LABEL = {"relevance": "Question relevance [GEval]", "answerability": "Answerability [GEval]",
                "pedagogy": "Pedagogical value [GEval]", "faithfulness": "Faithfulness"}


def holdout(day: str, n: int) -> Path:
    """Fresh rows from a class nobody labelled: questions and follow-ups the follower drafted,
    one metric per case, cycling through the four question metrics. Appends to holdout.csv;
    a class already in the sheet is skipped, and labels already given are never touched."""
    study = HERE.parents[1]
    live = study / "lectures" / "live" / day
    out = HERE / "calibration" / "holdout.csv"
    cases_f = HERE / "calibration" / "holdout_cases.json"
    existing = list(csv.DictReader(out.open())) if out.exists() else []
    if any(r["run"] == f"live-{day}" for r in existing):
        print(json.dumps({"rows": 0, "skipped": f"{day} already in the holdout"}))
        return out
    if not (live / "questions.jsonl").exists() and not (live / "qa_followups.jsonl").exists():
        print(json.dumps({"rows": 0, "skipped": f"no live questions for {day}"}))
        return out
    subprocess.run([sys.executable, str(HERE / "live_questions_dataset.py"), str(live), "--n", str(n * 2)],
                   check=True, capture_output=True, cwd=HERE)
    cases = json.loads((HERE / ".live_questions.json").read_text())
    kinds = ["relevance", "answerability", "pedagogy", "faithfulness"]
    rows, full = [], json.loads(cases_f.read_text()) if cases_f.exists() else {}
    for i, c in enumerate(cases[:n]):
        k = kinds[i % 4]
        rid = hashlib.sha1(f"holdout|{day}|{c['input']}|{k}".encode()).hexdigest()[:10]
        full[rid] = {"input": c["input"], "actual": c["actual_output"], "expected": "", "context": c["retrieval_context"]}
        rows.append({"id": rid, "run": f"live-{day}", "metric": METRIC_LABEL[k], "input": c["input"][:600],
                     "output": c["actual_output"][:900], "expected": "", "context": " | ".join(c["retrieval_context"])[:1200],
                     "judge_score": "", "judge_pass": "", "judge_reason": "", "human_pass": "", "note": "",
                     "label_source": "", "panel_claude": "", "panel_local": ""})
    cases_f.write_text(json.dumps(full))
    header = list(existing[0].keys()) if existing else FIELDS
    header += [k for k in FIELDS if k not in header]
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, header); w.writeheader(); w.writerows(existing + rows)
    os.replace(tmp, out)
    print(json.dumps({"rows": len(rows), "day": day, "total": len(existing) + len(rows), "file": str(out)}))
    return out


def _merge_write(sheet: Path, rid: str, values: dict):
    """Write judge columns for one row into the file as it is now, so labels the app saved
    meanwhile are kept (the app merges the same way in the other direction)."""
    rows = list(csv.DictReader(sheet.open()))
    header = list(rows[0].keys()) if rows else []
    header += [k for k in values if k not in header]
    for x in rows:
        if x["id"] == rid:
            x.update({k: v for k, v in values.items() if v != "" or k not in x})
    tmp = sheet.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, header); w.writeheader(); w.writerows(rows)
    os.replace(tmp, sheet)


def rejudge(version: str, sheet: Path, runs: int = 1):
    """Score every row of a sheet with one rubric version; write judge_<v>_* columns (v1 on the
    holdout also fills judge_pass so the app's blind reveal works)."""
    os.environ["JUDGE_RUBRIC"] = version
    import rubric
    import statistics
    from deepeval.test_case import LLMTestCase
    cases_f = sheet.with_name("holdout_cases.json" if sheet.name.startswith("holdout") else "full_cases.json")
    full = json.loads(cases_f.read_text())
    rows = list(csv.DictReader(sheet.open()))
    tag = version + (f"x{runs}" if runs > 1 else "")
    cols = [f"judge_{tag}_score", f"judge_{tag}_pass", f"judge_{tag}_reason"]
    warn = [f"judge_{tag}_claims_warning"] if rubric.claim_warning(version) else []
    cols += warn
    header = list(rows[0].keys()) + [c for c in cols if c not in rows[0]]
    for r in rows:
        if r.get(cols[1], "") in ("0", "1"):
            continue                                     # resumable
        c = full[r["id"]]
        ctx = c.get("context") or []
        ctx = [ctx] if isinstance(ctx, str) else list(ctx)
        tc = LLMTestCase(input=c.get("input") or r["input"], actual_output=c.get("actual") or r["output"],
                         expected_output=c.get("expected") or None, retrieval_context=ctx or None)
        kind = rubric.metric_kind(r["metric"])
        ms = rubric.faithfulness(0.8 if "notes" in r["run"] else 0.7, version) if kind == "faithfulness" else [rubric.geval(kind, version)]
        ws = rubric.claim_warning(version) if kind == "faithfulness" else []

        def vote(metric):
            """Majority of `runs` judge calls: (median score, pass, reason of the last call), or None on error."""
            got = []
            for _ in range(runs):
                try:
                    metric.measure(tc)
                    got.append((metric.score or 0.0, bool(metric.success), metric.reason))
                except Exception:
                    pass
            if len(got) < (runs // 2 + 1):
                return None
            ok = sum(g[1] for g in got) > len(got) / 2
            return statistics.median(g[0] for g in got), ok, f"{getattr(metric, 'name', metric.__class__.__name__)} ({sum(g[1] for g in got)}/{len(got)} pass): {got[-1][2]}"
        res = [vote(m) for m in ms]
        if all(x is not None for x in res):
            r[cols[0]] = f"{min(x[0] for x in res):.3f}"
            r[cols[1]] = str(int(all(x[1] for x in res)))
            r[cols[2]] = " | ".join(x[2] for x in res)[:900]
            for w_ in ws:
                x = vote(w_)
                if x is not None:
                    r[warn[0]] = "" if x[1] else f"claim check failed: {x[2]}"[:600]
            if version == "v1" and runs == 1 and r.get("judge_pass", "") == "":
                r["judge_score"], r["judge_pass"], r["judge_reason"] = r[cols[0]], r[cols[1]], r[cols[2]]
        _merge_write(sheet, r["id"], {k: r.get(k, "") for k in cols + ["judge_score", "judge_pass", "judge_reason"] if k in r})
        print(r["id"], kind, r.get(cols[1]), flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("dirs", nargs="+")
    e.add_argument("--out", default=str(HERE / "calibration" / "labels.csv")); e.add_argument("--n", type=int, default=50)
    r = sub.add_parser("report"); r.add_argument("path", nargs="?", default=str(HERE / "calibration" / "labels.csv"))
    h = sub.add_parser("holdout"); h.add_argument("--day", default="20261024"); h.add_argument("--n", type=int, default=15)
    j = sub.add_parser("rejudge"); j.add_argument("--rubric", default="v2")
    j.add_argument("--sheet", default=str(HERE / "calibration" / "labels.csv"))
    j.add_argument("--runs", type=int, default=1, help="majority of N judge calls per metric (3 for checks)")
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
    elif a.cmd == "holdout":
        holdout(a.day, a.n)
    elif a.cmd == "rejudge":
        rejudge(a.rubric, Path(a.sheet), a.runs)
    else:
        res = report(list(csv.DictReader(open(a.path))))
        Path(a.path).with_name(Path(a.path).stem + "_report.json").write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
