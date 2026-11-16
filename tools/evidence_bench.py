"""M-4: does the dense leg earn its place? Recall@5 of lexical search against hybrid
(lexical + embeddings, fused by reciprocal rank) on learner-written questions.

Golden file (one JSON object per line, written by the learner):
  {"q": "...", "lecture": "<lecture id or prefix>", "answer_line_ids": ["<line id>", ...]}
Line IDs come from `lecture search "<words>" --json`. A question counts as found when any of
the top five passages holds one of its answer lines.

Decision (NG-1 acceptance): ship hybrid only if it beats lexical by at least 10 points;
otherwise lexical only. Without STUDY_EMBED_MODEL only the lexical leg is measured.

  python3 tools/evidence_bench.py [--golden eval/golden/retrieval.jsonl] [--k 5]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def recall_at(con, rows, k, lectures_for, embed=None, model=None):
    import evidence
    found = 0
    for r in rows:
        hits = evidence.search(con, r["q"], k=k, lectures=lectures_for(r["lecture"]), embed=embed, embed_model=model)
        want = set(r["answer_line_ids"])
        found += any(want & set(h["line_ids"]) for h in hits)
    return found / len(rows) if rows else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--golden", default=str(HERE / "eval" / "golden" / "retrieval.jsonl"))
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args(argv)
    import evidence
    import lecture_kit as kit
    g = Path(a.golden)
    if not g.exists():
        sys.exit(f"no golden file at {g}: write questions with their answer line IDs first (see the docstring)")
    rows = [json.loads(l) for l in g.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("q") and r.get("answer_line_ids")]
    con = evidence.connect(kit.INDEX)
    lectures_for = lambda w: [i for i, _ in kit._index_targets(w)] or None
    lex = recall_at(con, rows, a.k, lectures_for)
    embed, model = kit._embedder()
    hyb = recall_at(con, rows, a.k, lectures_for, embed, model) if model else None
    decision = "hybrid" if hyb is not None and hyb - lex >= 0.10 else "lexical"
    rep = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "questions": len(rows), "k": a.k,
           "recall_lexical": round(lex, 3), "recall_hybrid": None if hyb is None else round(hyb, 3),
           "embed_model": model or None, "decision": decision, "target_questions": 40}
    out = HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-evidence-m4.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
