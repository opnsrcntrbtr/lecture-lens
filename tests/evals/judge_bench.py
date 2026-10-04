"""Judge benchmark: is Laya (System-1, 421M) a usable faithfulness judge next to
the oMLX 27B LLM judge?

    python judge_bench.py OUT_DIR [--skip-llm] [--skip-metric]

Part 1 — claim support. 72 hand-labelled claim/context pairs (judge_pairs.json,
36 supported / 36 not). Each judge says P(supported). We report accuracy and
F1 for catching unsupported claims at 0.5, AUROC, ECE, per-kind accuracy and
latency.

Part 2 — inside DeepEval. FaithfulnessMetric on 12 synthetic answers (two
true claims + one planted false claim each; ideal score 0.67) in eval_mode
llm (27B does everything) vs hybrid (27B extracts claims, Laya decides).

Requires the judge model loaded in oMLX (run_judge_bench.sh swaps it in).
Writes OUT_DIR/judge_bench.json, OUT_DIR/summary.json, OUT_DIR/report.md.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent

LLM_PROMPT = """You are checking whether a claim is supported by lecture material.

CONTEXT:
{context}

CLAIM: {claim}

A claim is supported only if everything in it — every number, name, order and relationship — is stated in the context or follows directly from it. A claim that changes a detail, contradicts the context, or adds something the context does not say is NOT supported.

Reply with JSON: {{"supported": true or false, "confidence": a number from 0.5 to 1.0 for how sure you are}}"""


def load():
    contexts = json.loads((HERE / "contexts.json").read_text())
    pairs = json.loads((HERE / "judge_pairs.json").read_text())["pairs"]
    for p in pairs:
        p["context"] = "\n\n".join(contexts[p["ctx"]])
    return contexts, pairs


# ---------------------------------------------------------------- metrics
def scores(labels, probs, threshold=0.5):
    """Positive class = UNSUPPORTED (the judge's job is to catch hallucinations)."""
    pred_unsup = [p < threshold for p in probs]
    true_unsup = [lab == 0 for lab in labels]
    tp = sum(a and b for a, b in zip(pred_unsup, true_unsup))
    fp = sum(a and not b for a, b in zip(pred_unsup, true_unsup))
    fn = sum(b and not a for a, b in zip(pred_unsup, true_unsup))
    acc = sum((p >= threshold) == (lab == 1) for p, lab in zip(probs, labels)) / len(labels)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"accuracy": acc, "precision_unsupported": prec, "recall_unsupported": rec, "f1_unsupported": f1,
            "auroc": auroc(labels, probs), "ece": ece(labels, probs), "brier": brier(labels, probs)}


