# SPEC-2026: Study Pipeline & Local-AI Architecture (`lecture-lens`)

**Status:** Proposed  
**Date:** 2026-11-16  
**Author:** local-ai-edtech (swarm)  
**Scope:** `lecture-lens` pipeline, local oMLX/MLX-VLM inference, multimodal alignment, citation grounding, FSRS/Anki & Obsidian graph linking, and eval-gated upgrades.

---

## 1. Non-Negotiable Invariants (`CLAUDE.md` & `app/docs/`)

Every proposed change in this specification MUST preserve the existing architectural invariants:

1. **Stdlib-only Python 3 & zsh for core runtime:** `lecture_kit.py`, `live_class.py`, `live_qa.py`, `curate.py`, `cite.py`, `name_guard.py`, `companion_server.py`, `study.py`, and `study_aids.py` run on system Python 3 with zero third-party runtime dependencies (Pillow is optional with graceful fallback). Heavy ML/eval packages (`deepeval`, `laya`, `py-fsrs` if adopted for offline simulation) stay isolated in `.venv-eval/`.
2. **Nothing leaves the machine:** All capture (`127.0.0.1:3030`), inference (`127.0.0.1:8001`), and browser-extension bridging (`127.0.0.1:47110`) remain strictly loopback-only with telemetry disabled (`DEEPEVAL_TELEMETRY_OPT_OUT=1`).
3. **Deterministic vs. LLM boundary:** Code owns session segmentation, keyframe deduplication, player-box cropping, name redaction (`name_guard.py`), citation moment matching (`cite.py`), and file layout. The local LLM/VLM is invoked **only for judgement** (describing a slide, writing notes, generating cards/questions, coaching).
4. **Stable `--json` contracts for `Lecture Lens.app`:** CLI entrypoints (`sp-mode --json`, `lecture doctor --json`, `lecture list --json`, `lecture build --json`, `study ask --json`, `lecture curate --json`, `lecture cite --json`, `lecture after --json`) emit exactly one JSON document on `stdout` and progress/warnings on `stderr` (verified by `check.sh`).
5. **Single-shot RAG in the app (`03-ai-first-review.md`):** No autonomous tool-calling or multi-step agent loops inside `Lecture Lens.app`; captured screen/web text is always framed as untrusted quoted data.

---

## 2. Current State & Gaps in `lecture-lens`

### 2.1 Deterministic vs. LLM Boundary
- **Current state:** `lecture-lens` enforces a clean split across its modules:
  - `lecture_kit.py` handles DB/HTTP queries (`HttpDB`), sessionization (`sessions()`), dhash/Jaccard keyframe picking (`keyframes()`), chunked MP4 frame extraction (`extract_from_chunk()`), and `manifest.json` generation.
  - `name_guard.py` deterministically strips attendee names using dictionary lookup, phonetic similarity (`phonetic_key()`), Q&A panel author extraction, and persisted `Roster` decisions before any text reaches the LLM or disk.
  - `cite.py` deterministically computes supporting transcript moments (`moments()` via content-word overlap in a `±10 min` window) and active slide (`slide_at()`) with zero LLM calls.
  - `curate.py` enforces deterministic post-generation filtering (`parse()`, `repeats()`, `grounded()`) on LLM-proposed live-class questions.
- **Gaps:**
  1. **No deterministic claim-to-source verification in `make_notes()` / `make_cards()`:** `lecture_kit.py` prompts the LLM to cite `[00:12:30]` and `[slide 4]`, but does not deterministically verify that cited timecodes or slide numbers actually exist in `manifest.json` / `transcript.md` / `slides.md` or share content words with the claim.
  2. **Prompt-level vs. structural untrusted-input isolation:** While prompts quote captured text, long-context builds (`PART_CHARS` digesting) concatenate OCR, portal text, and Q&A without structured delimiters per source type.

