# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first; its hard rules apply in full. This file adds what is specific to working here with Claude.

## The one thing to get right

This repository is public and the owner's study material is not. Before writing any text (code, test data, doc, commit message), check that it names no institution, platform, course or person and quotes no lecture content. If the user gives you such material to work with, keep it in the ignored folders (`lectures/`, `notes/`, `private/`) and never copy it into tracked files.

`python3 tools/content_guard.py --all` must print `content guard: clean` before you say a task is done.

## Working method

- Run `./check.sh` after changes. It includes the unit tests.
- Long jobs (lecture builds, evals) run in the background; they take minutes to hours on local models. Check that no capture is running in live mode first, and ask before starting an eval.
- When a run produces a number (pass rate, word error rate), report it with what it was measured on and how many cases.
- Do not rebuild the app while a class is being captured: a rebuild can need a capture restart.
- Do not create signing certificates or change macOS privacy settings; tell the user what to do.

## Publishing

- Commit normally; the pre-commit and commit-msg hooks run the guard.
- Push only with `tools/publish.sh`, and only when the user asks for a push.
- If the guard reports a finding, fix the content. Do not bypass, and do not delete the hash.

## Where things are explained

`website/docs/`: architecture, capture guides, evaluation, decisions (ADRs), roadmap, design review, tech-debt register, content guard.
