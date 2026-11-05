---
sidebar_position: 6
---

# Use the model inside Chrome (Lecture Lens Studio)

Lecture Lens Studio runs the language model in Google Chrome instead of on a model server. It uses **Gemini Nano**, the model built into Chrome, through the Prompt API. When Nano is not available, it uses an **open model on WebGPU** (WebLLM, default Qwen3 4B). There is no server: the page reads and writes your `lectures` folder directly, and nothing leaves the browser. The decision record is [ADR-006](../decisions/ADR-006-in-browser-llm.md).

Capture, transcripts, keyframes, the slide log and Q&A threads still come from the Python toolkit. The Studio does the model steps:

| Job | What it writes |
|---|---|
| After class | Slide descriptions (`slides.json`, `slides.md`), `digest.md`, `notes.md`, `cards.tsv`, `studio_result.json` |
| Ask | An answer with cited timecodes and slides; each question is logged to `studio_ask.jsonl` |
| Live class | `outline.jsonl` and `questions.jsonl` every 10 minutes; answers the app's **Sync now** |
| Recap | `lectures/recaps/<module>.md` |

## Requirements

- **Gemini Nano:** Chrome 148 or later for the hosted page, or 138 or later for the extension.
  - macOS 13+, Windows 10/11, Linux or a Chromebook Plus.
  - At least 22 GB free on the drive with your Chrome profile.
  - A GPU with more than 4 GB of memory, or 16 GB RAM and 4 CPU cores.
  
  Chrome downloads the model once, on first use.
- **WebGPU fallback:** any Chrome with WebGPU and about 3 GB of free GPU memory. The weights (about 2.5 GB) download once per site.
- **Folder access:** Chrome's File System Access API. Firefox and Safari don't have it, nor the Prompt API.

To check your Chrome, open the Studio and look at the first card. It shows the state of Gemini Nano (`available`, `downloadable`, `downloading` or `unavailable`), whether it can read images, and whether WebGPU is present.

## Set up

