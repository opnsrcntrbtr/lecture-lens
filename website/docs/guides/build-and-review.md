---
title: Build and review
---

# Build and review

## Commands

```sh
./lecture list --since 7d                 # sessions found in the captures
./lecture build last                      # the most recent one
./lecture build 20260110-1000             # by id
./lecture build --from 10:00 --to 11:30   # one session for a time window
./lecture build last --no-vision          # skip slide descriptions (fast)
./lecture doctor                          # database, frames, vision model, vault
```

## How a build works

1. **Transcript.** Speech-to-text rows for the session's time span, from the audio device with the most text. Deterministic.
2. **Keyframes.** Frames the recorder saved on a visual change, filtered to distinct slides. Two frames are the same slide only if they look alike **and** their on-screen text overlaps; slides from one deck share a template, so image similarity alone merges them.
3. **Slide descriptions.** One vision-model call per slide, cached, so a rebuild is cheap.
4. **Notes.** If the transcript fits one model context, notes are written from it directly. If not, each part is digested first and notes are written from the digests, so a long class is covered to the end.
5. **Cards.** From each part of the transcript and from the slide text.

## Prompt rules that came from real failures

- Two different values for one fact: report both and who gave each.
- A question that was deferred: say it was not answered. Never infer the answer.
- A statistic on a slide with no source: write "the slide claims".
- Names: use the spelling on the slide, not the one from speech-to-text.

## Review checklist

- [ ] Slide count is plausible.
- [ ] Names match the slides.
- [ ] Nothing in the notes answers what the class left open.
- [ ] Each card has one idea and a short answer.
