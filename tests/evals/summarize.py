"""Turn DeepEval's saved test run into eval/results/<ts>-deepeval/summary.json.

Reads test_run_*.json that DeepEval writes when DEEPEVAL_RESULTS_FOLDER is set
(falls back to .deepeval/.latest_run_full.json). Tolerant to camelCase/snake_case.

    python summarize.py <results_dir>
"""
import glob
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def g(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def main(res_dir):
    res = Path(res_dir)
    runs = sorted(glob.glob(str(res / "test_run_*.json"))) or [str(HERE / ".deepeval" / ".latest_run_full.json")]
    run = json.loads(Path(runs[-1]).read_text())
    cases = g(run, "testCases", "test_cases", default=[]) or []
    per_metric, per_suite, failures = {}, {}, []
    for c in cases:
        name = g(c, "name", default="")
        suite = "ask" if "test_ask" in name else "notes" if "test_notes" in name else "cards" if "test_cards" in name else "other"
        ok_all = True
        for m in g(c, "metricsData", "metrics_data", default=[]) or []:
            mname = g(m, "name", default="?")
            key = f"{suite}:{mname}"
            score = g(m, "score")
            success = bool(g(m, "success", default=False))
            ok_all &= success
            b = per_metric.setdefault(key, {"scores": [], "pass": 0, "n": 0, "threshold": g(m, "threshold")})
            b["n"] += 1
            b["pass"] += success
            if score is not None:
                b["scores"].append(score)
            if not success:
                failures.append({"suite": suite, "metric": mname, "input": g(c, "input", default="")[:160],
                                 "score": score, "reason": (g(m, "reason") or "")[:400]})
        s = per_suite.setdefault(suite, {"pass": 0, "n": 0})
        s["n"] += 1
        s["pass"] += ok_all
    metrics = {k: {"mean": round(statistics.mean(v["scores"]), 3) if v["scores"] else None,
                   "pass": v["pass"], "n": v["n"], "threshold": v["threshold"]} for k, v in sorted(per_metric.items())}
    outputs = json.loads((HERE / ".outputs.json").read_text()) if (HERE / ".outputs.json").exists() else {}
    lat = [r.get("latency_s") for r in outputs.get("ask", []) if r.get("latency_s")]
    try:
        git = subprocess.run(["git", "-C", str(HERE.parent.parent), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        git = None
    summary = {"suite": "smoke" if os.environ.get("EVAL_SUITE") == "smoke" else "deepeval-study", "generator": outputs.get("generator"),
               "judge": os.environ.get("OMLX_JUDGE_MODEL"), "prompt_versions": outputs.get("prompt_versions"),
               "cases": per_suite, "metrics": metrics,
               "latency": {"ask_p50": round(statistics.median(lat), 2) if lat else None},
               "failures": failures[:50], "source": runs[-1], "git": git}
    (res / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"cases": per_suite, "metrics": metrics}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
