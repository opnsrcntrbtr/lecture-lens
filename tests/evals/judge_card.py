"""Judge card (EP-1): how far each judged criterion can be trusted.

For every criterion (metric) and rubric version in the labelled sheets (calibration/labels.csv,
calibration/holdout.csv), against the learner's or panel's pass/fail labels:
  n_labels, valid_rate (a judge verdict that is missing or unparseable counts as missing,
  never as a fail), tpr and tnr with Wilson intervals (pass is the positive class), Cohen's
  kappa beside raw agreement, position_flip_rate for pairwise judging when order-swapped
  results exist, judge model, DeepEval version and date.
A criterion with fewer than 60 labels or a valid rate under 95% is "exploratory"; one with
enough labels but TPR or TNR under 0.80 is "unreliable"; otherwise "ok". Labels are split
by a hash of the row id into few-shot (10%), development (40%) and a frozen test set (50%);
the card reports all labels and the test split, and records each judge version's first use of
the test split in calibration/test_runs.json.

  python3 tests/evals/judge_card.py [--sheets calibration/labels.csv calibration/holdout.csv] [--out eval/judge_card.json]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MIN_LABELS, MIN_VALID, MIN_RATE = 60, 0.95, 0.80


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - r) / d, 3), round((c + r) / d, 3)]


def kappa(pairs: list[tuple[int, int]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else (1.0 if po == 1 else 0.0)


def split(row_id: str) -> str:
    h = int(hashlib.sha256(row_id.encode()).hexdigest()[:8], 16) % 100
    return "fewshot" if h < 10 else "dev" if h < 50 else "test"


def versions(rows: list[dict]) -> list[str]:
    keys = set().union(*(r.keys() for r in rows)) if rows else set()
    vs = ["v1"] if "judge_pass" in keys or "judge_v1_pass" in keys else []
    return vs + sorted({k[len("judge_"):-len("_pass")] for k in keys if k.startswith("judge_v") and k.endswith("_pass")
                        and k != "judge_v1_pass"})


def verdict(r: dict, v: str) -> int | None:
    raw = (r.get("judge_v1_pass") or r.get("judge_pass", "")) if v == "v1" else r.get(f"judge_{v}_pass", "")
    raw = str(raw).strip().lower()
    return 1 if raw in ("1", "true", "pass") else 0 if raw in ("0", "false", "fail") else None


def criterion_card(rows: list[dict], v: str, flips: dict | None = None) -> dict:
    lab = [r for r in rows if str(r.get("human_pass", "")).strip() in ("0", "1")]
    scored = [(verdict(r, v), int(r["human_pass"])) for r in lab]
    valid = [(j, h) for j, h in scored if j is not None]
    pos = [(j, h) for j, h in valid if h == 1]
    neg = [(j, h) for j, h in valid if h == 0]
    tp, tn = sum(j == 1 for j, _ in pos), sum(j == 0 for j, _ in neg)
    tpr = round(tp / len(pos), 3) if pos else None
    tnr = round(tn / len(neg), 3) if neg else None
    n = len(lab)
    vr = round(len(valid) / n, 3) if n else None
    flags = [f for f, x in (("low_tpr", tpr), ("low_tnr", tnr)) if x is not None and x < MIN_RATE]
    status = "exploratory" if n < MIN_LABELS or (vr or 0) < MIN_VALID else ("unreliable" if flags else "ok")
    return {"n_labels": n, "valid_rate": vr, "tpr": tpr, "tpr_ci": wilson(tp, len(pos)), "tnr": tnr,
            "tnr_ci": wilson(tn, len(neg)), "kappa": kappa(valid),
            "raw_agreement": round(sum(j == h for j, h in valid) / len(valid), 3) if valid else None,
            "position_flip_rate": (flips or {}).get("rate"), "flags": flags, "status": status}


def build(sheets: list[Path], judge_model: str | None = None, deepeval_version: str | None = None,
          flips_by_criterion: dict | None = None, test_runs: Path | None = None) -> list[dict]:
    rows = []
    for s in sheets:
        if s.exists():
            rows += [{**r, "_sheet": s.name} for r in csv.DictReader(s.open())]
    by = defaultdict(list)
    for r in rows:
        by[r.get("metric") or "unknown"].append(r)
    today = dt.date.today().isoformat()
    out = []
    used = json.loads(test_runs.read_text()) if test_runs and test_runs.exists() else {}
    ran = defaultdict(set)   # sheet -> rubric versions it holds verdicts for (a version never run on a sheet is not "invalid")
    for r in rows:
        for v in versions([r]):
            if verdict(r, v) is not None:
                ran[r["_sheet"]].add(v)
    for crit, rs_all in sorted(by.items()):
        for v in versions(rs_all):
            rs = [r for r in rs_all if v in ran[r["_sheet"]]]
            if not any(verdict(r, v) is not None for r in rs):
                continue
            all_ = criterion_card(rs, v, (flips_by_criterion or {}).get(crit))
            test = criterion_card([r for r in rs if split(r.get("id", "")) == "test"], v)
            key = f"{judge_model or 'judge'}|{v}"
            out.append({"criterion": crit, "rubric_version": v, **all_,
                        "test_split": {k: test[k] for k in ("n_labels", "tpr", "tnr", "kappa", "status")},
                        "test_split_first_used": used.get(key, today),
                        "judge_model": judge_model, "deepeval_version": deepeval_version, "date": today})
            used.setdefault(key, today)
    if test_runs:
        test_runs.parent.mkdir(parents=True, exist_ok=True)
        test_runs.write_text(json.dumps(used, indent=1))
    return out


def _deepeval_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("deepeval")
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", nargs="*", default=[str(HERE / "calibration" / "labels.csv"),
                                                    str(HERE / "calibration" / "holdout.csv")])
    ap.add_argument("--out", default=str(ROOT / "eval" / "judge_card.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    flips_f = HERE / "calibration" / "pairwise_flips.json"
    flips = json.loads(flips_f.read_text()) if flips_f.exists() else None
    card = build([Path(s) for s in a.sheets], os.environ.get("OMLX_JUDGE_MODEL") or None, _deepeval_version(), flips,
                 HERE / "calibration" / "test_runs.json")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(card, indent=1))
    if a.json:
        print(json.dumps({"ok": True, "card": card}))
    else:
        for c in card:
            print(f"{c['criterion'][:34]:<34} {c['rubric_version']:<8} n={c['n_labels']:<3} valid={c['valid_rate']} "
                  f"tpr={c['tpr']} tnr={c['tnr']} kappa={c['kappa']} agree={c['raw_agreement']} {c['status']}")


if __name__ == "__main__":
    main()
