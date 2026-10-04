---
slug: /
title: Lecture Lens
sidebar_label: Overview
---

# Lecture Lens

Lecture Lens turns the lectures you attend online into a transcript, slide-linked notes and flashcards. It runs on your own Mac with local models, and nothing is uploaded.

It has two parts:

- **A toolkit** (zsh and Python) that starts and stops capture, finds lecture sessions in what was captured, and builds study material.
- **A macOS app** (SwiftUI) that controls the toolkit, shows health, lists lectures, answers questions and runs evaluations.

Capture itself is done by [screenpipe](https://github.com/screenpipe/screenpipe), which you install separately.

## Who it is for

A working professional taking a part-time online course: recorded lectures during the week, a live class at the weekend, and little time to re-watch either.

## What you get per lecture

| File | What it is |
| --- | --- |
| `transcript.md` | Timecoded speech-to-text |
| `slides/` and `slides.md` | The distinct slides, each with a description and its on-screen text |
| `notes.md` | Notes with time and slide citations, action items and open questions |
| `cards.tsv` | Flashcards you can import into a spaced-repetition app |
| `manifest.json` | What was built, from what, with which models and prompt versions |

## Limits you should know first

- **macOS on Apple silicon only.**
- **Copy-protected video is not recorded.** On those pages screen capture pauses and audio continues. See [Privacy and protected video](concepts/privacy.md).
- **Transcripts are incomplete.** In one measured live class about a quarter of spoken words were missing compared with the meeting app's own transcript. See [Tech debt](project/tech-debt.md).
- **Local models are slow.** A 90-minute class takes roughly ten minutes to build; a full evaluation run takes over an hour.

## Where to go next

1. [Install](getting-started/install.md)
2. [Configure for your course](getting-started/configure.md)
3. [Capture a live class](guides/live-classes.md) or a [recorded lecture](guides/recorded-lectures.md)
