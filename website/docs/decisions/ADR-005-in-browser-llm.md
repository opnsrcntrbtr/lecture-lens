# ADR-005: run the language model inside Chrome (Lecture Lens Studio)

**Status:** Accepted (MVP) · **Date:** 2026-11-05 · **Deciders:** project maintainer

## Context

Every generated artefact (notes, flashcards, digests, slide descriptions, answers, live outline and questions) comes from a local model served by oMLX over an OpenAI-style HTTP API on `127.0.0.1:8001`. That keeps data on the Mac, but it costs a model server, 20–40 GB of weights and a Mac that can hold a 27B–35B model.

Chrome now ships a small on-device model, Gemini Nano, behind the built-in AI APIs, and WebGPU can run open models in a page. Both run in the browser with no server and no network after the first download. Measured on the maintainer's Mac (Chrome 156, Apple GPU, 5 Nov 2026):

| Check | Result |
|---|---|
| `LanguageModel.availability()` text in/out (en) | `available` |
| … with image input | `available` |
| `Summarizer.availability()` | `available` |
| `session.contextWindow` | 9,216 tokens |
| First `create()` / a short JSON-constrained prompt | 16.2 s / 2.8 s |

Facts this decision relies on (Chrome for Developers docs, read 5 Nov 2026):
- The Prompt API is stable for web pages from Chrome 148 and for extensions from Chrome 138. Summarizer, Translator and Language Detector are stable from 138. Writer, Rewriter and Proofreader are still trials. Sampling controls are an origin trial on the web (`samplingMode`); extensions get `temperature` and `topK`.
- Requirements: macOS 13+, Windows 10/11, Linux or Chromebook Plus; 22 GB free on the profile volume; a GPU with more than 4 GB VRAM, or 16 GB RAM and 4 cores. Audio input needs a GPU. Model input/output languages: en, es, ja, de, fr.
- `availability()` returns `unavailable`, `downloadable`, `downloading` or `available`. A download needs a user gesture and reports `downloadprogress`.
- `responseConstraint` takes a JSON Schema for structured output. `measureContextUsage()` and `contextoverflow` manage the window. The API is not available in Web Workers.
- WebLLM 0.2.87 runs MLC-compiled models on WebGPU with an OpenAI-style `chat.completions` API. Prebuilt text models include `Qwen3-1.7B`, `Qwen3-4B`, `Qwen3.5-2B`/`4B` (q4f16, about 2.6 GB VRAM, 4,096-token window by default). The vision model is `Phi-3.5-vision` (1,024-token window).
- File System Access (`showDirectoryPicker`, handles stored in IndexedDB, `queryPermission`/`requestPermission`) lets a Chrome page read and write a folder the user picks. Firefox and Safari do not support it.

## Decision

1. **Add a provider switch, keep oMLX.** `STUDY_LLM_PROVIDER=omlx` (default) changes nothing. `STUDY_LLM_PROVIDER=browser` makes the Python pipeline do only the deterministic work: capture, transcript, keyframes, OCR text, slide log, Q&A threads. It leaves job files that Lecture Lens Studio, a page running in Chrome, picks up and completes. Evals and the judge stay on oMLX: a 3–4B model is not a trustworthy judge.
2. **One codebase, two packagings.** `studio/` is plain ES modules with no framework. It loads as an unpacked **Chrome extension** (works offline; `temperature`/`topK`) and is published as a **static page on the docs site** (`/lecture-lens/studio/`, https, no backend; Chrome 148+). Neither talks to any server of ours. Captured data never leaves the browser and the lectures folder.
3. **Engines.** Gemini Nano through the Prompt API is used first. A WebLLM model on WebGPU (default `Qwen3-4B-q4f16_1-MLC`) is the fallback when Nano is unavailable or the user picks it. Both sit behind one small interface: `prompt(system, user, {schema, maxTokens})` and `describeImage(blob, prompt)`. The WebLLM bundle is vendored at build time, not loaded from a CDN, so the extension needs no remote code.
4. **Jobs, all in the browser:**
   - **Slides:** describe each keyframe with Nano's image input. Fallback engines skip this unless the vision model is chosen.
   - **Notes:** map-reduce for the 9K window. Transcript parts of about 10,000 characters become timecoded digests, the digests (reduced again if needed) plus slide text become notes, and cards are made per part with a JSON-schema constraint, deduplicated as in Python.
   - **Recap:** one page per module from that module's notes.
   - **Ask:** BM25 over transcript lines and slides, then a cited answer (`[00:12:30]`, `[slide 4]`).
   - **Live:** every 10 minutes after the window's transcript has arrived, write an outline and three questions to the same `outline.jsonl` / `questions.jsonl` the app already shows. `sync_request.json` / `sync_done.json` are answered too, so the app's **Sync now** works unchanged.
5. **File contracts, not sockets.** The Python side writes `studio_job.json` (transcript lines, slide source, settings) and `slides.json` into the lecture folder. The Studio writes `notes.md`, `cards.tsv`, `digest.md`, the descriptions in `slides.json`/`slides.md`, and `studio_result.json`. `lecture finish` then writes the vault note. Formats match the oMLX path, so the app, the vault and the evals read either output.
6. **Prompts are versioned per engine** (`notes-nano-v1`, `cards-nano-v1`, …) and recorded with the model name in `manifest.json` and `studio_result.json`. They follow the Python prompts' rules (two values reported as two, deferred questions marked unanswered, slide claims attributed) in fewer words, because a small model follows short prompts better.

## Consequences

- A Mac or PC with Chrome and about 22 GB free can make notes without oMLX; the 35B generator becomes optional. Notes from a 3–4B model are shorter and miss more. The eval in the PR measures how much on the synthetic suite before anyone relies on it.
- The live outline and questions need the Studio tab open during class (Chrome throttles background tabs to about 1 timer per minute, which fits a 60-second poll).
- The folder permission must be granted again after Chrome restarts: one click on **Reconnect folder**.
- Only Chrome (and Chromium browsers that ship the APIs) is supported. Firefox and Safari lack both the Prompt API and File System Access.
- Prompt quality is now maintained in two languages. The JS unit tests pin the rules the Python tests pin (card dedupe, question parsing, grounding, window readiness).

## Alternatives considered

- **A localhost bridge** (Python → WebSocket → a Chrome tab): rejected, since it is a server and the goal is none.
- **Native messaging host:** a local process plus registry/manifest installation, more moving parts than a file contract, and no gain for batch jobs.
- **WebLLM only:** works in more browsers with WebGPU, but downloads 2–3 GB per model per origin, while Nano is shared by every site and the extension and is already on the device.
- **Writer/Rewriter APIs:** still trials; the Prompt API covers the same jobs.

## Phases

1. **MVP (this ADR):** provider switch, Studio (both packagings), the five jobs, JS + Python unit tests, an eval of Nano against the Qwen generator on the synthetic suite.
2. Live follow-ups and prospective answers for Q&A threads in the Studio. The Swift app opens the Studio and shows engine status.
3. Summarizer API for part digests once its quota is measured. Nano audio input as a whisper alternative for short clips.
