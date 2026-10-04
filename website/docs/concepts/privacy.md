---
title: Privacy and protected video
---

# Privacy and protected video

## What stays on your machine

Everything. Captures are stored by the recorder locally. Lecture Lens sends captured text only to the model server address in your `.env`, which should be `127.0.0.1`. There is no telemetry, no hosted evaluation and no account.

## What is captured

Only what the mode allows:

- **Portal mode:** your browser, and only on the sites in `allowed-domains.txt`.
- **Live mode:** the meeting app, and browser windows whose title matches your setting.
- Anything whose app or window title contains a word in `SP_IGNORE` is never recorded. Keyboard and clipboard capture are off. Private browser windows are ignored.

Known gap: in live mode every window of the meeting app is captured, including its home or account screen if you bring it to the front.

## Your microphone

The recorder transcribes the microphone as well as system audio. Lecture builds use the system audio only, but the microphone text is in the recorder's database. Turn the microphone off in the recorder's settings if you do not want that.

## Copy-protected video

Many course portals play lectures through a player with digital rights management. While any screen capture session is active, macOS blanks such video: you see black and hear audio.

Lecture Lens does not try to defeat this. Instead:

- While a protected player page is visible, screen capture is released entirely, so you can watch normally.
- Audio keeps recording through a path that does not involve screen capture.
- The page is recognised by its address, which is why the app needs Accessibility permission.

> **Needs recorder support.** Choosing your own protected pages (`SP_DRM_URLS`), keeping audio during the pause, and tapping one app's audio rely on recorder behaviour that the author runs as local changes to the recorder. Those changes are not in this repository, because the recorder's licence does not allow redistributing them. With a stock recorder, `--pause-on-drm-content` covers only the pages it already knows, and audio may pause too. See [Licensing and names](../project/licensing-and-names.md).

You tell it which pages are protected with `SP_DRM_URLS`. The result on those pages is a transcript, notes and cards, with no slide images.

## Other people

A live class contains other people's voices and names. The output is for your own study. Do not publish it, and check your course's terms before recording at all.

## This repository

The public repository must never contain captured or course material. See the [content guard](../project/content-guard.md).
