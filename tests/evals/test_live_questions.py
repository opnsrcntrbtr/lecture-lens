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
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams as P

from judge import JUDGE

HERE = Path(__file__).parent
CASES = json.loads((HERE / ".live_questions.json").read_text())

RELEVANCE = GEval(
    name="Question relevance",
    evaluation_steps=[
        "Read the retrieval context: what the lecturer said around the time the question was drafted, and any thread.",
        "Check the question is about a concept, framework, example or claim that appears in that context.",
        "For a follow-up, check it builds on the thread's reply instead of repeating the thread question.",
        "Penalise generic questions that would fit any lecture.",
    ],
    evaluation_params=[P.INPUT, P.RETRIEVAL_CONTEXT], threshold=0.6, model=JUDGE, async_mode=False)

ANSWERABLE = GEval(
    name="Answerability",
    evaluation_steps=[
        "Decide whether the lecturer or teaching assistant could answer the question in one or two minutes.",
        "Penalise questions that are ambiguous, contain several unrelated questions, or rest on a false premise given the context.",
    ],
    evaluation_params=[P.INPUT, P.RETRIEVAL_CONTEXT], threshold=0.6, model=JUDGE, async_mode=False)

PEDAGOGY = GEval(
    name="Pedagogical value",
    evaluation_steps=[
        "Judge the cognitive level the question demands: recall is low; explain, apply, compare or evaluate is high.",
        "Higher scores for questions that expose a criterion, trade-off, edge case or application to a real product.",
        "Penalise logistics questions unless they help the learner prepare or practise.",
    ],
    evaluation_params=[P.INPUT], threshold=0.6, model=JUDGE, async_mode=False)

FAITHFUL = FaithfulnessMetric(threshold=0.7, model=JUDGE, async_mode=False)


@pytest.mark.parametrize("c", CASES, ids=lambda c: f"{c['kind']}:{c['input'][-60:]}")
def test_live_question(c):
    assert_test(LLMTestCase(input=c["input"], actual_output=c["actual_output"], retrieval_context=c["retrieval_context"]),
                metrics=[RELEVANCE, ANSWERABLE, PEDAGOGY, FAITHFUL])
