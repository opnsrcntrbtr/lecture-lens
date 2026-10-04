"""Metric lists for the study-pipeline eval suite. Judge: local oMLX (judge.py).

The generator (Qwen3.6-35B-A3B) never judges itself: every metric here runs on
the separate judge model, swapped in by run.sh.
"""
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.test_case import SingleTurnParams as P

from judge import JUDGE

# ---- Grounded Q&A (study.answer_from) ---------------------------------------
# The failure that matters most: an answer that sounds right but isn't in the
# lecture. Faithfulness guards that; Correctness checks it against the golden's
# expected answer; Relevancy catches answers that are grounded but off-question.
ASK_METRICS = [
    FaithfulnessMetric(threshold=0.8, model=JUDGE, async_mode=False),
    AnswerRelevancyMetric(threshold=0.7, model=JUDGE, async_mode=False),
    GEval(
        name="Correctness",
        criteria=(
            "The actual output answers the question with the same facts, numbers and relationships "
            "as the expected output. Extra correct detail is fine; any contradicted or changed fact, "
            "number or ordering is a serious error. Ignore style and length."
        ),
        evaluation_params=[P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT],
        threshold=0.7, model=JUDGE, async_mode=False,
    ),
]

# ---- Lecture notes (lecture_kit.make_notes) ----------------------------------
NOTES_METRICS = [
    FaithfulnessMetric(threshold=0.85, model=JUDGE, async_mode=False),
    GEval(
        name="Coverage",
        criteria=(
            "Every number, named framework, list item and relationship in the retrieval context "
            "appears correctly in the notes. Missing or altered items lower the score."
        ),
        evaluation_params=[P.ACTUAL_OUTPUT, P.RETRIEVAL_CONTEXT],
        threshold=0.7, model=JUDGE, async_mode=False,
    ),
]

# ---- Flashcards (lecture_kit.make_cards) -------------------------------------
CARDS_METRICS = [
    FaithfulnessMetric(threshold=0.85, model=JUDGE, async_mode=False),
    GEval(
        name="Card quality",
        criteria=(
            "Each card tests understanding (a why, when, trade-off or application), not trivia; its "
            "answer is correct according to the retrieval context; questions are not duplicates."
        ),
        evaluation_params=[P.ACTUAL_OUTPUT, P.RETRIEVAL_CONTEXT],
        threshold=0.7, model=JUDGE, async_mode=False,
    ),
]
