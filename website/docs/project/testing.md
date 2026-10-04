---
title: Testing strategy
---

# Testing strategy

Cheapest first. The first three layers need no model and no running capture, so they run on every change and in CI.

| Layer | Covers | Runs | Time |
| --- | --- | --- | --- |
| 1. Unit (pytest) | Session splitting, time-window sessions, transcript parts, title rules, card JSON salvage, keyframe text comparison, word error rate, the content guard | Every change; CI | seconds |
| 2. Contract | `--json` output of each tool and the Swift types that decode it | Every change; Swift tests in CI on macOS | under a minute |
| 3. Replay (planned) | Recorded health sequences and a small database fixture drive the watchdog and the build | Every change | under a minute |
| 4a. Smoke eval | 6 fixed cases through generator and judge | Prompt, model or build-logic change; not during capture | about 12 minutes |
| 4b. Full eval | All question, notes and card cases, plus your private session cases | On request | over an hour |
| 4c. Judge benchmark | Labelled pairs: is the judge right? | When the judge or its prompt changes | about 10 minutes |

## Every fault becomes a named test

| Test | Asserts |
| --- | --- |
| `test_window_session_keeps_early_audio` | A window starting before the first frame includes the earlier audio |
| `test_long_transcript_is_fully_covered` | Parts of a long transcript join back to the input exactly |
| `test_two_slides_of_one_deck_differ_by_text` | Template-alike slides are kept apart by their text |
| `test_ui_chrome_is_not_slide_text` | Meeting-app controls do not count as slide content |
| `test_judge_scores_the_answer_not_its_source_list` | The judge sees the answer body only |
| `test_the_repository_itself_is_clean` | The tracked tree passes the content guard |

Planned with the capture guard: a zero-writes replay must turn the Screen light red within 60 seconds, and a protected-video pause must not.

## Not automated

Starting real capture, permission prompts and protected-video behaviour need the signed app and a person. They are covered by the preflight in [Capture a live class](../guides/live-classes.md).

## Test data

Invented subjects only (biology, astronomy). Never real course content: the content guard fails the build if it appears.
