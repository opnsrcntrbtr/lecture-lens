# VLM evaluation harness — design

Evaluates whether the vision model served by oMLX is good enough to describe
lecture slides inside `lecture_kit.py`, and keeps being good enough after
config or model changes. Fully offline; stdlib + Pillow + ffmpeg.

## 1. Requirements

**Functional**
- F1 Hallucination guard: with no image, a blank image, or a talking-head frame,
  the model must say so (or answer `SKIP`) and must not invent slide content.
- F2 Fidelity: titles, bullets, chart values and table cells reproduced exactly.
- F3 Structure: flowchart order, 2×2 quadrant membership, funnel stages, trend
  direction — relationships, not just labels.
- F4 Video: a short clip of several slides — are all slides recovered, in order?
- F5 Configurations: thinking off vs on, same cases, same seed.
- F6 Performance: latency per case (p50/p95), output tokens/s, model memory.

**Non-functional**
- Reproducible: dataset generated deterministically from code; temperature 0.
- Offline: talks only to 127.0.0.1:8001.
- Fast enough to rerun after any change: full run < ~20 min on the M4.
- Never trusts the model's own claim that it saw the image (see §3.3).

**Constraints**
- Single machine (M4, 48 GB), one resident 35B-A3B model (~21 GB).
- No new Python dependencies beyond Pillow (already installed).

## 2. High-level design

```
 gen_dataset.py ──► cases/<id>.jpg|.mp4 + cases.jsonl (ground truth)
                               │
 run_eval.py ── for config in {think_off, think_on}:
                 for case in cases:
                   vlm_client.ask(image|video, prompt) ─► oMLX /v1/chat/completions
                     returns {text, prompt_tokens, completion_tokens, latency}
                   received = image_received(prompt_tokens)   ◄─ baseline probe
                   score    = scorers[case.kind](text, truth, received)
                 ─► results/<ts>/results.jsonl  (one row per case×config)
                               │
 report.py ──► results/<ts>/report.md + summary.json (per-dimension pass rates,
                                                     latency, memory, failures)
```

The client and the `image_received` guard live in one module that
`lecture_kit.py` also imports, so production and evaluation share the exact code
path that failed before.

## 3. Deep dive

### 3.1 Case record (`cases.jsonl`)
```json
{"id": "chart_bar_01", "dim": "fidelity", "kind": "bar_chart",
 "media": "cases/chart_bar_01.jpg", "prompt": "describe",
 "truth": {"phrases": ["Model Evaluation: Precision vs Recall"],
           "numbers": ["0.82", "0.64", "0.72"],
           "order": [], "pairs": [], "expect_skip": false,
           "forbidden": []}}
```
- `phrases` — must appear (normalized: case, whitespace, punctuation, ≥0.9 fuzzy)
- `numbers` — must appear exactly (after normalizing "," and "%")
- `order` — labels whose first mentions must appear in this order (flows, video)
- `pairs` — (group, member) co-occurrence on the same line (quadrants, table rows)
- `expect_skip` — negative controls
- `forbidden` — plausible-but-absent content; mentioning it is a hallucination

### 3.2 Scoring (pure functions, no model involved)
| Dimension | Score | Pass |
|---|---|---|
| fidelity | recall of phrases and numbers | ≥ 0.9 |
| structure | 0.5·label recall + 0.5·(order agreement or pair recall) | ≥ 0.8 |
| hallucination | 1 if skip/“no image” and no forbidden token, else 0 | = 1 |
| video | slide-title recall × order agreement | ≥ 0.8 |

An LLM-as-judge (e.g. deepeval with the local 27B) was considered and deferred:
it adds a second model's variance to the measurement. Revisit for free-form
"explain this diagram" quality, where string matching under-credits paraphrase.

### 3.3 The silent-drop guard
Measured once per run: `baseline = prompt_tokens(prompt, no image)`. A request
counts as *image received* only if `prompt_tokens ≥ baseline + 64` (one
low-resolution image is ≥ ~256 tokens with this processor: patch 16, merge 2).
If not received, the case is scored **INFRA_FAIL**, never as a model answer.
In production, `lecture_kit` refuses the description instead of storing it.

### 3.4 Errors and retries
One retry on timeout/5xx; then the row is recorded with `error`, the run
continues. The run aborts early only if the first 3 image cases all fail the
received check (engine is in text-only mode — nothing downstream is meaningful).

### 3.5 Memory and speed
- `current_model_memory` from `GET /health` before and after the run.
- Per case: wall latency, `completion_tokens / (latency - prefill)` where the
  server reports it, else tokens / latency.
- Vision feature cache (oMLX SSD cache) is noted: the second config re-uses
  encoded images, so think-on latency is measured with a warm cache — reported
  as such rather than hidden.

## 4. Scale and reliability
N ≈ 30 cases × 2 configs ≈ 60 calls; at ~5–30 s each → 5–20 min. Sequential on
purpose: concurrency would distort latency numbers on a single-GPU machine.

## 5. Trade-offs
| Decision | Chosen | Cost | Revisit when |
|---|---|---|---|
| Synthetic vs real slides | synthetic (exact truth) | misses real portal styling, video compression artefacts | a few real course portal keyframes are available |
| String scoring vs LLM judge | deterministic | under-credits good paraphrase | evaluating explanation quality |
| Sequential calls | yes | slower run | never on one GPU |
| Shared client with production | yes | eval changes can affect prod | — (that is the point) |
