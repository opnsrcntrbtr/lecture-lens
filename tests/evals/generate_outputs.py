"""Phase G: run the real study code paths with the GENERATOR loaded, cache outputs.

Answers come from study.answer_from (the same function `study ask` uses) on the
golden's context; notes and cards from lecture_kit.make_notes / make_cards on
each lecture context. Judging happens later, with a different model loaded.

    python generate_outputs.py  ->  .outputs.json
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
STUDY = HERE.parent.parent
sys.path.insert(0, str(STUDY))
os.environ.setdefault("OMLX_MODEL", "Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx")

import study  # noqa: E402
import lecture_kit  # noqa: E402


_SOURCES_TAIL = re.compile(r"\n[ \t>*#_-]*sources used\b.*\Z", re.I | re.S)

def answer_body(answer):
    """The answer without its trailing 'Sources used' list.

    study.answer_from ends every answer with a source list (timestamps, app, page).
    The judge only sees the context text, so in the 2026-10-03 baseline it scored
    that list as fabricated or irrelevant: about 12 of 17 failed ask cases. The
    list is a UI feature, checked by the app; the judge scores the answer itself.
    """
    return _SOURCES_TAIL.sub("", answer).rstrip()


def rows_from(chunks):
    """Shape context chunks like screenpipe search rows (what answer_from receives)."""
    out = []
    for i, c in enumerate(chunks):
        src = "audio" if c.startswith("Transcript") else "accessibility"
        out.append({"ts": f"2026-09-29T10:{i:02d}:00Z", "src": src,
                    "where": "learn.example.edu · Lecture 12", "app": "Google Chrome", "text": c})
    return out


def main():
    # EVAL_DATASET / EVAL_LIMIT exist for smoke runs (a couple of cases end to end
    # before committing an hour of GPU to the full suite).
    goldens = json.loads((HERE / os.environ.get("EVAL_DATASET", ".dataset.json")).read_text())
    contexts = json.loads((HERE / os.environ.get("EVAL_CONTEXTS", "contexts.json")).read_text())
    limit = int(os.environ.get("EVAL_LIMIT", "0")) or None
    goldens, contexts = goldens[:limit], contexts[:limit]
    # Cases from real captured sessions (session_<date>_goldens.json / _contexts.json),
    # hand-checked against slides. Full runs include them; smoke runs name their own files.
    if os.environ.get("EVAL_SESSIONS", "1") == "1" and not limit:
        for f in sorted(HERE.glob("session_*_goldens.json")): goldens += json.loads(f.read_text())
        for f in sorted(HERE.glob("session_*_contexts.json")): contexts += json.loads(f.read_text())
    out = {"generator": study.MODEL, "prompt_versions": {
        "ask": study.PROMPT_VERSION, "notes": lecture_kit.NOTES_PROMPT_VERSION,
        "cards": lecture_kit.CARDS_PROMPT_VERSION}, "ask": [], "notes": [], "cards": []}
    t0 = time.time()
    for i, g in enumerate(goldens, 1):
        ctx = g.get("context") or []
        t = time.time()
        ans, used, _ = study.answer_from(g["input"], rows_from(ctx))
        out["ask"].append({"input": g["input"], "actual_output": answer_body(ans), "answer_with_sources": ans, "expected_output": g.get("expected_output"),
                           "retrieval_context": [r["text"] for r in used], "latency_s": round(time.time() - t, 2)})
        print(f"  ask {i}/{len(goldens)} {time.time() - t:.1f}s", file=sys.stderr, flush=True)
    for i, ctx in enumerate(contexts, 1):
        source = "\n\n".join(ctx)
        t = time.time()
        notes = lecture_kit.make_notes(f"Lecture segment {i}", source)
        cards = lecture_kit.make_cards(f"Lecture segment {i}", source, n=4)
        out["notes"].append({"input": f"notes for lecture segment {i}", "actual_output": notes,
                             "retrieval_context": ctx, "latency_s": round(time.time() - t, 2)})
        out["cards"].append({"input": f"flashcards for lecture segment {i}",
                             "actual_output": "\n".join(f"Q: {c.get('q')}\nA: {c.get('a')}" for c in cards) or "(no cards)",
                             "n_cards": len(cards), "retrieval_context": ctx})
        print(f"  notes+cards {i}/{len(contexts)} {time.time() - t:.1f}s", file=sys.stderr, flush=True)
    out["seconds"] = round(time.time() - t0, 1)
    (HERE / ".outputs.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"ask": len(out["ask"]), "notes": len(out["notes"]), "cards": len(out["cards"]),
                      "seconds": out["seconds"]}))


if __name__ == "__main__":
    main()
