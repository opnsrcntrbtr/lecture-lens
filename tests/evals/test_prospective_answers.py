"""Judge prospective answers (drafted when the learner asked, before staff replied)
against the staff reply that came later. Run via run_prospective_eval.sh.

Agreement: same verdict and the same deciding criterion as the reply.
Calibration: confident drafts should agree; a draft that is unsure should say what is
not covered rather than invent it.
Faithfulness: claims rest on what was said before the question or the cited evidence.
"""
import json
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams as P

from judge import JUDGE

CASES = json.loads((Path(__file__).parent / ".prospective.json").read_text())

AGREEMENT = GEval(
    name="Answer agreement",
    evaluation_steps=[
        "The expected output is the reply staff gave in class; treat it as correct.",
        "Check whether the actual output reaches the same verdict or recommendation.",
        "Check whether it names the same deciding criterion, mechanism or example; partial credit for a compatible one.",
        "Penalise any statement that contradicts the expected output. Do not penalise brevity.",
    ],
    evaluation_params=[P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT], threshold=0.5, model=JUDGE, async_mode=False)

HEDGING = GEval(
    name="Calibrated hedging",
    evaluation_steps=[
        "Read the retrieval context: the lecture just before the question.",
        "If the context settles the question, the answer should be direct.",
        "If it does not, the answer should say what is not covered instead of asserting specifics as lecture content.",
        "Penalise invented numbers, names or citations that appear in neither the context nor general knowledge.",
    ],
    evaluation_params=[P.INPUT, P.ACTUAL_OUTPUT, P.RETRIEVAL_CONTEXT], threshold=0.6, model=JUDGE, async_mode=False)

FAITHFUL = FaithfulnessMetric(threshold=0.6, model=JUDGE, async_mode=False)


@pytest.mark.parametrize("c", CASES, ids=lambda c: c["input"][:50])
def test_prospective_answer(c):
    assert_test(LLMTestCase(input=c["input"], actual_output=c["actual_output"], expected_output=c["expected_output"],
                            retrieval_context=c["retrieval_context"]),
                metrics=[AGREEMENT, HEDGING, FAITHFUL])
