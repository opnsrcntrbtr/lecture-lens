"""DeepEval suite for the study pipeline (no tracing: results stay on this Mac).

Outputs are generated beforehand by generate_outputs.py with the GENERATOR
model loaded; this file only judges them, with the JUDGE model loaded. Run via:

    ./run.sh            (does the model swaps and writes summary.json)
    deepeval test run test_study.py     (if the judge is already loaded)
"""
import json
from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase

from metrics import ASK_METRICS, CARDS_METRICS, NOTES_METRICS

HERE = Path(__file__).parent
OUT = json.loads((HERE / ".outputs.json").read_text())


@pytest.mark.parametrize("row", OUT["ask"], ids=lambda r: r["input"][:60])
def test_ask(row):
    assert_test(
        test_case=LLMTestCase(input=row["input"], actual_output=row["actual_output"],
                              expected_output=row["expected_output"],
                              retrieval_context=row["retrieval_context"]),
        metrics=ASK_METRICS,
    )


@pytest.mark.parametrize("row", OUT["notes"], ids=lambda r: r["input"])
def test_notes(row):
    assert_test(
        test_case=LLMTestCase(input=row["input"], actual_output=row["actual_output"],
                              retrieval_context=row["retrieval_context"]),
        metrics=NOTES_METRICS,
    )


@pytest.mark.parametrize("row", OUT["cards"], ids=lambda r: r["input"])
def test_cards(row):
    assert_test(
        test_case=LLMTestCase(input=row["input"], actual_output=row["actual_output"],
                              retrieval_context=row["retrieval_context"]),
        metrics=CARDS_METRICS,
    )