def auroc(labels, probs):
    pos = [p for p, lab in zip(probs, labels) if lab == 1]
    neg = [p for p, lab in zip(probs, labels) if lab == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def ece(labels, probs, bins=10):
    """Expected calibration error of P(supported), equal-width bins."""
    total, n = 0.0, len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            conf = statistics.mean(probs[i] for i in idx)
            acc = statistics.mean(labels[i] for i in idx)
            total += len(idx) / n * abs(conf - acc)
    return total


def brier(labels, probs):
    return statistics.mean((p - lab) ** 2 for p, lab in zip(probs, labels))


def by_kind(pairs, probs):
    out = {}
    for p, pr in zip(pairs, probs):
        k = out.setdefault(p["kind"], {"n": 0, "correct": 0})
        k["n"] += 1
        k["correct"] += int((pr >= 0.5) == (p["label"] == 1))
    return {k: {**v, "accuracy": v["correct"] / v["n"]} for k, v in sorted(out.items())}


def latency(xs):
    xs = sorted(xs)
    return {"mean_ms": 1000 * statistics.mean(xs), "p50_ms": 1000 * xs[len(xs) // 2],
            "p95_ms": 1000 * xs[min(len(xs) - 1, int(0.95 * len(xs)))], "total_s": sum(xs)}


# ---------------------------------------------------------------- judges
def run_laya(pairs):
    from laya_judge import LayaSystemOne
    t0 = time.perf_counter()
    laya = LayaSystemOne()
    load_s = time.perf_counter() - t0
    laya.supported("warm-up context", "warm-up claim")  # first MPS pass compiles kernels
    probs, lat = [], []
    for p in pairs:
        t = time.perf_counter()
        probs.append(laya.supported(p["context"], p["claim"]))
        lat.append(time.perf_counter() - t)
    print(f"  laya: {len(pairs)} pairs, load {load_s:.1f}s", file=sys.stderr)
    return laya, probs, lat, load_s


def run_llm(pairs):
    from judge import JUDGE, _first_json
    probs, lat, errors = [], [], 0
    for i, p in enumerate(pairs, 1):
        t = time.perf_counter()
        try:
            j = _first_json(JUDGE.generate(LLM_PROMPT.format(context=p["context"], claim=p["claim"])))
            sup = j.get("supported")
            sup = sup if isinstance(sup, bool) else str(sup).lower() in ("true", "yes", "1")
            conf = min(1.0, max(0.5, float(j.get("confidence", 1.0))))
            probs.append(conf if sup else 1 - conf)
        except Exception as e:  # noqa: BLE001 — count, score as undecided
            errors += 1
            probs.append(0.5)
            print(f"  llm pair {i}: {e}", file=sys.stderr)
        lat.append(time.perf_counter() - t)
        if i % 12 == 0:
            print(f"  llm: {i}/{len(pairs)}", file=sys.stderr)
    return probs, lat, errors


def run_metric_modes(contexts, pairs, laya):
    """FaithfulnessMetric on planted-error answers: llm vs hybrid (Laya decides)."""
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
    from judge import JUDGE

    cases = []
    for ci, ctx in enumerate(contexts):
        sup = [p["claim"] for p in pairs if p["ctx"] == ci and p["label"] == 1][:2]
        bad = [p["claim"] for p in pairs if p["ctx"] == ci and p["label"] == 0 and p["kind"] != "unmentioned"][:1]
        cases.append(LLMTestCase(input="Summarise this part of the lecture.",
                                 actual_output=" ".join(sup + bad), retrieval_context=ctx))
    results = {}
    for mode in ("llm", "hybrid"):
        rows, t0 = [], time.perf_counter()
        for tc in cases:
            kw = {"model": JUDGE, "async_mode": False, "include_reason": False, "eval_mode": mode}
            if mode != "llm":
                kw["system_one_model"] = laya
            try:
                m = FaithfulnessMetric(**kw)
                m.measure(tc)
                rows.append(m.score)
            except Exception as e:  # noqa: BLE001
                rows.append(None)
                print(f"  metric {mode}: {type(e).__name__}: {e}"[:300], file=sys.stderr)
        ok = [r for r in rows if r is not None]
        results[mode] = {
            "scores": rows, "n_ok": len(ok),
            "mean": statistics.mean(ok) if ok else None,
            # 2 of 3 claims true: a judge that catches the planted error scores < 1.
            "caught_planted_error": sum(r < 0.999 for r in ok),
            "mae_vs_ideal": statistics.mean(abs(r - 2 / 3) for r in ok) if ok else None,
            "seconds": time.perf_counter() - t0,
        }
        print(f"  metric {mode}: mean {results[mode]['mean']}, {results[mode]['seconds']:.0f}s", file=sys.stderr)
    return results


# ---------------------------------------------------------------- main
def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else HERE / ".bench"
    out.mkdir(parents=True, exist_ok=True)
    contexts, pairs = load()
    labels = [p["label"] for p in pairs]
    res = {"suite": "judge-bench", "pairs": len(pairs), "supported": sum(labels), "judges": {}}

    print("== Laya", file=sys.stderr)
    laya, lp, ll, load_s = run_laya(pairs)
    res["judges"]["laya"] = {"model": laya.get_model_name(), **scores(labels, lp),
                             "by_kind": by_kind(pairs, lp), "latency": latency(ll), "load_s": load_s}

    lmp = None
    if "--skip-llm" not in sys.argv:
        print("== oMLX LLM judge", file=sys.stderr)
        from judge import JUDGE
        lmp, lml, errs = run_llm(pairs)
        res["judges"]["omlx"] = {"model": JUDGE.get_model_name(), **scores(labels, lmp),
                                 "by_kind": by_kind(pairs, lmp), "latency": latency(lml), "errors": errs}
        # Where do they disagree? (useful for deciding a hybrid gate)
        res["disagreements"] = [
            {"claim": p["claim"], "label": p["label"], "kind": p["kind"], "laya": round(a, 3), "omlx": round(b, 3)}
            for p, a, b in zip(pairs, lp, lmp) if (a >= 0.5) != (b >= 0.5)]

    if "--skip-metric" not in sys.argv and "--skip-llm" not in sys.argv:
        print("== FaithfulnessMetric: llm vs hybrid", file=sys.stderr)
        res["faithfulness_modes"] = run_metric_modes(contexts, pairs, laya)

    res["per_pair"] = [{"claim": p["claim"], "label": p["label"], "kind": p["kind"], "laya": round(a, 4),
                        **({"omlx": round(lmp[i], 4)} if lmp else {})}
                       for i, (p, a) in enumerate(zip(pairs, lp))]
    (out / "judge_bench.json").write_text(json.dumps(res, indent=2))

    # summary.json in the shape the app's Evals screen reads
    metrics = {}
    for name, j in res["judges"].items():
        for k in ("accuracy", "f1_unsupported", "auroc", "ece"):
            if j.get(k) is not None:
                metrics[f"{name}:{k}"] = {"mean": round(j[k], 4)}
        metrics[f"{name}:latency_ms"] = {"mean": round(j["latency"]["mean_ms"], 1)}
    for mode, r in res.get("faithfulness_modes", {}).items():
        if r["mean"] is not None:
            metrics[f"faithfulness[{mode}]:score"] = {"mean": round(r["mean"], 4),
                                                       "pass": r["caught_planted_error"], "n": r["n_ok"]}
    (out / "summary.json").write_text(json.dumps({
        "suite": "judge-bench", "judge": res["judges"].get("omlx", {}).get("model"),
        "generator": res["judges"]["laya"]["model"], "metrics": metrics}, indent=2))
    (out / "report.md").write_text(report(res))
    print(json.dumps({k: v for k, v in res.items() if k not in ("per_pair", "disagreements")}, indent=2))


def report(r):
    L = ["# Judge benchmark — Laya vs oMLX", "",
         f"{r['pairs']} labelled claim/context pairs ({r['supported']} supported). "
         "Positive class for precision/recall/F1 = *unsupported* (a hallucination the judge should catch).", "",
         "| | accuracy | F1 (unsupported) | AUROC | ECE | Brier | mean latency |", "|---|---|---|---|---|---|---|"]
    for name, j in r["judges"].items():
        L.append(f"| {j['model']} | {j['accuracy']:.3f} | {j['f1_unsupported']:.3f} | "
                 f"{(j['auroc'] or 0):.3f} | {j['ece']:.3f} | {j['brier']:.3f} | {j['latency']['mean_ms']:.0f} ms |")
    L += ["", "## Accuracy by claim kind", "", "| kind | n | " + " | ".join(r["judges"]) + " |",
          "|---|---|" + "---|" * len(r["judges"])]
    kinds = r["judges"]["laya"]["by_kind"]
    for k, v in kinds.items():
        L.append(f"| {k} | {v['n']} | " + " | ".join(f"{j['by_kind'][k]['accuracy']:.2f}" for j in r["judges"].values()) + " |")
    if r.get("faithfulness_modes"):
        L += ["", "## FaithfulnessMetric: llm vs hybrid (12 answers, 1 planted error each, ideal 0.67)", "",
              "| mode | mean score | caught planted error | MAE vs 0.67 | time |", "|---|---|---|---|---|"]
        for m, x in r["faithfulness_modes"].items():
            L.append(f"| {m} | {x['mean'] if x['mean'] is None else round(x['mean'], 3)} | {x['caught_planted_error']}/{x['n_ok']} | "
                     f"{x['mae_vs_ideal'] if x['mae_vs_ideal'] is None else round(x['mae_vs_ideal'], 3)} | {x['seconds']:.0f}s |")
    if r.get("disagreements"):
        L += ["", f"## Disagreements ({len(r['disagreements'])})", ""]
        for d in r["disagreements"]:
            L.append(f"- [{d['kind']}, label {d['label']}] laya {d['laya']} · omlx {d['omlx']} — {d['claim']}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
