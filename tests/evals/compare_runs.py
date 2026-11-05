"""Compare two eval runs case by case.

    python compare_runs.py <before>/summary.json <after>/summary.json [--json]

For each metric, the cases scored in both runs are paired and compared with
McNemar's exact test (McNemar 1947; Dietterich 1998 recommends it for two
systems scored on the same cases). Each run's pass rate is shown with its 95%
Wilson interval. A small p (< 0.05) says the change is unlikely to be noise;
a large p on six cases says only that six cases cannot tell.

Needs summaries written by summarize.py with `per_case` (older ones lack it).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats  # noqa: E402


def compare(before: dict, after: dict) -> dict:
    def index(s):
        out = {}
        for r in s.get("per_case") or []:
            out.setdefault(r["metric"], {})[r["case"]] = bool(r["success"])
        return out
    a, b = index(before), index(after)
    if not a or not b:
        raise ValueError("both summaries need per_case (re-run summarize.py)")
    res = {}
    for metric in sorted(set(a) | set(b)):
        shared = sorted(set(a.get(metric, {})) & set(b.get(metric, {})))
        if not shared:
            res[metric] = {"paired": 0, "note": "no case scored in both runs"}
            continue
        pa = [a[metric][c] for c in shared]; pb = [b[metric][c] for c in shared]
        t = stats.mcnemar_exact(pa, pb)
        res[metric] = {"paired": len(shared),
                       "before": {"pass": sum(pa), "ci95": stats.wilson(sum(pa), len(pa))},
                       "after": {"pass": sum(pb), "ci95": stats.wilson(sum(pb), len(pb))},
                       "fixed": t["b_only"], "broke": t["a_only"], "p_value": round(t["p_value"], 4)}
    return res


def report(res: dict) -> str:
    L = ["| metric | paired | before | after | fixed | broke | p (McNemar) |", "|---|---|---|---|---|---|---|"]
    for m, r in res.items():
        if not r.get("paired"):
            L.append(f"| {m} | 0 | | | | | {r.get('note', '')} |"); continue
        n = r["paired"]
        L.append(f"| {m} | {n} | {stats.fmt_rate(r['before']['pass'], n)} | {stats.fmt_rate(r['after']['pass'], n)} "
                 f"| {r['fixed']} | {r['broke']} | {r['p_value']:.3f} |")
    return "\n".join(L)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    before, after = (json.loads(Path(p).read_text()) for p in argv[:2])
    res = compare(before, after)
    print(json.dumps(res, indent=1) if "--json" in argv else report(res))


if __name__ == "__main__":
    main(sys.argv[1:])
