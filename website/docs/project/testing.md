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
| `test_late_relevant_capture_survives_a_tight_budget` | A relevant capture beats thirty earlier, irrelevant ones for the context budget (BM25) |
| `test_out_of_range_citation_is_reported` | An answer citing a source that was never sent is flagged |
| `test_passes_grow_the_interval_and_a_fail_shrinks_it` | The FSRS-5 schedule spaces passed cards out and brings failed ones back |
| `test_recall_first_prefers_reasoning_cards_and_keeps_notes` | Notes open with questions; no answer appears before the notes |
| `test_wilson_known_values`, `test_mcnemar_exact`, `test_cohen_kappa_textbook_example` | Eval statistics match published worked values |
| `test_judge_pairs_are_balanced_and_well_formed` | The judge benchmark stays balanced and consistent |
| `test_normalised_wer_ignores_formatting_only_differences` | "twenty-five" and "25", "didn't" and "did not" are not word errors |
| `test_changed_cells_separate_template_slides_but_not_capture_noise` | Two slides of one deck are kept apart; a cursor or webcam change is not a new slide |

Each of these rests on a published method; see [Research basis](research-basis.md). `eval/keyframe_bench.py` is the measurement behind the last one ([ADR-005](../decisions/ADR-005-keyframe-ssim.md)).

Planned with the capture guard: a zero-writes replay must turn the Screen light red within 60 seconds, and a protected-video pause must not.

## Not automated

Starting real capture, permission prompts and protected-video behaviour need the signed app and a person. They are covered by the preflight in [Capture a live class](../guides/live-classes.md).

## Test data

Invented subjects only (biology, astronomy). Never real course content: the content guard fails the build if it appears.