### 2.2 Slide Keyframe Deduplication & Vision Processing
- **Current state (`lecture_kit.py:keyframes()`, `eval/player_crop.py`, `eval/vlm_client.py`):**
  - Prefers `capture_trigger='visual_change'` frames, computes 64-bit `dhash()` (Hamming distance `< 12`), and breaks template ties using OCR token Jaccard (`len(toks) >= 40` and `_jaccard < 0.5`).
  - Caps output at `max_frames=40` via uniform index stepping (`step = len(picked) / max_frames`).
  - Crops keyframes to the detected lecture player (`player_box()`) and caps the long edge at `1600 px` (`STUDY_VL_MAX_EDGE=1600`), cutting image tokens by ~48% (1,402 vs. 2,690 tokens) and eliminating browser-chrome leakage (`ADR-001`).
  - Uses `eval/vlm_client.py` with `OMLX_VL_THINK=false` and a **received-image guard** (`prompt_tokens` must exceed the text-only baseline by `>= 64` tokens/image) to catch oMLX's silent `force_lm=True` text-only reload bug.
- **Gaps:**
  1. **Uniform subsampling drops dense slide transitions:** When `len(picked) > 40`, uniform stepping (`picked[int(i * step)]`) can drop dense slide bursts in technical sections while keeping redundant frames from slow Q&A segments.
  2. **Fixed `dhash` threshold fails on animated builds & talking-head overlays:** Incremental bullet reveals or small picture-in-picture lecturer movements can either trigger duplicate keyframes (burning ~5.3 s/frame VLM latency) or fall below `distance=12` when OCR is sparse (e.g., architecture diagrams).
  3. **No slide-to-transcript semantic/temporal alignment:** `build()` passes `SLIDES:` and `TRANSCRIPT:` as two separate blocks (or `DIGEST OF THE FULL TRANSCRIPT` when over `STUDY_CTX_CHARS`). When a deck PDF is present (`deck_source()`), `KEYFRAME TIMES` only includes a `[:90]` char snippet, losing fine-grained alignment between what the lecturer said at `[hh:mm:ss]` and which slide/diagram was on screen.
  4. **DRM video blind spot (`ADR-004`):** Widevine-protected course portal playback releases screen capture (`SCREENPIPE_DRM_KEEP_AUDIO=1`), so `slides/` is empty unless a course PDF is manually ingested.

### 2.3 Live Class & Q&A Pipeline (`live_class.py`, `live_qa.py`, `curate.py`, `cite.py`)
- **Current state:**
  - `live_class.py` (1,051 lines) tails audio chunks (`finished_files()`, `transcribe()` with Whisper), detects slide changes (`new_slides()`, `ocr_frame()`), parses the Zoom/Meet Q&A panel (`live_qa.parse_panel()`, `build_threads()`), and runs `curate.run()` in rounds to surface `"now"` and `"later"` questions in `top_questions.md` and `questions.jsonl`.
  - `live_qa.py` supports prospective question drafting (`needs_prospective()`, `prospective_prompt()`) and `qa_export.py` / `cmd_qa_import` reconciles Zoom's post-class Q&A export.
- **Gaps:**
  1. **Lexical-only grounding in `cite.py` and `curate.py`:** `cite.moments()` and `curate.grounded()` rely solely on exact stemmed/lowercased word overlap (`min_shared=2` or `3`). Paraphrased concepts (e.g., "KV-cache paging" vs. "PagedAttention", or Whisper STT homophone errors) miss valid moments.
  2. **No live slide-to-utterance chaptering:** `live_class.py` tracks `slides.jsonl` and `whisper_segments.jsonl` independently; `curate.prompt()` receives `slides[-4:]` titles rather than the exact transcript span spoken *while* each slide was active.
  3. **Post-class `qa.md` vs. `notes.md` linkage:** `qa_src` is appended to `slide_src`, but Q&A threads are not cross-linked to the specific `notes.md` concept headings or Obsidian `[[Concept]]` nodes.

