---
title: Evaluation
---

# Evaluation

Evaluation answers one question: did this change to a prompt, a model or the code make the study material better or worse? Everything runs locally; no results leave the machine.

## Suites

| Command | What | Typical time |
| --- | --- | --- |
| `tests/evals/run.sh --smoke` | 6 fixed cases: 2 questions, 2 notes, 2 card sets | about 12 minutes |
| `tests/evals/run.sh` | Full suite | well over an hour |
| `tests/evals/run.sh --judge-only` | Re-score cached outputs | judge time only |
| `tests/evals/run_judge_bench.sh` | Labelled claim/context pairs: how often is the judge right? | about 10 minutes |
| `eval/run_eval.py` | Slide reading on synthetic slides with exact answers | minutes |

Each run writes `eval/results/<timestamp>-<suite>/summary.json` with the models, prompt versions, case counts and metric means. That folder is the run history; it is ignored by git.

## How a run works

The generator model produces answers, notes and cards. Then the runner swaps in a **different** model as judge, scores the outputs with [DeepEval](https://github.com/confident-ai/deepeval) metrics (Faithfulness, Answer Relevancy, and G-Eval criteria for correctness, coverage and card quality), and swaps the generator back, even if the run fails.

A run refuses to start while capture is running in live mode.

## Reading results

- **Smoke runs have two cases per kind.** Compare pass or fail per case, not means; a 0.07 difference on two cases is noise.
- **Check what a failure is before fixing the model.** In the first full baseline, most failed question-answering cases were the judge penalising the answer's source list, which it could not see in the context. The harness now scores the answer body only.
- **Trust the judge only as far as the judge benchmark.** A perfect score on easy pairs says little; add hard pairs (derived claims, partial support).

### How sure a number is

Every pass rate in `summary.json` carries `ci95`, a 95% Wilson score interval. On the six smoke cases, 5 of 6 passing is "somewhere between 44% and 97%": enough to catch a collapse, not a small change. Quote the interval with the rate.

After each run, `run.sh` compares it with the previous run of the same suite and writes `compare.md`: per metric, the cases fixed and broken, and McNemar's exact test on those pairs. Compare any two runs by hand with:

```sh
tests/evals/compare_runs.py eval/results/<before>/summary.json eval/results/<after>/summary.json
```

A p-value above 0.05 does not mean "no change"; on a few cases it usually means the suite is too small to tell. The methods and references are on [Research basis](../project/research-basis.md).

## Adding cases from your own classes

Cases built from a real class are course material. Keep them in files named `tests/evals/session_*`, which git ignores and the content guard blocks. They run with the full suite on your machine.