1. In `.env`, set `STUDY_LLM_PROVIDER=browser`, or pick **Settings → Language model → In Chrome** in the app. Evals and the judge keep using oMLX.
2. Open the Studio in one of two ways:
   - **Hosted page:** [the Studio](https://opnsrcntrbtr.github.io/lecture-lens/studio/) on this docs site, or **Settings → Open Lecture Lens Studio** in the app.
   - **Extension (works offline):** run `cd studio && npm ci --ignore-scripts && npm run build` (the install scripts only fetch native runtimes the browser build does not use). In `chrome://extensions`, turn on Developer mode, click **Load unpacked** and choose `studio/dist`. The toolbar button opens the Studio.
3. Click **Prepare model**. The first time, Chrome downloads Gemini Nano; the progress bar shows it.
4. Click **Choose lectures folder** and pick the `lectures` folder of your install. After Chrome restarts, one click on **Reconnect** grants access again.

## After class

`./lecture build` (or the app's build) works as before. In browser mode it skips the model and writes `studio_job.json` beside the transcript.

In the Studio's **After class** tab the lecture shows as *pending*. Click **Run**, or **Run all pending**. With **Describe slides** ticked, the Studio first describes each keyframe with Nano's image input, then writes the notes and cards from those descriptions. A 3-hour class takes several minutes.

Options in the **After class** tab:

- **Fast digests:** long lectures are digested part by part with Chrome's Summarizer API instead of the Prompt API. It is faster, but it takes no instructions beyond a short context. Measured on three parts: 11 s against 18 s per part, but it kept 0.37 of each part's content words against 0.60. Leave it off unless time matters more than detail. In both modes, each digest bullet gets the timecode of the transcript line it came from.
- **Transcribe in the browser:** builds made with `STUDY_STUDIO_TRANSCRIBE=1` also save the lecture's audio (`audio/` and `audio.json`; hard links, so no extra disk on the same drive). The Studio then transcribes it with Whisper on WebGPU before writing the notes.
  - It writes `transcript_studio.md` and uses it instead of the capture transcript.
  - It never replaces a merged reference transcript (such as a meeting app's export).
  - Pick the model next to the option:
    - *base* is about 150 MB and fast;
    - *large-v3 turbo* is about 1 GB and needs WebGPU.
  
  The weights download once.

Then update the vault note, either with **Finish** on the lecture in the app or from a terminal:

```bash
./lecture finish last        # or a session id, or: ./lecture finish all
```

The notes have the same sections as the oMLX notes. The byline names the engine, and `manifest.json` records the engine and the prompt versions.

## Review your cards

The **Review** tab asks the flashcards that are due across all your lectures, oldest lecture first, then up to ten new ones. Answer from memory, click **Show answer**, then **Pass** or **Fail**. It needs no model, so it works even where Gemini Nano is unavailable.

Each answer is appended to the lecture's `reviews.jsonl`, the same log `./lecture review` uses, and both schedule with the same FSRS-5 code (the Studio's port is tested against the Python on shared logs). A card failed three times is listed as one to rewrite.

## During a live class

Start capture in live mode as usual. In browser mode the follower keeps transcribing and logging slides, and leaves the outline and questions to the Studio. It writes `studio_live_job.json` with the class title.

In the Studio's **Live class** tab, pick today and click **Start following**. Keep the tab open: Chrome slows background tabs to about one timer a minute, which is what the follower needs. The app's Live tab shows the outline and questions as before, says *Studio drafting with …* while the Studio is following, and **Sync now** is answered by the Studio.

With **Draft Q&A follow-ups and answers** ticked, each round also covers the Q&A panel:

- **Follow-ups:** it drafts follow-ups for threads that got a staff reply. Logistics threads get one, content threads get two, and only follow-ups grounded in the thread or the transcript are kept.
- **Your own question:** for your newest unanswered question, it drafts the answer you are likely to get, with a confidence and the point to listen for.

These go to the same files the Python follower used, and the follower still scores each drafted answer against the staff reply.

## Limits

- **Long parts are split, not dropped.** The Studio measures how many characters fit a token on each lecture with Gemini Nano's own counter, and sizes parts from that. If a part still overflows the window, it is split in two and each half is digested.
- **Notes are less complete.** Gemini Nano is a small model with a 9,216-token window, so long classes go through digests, which lose detail. Check the notes against the transcript before you rely on them. The [evaluation guide](evaluation.md) shows how to score Studio output with the local judge.
- **Slide descriptions** need an engine that reads images. With the WebGPU fallback, pick the Phi-3.5 vision model, or untick **Describe slides**: the notes then use the on-screen text.
- **Live transcription** still runs in whisper.cpp; browser transcription is for after class.
- **Citations are checked, not trusted.** A small model sometimes cites a slide that was never in its source. The Studio removes `[slide n]` and `[deck n]` citations that point nowhere and records how many in `studio_result.json` (`citations_removed`). In **Ask**, a citation to a source that wasn't shown, or an answer with no citation, is listed under the answer.

## Evaluate the Studio

1. Open `eval.html` (hosted, or in the extension).
2. Pick the engine, click **Choose tests/evals folder and run** and select `tests/evals`. The page writes `.outputs.gemini-nano.json` there.
3. Score it with the local judge:

```bash
cd tests/evals && EVAL_OUTPUTS=.outputs.gemini-nano.json ./run.sh --judge-only
```


4. **Speech bench.** The second card of `eval.html` takes an audio file and the text spoken in it. It runs Whisper base, Whisper small.en and, if available, Gemini Nano's audio input on the same audio, and reports word error rate and time for each.
5. Compare it with the oMLX generator's outputs, case by case and without a model:

```bash
cd tests/evals && python compare_outputs.py .outputs.json .outputs.gemini-nano.json
```

Each check is paired on the same inputs. Numbers come with a 95% interval for the difference and a sign test, and pass/fail checks with Wilson intervals and McNemar's test. On 12 notes or 36 answers, a difference whose interval includes zero has not been shown. The same table counts citations to slides that were never in the source (`notes:refs_ok`). For the judge's scores, compare the two `summary.json` files with `compare_runs.py`: cases are keyed by input, so the two generators pair up the same way.