### 2.4 Companion Browser Extension (`extension/`, `companion_server.py`, `portal.py`)
- **Current state:**
  - MV3 Chrome extension (`manifest.json` v0.1.0) scoped to `learn.example.edu/learner/*` and `learn.example.edu/*`, posting events (`page`, `progress`, `bookmark`, `quiz_submitted`, `paused_for_question`) to `127.0.0.1:47110` with `X-Companion-Token` auth and no CORS (`do_OPTIONS` returns 405).
  - `companion_server.py` persists `lectures/portal/segments/<sid>.json|.md`, `events.jsonl`, `bookmarks.jsonl`, and `quiz_log.jsonl`.
  - `lecture_kit.py` uses `portal_segments()` for exact page text (`page_text.md`) and `cmd_coach` explains submitted quizzes via `study_aids.py`.
- **Gaps:**
  1. **Bookmarks (`Command+Shift+K`) are under-utilized in `build()`:** `bookmarks.jsonl` records learner-flagged moments during portal playback, but `lecture_kit.build()` does not boost or pin bookmarked timestamps in `keyframes()`, `notes.md`, or `cards.tsv`.
  2. **Quiz coaching (`cmd_coach`) is decoupled from spaced repetition:** Wrong/uncertain quiz submissions in `quiz_log.jsonl` are explained once by `cmd_coach`, rather than automatically generating targeted remedial flashcards or adjusting review priority.
  3. **Extension scope:** Only matches `learn.example.edu` domains; Zoom web client (`zoom.us/wc`) or external reference tabs have no companion telemetry.

### 2.5 Evaluation Harness (`eval/` & `tests/evals/`)
- **Current state:**
  - **Vision eval (`eval/`):** 22 synthetic slides (`gen_dataset.py`) + realistic Retina Chrome stress captures (`gen_stress.py --max-edge 1600`), deterministic scorer (`test_scoring.py`, 19 self-tests), and `run_eval.py`. Gated `Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx` at 21/21 production-input accuracy (`ADR-001`).
  - **Text/RAG eval (`tests/evals/`):** DeepEval suite (`FaithfulnessMetric`, `AnswerRelevancyMetric`, `GEval` Correctness/Coverage/Card quality) with `omlx_swap.py` swapping between the 35B generator and 27B judge (`run.sh`, `--smoke` 6-case gate), plus `laya_judge.py` (421M System-1) vs. 27B judge benchmark (`run_judge_bench.sh`) and `calibration.py` / `rubric.py`.
