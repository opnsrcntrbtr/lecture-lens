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

## Check before you trust the notes

- `manifest.json`: is `slides` plausible for the length of the class? A handful of slides for a long class means frame extraction failed; the build log says why.
- Read the notes against the transcript for: an answer invented for a question the teacher deferred, one value where two were given, and names spelled from speech instead of from slides.
