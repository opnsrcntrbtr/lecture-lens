# Lecture Lens — system design (v1)

## 1. Requirements

**Functional**
- F1 Capture: start/stop, portal/live mode, allowed domains; live health of screen
  frames, App-Tap audio, vision model, oMLX; permissions checklist.
- F2 Lectures: list sessions/lectures, open transcript, slides (image + description),
  notes and cards; build or rebuild one.
- F3 Ask: grounded Q&A over captures with citations; feedback on any AI output.
- F4 Evals: run the vision eval and the DeepEval suite; show score history; turn
  feedback into test cases; benchmark judges (local oMLX vs Laya).
- F5 Settings: models, thinking mode, capture tuning, domains, paths.

**Non-functional**
- The app is the **TCC-responsible process** for screenpipe (it launches it).
- Offline: only 127.0.0.1 traffic (screenpipe :3030, oMLX :8001). No telemetry.
- One implementation of each capability. The app calls the tested toolkit and
  never re-implements it.
- Never hides a failure. Every background failure becomes a visible state.
- The UI never blocks. Heavy jobs run serially (one GPU), with visible progress.

**Constraints:** single user; Swift 6.4 / macOS 27 / Xcode 27; no Apple
Development certificate yet (ad-hoc signing means permissions are re-granted
after each rebuild until one exists).

## 2. High-level design

```
 ┌──────────────────────── Lecture Lens.app (SwiftUI) ───────────────────────┐
 │ MenuBarExtra  ── status lights · start/stop · mode · open window              │
 │ Window: Capture │ Lectures │ Ask │ Evals │ Settings                            │
 │                                                                               │
 │ AppModel (@Observable, @MainActor)                                            │
 │   ├─ HealthMonitor ── polls: screenpipe /health (5 s), oMLX /api/status (10 s),│
 │   │                    `lecture doctor --json` (60 s, vision probe on demand)  │
 │   ├─ CaptureController ─ runs sp-start / sp-stop / sp-mode / sp-domains        │
 │   ├─ ToolRunner ─ Process + JSON contract (stdout = one JSON doc, stderr = log)│
 │   ├─ JobQueue ─ one heavy job at a time (build, eval, benchmark); progress     │
 │   ├─ LectureStore ─ reads lectures/*/manifest.json + files (read-only)  │
 │   ├─ FeedbackStore ─ appends feedback/feedback.jsonl                    │
 │   └─ EnvFile ─ line-preserving edits of .env                            │
 └──────┬───────────────────┬──────────────────────────┬─────────────────────────┘
        │ launches          │ runs (JSON contract)     │ HTTP (localhost)
        ▼                   ▼                          ▼
  sp-start → screenpipe   study / lecture / eval      screenpipe :3030   oMLX :8001
  (+ sp-autoswitch)       (Python, stdlib)            (health, search)   (LLM+VLM)
        │                   │
        ▼                   ▼
  ~/.screenpipe/db.sqlite   lectures/…  feedback/…  eval/results/…
                            eval/deepeval/ (uv venv: deepeval, laya)
```

### Process lifecycle and permissions
The app launches `sp-start` with `Process`. `sp-start` runs screenpipe in the
background and starts `sp-autoswitch`, and the autoswitcher restarts screenpipe
on mode changes. Every process in that chain descends from the app, so macOS
treats **Lecture Lens** as the responsible app: permission prompts name it,
and the grants in System Settings belong to it. screenpipe keeps running if the
window closes (menu-bar app); **Quit** asks whether to stop capture too.

## 3. Contracts

### 3.1 Tool JSON contract
Every tool the app calls gets a `--json` flag:
- stdout: exactly one JSON document.
- stderr: human progress, streamed into the job log.
- exit code: 0 success, 1 domain failure (JSON still printed, with `"ok": false`), 2 usage error.

| Command | JSON (abridged) |
|---|---|
| `lecture doctor --json` | `{ok, checks:[{id, ok, level, message, fix}]}` — ids: database, frames, app_audio, ffmpeg, pillow, vision, vault |
| `lecture list --json [--since 30d]` | `{sessions:[{id, title, start, end, minutes, frames, built, folder}]}` |
| `lecture build <id> --json` | `{ok, folder, manifest:{…}}` |
| `study ask "…" --json [--since]` | `{answer, sources:[{n, ts, src, where, text}], queries, model, latency_s}` |
| `sp-mode --json` | `{running, mode, pinned, autoswitch}` |

