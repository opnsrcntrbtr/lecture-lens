"""Judge instructions, versioned. Every eval suite builds its metrics here so the judge
reads the same rubric everywhere and results record which version scored them.

    JUDGE_RUBRIC=v2.1 (default) | v2 | v1

v1: the original QGEval-style steps (Oct 2026).
v2: v1 plus an explicit scoring contract, from calibration against the learner's own labels
    (tests/evals/calibration, 29 Oct 2026): v1 passed compound questions (answerability and
    pedagogy false-pass 43%), failed relevant questions whose concept sat outside the
    transcript window (relevance false-fail 57%), failed good notes on coverage, and missed a
    wrong cost figure in faithfulness. v2 separates those criteria and adds a claim-level
    support check that a faithfulness row must also pass.
v2.1: per metric, the better of v1 and v2 on the 50-row calibration set (29 Oct 2026):
    relevance, coverage and answerability use v2 (relevance agreement 50% -> 86%, coverage 4/4);
    pedagogy keeps v1 (v2 dropped it 71% -> 43%); faithfulness is gated on DeepEval only, and
    the claim-support check is reported as a warning (it caught two unsupported answers DeepEval
    passed, but failed four good ones and missed a wrong cost figure in long notes).
    The same judge at temperature 0 changed its verdict on 16 of 50 rows between two runs, so
    calibration and before/after checks take the majority of 3 runs (calibration.py --runs 3).
"""
from __future__ import annotations

import os

from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import SingleTurnParams as P

from judge import JUDGE

VERSION = os.environ.get("JUDGE_RUBRIC", "v2.1")
# v2.1 picks, per metric, which version's steps and threshold to use
MIX = {"v2.1": {"relevance": "v2", "coverage": "v2", "answerability": "v2", "pedagogy": "v1", "claims": "v2"}}

STEPS = {
    "v1": {
        "relevance": [
            "Read the retrieval context: what the lecturer said around the time the question was drafted, and any thread.",
            "Check the question is about a concept, framework, example or claim that appears in that context.",
            "For a follow-up, check it builds on the thread's reply instead of repeating the thread question.",
            "Penalise generic questions that would fit any lecture.",
        ],
        "answerability": [
            "Decide whether the lecturer or teaching assistant could answer the question in one or two minutes.",
            "Penalise questions that are ambiguous, contain several unrelated questions, or rest on a false premise given the context.",
        ],
        "pedagogy": [
            "Judge the cognitive level the question demands: recall is low; explain, apply, compare or evaluate is high.",
            "Higher scores for questions that expose a criterion, trade-off, edge case or application to a real product.",
            "Penalise logistics questions unless they help the learner prepare or practise.",
        ],
        "coverage": [
            "The expected output lists the deck's key points (part recaps and frameworks).",
            "For each key point, check whether the notes state it or its substance.",
            "Score the share of key points covered; do not reward points not in the expected output.",
        ],
    },
    "v2": {
        "relevance": [
            "Name the lecture concept, framework, example or claim the question is about. The retrieval context is only a short "
            "transcript window: a concept taught elsewhere in the same lecture still counts, so do not fail a question only "
            "because the window lacks its words.",
            "If the input is a follow-up under a thread, check that it builds on that thread's topic. A follow-up that ignores a "
            "logistics thread (recordings, polls, audio, schedule, teams, coding help) and switches to lecture content scores low.",
            "Penalise generic questions that would fit any lecture.",
            "Do not penalise compound phrasing, a missing source or an unsupported premise here; other metrics judge those.",
        ],
        "answerability": [
            "Count the distinct asks. A second sentence that only narrows the first, proposes an answer to it, or asks for an "
            "example of the same thing is the same ask. Two different asks (a different topic, or a separate yes/no decision "
            "about something else) score low.",
            "Decide whether staff could answer the single ask in one or two minutes. An open-ended debate with no answerable "
            "core, such as 'how do we reconcile X with Y', scores low.",
            "A premise the context does not support is not a failure; only a premise the context contradicts is.",
            "Judge the question only, never the drafted answer.",
        ],
        "pedagogy": [
            "If the question makes two different asks (see: a second question on a different topic), score it low.",
            "Otherwise judge the cognitive level: recall is low; explain, apply, compare or evaluate is high.",
            "Higher scores for a single ask that exposes a criterion, trade-off, edge case or application to a real product.",
            "Penalise logistics questions unless they help the learner prepare or practise.",
        ],
        "coverage": [
            "The expected output lists the deck's key points (part recaps and frameworks).",
            "For each key point, check whether the notes state its substance, in any wording, order or section.",
            "Score the share of key points covered. A few minor omissions still deserve a passing score; do not penalise extra "
            "detail or a different structure, and do not reward points not in the expected output.",
        ],
        "claims": [
            "List every specific claim in the actual output: names, products, examples, numbers, dates, and attributions such "
            "as 'the professor said' or 'the lecture shows'.",
            "For each claim, find it in the retrieval context. Numbers must match exactly, and any arithmetic shown must be correct "
            "against the context's figures.",
            "One claim that is absent from the context or contradicts it makes the score low. A hedge saying something is not "
            "covered is not a claim.",
        ],
    },
}
THRESHOLD = {"v1": {"relevance": 0.6, "answerability": 0.6, "pedagogy": 0.6, "coverage": 0.7, "claims": 0.7},
             "v2": {"relevance": 0.6, "answerability": 0.6, "pedagogy": 0.6, "coverage": 0.6, "claims": 0.7}}
NAMES = {"relevance": "Question relevance", "answerability": "Answerability", "pedagogy": "Pedagogical value",
         "coverage": "Coverage", "claims": "Claim support"}
PARAMS = {"relevance": [P.INPUT, P.RETRIEVAL_CONTEXT], "answerability": [P.INPUT, P.RETRIEVAL_CONTEXT],
          "pedagogy": [P.INPUT], "coverage": [P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT],
          "claims": [P.ACTUAL_OUTPUT, P.RETRIEVAL_CONTEXT]}


def resolve(kind: str, version: str | None = None) -> str:
    v = version or VERSION
    return MIX.get(v, {}).get(kind, v)


def geval(kind: str, version: str | None = None) -> GEval:
    v = resolve(kind, version)
    return GEval(name=NAMES[kind], evaluation_steps=STEPS[v][kind], evaluation_params=PARAMS[kind],
                 threshold=THRESHOLD[v][kind], model=JUDGE, async_mode=False)


def faithfulness(threshold: float = 0.7, version: str | None = None) -> list:
    """Gating faithfulness metrics. v1 and v2.1: DeepEval Faithfulness. v2: plus the claim-support
    check, and a row passes only if both pass."""
    v = version or VERSION
    m = [FaithfulnessMetric(threshold=threshold, model=JUDGE, async_mode=False)]
    return m + ([geval("claims", "v2")] if v == "v2" else [])


def claim_warning(version: str | None = None) -> list:
    """v2.1: the claim-support check, reported but not gating (empty for other versions)."""
    return [geval("claims", "v2")] if (version or VERSION) == "v2.1" else []


def metric_kind(name: str) -> str:
    """Map a metric name as results print it ('Answerability [GEval]') to a rubric kind."""
    n = name.lower()
    for k, label in NAMES.items():
        if n.startswith(label.lower()):
            return k
    return "faithfulness"
