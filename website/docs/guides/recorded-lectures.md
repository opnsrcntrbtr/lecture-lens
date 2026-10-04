---
title: Capture a recorded lecture
---

# Capture a recorded lecture

For lectures you play in the browser on your course portal.

## Before you start

- Mode is `portal` (`./sp-mode`).
- The portal's host is in `allowed-domains.txt`, and its player path is in `SP_DRM_URLS` if the video is copy-protected.

## Steps

1. In the app, click **Start capture**.
2. Play the lecture.
3. After a minute, check the Capture screen. On a protected player the Screen light reads **paused on protected video — audio still recording**. That is expected.
4. When you finish, click **Stop**.
5. Open **Lectures** and click **Build** on the session, or run `./lecture build last`.

## What you get on a protected player

Audio only: a transcript, notes and cards. There are no slide images, because the screen is not captured while the player is visible. If the course publishes slide decks, add them to the lecture folder yourself.

## If the video goes black

Capture is running without the pause. Stop capture, then check that the app has Accessibility permission (it reads the browser's address to recognise the player page) and that the player's path is in `SP_DRM_URLS`. Background: [ADR-004](../decisions/ADR-004-drm-video-black-screen.md).
