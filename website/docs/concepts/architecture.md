---
title: Architecture
---

# Architecture

Three layers, one direction of control. The app launches tools and reads their JSON; the tools talk to two local services and the file system.

```
┌──────────────────────────── Lecture Lens.app (SwiftUI) ───────────────────────────┐
│  Capture: start/stop, mode, health lights   Lectures: list, build, open           │
│  Ask: grounded Q&A, rate answers            Evals: run suites, job log, results   │
└──────────────────────────────────┬─────────────────────────────────────────────────┘
                                   │ argv arrays in, JSON out (never a shell string)
┌──────────────────────────────────▼─────────────────────────────────────────────────┐
│  Toolkit: sp-start · sp-stop · sp-mode · sp-autoswitch · sp-domains                │
│           lecture (lecture_kit.py) · study (study.py) · transcript_ref.py          │
│           tests/evals/run.sh · eval/run_eval.py                                    │
└───────────────┬──────────────────────────────┬──────────────────────┬──────────────┘
                │ HTTP 127.0.0.1:3030          │ HTTP 127.0.0.1:8001  │ files
        screenpipe recorder             local model server      .env, lectures/, eval/results/
```

## Why the app starts the recorder

macOS attaches recording permissions to the app that launches the recorder. Started from the app, the recorder inherits the app's grants. Started from a terminal or an automation shell, it inherits that process's grants, usually none, and waits for a permission prompt nobody sees.

## Sessions

A lecture session is a start, an end and a source:

- **A browser page** on your course portal: frames matched by URL. On protected players there are no frames, so a page logger writes markers instead.
- **A live app**: frames matched by application name.
- **An explicit time window**: `--from` and `--to`.

Audio is selected by time, with a short lead-in, from the audio device that carried the most text.

## Reading the database while capture runs

The recorder holds an exclusive lock on its database. The toolkit first tries to open the file read-only; if it is locked, it reads through the recorder's local SQL endpoint instead, with the same query code.

## One model slot

The model server holds one large model at a time. Lecture builds and question answering need the generator; evaluation needs the judge. The eval runner swaps models and always swaps back; the app holds model-calling actions while the judge is loaded; evals refuse to start during a live class.

## Settings have one source

The recorder persists every command-line flag in its own store, and some flags cannot be unset from the command line. A setting left from one mode can silently change another: a browser-site allowlist from portal mode made the recorder skip every native window in live mode. `sp-start` therefore rewrites the mode-owned settings before each start. Extending this to every mode-owned key is tracked in [tech debt](../project/tech-debt.md).

## Failure must be visible

Three of the worst faults so far were silent: frames skipped while the light stayed green, frame extraction returning nothing, a restart waiting on a permission. The rule since: compare attempts with results, and print a warning instead of returning an empty value.
