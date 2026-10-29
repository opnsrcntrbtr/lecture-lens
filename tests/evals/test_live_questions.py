"""Judge the live-class questions and Q&A follow-ups (local judge, no tracing).

Rubric after QGEval (Fu et al., EMNLP 2024): relevance, answerability and
pedagogical value scored separately, with fixed evaluation steps so scores are
comparable run to run; Faithfulness checks the draft answer against the
transcript window, the staff reply and the cited evidence. Run via
run_live_eval.sh, which loads the judge and restores the generator.
"""
import json
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase, SingleTurnParams as P

import rubric   # versioned judge instructions (JUDGE_RUBRIC)

HERE = Path(__file__).parent
CASES = json.loads((HERE / ".live_questions.json").read_text())

RELEVANCE, ANSWERABLE, PEDAGOGY = rubric.geval("relevance"), rubric.geval("answerability"), rubric.geval("pedagogy")
FAITHFUL = rubric.faithfulness(0.7)   # v2: DeepEval Faithfulness + claim support


@pytest.mark.parametrize("c", CASES, ids=lambda c: f"{c['kind']}:{c['input'][-60:]}")
def test_live_question(c):
    assert_test(LLMTestCase(input=c["input"], actual_output=c["actual_output"], retrieval_context=c["retrieval_context"]),
                metrics=[RELEVANCE, ANSWERABLE, PEDAGOGY, *FAITHFUL])


CLAIMS = rubric.claim_warning()   # v2.1: reported, never gating


@pytest.mark.xfail(strict=False, reason="warning only: claim-support check (rubric v2.1)")
@pytest.mark.parametrize("c", CASES, ids=lambda c: f"claims:{c['input'][-60:]}")
def test_claims_warning(c):
    if not CLAIMS:
        pytest.skip("this rubric has no claim warning")
    assert_test(LLMTestCase(input=c["input"], actual_output=c["actual_output"], retrieval_context=c["retrieval_context"]), metrics=CLAIMS)
