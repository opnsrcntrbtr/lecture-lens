"""Judge a lecture's notes against what was actually taught (local judge).

For each notes file in NOTES_EVAL (a JSON list of {"label", "notes", "folder"}):
- Faithfulness, per core section (TL;DR, Key concepts, Worked examples, How this
  applies): every claim must be supported by the class deck (deck.json) or the
  transcript digest (digest.md). Smaller cases than whole notes, so a local judge
  finishes and the failing section is visible.
- Coverage: does the whole note carry the deck's key points (the part recaps and
  the frameworks of the parts that were taught)?
Run via run_notes_eval.sh, which loads the judge and restores the generator.
"""
import json
import os
import re
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams as P

from judge import JUDGE
import rubric   # versioned judge instructions (JUDGE_RUBRIC)

CORE = ("TL;DR", "Key concepts", "Worked examples", "How this applies")


def _sections(md: str) -> dict[str, str]:
    parts = re.split(r"(?m)^## ", md)
    return {p.split("\n", 1)[0].strip(): p.split("\n", 1)[1].strip() for p in parts[1:] if "\n" in p}


def _context(folder: Path, budget: int = 9000) -> list[str]:
    deck = json.loads((folder / "deck.json").read_text()) if (folder / "deck.json").exists() else []
    chunks, cur = [], ""
    for s in deck:
        line = f"[deck {s['n']}] {s['title']}: " + re.sub(r"\s+", " ", s["text"]) + "\n"
        if len(cur) + len(line) > budget:
            chunks.append(cur); cur = ""
        cur += line
    if cur:
        chunks.append(cur)
    dg = folder / "digest.md"
    if dg.exists():
        t = dg.read_text()
        chunks += [t[i:i + budget] for i in range(0, len(t), budget)]
    return chunks


def _key_points(folder: Path) -> str:
    deck = json.loads((folder / "deck.json").read_text())
    # key slides: recaps and summaries by default; NOTES_KEY_TITLES (a regex) adds a course's own frameworks
    pat = re.compile(os.environ.get("NOTES_KEY_TITLES", r"recap|summary|takeaway|key points"), re.I)
    keep = [s for s in deck if pat.search(s["title"])]
    return "\n".join(f"- {s['title']}: " + re.sub(r"\s+", " ", s["text"])[:300] for s in keep)


CASES = []
for spec in json.loads(os.environ.get("NOTES_EVAL", "[]")):
    folder, md = Path(spec["folder"]), Path(spec["notes"]).read_text()
    ctx = _context(folder)
    secs = _sections(md)
    for name, body in secs.items():
        if name.startswith(CORE):
            CASES.append({"id": f"{spec['label']}:{name[:20]}", "kind": "faithful",
                          "case": LLMTestCase(input=f"Notes section '{name}'", actual_output=body[:6000], retrieval_context=ctx)})
    CASES.append({"id": f"{spec['label']}:coverage", "kind": "coverage",
                  "case": LLMTestCase(input="Lecture notes for the class", actual_output=md[:12000],
                                      expected_output=_key_points(folder))})

FAITHFUL = rubric.faithfulness(0.8)   # v2: DeepEval Faithfulness + claim support
COVERAGE = rubric.geval("coverage")


@pytest.mark.parametrize("c", CASES, ids=lambda c: c["id"])
def test_notes(c):
    assert_test(c["case"], metrics=FAITHFUL if c["kind"] == "faithful" else [COVERAGE])


CLAIMS = rubric.claim_warning()   # v2.1: reported, never gating


@pytest.mark.xfail(strict=False, reason="warning only: claim-support check (rubric v2.1)")
@pytest.mark.parametrize("c", [c for c in CASES if c["kind"] == "faithful"], ids=lambda c: "claims:" + c["id"])
def test_claims_warning(c):
    if not CLAIMS:
        pytest.skip("this rubric has no claim warning")
    assert_test(c["case"], metrics=CLAIMS)
