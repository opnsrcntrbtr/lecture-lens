"""Generate goldens from the lecture contexts with DeepEval's Synthesizer.

Run with the JUDGE loaded (run.sh does this). Uses the Python API rather than
`deepeval generate` only because the CLI cannot turn the judge's server-side
thinking off per request (see judge.py); the method is the same: contexts.

    python generate_goldens.py [--per-context 3]
"""
import argparse
import json
from pathlib import Path

from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import StylingConfig

from judge import JUDGE

HERE = Path(__file__).parent

ap = argparse.ArgumentParser()
ap.add_argument("--per-context", type=int, default=3)
a = ap.parse_args()

contexts = json.loads((HERE / "contexts.json").read_text())
synth = Synthesizer(
    model=JUDGE, async_mode=False,
    styling_config=StylingConfig(
        scenario="A learner revising a recorded lecture of an executive AI product management course",
        task="Answer the learner's question using only what the lecture said and showed",
        input_format="A short natural question a learner would type, sometimes about a number, "
                     "a framework, an ordering or a trade-off shown on a slide",
        expected_output_format="A concise answer (1-4 sentences) stating the exact facts and numbers from the lecture",
    ),
)
goldens = synth.generate_goldens_from_contexts(
    contexts=contexts, include_expected_output=True, max_goldens_per_context=a.per_context)
# Write the dataset ourselves, first thing: Synthesizer.save_as rejects dotted
# names like ".dataset", and a 35-minute generation must never be lost to a
# save error. Same fields save_as would write.
path = HERE / ".dataset.json"
path.write_text(json.dumps([{"input": g.input, "expected_output": g.expected_output, "context": g.context,
                             "source_file": g.source_file} for g in goldens], indent=2))
print(json.dumps({"goldens": len(goldens), "path": str(path), "judge_calls": JUDGE.calls,
                  "judge_seconds": round(JUDGE.seconds, 1)}))