### 3.2 Feedback record (`feedback/feedback.jsonl`, append-only)
```json
{"id":"uuid","ts":"2026-09-29T18:02:11Z","target":"answer|note|slide|card",
 "ref":{"lecture":"20260929-1830","slide":4,"question":"…"},
 "input":"question or slide path","output":"what the model said",
 "context":["cited source texts…"],"rating":1,"correction":"optional text",
 "model":"Swift-Qwen3.8-27b-oQ4e-mtp","prompt_version":"slide-v2"}
```
Append-only, so nothing is ever rewritten. `eval/deepeval/feedback_goldens.py`
turns it into DeepEval goldens: a correction becomes the expected output, and a
thumbs-down without a correction becomes a known-bad example.

### 3.3 Eval results
Every run writes `eval/results/<ts>-<suite>/summary.json`:
```json
{"suite":"deepeval-ask|vision|judge-bench","model":"…","judge":"…",
 "metrics":{"faithfulness":{"mean":0.91,"pass":18,"n":20}, "…":{}},
 "latency":{"p50":4.7,"p95":8.4},"started":"…","git":"50624d0"}
```
The app plots history from these files. No database is needed.

## 4. Deep dive

### 4.1 Health model
Each signal is `ok | degraded | down | unknown`, with a *reason* and a *fix action*:

| Signal | Source | Down means | Fix action |
|---|---|---|---|
| Screen | frames in last 2 min while capturing | permission missing or display asleep | open Privacy → Screen & System Audio Recording |
| App audio | App-Tap transcripts in last 10 min while Chrome plays | tap refused by OS | same pane; restart capture |
| Vision | doctor probe ("PROBE 4721") | model loaded text-only | "Reload model in VLM mode" (oMLX admin API) |
| oMLX | `/api/status` | server down | open oMLX |
| Screenpipe | `/health` | not running | Start |

### 4.2 Jobs
`JobQueue` runs heavy work serially: lecture builds, evals and the judge benchmark
all compete for the one GPU. Ask requests are interactive and never queue
behind a build; they run immediately and may be slower while a build runs, and
the UI says so. Every job keeps its stderr log for the log view.

### 4.3 Failure handling
- Tool exits non-zero → the job shows a red card with the last 20 stderr lines and the command to rerun.
- JSON parse failure → treated as a tool bug; the raw stdout is kept for the log.
- oMLX down during Ask → an inline error instead of a spinner; retry button.
- `.env` edits are atomic (temp file + rename) and keep comments; the previous file is kept as `.env.bak`.
- screenpipe crashes → HealthMonitor shows it within 5 s; one-click restart.

## 5. Evaluation architecture (DeepEval + judge benchmark)

```
fixtures (synthetic lecture: transcript + slide descriptions + questions + expected answers)
   + feedback goldens (your corrections)
        │
        ▼
 deepeval test cases ── system under test = the real study/lecture_kit code paths
        │                  (retrieval, answer, notes, cards, slide description)
        ▼
 metrics: Faithfulness · AnswerRelevancy · ContextualRelevancy · Hallucination · GEval(correctness)
 judge:   OmlxJudge (DeepEvalBaseLLM → 127.0.0.1:8001, thinking off, temperature 0)
        │
        └─ judge benchmark: claim-support pairs with known labels
             System 2: OmlxJudge verdicts   vs   System 1: Laya `noul` P(supported)
             → accuracy, F1, ECE, latency; later: agreement with your labels (κ)
```

## 6. Trade-offs

| Decision | Chosen | Cost | Revisit when |
|---|---|---|---|
| SwiftPM package + bundling script vs Xcode project | SwiftPM | no Interface Builder or asset catalogs | UI needs heavy assets or App Store packaging |
| App calls tools vs reimplementing in Swift | tools | ~100–300 ms process spawn per call | a call becomes interactive-latency-critical (then add a daemon) |
| Polling health vs push | polling (5–60 s) | small CPU cost | screenpipe exposes events we need |
| JSONL feedback vs SQLite | JSONL | full scan to read (fine below ~100k rows) | feedback exceeds ~50k rows |
| Unsandboxed app | required (launch processes, read ~/.screenpipe) | no App Store | never, for this use |
| Ad-hoc signing | only option today | permissions re-granted on each rebuild | an Apple Development cert exists (sign into Xcode) |
