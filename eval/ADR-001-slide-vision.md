# ADR-001: How lecture_kit uses a vision model to describe slides

**Status:** Accepted
**Date:** 2026-09-29
**Deciders:** project maintainer
**Evidence:** `eval/results/` — 20260929-221333 (clean, both modes), -222019-cases_stress (realistic, both modes), -223529-cases_stress_1600, and the final regression: -224200 (clean) and -224350-cases_prod (production preprocessing)

## Context

`lecture_kit` turns keyframes of lecture videos (captured by screenpipe as full
Retina screenshots of Chrome) into slide descriptions for notes, flashcards and
Obsidian. Everything must stay on this MacBook Pro M4 (48 GB). The candidate
model is `Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx` (a natively multimodal 35B MoE with
3B active and a 27-layer ViT), served by oMLX 0.7.0rc1.

The following was discovered while testing, and it shapes the decision:

1. **Silent image drop.** oMLX loaded the model text-only (`force_lm=True`)
   after a text benchmark. Image parts were stripped (`prompt_tokens` 37, not
   919), and the model then *confidently invented* slide contents: "Recall bar
   is 0.95", title "Model Performance". Nothing in the response signalled it.
2. **`top_p` was 950.0** in the saved model settings (intended 0.95).
3. **Native video is rejected**: `400: Video input is not supported by oMLX`,
   even though the architecture supports video.
4. **Page chrome pollutes descriptions.** On realistic captures, 9 of 20 answers
   (thinking off) and 18 of 20 (thinking on) repeated the tabs, URL or course
   sidebar as if they were slide content.
5. **A lecture title in the prompt flipped good slides to SKIP** with the first
   prompt wording. Production always passes the title, so every real slide
   would have been dropped. Only an end-to-end run of the production path caught it.

## Decision

1. **Model and path:** use `Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx` through oMLX's
   OpenAI-compatible endpoint, via one shared client (`eval/vlm_client.py`) that
   both production and the eval use.
2. **Thinking off** for slide description (`OMLX_VL_THINK=false`).
3. **Never trust a description without proof the image arrived.** A response
   counts only if `prompt_tokens` exceeds the text-only baseline by at least
   64 tokens per image. Otherwise the description is discarded and a
   "reload as VLM" warning is logged. `lecture doctor` probes with a real
   image ("PROBE 4721") rather than checking that the model is listed.
4. **Preprocess every keyframe:** crop to the lecture player, found as the
   region that changes across the session's keyframes (`eval/player_crop.py`),
   then cap the long edge at 1600 px.
5. **Video = sampled frames sent as images**, never `video_url`.
6. **Content-first prompt** with SKIP as the explicit exception; the eval always
   runs with a lecture title in the prompt, as production does.

## Options considered

### A. Qwen3.6-35B-A3B via oMLX, thinking off (chosen)
| Dimension | Assessment |
|---|---|
| Quality | clean 21/22 (the one miss is native video, rejected by the server) · **production-input 21/21** |
| Speed | p50 4.7 s clean · **5.3 s production-input** (p95 8.4 s) |
| Tokens | 1,402 image tokens per production keyframe vs 2,690 for a raw Retina frame (−48%) |
| Memory | 23.8 GB resident, unchanged across runs; already loaded for text use |
| Complexity | Low: same server as the text model |

**Pros:** best measured quality; no extra model in RAM; thinking off is also the
most robust against hallucination on realistic frames.
**Cons:** depends on oMLX loading the model in VLM mode. That is mitigated by the
received-guard and the doctor probe.

### B. Same model, thinking on
| Dimension | Assessment |
|---|---|
| Quality | 22/22 clean, but **15/21 realistic**: failed 3 of 5 hallucination checks by describing the web page instead of SKIP; one answer leaked its reasoning into the content |
| Speed | p50 8.6 s clean, 22.6 s realistic; p95 up to 66 s |
| Leakage | 18 of 20 answers repeated page chrome |

**Rejected:** slower and less reliable on exactly the frames production sees.

### C. Dedicated smaller VL model (e.g. Qwen3-VL-8B-Instruct-4bit, ~5 GB)
**Not needed:** option A already passes every check, and a second model costs
RAM alongside the 27B text model. Revisit if the 35B must be unloaded for
memory, or if real-portal accuracy falls short.

### D. OCR / accessibility text only
**Rejected as the primary path:** it loses diagram structure (flow order,
quadrants, hierarchy), which is the point of describing slides. It remains the
automatic fallback whenever vision is unavailable.

## Trade-off analysis

The deciding evidence came from the realistic stress set, not the clean one.
On clean slides both modes pass everything, so the clean set alone would have
suggested "thinking on, for safety". On realistic captures, thinking on is the
*unsafe* option: it reasons its way into describing whatever text is on screen.
Cropping to the player and capping resolution removes the chrome problem at the
source rather than relying on the prompt alone, and cuts image tokens by 48%.

The received-guard costs one extra tiny text-only request per distinct prompt
(cached per run). That is negligible next to the cost of confident fabrication
entering study notes.

## Consequences

- **Easier:** slide notes carry verbatim numbers and real diagram structure;
  model or config changes are checked by rerunning `eval/run_eval.py` (~3 min per set).
- **Harder:** oMLX upgrades or model swaps must be re-evaluated; the eval is the gate.
- **Revisit:**
  - when real course portal keyframes exist (grant screen recording, then add 10–20 hand-labelled frames);
  - if oMLX adds `video_url` support (compare against frame sampling);
  - the player-crop heuristic on sessions that are mostly a talking head (few changes, so it falls back to full frames).

## Action items

1. [x] Fix `top_p` 950 → 0.95; reload the model as VLM (verified: `VLMBatchedEngine`, 919 prompt tokens).
2. [x] Shared client with image-received guard; `lecture doctor` image probe.
3. [x] Player crop + 1600 px cap in `lecture_kit`.
4. [x] Content-first prompt; the eval passes a lecture title.
5. [ ] Maintainer: grant Screen & System Audio Recording, then capture one real lecture and run `lecture build last`.
6. [ ] Add 10–20 real, hand-labelled course portal keyframes as `eval/cases_real/`.
7. [ ] Avoid running oMLX text accuracy benchmarks on this model while lectures are processing (they reload it text-only); or re-run `lecture doctor` afterwards.
