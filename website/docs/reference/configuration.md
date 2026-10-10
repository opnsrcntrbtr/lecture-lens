---
title: Configuration reference
---

# Configuration reference

Set in `.env` (ignored by git). `.env.example` has every variable with a safe placeholder.

## Models

| Variable | Meaning |
| --- | --- |
| `OMLX_BASE_URL` | OpenAI-compatible endpoint of your local model server |
| `OMLX_MODEL` | Text model for notes, cards and answers |
| `OMLX_VL_MODEL` | Vision-capable model for slides; blank uses on-screen text only |
| `OMLX_VL_THINK` | `false` unless you have evaluated otherwise |
| `OMLX_JUDGE_MODEL` | Judge for evaluation; must differ from the generator |
| `OMLX_API_KEY` | Only if your server requires one. Never commit it |

## Capture

| Variable | Meaning |
| --- | --- |
| `SP_BIN` | Path to the recorder binary |
| `SCREENPIPE_URL` | Its local API, default `http://localhost:3030` |
| `SP_PORTAL_APPS` | Browser captured in portal mode |
| `SP_LIVE_APPS` | Meeting app names, comma-separated |
| `SP_LIVE_BROWSER_TITLES` | `<app>::<title contains>` for browser windows also captured in live mode |
| `SP_AUTO_SWITCH`, `SP_LIVE_COOLDOWN_MIN` | Follow meetings automatically |
| `SP_URL_ALLOW` | Extra allowed hosts for portal mode |
| `SP_IGNORE` | App or title words that are never recorded |
| `SP_DRM_URLS` | Pages with protected video: `host` or `host/path-prefix`, comma-separated |
| `SP_AUDIO_ENGINE` | Speech-to-text engine name |
| `SCREENPIPE_AUDIO_TAP_APPS` | App whose own audio is tapped |
| `SP_VISUAL_CHECK_MS`, `SP_VISUAL_CHANGE_THRESHOLD`, `SP_MIN_CAPTURE_MS`, `SP_IDLE_CAPTURE_MS` | Frame sampling |

## Course and output

| Variable | Meaning |
| --- | --- |
| `STUDY_LECTURE_SITE` | Host of your course portal |
| `STUDY_PORTAL_NAME` | Portal name as shown in window titles |
| `STUDY_COURSE_NAME` | Written into each lecture note |
| `STUDY_LIVE_FOLLOWER` | Start `live_class.py` with live-mode capture (default `true`) |
| `STUDY_LIVE_TITLE`, `STUDY_LIVE_HOURS`, `STUDY_LIVE_PROMPT` | Live note title, how long the follower runs (default 4 h), whisper vocabulary prompt |
| `LIVE_SLIDE_MARKER`, `LIVE_SLIDE_FOOTER` | Regexes for your deck's slide marker and footer (optional) |
| `LIVE_QA_STAFF` | Staff names in the Q&A panel, comma-separated; everyone else is unnamed |
| `LIVE_AUDIO_DEVICE` | Audio device the follower re-transcribes (default `System Audio (output)`) |
| `STUDY_TRANSCRIPT_SOURCE` | `screenpipe` to build from the recorder's own transcript even when a live whisper transcript exists |
| `STUDY_LIVE_APPS` | App names treated as live classes when finding sessions (default `zoom`) |
| `STUDY_LECTURE_DIR`, `STUDY_NOTES_DIR` | Output folders |
| `STUDY_VAULT`, `STUDY_VAULT_SUBDIR` | Obsidian vault and subfolder |
| `STUDY_AUDIO_DEVICE` | `auto` picks the non-microphone device with the most text |
| `STUDY_CTX_CHARS` | Characters of source per model call (default 60000) |
| `STUDY_PART_CHARS` | Size of transcript parts for long sessions (default 24000) |
| `STUDY_SESSION_GAP_MIN`, `STUDY_MIN_SESSION_MIN` | Session splitting |
| `STUDY_VL_MAX_EDGE` | Long edge of images sent to the vision model |
| `STUDY_PYTHON` | Python to use when the default lacks Pillow |

## Evaluation

| Variable | Meaning |
| --- | --- |
| `EVAL_FORCE=1` | Run an eval even while live capture is on (not advised) |
| `DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE` | Per-metric budget; the runner sets 1200 s for a slow local judge |
