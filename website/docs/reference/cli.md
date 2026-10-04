---
title: Command reference
---

# Command reference

All commands live in the repository root and read `.env`.

## Capture

| Command | Does |
| --- | --- |
| `./sp-start [--mode portal\|live] [--fg]` | Start the recorder with the capture profile for the mode. Normally the app runs this |
| `./sp-stop` | Stop the recorder and the auto-switcher |
| `./sp-mode [live\|portal\|auto] [--json]` | Show, pin or unpin the mode |
| `./sp-domains list\|add <host[:subdomains]>\|remove <host>` | Manage the portal allowlist |
| `./diag-drm [seconds]` | Phased test of which capture path blanks protected video |

## Lectures

| Command | Does |
| --- | --- |
| `./lecture list [--since 7d] [--json]` | Sessions found in the captures |
| `./lecture build [<id>\|last\|all] [--since 7d]` | Build one or more sessions |
| `./lecture build --from HH:MM [--to HH:MM] [--title T]` | Build one session for a time window |
| `… --no-vision` | Skip slide descriptions |
| `… --cards N` | Upper bound on cards (default 20) |
| `./lecture watch [--idle 5]` | Build each session a few minutes after it ends |
| `./lecture doctor [--quick] [--json]` | Health checks |
| `./transcript_ref.py <folder> <reference.txt>` | Merge a reference transcript and measure word error rate |

## Study

| Command | Does |
| --- | --- |
| `./study ask "question" [--since 7d] [--app NAME] [--json]` | Answer from your captures, with sources |
| `./study summarize [--since 3h \| --date YYYY-MM-DD]` | Notes for a period |
| `./study flashcards [--since 3h] [-n 20]` | Cards for a period |
| `./study check` | Recorder and model server health |

## Evaluation

| Command | Does |
| --- | --- |
| `tests/evals/run.sh [--smoke\|--judge-only\|--regen-goldens]` | Judged evaluation |
| `tests/evals/run_judge_bench.sh` | Judge benchmark |
| `eval/run_eval.py --configs think_off` | Slide-reading evaluation |

## Maintenance

| Command | Does |
| --- | --- |
| `./check.sh` | Unit tests, contract checks, self-tests |
| `app/build_app.sh [--test] [--open]` | Build, sign and install the app |
| `tools/setup_hooks.sh` | Turn on the content guard hooks |
| `tools/content_guard.py --all` | Scan the tracked tree |
| `tools/publish.sh [--dry-run]` | Guarded push |
