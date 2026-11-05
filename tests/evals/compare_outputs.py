"""Compare two generators' eval outputs with model-free checks, case by case, with uncertainty.

    python compare_outputs.py <A outputs.json> <B outputs.json> [--json]

A and B are files in the shape generate_outputs.py writes (.outputs.json) or the Studio's
eval page writes (.outputs.gemini-nano.json). Cases are paired by their input, so only
cases both runs answered are compared.

Checks (no model; the local judge's scores are compared with compare_runs.py):
  ask    recall    share of the expected answer's content words in the answer
         words     answer length; latency_s
         cites_ok  no citation to a source that was not given, and not uncited (study.check_citations)
  notes  sections  all seven note headings present
         recall    share of the context's content words in the notes
         words     notes length; latency_s
         refs_ok   no [slide n] / [deck n] citation to a slide or deck the context does not contain
  cards  four      exactly four cards

Numbers come with a paired bootstrap interval for the mean difference B - A (Efron &
Tibshirani 1993) and an exact sign test (Dixon & Mood 1946); pass/fail checks with each
run's Wilson interval and McNemar's exact test (McNemar 1947). With 12 or 36 cases, a
difference whose interval includes 0 is not shown to be real.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent.parent))
import stats  # noqa: E402
from live_qa import content_words  # noqa: E402
from study import check_citations  # noqa: E402

NOTE_HEADINGS = ["TL;DR", "Key concepts", "Frameworks & models", "Worked examples and numbers",
                 "How this applies to building AI products", "Action items for the learner", "Open questions"]
REF = re.compile(r"\[(slide|deck)\s+(\d+)(?:\s*@[^\]]*)?\]", re.I)


def recall(reference: str, text: str) -> float | None:
    ref = content_words(reference)
    return len(ref & content_words(text)) / len(ref) if ref else None


def refs_ok(text: str, context: str) -> bool:
    known = {(k.lower(), int(n)) for k, n in REF.findall(context)}
    return all((k.lower(), int(n)) in known for k, n in REF.findall(text))


def has_sections(md: str) -> bool:
    heads = [h.strip().lower() for h in re.findall(r"^##\s+(.+)$", md, re.M)]
    return all(any(h.startswith(want.lower()[:8]) for h in heads) for want in NOTE_HEADINGS)


def case_checks(kind: str, r: dict) -> dict:
    out, ctx = r.get("actual_output") or "", "\n\n".join(r.get("retrieval_context") or [])
    if kind == "ask":
        warns, _ = check_citations(out, len(r.get("retrieval_context") or []))
        return {"recall": recall(r.get("expected_output") or "", out), "words": len(out.split()),
                "latency_s": r.get("latency_s"), "cites_ok": not warns}
    if kind == "notes":
        return {"sections": has_sections(out), "recall": recall(ctx, out), "words": len(out.split()),
                "latency_s": r.get("latency_s"), "refs_ok": refs_ok(out, ctx)}
    n = r.get("n_cards")
    if n is None:
        n = len(re.findall(r"^Q:", out, re.M))
    return {"four": n == 4}


def compare(a: dict, b: dict) -> dict:
    res = {}
    for kind in ("ask", "notes", "cards"):
        A = {r["input"]: case_checks(kind, r) for r in a.get(kind, [])}
        B = {r["input"]: case_checks(kind, r) for r in b.get(kind, [])}
        shared = [k for k in A if k in B]
        if not shared:
            continue
        for check in A[shared[0]]:
            pa = [A[k][check] for k in shared]; pb = [B[k][check] for k in shared]
            if all(isinstance(x, bool) for x in pa + pb):
                t = stats.mcnemar_exact(pa, pb)
                res[f"{kind}:{check}"] = {"paired": len(shared), "type": "rate",
                                          "a": {"pass": sum(pa), "ci95": stats.wilson(sum(pa), len(pa))},
                                          "b": {"pass": sum(pb), "ci95": stats.wilson(sum(pb), len(pb))},
                                          "p_value": round(t["p_value"], 4)}
                continue
            pairs = [(x, y) for x, y in zip(pa, pb) if x is not None and y is not None]
            if not pairs:
                continue
            diffs = [y - x for x, y in pairs]
            mean = lambda xs: sum(xs) / len(xs)
            st = stats.sign_test(diffs)
            res[f"{kind}:{check}"] = {"paired": len(pairs), "type": "number",
                                      "a": {"mean": mean([x for x, _ in pairs]), "median": statistics.median(x for x, _ in pairs)},
                                      "b": {"mean": mean([y for _, y in pairs]), "median": statistics.median(y for _, y in pairs)},
                                      "diff_mean": mean(diffs), "diff_ci95": stats.bootstrap_ci(mean, diffs),
                                      "sign_test": st}
    return res


def report(res: dict, name_a="A", name_b="B") -> str:
    L = [f"| check | paired | {name_a} | {name_b} | {name_b} − {name_a} (95% CI) | p |", "|---|---|---|---|---|---|"]
    for k, r in res.items():
        n = r["paired"]
        if r["type"] == "rate":
            L.append(f"| {k} | {n} | {stats.fmt_rate(r['a']['pass'], n)} | {stats.fmt_rate(r['b']['pass'], n)} | "
                     f"{r['b']['pass'] - r['a']['pass']:+d} cases | {r['p_value']:.3f} (McNemar) |")
        else:
            ci = r["diff_ci95"]
            ci_s = f" ({ci[0]:+.3f} to {ci[1]:+.3f})" if ci else ""
            L.append(f"| {k} | {n} | mean {r['a']['mean']:.3f}, median {r['a']['median']:.3g} | "
                     f"mean {r['b']['mean']:.3f}, median {r['b']['median']:.3g} | {r['diff_mean']:+.3f}{ci_s} | "
                     f"{r['sign_test']['p_value']:.3f} (sign test, {r['sign_test']['better']} up / {r['sign_test']['worse']} down) |")
    return "\n".join(L)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    a, b = (json.loads(Path(p).read_text()) for p in argv[:2])
    res = compare(a, b)
    if "--json" in argv:
        print(json.dumps(res, indent=1))
    else:
        print(report(res, a.get("generator") or "A", b.get("generator") or "B"))


if __name__ == "__main__":
    main(sys.argv[1:])
