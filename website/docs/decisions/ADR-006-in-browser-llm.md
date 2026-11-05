# ADR-006: run the language model inside Chrome (Lecture Lens Studio)

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

1. **MVP (PR #29):** provider switch, Studio (both packagings), the five jobs, JS + Python unit tests, a model-free comparison of Nano and the Qwen generator on the synthetic suite.
2. **Checks (PR #30):** citation checks, token-measured window fitting, recall-first notes, FSRS review, paired eval statistics.
3. **Parity and transcription (this round):**
   - **Live Q&A in the Studio.** Follow-ups for answered threads, and a prospective answer for the learner's own unanswered question. This is a port of `live_qa.py` and `qa_round`, with a shared fixture so the Python and JS rules cannot drift. The output goes to the same `qa_followups.jsonl` / `qa_prospective.jsonl` files. The Python follower still builds the threads (attendee names removed there) and scores prospective answers against staff replies.
   - **Transcription in the browser.** With `STUDY_STUDIO_TRANSCRIBE=1`, `lecture build` hard-links the lecture's system-audio chunks into `audio/` and lists them in `audio.json` with their offsets. The Studio decodes them with the browser's own decoder and resamples them to 16 kHz. It skips silent chunks, transcribes the rest with Whisper (transformers.js, WebGPU, falling back to WASM), drops whisper's usual silence hallucinations, writes `transcript_studio.md`, and uses those lines for the notes. A merged reference transcript is never replaced. transformers.js and the ONNX runtime files are vendored by the build. Gemini Nano's audio input is benchmarked against Whisper on the same audio (`eval.html`, speech bench), not used by default.
   - **Fast digests:** Chrome's Summarizer API (`key-points`, long) for transcript parts, as an option measured against the Prompt API digests.
   - **App:**
     - Lectures show where a lecture is in the browser path: waiting for the Studio, done there, or finished. They offer **Open Studio** and **Finish**, which runs `lecture finish`.
     - The Live tab shows when the Studio is drafting, from its `studio_live.json` heartbeat.
   - **Measured on the maintainer's Mac** (Chrome 156, 5 Nov 2026). The local judge was running on the same GPU during the speech and digest runs, so the times are upper bounds.
     - **Speech, word error rate on 45 s of synthetic speech** (invented text, macOS text-to-speech, AAC):

       | Engine | Word error rate | Time |
       |---|---|---|
       | Whisper base (WebGPU) | 0.074 | 7.3 s |
       | Whisper small.en (WebGPU) | 0.020 | 9.8 s |
       | Gemini Nano audio input | 0.034 | 11.6 s |
       | whisper.cpp large-v3-turbo q8 (native) | 0.034 | 13.3 s |

       Whisper large-v3 turbo in the browser failed to download through the network here. Synthetic speech is easier than a lecture hall, so these numbers rank engines; they do not predict lecture accuracy.
     - **Digests, 3 transcript parts from the synthetic suite:**
       - The Summarizer API took 11.3 s per part against 17.7 s for a Prompt API digest.
       - It kept fewer of the part's content words: 0.37 against 0.60.
       - Neither kept a single timecode. That is now repaired deterministically: each bullet gets the timecode of the line it shares most words with.
       - Fast digests stay off by default.
4. **Later:** live transcription in the browser (whisper.cpp keeps that job); Writer and Rewriter once they leave trial; publishing the extension to the Chrome Web Store.
