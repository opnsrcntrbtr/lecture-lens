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
| `./lecture qa-import <id> <export.txt>` | Use a meeting app's Q&A export: anonymise it for the build and score the live capture against it |
| `./lecture watch [--idle 5]` | Build each session a few minutes after it ends |
| `./lecture doctor [--quick] [--json]` | Health checks |
| `./lecture review` | Today's cards within the day's share of your weekly minutes, lowest predicted recall first (FSRS-6); pass/fail goes to `reviews.jsonl` in the lecture's folder |
| `./lecture review stats\|plan [--json]` | Due and new counts, minutes this week, target recall, cards that keep failing; `plan` re-runs the workload simulation |
| `./lecture review settings --minutes 60 --class-days Sat` | Weekly budget and class days (half share); no streaks |
| `./lecture review card --card ID [--q ..] [--a ..] [--suspend\|--delete]` | Edit, suspend or delete a card |
| `./lecture mastery [--json]` | Per module: predicted recall, closed-book accuracy (also at Apply and above), confidence grid, sure-but-wrong items |
| `./lecture lanes [--json]` | How long each kind of model caller waited (interactive, time-boxed, background, build), from `lectures/llm_slot_metrics.jsonl` |
| `./lecture capstone peer add\|consent\|review\|doc\|check\|feedback\|classify\|overlap` | Peer review coach: checks the reviews you write and sorts feedback you receive; never writes a comment |
| `./lecture capstone sim start [--full [--practice]]`, `sim next\|decide\|update\|reply\|rate\|hotwash [--postmortem ID]` | Incident drill, you as incident commander. Micro: 20 min, 3 injects. Full: 60 min, 5 injects unlocked by the clock, status updates between injects; the hot-wash rates comms cadence, the reporting window named to the regulator (from the dated snapshot, each clock marked verify), a written golden-set case, and action items from your postmortem. Your self-rating comes first |
| `./lecture agenda [ID] [--no-notes] [--cached] [--json]` | Agenda parts covered, partial, moved or skipped as the class ran; adds a "How the class ran" block to the notes |
| `./lecture prequestions make\|show\|guess\|mark\|resolve --day YYYYMMDD` | Before class, 3 to 5 questions from the agenda in `live/<day>/agenda.txt`, spread across its parts (exercises and breaks skipped); you type a guess for each. After class, each is checked against what was said. The Live screen makes them for the next class within a week |
| `./lecture notes-v2 [ID]` | Notes written outline first: a title from the candidate list, verbatim extracts per section, each section from its own lines, then an audit; into `notes_v2/` beside the current notes |
| `./lecture scenes [ID] [--no-vision]` | Classify kept frames as slide, shared document, gallery, break or other (window title, dwell, on-device OCR layout, vision model last) into `slides.jsonl`. Build the OCR helper once: `swiftc -O tools/ocr/main.swift -o tools/ocr/ocr` |
| `./lecture scene-labels list\|set --id ID --label L` | The labelled keyframes for `tools/scene_bench.py`; `tools/scene_prelabel.py` pre-labels them with the vision model |
| `./lecture cornell show\|build\|summary ID [--text ..]` | Cornell view: cue questions from the notes' timed points (also as cards), your own summary |
| `./lecture teachback start --lecture ID [--section S]` / `score --id X --marks s1,s3` | Find the planted errors; scored against the hidden key |
| `./lecture doctor --privacy [--sync-guard REPO]` | Checks that endpoints, evals and capture stay local; refreshes the guard's hashed roster |
| `./transcript_ref.py <folder> <reference.txt>` | Merge a reference transcript and measure word error rate |
| `./lecture next [--json]` | The next class in the programme calendar |
| `./lecture after [<id>\|last] [--json]` | After class in one go: import the meeting app's files, build, check that no removed attendee name is left in the lecture files, rewrite qa.md. Writes `after_class.json`; refuses while capture runs or the judge is loaded |
| `./lecture curate [--day yyyymmdd] [--background] --json` | Live class: one round of curated top questions from what was taught since the last round, the Q&A, the agenda and the evidence bank. Appends to `questions.jsonl` and `top_questions.md` |
| `./lecture cite --t <time> --text <q and answer> [--sources S1,S2] [--day yyyymmdd] --json` | Citations for an answer: the evidence-bank sources it names and the transcript lines near its time that share its words, with the slide then |
| `./lecture finish [<id>\|last\|all]` | Browser mode: after Lecture Lens Studio ran a lecture, write its vault note and update `manifest.json` |
| `./live-sync questions\|outline\|slides\|all` | Ask the running live follower to update now instead of at its next 10-minute batch; waits up to 3 minutes for the result |
| `tools/merge_reference_transcript.py <live_dir> <reference.txt> <out.md> [--keep 1,2] [--label 1=Name]` | Put clock times on a dictation app's `Speaker N: text` export by aligning it to the live whisper words; speakers not kept become "Attendee"; the follower's own lines fill any gap over 90 s. `lecture build` uses `transcript_merged.md` when present |
| `tools/retranscribe_recordings.py <folder> <start_utc> <end_utc>` | Re-transcribe saved system-audio chunks with whisper.cpp (for a recording played back on this Mac) |

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