- **Gaps:**
  1. **Same-family judge bias (`03-ai-first-review.md` HIGH #1):** Both generator (`Qwen3.6-35B-A3B`) and judge (`Swift-Qwen3.8-27b-oQ4e-mtp`) belong to the Qwen family, risking perplexity-driven self-preference bias.
  2. **Missing `cases_real/` vision set (`ADR-001` Action #6):** Vision eval still relies on synthetic/stress slides rather than hand-labelled real course portal/Zoom keyframes.
  3. **Feedback-to-golden loop is manual:** `Lecture Lens.app` writes 👍/👎 corrections to `feedback/feedback.jsonl`, and `calibration.py` exists, but there is no automated `lecture eval-promote` step turning 👎 corrections into regression cases in `tests/evals/`.
  4. **Judge latency:** 27B judge at ~10 tok/s requires `DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE=1200` for Faithfulness claim extraction.

---

## 3. Research Findings & Nov 2026 References

All external references below are grounded in November 2026 web search results:

| Domain | Key Finding / Pattern | Application to `lecture-lens` | Source |
|---|---|---|---|
| **Apple Silicon VLM / MLX** | `mlx-vlm` provides native unified-memory vision+text inference on Apple Silicon with APC/KV-cache quantization; 4-bit 8B (`Qwen3-VL-8B-Instruct-4bit`) fits 24–32 GB Macs, while 4-bit 27B–35B MoE models fit 48 GB Macs comfortably alongside macOS (~8 GB overhead). | Keep `Qwen3.6-35B-A3B` (4-bit, ~23.8 GB) as single-server VLM+LLM on M4 48 GB; retain `OMLX_VL_THINK=false` and the `prompt_tokens` received-image guard to prevent silent `force_lm=True` text-only fallback. | mlx-vlm (GitHub), Hardware Sizing |
| **Multimodal Slide–Audio Alignment** | Unsupervised lecture segmentation (e.g., TW-FINCH clustering + visual/OCR/narration matching) and hierarchical discourse parsing outperform fixed-interval sampling because slide transitions are sparse relative to continuous speech; **$M^3AV$** and **SVLTA** establish benchmarks for sparse visual-to-speech synchronization. | Group transcript segments into **slide-bounded windows** `t_slide_i, t_slide_{i+1})` before summarization/card generation, replacing uniform `max_frames=40` stepping with salience/stability-weighted keyframe selection. | [Unsupervised Lecture Segmentation, M3AV Dataset, SVLTA (CVF) |
| **Citation Grounding & Faithfulness** | **Citation faithfulness** (Wallat et al., ICTIR) distinguishes genuine source reliance from *post-rationalization* (where LLMs generate from parametric memory and attach plausible `[n]` tags). The **AIS** standard and 3-step atomic claim entailment (claim extraction → context entailment → ratio) catch ghost citations and granularity mismatches. | Add a deterministic post-generation citation verifier in `lecture_kit.py` / `cite.py` that checks every `[hh:mm:ss]` and `[slide N]` reference against actual timecodes/slides and flags unsupported claims; pair structured output with span-level attribution. | Citation Faithfulness (TU Delft), Ragas Faithfulness |
| **FSRS Spaced Repetition & Anki** | **FSRS** (DSR: Difficulty, Stability, Retrievability; 21 parameters) reduces review load by **20–40%** vs. SM-2 at equal retention (target `0.85–0.90`). Native in Anki ≥ 23.10; **`py-fsrs`** (Open Spaced Repetition) provides lightweight Python scheduling/simulation. | Emit Anki-native headers with stable card GUIDs, `slide`/`timecode`/`module` tags, and source links so Anki's built-in FSRS scheduler preserves review history across lecture rebuilds; optionally support local FSRS review simulation in `.venv-eval`. | py-fsrs (GitHub), Anki FSRS Guide |
| **DeepEval & LLM-as-Judge Calibration** | Same-family judges suffer perplexity-based **self-preference bias**. Best practices: **cross-family judge decoupling**, **System-1 calibrated decision models** (e.g., DeepEval **`JevEval`** / Laya) outputting calibrated probabilities instead of CoT text, **DAG/programmatic checks** for deterministic constraints, and **Cohen's κ** calibration against a human-labeled golden set. | Decouple judge from Qwen family (e.g., Gemma/Llama/Mistral judge in oMLX) or use calibrated Laya (`kappa >= 0.6` vs. the learner's `feedback.jsonl`); move schema/citation checks to deterministic Python assertions before invoking the judge. | Self-Preference Bias (OpenReview), DeepEval JevEval & DAG, Confident AI Best Practices |

---

## 4. Proposed Enhancements & Design Specifications

### 4.1 Multimodal Slide–Transcript Temporal Alignment
**Goal:** Replace loose, separate `TRANSCRIPT:` + `SLIDES:` concatenation with deterministic **slide-bounded alignment** (`aligned_segments.json`), improving note faithfulness and reducing context waste.

1. **Salience- & Stability-Weighted Keyframe Selection (`lecture_kit.py:keyframes()`):**
   - Keep `dhash()` + OCR Jaccard dedup, but add a **minimum dwell/stability filter** (e.g., ignore transient transitions `< 2 s` if a stable frame follows within `5 s`) and **bookmark pinning** (any timestamp in `lectures/portal/bookmarks.jsonl` within the session window is pinned as a mandatory keyframe).
   - When `len(picked) > max_frames`, replace uniform index stepping with **information-density greedy selection** (retaining frames with highest OCR token novelty + duration coverage + any pinned bookmarks).
2. **Deterministic Slide-Bounded Windowing (stdlib-only):**
   - Given sorted keyframes $S_1 \dots S_k$ at offsets $t_1 \dots t_k$, partition `transcript_lines` (or `whisper_segments.jsonl`) into windows $W_i = [t_i, t_{i+1})$.
   - When a course PDF deck is present (`deck_source()`), match each keyframe $S_i$ to its best deck slide via deterministic token Jaccard (`slides.screen_text` $\cap$ `deck[j]`), annotating `deck[j]` with exact `[active: mm:ss–mm:ss]` spans.
   - Emit `aligned_segments.json` in the lecture folder (read-only for `Lecture Lens.app` to enable future slide-to-transcript jump without changing existing `--json` CLI shapes).
3. **Chunked Digesting Along Slide Boundaries:**
   - Update `_split_lines()` in `lecture_kit.py` to prefer splitting `PART_CHARS` chunks at slide boundaries $W_i$ rather than arbitrary line counts, preserving visual+spoken context together.

### 4.2 Strict Citation Grounding & Verification
**Goal:** Eliminate post-rationalized or "ghost" citations in `notes.md`, `cards.tsv`, `study ask`, and `curate.py`.

1. **Deterministic Citation Linter (`cite.py`):**
   - Add `verify_citations(text, valid_slides, transcript_segments, min_shared=1) -> list[dict]` (pure stdlib):
     - Parses every `[hh:mm:ss]` / `[mm:ss]` and `[slide N]` reference in `notes.md` and `cards.tsv`.
     - Verifies the timestamp falls within `[0, duration]` and the slide index `1 <= N <= len(slides)`.
     - Checks that the sentence/bullet containing `[hh:mm:ss]` shares at least `min_shared` content words (or numeric tokens) with transcript lines in `±90 s` of that timestamp (or with `slides[N-1]`'s `description`/`screen_text`).
     - Flags ungrounded or out-of-range citations in `manifest.json` (`"citation_warnings": [...]`) and stderr, never silently passing fabricated timecodes into Obsidian or Anki.
2. **Two-Pass / Constrained Attribution for `study ask`:**
   - Keep single-shot RAG for speed, but include explicit chunk IDs `[C1]`, `[C2]` in the retrieved context and require `study ask --json` to return `sources` filtered/re-ranked by `cite.moments()` overlap so the app only displays genuinely supporting moments.
3. **Phonetic/Token Normalization in `cite.words()`:**
   - Extend `cite.words()` with lightweight stdlib normalization (strip plural `-s/-es`, common hyphenation variants like `kv-cache`/`kvcache`) to reduce false misses on Whisper STT output without adding embedding dependencies.

### 4.3 FSRS-Ready Anki Export & Obsidian Graph Linking
**Goal:** Upgrade spaced repetition from stateless TSV dumps to FSRS-optimized, history-preserving Anki decks and a connected Obsidian knowledge graph.

1. **Stable-GUID, FSRS-Ready Anki Export (`study_aids.py:anki_export()`, `lecture_kit.py:cmd_anki`):**
   - Keep `cards.tsv` in each lecture folder for backward compatibility, and enhance `anki_export()` (which already emits Anki `#separator:tab` / `#deck:` / `#tags:` headers) with:
     - **Deterministic Card GUID:** Add a `guid` column (`hashlib.sha1(f"{lecture_id}:{normalized_question}").hexdigest()[:12]`) and `#guid column:1` header so re-running `lecture build` or `lecture anki` **updates** existing cards in Anki without resetting FSRS `Difficulty` / `Stability` review history.
     - **Rich Metadata Tags & Evidence Link:** Include `module::<slug>`, `lecture::<id>`, and `at::<hhmmss>` tags, plus source timecode/slide in an extra `Source` field.
     - **Quiz-Driven Remedial Cards:** When `cmd_coach` explains a wrong answer from `quiz_log.jsonl`, optionally emit a targeted remedial card tagged `remedial::quiz` into `cards.tsv` / `anki_import.txt`.
   - Document recommended Anki FSRS settings in `README.md` (enable FSRS, Desired Retention `0.90`, optimize after accumulating review logs). No `py-fsrs` dependency is needed in the core toolkit; `py-fsrs` may be added to `.venv-eval/` only if offline retention simulation is built later.
2. **Obsidian Graph Linking (`lecture_kit.py:build()`):**
   - **Canonical Concept Normalization:** Maintain a lightweight `<vault>/_concepts.json` (or `aliases` map) so `[[RAG]]`, `[[Retrieval-Augmented Generation]]`, and `[[Retrieval Augmented Generation]]` resolve to a single canonical `[[Retrieval-Augmented Generation (RAG)]]` wikilink.
   - **Cross-Lecture & Module Links:** In each Obsidian lecture note's YAML frontmatter and `## Related` footer, link the previous/next lecture in the same `module` (via `course_calendar.py` / `manifest.json`) and embed `[[<module> Recap]]` so Obsidian's graph view clusters concepts by course module rather than isolated daily notes.

### 4.4 Eval-Gated Model, Prompt & Judge Upgrades
**Goal:** Eliminate same-family judge bias and close the feedback-to-eval loop.

1. **Cross-Family Judge Decoupling (`tests/evals/omlx_swap.py`, `judge.py`):**
   - Separate `OMLX_MODEL` (generator: `Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx`) from `OMLX_JUDGE_MODEL` using a **non-Qwen** model family (e.g., a Gemma-3 / Llama-3.x / Mistral-series 24B–27B 4-bit MLX model served by oMLX) so generator and judge never share pre-training perplexity distributions.
   - Record both `generator_model` and `judge_model` in every `eval/results/<ts>-deepeval/summary.json` (as required by `03-ai-first-review.md`).
2. **Calibrated System-1 Judge (`laya_judge.py` + `calibration.py`):**
   - Keep `laya_judge.py` (421M) as the fast (`~1 min`, zero model-swap) pre-filter/sanity gate, and calibrate its temperature/threshold against the learner's `feedback/feedback.jsonl` labels targeting **Cohen's $\kappa \ge 0.6$** (`01-product-brief.md`).
   - Mirror DeepEval's **DAG / programmatic-first** pattern: run deterministic checks (`cite.verify_citations()`, JSON schema validity, `name_guard` leak check) *before* invoking the LLM judge, reserving LLM judge tokens strictly for semantic faithfulness and card quality.
3. **Automated Feedback-to-Golden Promotion (`tests/evals/`):**
   - Add a `promote_feedback.py` script in `tests/evals/` that reads `👍`/`👎` + corrections from `feedback/feedback.jsonl`, redacts any PII via `name_guard`, and appends anonymized regression cases to `tests/evals/.dataset.json` (and `eval/cases_real/` for slide corrections).
4. **Vision Eval Completion (`eval/cases_real/`):**
   - Once real course portal/Zoom keyframes are captured via `Lecture Lens.app` (confirming `frames > 0`), curate 15–20 hand-labelled real frames into `eval/cases_real/` as a mandatory regression gate alongside `cases_stress_1600` before any `OMLX_VL_MODEL` or `vlm_client.py` prompt change.

---

## 5. Phased Implementation Plan (for Future Execution)

| Phase | Scope | Key Files | Verification Gate |
|---|---|---|---|
| **P1: Grounding & Bookmarks** | Add `cite.verify_citations()`, bookmark-pinned keyframes, and slide-bounded transcript chunking (`aligned_segments.json`). | `cite.py`, `lecture_kit.py` | `check.sh` + `pytest tests/unit` + `lecture build last --json` |
| **P2: Anki FSRS & Obsidian** | Add deterministic `#guid` column in `study_aids.py:anki_export()`, canonical concept aliases, and module prev/next links in Obsidian notes. | `study_aids.py`, `lecture_kit.py` | `check.sh` + unit test for GUID stability & Anki TSV/header format |
| **P3: Judge & Eval Loop** | Switch `OMLX_JUDGE_MODEL` to a non-Qwen family in `.env` / `omlx_swap.py`, add `promote_feedback.py`, and populate `eval/cases_real/`. | `tests/evals/omlx_swap.py`, `tests/evals/judge.py`, `eval/run_eval.py` | `tests/evals/run.sh --smoke` + `run_judge_bench.sh` ($\kappa \ge 0.6$) |
