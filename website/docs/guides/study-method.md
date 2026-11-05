---
title: A study method that uses the output
---

# A study method that uses the output

Notes you did not write are easy to read and easy to forget. This page says how to use what Lecture Lens produces so that it helps you remember.

## What the research says

- A review of ten study techniques rated **practice testing** and **distributed practice** as high utility, and summarisation, highlighting and rereading as low. ([Dunlosky and others, 2013](https://www.psychologicalscience.org/news/releases/which-study-strategies-make-the-grade.html), publisher's summary.)
- In a field experiment, students with unrestricted access to a chat model did 48% better on practice problems and 17% worse on a later exam taken without it. A tutor version with guardrails removed most of the harm. ([Bastani and others, 2025](https://ideas.repec.org/a/nas/journl/v122y2025pe2422633122.html), abstract.)

The second study is on school mathematics; applying it to adult professional learning is an inference.

## The loop

The full references for this page are on [Research basis](../project/research-basis.md).

1. **Before class.** Read the pre-read and write three questions you expect the class to answer.
2. **During class.** Capture runs. Write only your doubts.
3. **Same day, before opening the notes.** From memory: the three main ideas, and what you are expected to produce. Then read the notes and correct yourself.
4. **Spaced review.** Run `./lecture review` daily; answer each card from memory, then mark it pass or fail. The gaps are not fixed: each card is scheduled with the FSRS-5 memory model so that it comes back when its predicted recall falls to 90% (`STUDY_REVIEW_RETENTION`). The evidence supports spacing in general; FSRS's particular model is fitted to other people's review logs. A card that fails three times is listed by `./lecture review --stats` as one to rewrite or split.
5. **Weekly.** One question that applies the week's ideas to your own project.

## What this means for the product

The [roadmap](../project/roadmap.md) puts recall-first notes and a card review log ahead of longer or prettier notes.
