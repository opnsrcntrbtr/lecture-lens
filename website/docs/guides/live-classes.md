---
title: Capture a live class
---

# Capture a live class

A live class cannot be repeated, so the first minute matters more than anything after it.

## Five minutes before

1. `./sp-mode live` (or leave auto-switch on).
2. Check `SP_LIVE_APPS` in `.env` contains the name macOS reports for your meeting app.
3. Make sure no evaluation is running: the model server should have the generator loaded.
4. Click **Start capture** in the app. Never start capture from a terminal: the shell does not hold the recording permissions, and the recorder will wait forever.

## The first minute: prove data is being saved

A green light is not proof. Check the recorder's health endpoint:

```sh
curl -s http://127.0.0.1:3030/health | python3 -m json.tool | grep -E "frames_db_written|capture_attempts|last_audio_timestamp"
```

- `frames_db_written` must rise while the meeting window is in front.
- If `capture_attempts` rises and `frames_db_written` stays at 0, every frame is being skipped. The usual cause is a browser-site allowlist left over from portal mode; `sp-start` clears it in live mode, so stop and start capture once from the app.
- `last_audio_timestamp` must be within the last minute.

## During class

- Keep the meeting window in front. If another window of the meeting app (its home screen, a chat pop-out) is in front, the slides are not captured and that window is.
- Do not run builds or evaluations: they compete for the one model slot.

## The live follower

In live mode, `sp-start` also starts `live_class.py`, a background loop that only reads what the recorder has already saved. It never starts, stops or reconfigures capture, and it skips a round when the recorder's health is not ok. Turn it off with `STUDY_LIVE_FOLLOWER=false`. `sp-stop` stops it.

Every minute it writes to `lectures/live/<yyyymmdd>/` and to a live note in your vault:

| File | What it holds |
|---|---|
| `whisper_segments.jsonl` | The class re-transcribed with whisper.cpp, in 5-minute batches with 30 s of overlap, at background priority |
| `slides.jsonl` | One entry per distinct slide. Set `LIVE_SLIDE_MARKER` (and optionally `LIVE_SLIDE_FOOTER`) to your deck's marker and footer regexes for clean titles |
| `outline.jsonl` | Three bullets every 10 minutes from the transcript and slide titles |
| `questions.jsonl` | Three questions to ask every 10 minutes, each with a draft answer. A draft may cite only the sources in `evidence.json`, a list you check yourself before class |
| `qa_threads.json` | The meeting app's Q&A panel as threads: a question and the staff replies |
| `qa_followups.jsonl`, `qa_view.json` | Drafted follow-ups for answered threads, and the curated set the app shows |
| `ask_now.json` | The top 3 questions to ask now, refreshed every 5 minutes |

### Q&A threads and follow-ups

- **Names.** List staff in `LIVE_QA_STAFF` (comma-separated). Staff replies keep their names; every other author becomes "Attendee", your own entries become "You", and attendee names quoted in a message become "another attendee". Without the list, staff are inferred, which can mislabel an attendee, so set it.
- **Triage.** Thanks and one-liners get no follow-up and are hidden. Logistics threads (recordings, slides, polls, audio) get one follow-up about the logistics. Content threads get two: one that pins the reply down, one that links it to what the teacher said around the time of the question (8 minutes before to 4 after).
- **Curation.** A follow-up must share a word with its own thread, and one that repeats a follow-up already shown under an earlier thread is dropped.
- **Ask now.** Candidates are scored by overlap with the last 5 minutes of speech, freshness and a supporting source. Anything close to a question already in the Q&A is dropped.

The app's **Live** tab shows all of this with a Copy button on each question. Paste into the meeting app yourself; nothing is posted for you.

### Why slides can stop appearing

The recorder stores a window's accessibility text in preference to OCR and runs OCR only when that text is empty. Some meeting apps expose their toolbar and docked panels as accessibility text, so the shared screen stops being read. The build's keyframes and slide descriptions still recover the slides after class.

## After class

```sh
./lecture build --from 10:00 --to 11:30 --title "Week 3 live class"
```

`--from` and `--to` are today's local time. A time window is used because audio often starts before the first slide and a long static slide must not split the session.

If the meeting app gives you its own transcript, put it in the lecture folder and merge it:

```sh
./transcript_ref.py lectures/<session> lectures/<session>/meeting_transcript.txt
./lecture build --from 10:00 --to 11:30 --title "Week 3 live class"   # rebuild: notes now use the merged transcript
```

This also writes `transcript_eval.json` with the word error rate of the local transcript against the reference.

When the live follower ran, the build uses its whisper transcript instead of the recorder's own speech-to-text (if it covers at least 60% of the class; `STUDY_TRANSCRIPT_SOURCE=screenpipe` turns this off), adds `qa.md` with the curated Q&A, and gives the notes the Q&A as an extra source.

## Add the deck and the Q&A export

Two files from the class make the build better than anything captured on screen:

- **The deck.** Put the `.pptx` in the lecture folder and rebuild. `tools/deck_text.py` reads each slide's text and speaker notes with the standard library only, and writes `deck.json`. The notes and cards then use the deck as their slide source, cited as `[deck N]`, in place of keyframe descriptions; `slides.md` keeps the keyframes. Lines repeated on a quarter or more of the slides (navigation, footers) are dropped.
- **The Q&A export.** If the meeting app lets you save its Q&A, run:

  ```sh
  ./lecture qa-import <session> path/to/qa_export.txt
  ```

  It merges the export's doubled threads and keeps staff (`LIVE_QA_STAFF`) named. Everyone else becomes "Attendee" (or "You"), and their names are removed from message text. It writes `qa_threads_export.json`, which the next build uses for `qa.md`; the live follow-ups stay attached. If the live follower ran, it also writes `qa_capture_eval.json`: how many of the export's questions and staff replies the live capture saw. Keep the raw export out of git, because it names attendees.

## Check before you trust the notes

- `manifest.json`: is `slides` plausible for the length of the class? A handful of slides for a long class means frame extraction failed; the build log says why.
- Read the notes against the transcript for: an answer invented for a question the teacher deferred, one value where two were given, and names spelled from speech instead of from slides.
