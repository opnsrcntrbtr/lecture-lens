"""M-11: do the capstone checkers find planted defects, and do the gates hold?

Runs every CC-1 checker on each folder of capstone_samples/ (A-C carry planted defects,
D and E are clean controls; labels.json says which checks should fail) with the local model,
and writes a checker card: per check true-positive and true-negative rates with Wilson
intervals, 'exploratory' unless TPR >= 0.80 and TNR >= 0.90, plus the gate pass rate and the
valid-output rate. 'unclear' counts as not detected (a miss on a defect, a pass on a control
is not credited either way: it is reported separately).

  python3 tools/capstone_bench.py [--folders A B C D E] [--checks ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import capstone as cs          # noqa: E402
import capstone_review as cr   # noqa: E402
import cite                    # noqa: E402  (wilson)


def card(rows: list[dict]) -> list[dict]:
    out = []
    for check in cr.CHECKERS:
        rs = [r for r in rows if r["check_id"] == check]
        pos = [r for r in rs if r["planted"]]
        neg = [r for r in rs if not r["planted"]]
        tp = sum(1 for r in pos if r["verdict"] == "fail")
        tn = sum(1 for r in neg if r["verdict"] == "pass")
        tpr = tp / len(pos) if pos else None
        tnr = tn / len(neg) if neg else None
        out.append({"check_id": check, "n_pos": len(pos), "n_neg": len(neg), "tpr": None if tpr is None else round(tpr, 3),
                    "tpr_ci": cite.wilson(tp, len(pos)), "tnr": None if tnr is None else round(tnr, 3),
                    "tnr_ci": cite.wilson(tn, len(neg)), "unclear": sum(1 for r in rs if r["verdict"] == "unclear"),
                    "status": "ok" if (tpr or 0) >= 0.80 and (tnr or 0) >= 0.90 else "exploratory"})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--folders", nargs="*", default=["A", "B", "C", "D", "E"])
    ap.add_argument("--checks", nargs="*", default=list(cr.CHECKERS))
    ap.add_argument("--runs", type=int, default=cr.RUNS)
    a = ap.parse_args(argv)
    import study
    llm = lambda system, user: study.llm(system, user, max_tokens=600, temperature=0.5, slot="build")
    samples = HERE / "capstone_samples"
    labels = json.loads((samples / "labels.json").read_text())
    rows, attempted, gated, invalid = [], 0, 0, 0
    for folder in a.folders:
        with tempfile.TemporaryDirectory() as tmp:
            ws = cs.Workspace(Path(tmp)).init()
            ws.register("prd", samples / folder / "prd.md", "prd")
            ws.register("strategy", samples / folder / "strategy.md", "strategy")
            for check in a.checks:
                r = cr.run_check(ws, check, llm, runs=a.runs)
                valid = [v for v in r["runs"] if v != "invalid"]
                attempted += len(valid)
                gated += len(r["gate_failures"])
                invalid += r["runs"].count("invalid")
                rows.append({"folder": folder, "check_id": check, "planted": check in labels[folder],
                             "verdict": r["verdict"], "runs": r["runs"], "shown": r["finding"] is not None,
                             "gate_failures": [g["why"] for g in r["gate_failures"]]})
                print(f"{folder} {check:<24} planted={check in labels[folder]!s:<5} verdict={r['verdict']:<8} runs={r['runs']}",
                      flush=True)
    rep = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": study.MODEL, "runs": a.runs,
           "card": card(rows), "gate_pass_rate": round(1 - gated / attempted, 3) if attempted else None,
           "valid_output_rate": round(attempted / (attempted + invalid), 3) if attempted + invalid else None,
           "rows": rows}
    out = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-capstone-m11.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in ("gate_pass_rate", "valid_output_rate")}))
    for c in rep["card"]:
        print(c)
    print("report:", out)


if __name__ == "__main__":
    main()
