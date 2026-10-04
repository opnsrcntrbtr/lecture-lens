---
title: Design review
---

# Design review of the app

Reviewed from the SwiftUI source (six screens, about 2,000 lines), not from screenshots, so alignment, spacing and contrast ratios are not assessed. No design system exists yet.

**Summary:** the app is honest about what it knows but checks too little. It reports what processes say about themselves, not what was saved.

## What works

- Status is a named light with a detail line and a fix, not colour alone.
- The pause on protected video is shown as expected behaviour, which prevents a false alarm.
- Destructive choices are confirmed, with a cancel option.
- The Evals screen states cost (duration, model swap) before the click.

## Findings

| # | Severity | Heuristic | Where | Issue | Effort | Fix |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Blocker | Visibility of status | Capture, Screen light | "frames arriving" came from a process status that stayed healthy while nothing was written | S | Drive the light from frames written; show frames saved in the last minute |
| 2 | Blocker | Error prevention | Evals, Run | A judged run can start during a live class and take the model the build needs | S | Disable Run with a reason while live capture is on |
| 3 | Major | User control | Evals, Jobs | A job of up to two hours cannot be cancelled | M | Cancel, then restore the generator |
| 4 | Major | Error recovery | Capture header | A failed start shows raw error output | M | Map known failures to a sentence and one action |
| 5 | Major | Visibility of status | Capture, mode picker | Shows the chosen mode, not the settings in effect | S | One line: windows included, sites allowed |
| 6 | Major | Accessibility (colour alone, names) | Evals results, Jobs | Pass rate is colour only; job state is an emoji; no accessibility labels | M | Text and symbols; label lights and icon buttons |
| 7 | Major | Consistency | Evals, suite description | Stated duration is about half the measured one | S | Show the last measured duration |
| 8 | Minor | Recognition | Evals results | No change shown against the previous run | M | Delta column |
| 9 | Minor | Help | Capture, Permissions | Permissions are static labels although their state is known | S | Granted or missing per row, with the matching Settings button |
| 10 | Minor | Efficiency | Lectures | No build from a time range | M | Add it |
| 11 | Minor | Plain language | Evals feedback | Emoji as labels; "goldens" is jargon | S | "Helpful", "Not helpful", "test cases" |

Fixing 1, 2 and 5 covers every fault seen in the first live class.

## Not reviewed

Visual quality, motion, and contrast need screenshots and a pass with assistive technology. That review is open.
