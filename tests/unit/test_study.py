"""Unit tests for study.py's pure parts: retrieval ranking and context packing.
No model, no capture."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import study  # noqa: E402


def row(text, ts):
    return {"ts": ts, "src": "audio", "where": "", "app": "", "text": text}


FILLER = [row("The orbit diagram shows the planets in order around the sun. " * 20, f"2026-01-01T00:{i:02d}:00Z")
          for i in range(30)]
RELEVANT = row("Mitochondria produce ATP through oxidative phosphorylation in the inner membrane.",
               "2026-01-01T01:00:00Z")


def test_bm25_puts_the_relevant_row_first():
    ranked = study.bm25_rank("How do mitochondria produce ATP?", FILLER + [RELEVANT])
    assert ranked[0] is RELEVANT
    assert len(ranked) == len(FILLER) + 1          # nothing dropped by ranking


def test_late_relevant_capture_survives_a_tight_budget():
    # Packed in time order, thirty early filler rows fill the budget and the late answer is lost.
    rows = FILLER + [RELEVANT]
    assert RELEVANT not in next(study.chunks(rows, budget=5000))
    ctx = next(study.chunks(study.bm25_rank("mitochondria ATP", rows), budget=5000))
    assert ctx[0] is RELEVANT


def test_ties_keep_time_order():
    a, b = row("comets have tails", "t1"), row("comets have tails", "t2")
    assert study.bm25_rank("comets", [a, b]) == [a, b]
    assert study.bm25_rank("nothing matches", [a, b]) == [a, b]


def test_rare_terms_outweigh_common_ones():
    common = [row("the cell is a cell with a cell wall", f"t{i}") for i in range(5)]
    rare = row("the cell wall contains cellulose", "t9")
    assert study.bm25_rank("cell cellulose", common + [rare])[0] is rare
