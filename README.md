# Lecture Lens

Turn the lectures you attend online into transcripts, slide-linked notes and flashcards, on your own Mac, with local models. Nothing is uploaded.

[![CI](https://github.com/opnsrcntrbtr/screenpipe/actions/workflows/ci.yml/badge.svg)](https://github.com/opnsrcntrbtr/screenpipe/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Lecture Lens is a study toolkit and a small native macOS app. It reads what [screenpipe](https://github.com/screenpipe/screenpipe) captures while you watch a recorded lecture or sit in a live class, and builds study material from it with a model running on your machine.

> **Status:** early, single-maintainer, macOS on Apple silicon only. It works for the author's weekly classes; expect rough edges.

## What it does

| You do | Lecture Lens does |
| --- | --- |
| Watch a recorded lecture in the browser | Records the lecture's audio, pauses screen capture on copy-protected players, logs which page you were on |
| Sit in a live class in a meeting app | Captures slides and audio, picks the distinct slides, describes them with a vision model |
| Click **Build** | Writes a timecoded transcript, notes with slide and time citations, and flashcards |
| Ask a question | Answers from your own captures, with sources |
| Change a prompt or a model | Runs a local evaluation so you can see whether quality moved |

## What it does not do

- It does not record copy-protected video. On those pages it keeps audio only.
- It does not send anything to a cloud service. Evaluation runs with a local judge model.
- It does not include screenpipe or any course material. You bring both.

## How it fits together

```
 Lecture Lens.app (SwiftUI)        start/stop capture, lectures, ask, evals
        │  argv in, JSON out
 toolkit (zsh + Python)            sp-start · sp-mode · lecture · study · tests/evals
        │  HTTP on 127.0.0.1, local files
 screenpipe (you install)  ·  local model server (you install)  ·  your files
```

## Requirements

- macOS 15 or later on Apple silicon; 32 GB of memory or more is realistic for the models.
- [screenpipe](https://github.com/screenpipe/screenpipe), installed and licensed by you. Read its licence: it is not open source, and Lecture Lens does not change that.
- A local OpenAI-compatible model server with a text model, a vision-capable model and, for evaluation, a second model as judge.
- Python 3.11+ with Pillow, `ffmpeg`, and Xcode command line tools for the app.

## Quick start

```sh
git clone https://github.com/opnsrcntrbtr/screenpipe.git ~/lecture-lens
cd ~/lecture-lens
tools/setup_hooks.sh                 # content guard hooks (required before you commit)
cp .env.example .env                 # set your model ids and your course portal's host
cp allowed-domains.example.txt allowed-domains.txt
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
./check.sh                           # unit tests and contract checks
app/build_app.sh --test --open       # build and open the app
```

Then, in the app: **Start capture**, attend the lecture, **Stop**, and **Build** the session under Lectures. From a terminal:

```sh
./lecture list --since 7d
./lecture build last
./lecture build --from 10:00 --to 11:30 --title "Week 3 live class"   # live classes
./study ask "what did the lecture say about evaluation?"
```

Capture must be started from the app, not from a shell: macOS ties recording permission to the app that launches the recorder.

## Evaluation

```sh
tests/evals/run.sh --smoke     # 6 fixed cases, about 12 minutes on a local judge
tests/evals/run.sh             # full suite; over an hour
tests/evals/run_judge_bench.sh # how far to trust the judge
```

Evaluations refuse to start while a live class is being captured, because they swap the model.

## Keeping course material out of this repository

This is a public repository about a private activity. Three layers keep them apart:

1. `.gitignore` excludes capture output, decks, recordings and local configuration.
2. `tools/content_guard.py` scans every commit and file before a push for blocked names (stored only as hashes), forbidden file types, secrets and personal data. It runs as pre-commit, commit-msg and pre-push hooks.
3. A plain `git push` is disabled; `tools/publish.sh` runs the guard and then pushes. CI runs the guard again.

A hook can be bypassed by someone determined to, and a hash list can be guessed against. The guard prevents accidents; it is not a legal review.

## Documentation

The docs site is built with Docusaurus from `website/`: getting started, capture guides, evaluation, architecture, decisions, the roadmap, the design review and the tech-debt register.

```sh
cd website && npm ci && npm start
```

## Licence and names

Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Lecture Lens is independent and unaffiliated with any university, course provider, platform or the makers of screenpipe. This README is not legal advice.
