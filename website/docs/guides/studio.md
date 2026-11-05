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
   - **Extension (works offline):** run `cd studio && npm ci && npm run build`. In `chrome://extensions`, turn on Developer mode, click **Load unpacked** and choose `studio/dist`. The toolbar button opens the Studio.
3. Click **Prepare model**. The first time, Chrome downloads Gemini Nano; the progress bar shows it.
4. Click **Choose lectures folder** and pick the `lectures` folder of your install. After Chrome restarts, one click on **Reconnect** grants access again.

## After class

`./lecture build` (or the app's build) works as before. In browser mode it skips the model and writes `studio_job.json` beside the transcript.

In the Studio's **After class** tab the lecture shows as *pending*. Click **Run**, or **Run all pending**. With **Describe slides** ticked, the Studio first describes each keyframe with Nano's image input, then writes the notes and cards from those descriptions. A 3-hour class takes several minutes.

Then update the vault note:

```bash
./lecture finish last        # or a session id, or: ./lecture finish all
```

The notes have the same sections as the oMLX notes. The byline names the engine, and `manifest.json` records the engine and the prompt versions.

## During a live class

Start capture in live mode as usual. In browser mode the follower keeps transcribing and logging slides, and leaves the outline and questions to the Studio. It writes `studio_live_job.json` with the class title.

In the Studio's **Live class** tab, pick today and click **Start following**. Keep the tab open: Chrome slows background tabs to about one timer a minute, which is what the follower needs. The app's Live tab shows the outline and questions as before, and **Sync now** is answered by the Studio.

## Limits

- **Notes are less complete.** Gemini Nano is a small model with a 9,216-token window, so long classes go through digests, which lose detail. Check the notes against the transcript before you rely on them. The [evaluation guide](evaluation.md) shows how to score Studio output with the local judge.
- **Slide descriptions** need an engine that reads images. With the WebGPU fallback, pick the Phi-3.5 vision model, or untick **Describe slides**: the notes then use the on-screen text.
- **Live Q&A follow-ups and prospective answers** are not in the Studio yet. In browser mode they are skipped.
- **Citations are checked, not trusted.** A small model sometimes cites a slide that was never in its source. The Studio removes `[slide n]` and `[deck n]` citations that point nowhere and records how many in `studio_result.json` (`citations_removed`). In **Ask**, a citation to a source that wasn't shown, or an answer with no citation, is listed under the answer.

## Evaluate the Studio

1. Open `eval.html` (hosted, or in the extension).
2. Pick the engine, click **Choose tests/evals folder and run** and select `tests/evals`. The page writes `.outputs.gemini-nano.json` there.
3. Score it with the local judge:

```bash
cd tests/evals && EVAL_OUTPUTS=.outputs.gemini-nano.json ./run.sh --judge-only
```
