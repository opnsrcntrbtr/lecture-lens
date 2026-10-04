# ADR-004: course portal lecture video goes black while capturing

**Status:** Accepted — verified live 2026-09-30 (video visible, audio transcribed, sessions from markers) · **Date:** 2026-09-30 · **Deciders:** project maintainer

## Symptom
Starting capture from Lecture Lens turns the embedded course portal lecture video black; audio keeps playing.

## Root cause (confirmed)
1. **The video is DRM-protected.** Inspected on the player page: video.js 8.24.1 + `videojs-contrib-eme`, source `application/dash+xml` from `manifest.prod.boltdns.net/.../bccenc/...` (a commercial player Common Encryption), key systems `com.widevine.alpha` and `com.microsoft.playready`.
2. **Screen-pixel capture blanks protected video.** Chrome on macOS stops presenting protected frames while a ScreenCaptureKit screen stream is active; audio is not protected, so it keeps playing. Upstream screenpipe documents the same behaviour (`crates/screenpipe-engine/src/drm_detector.rs`) and ships `--pause-on-drm-content`, but only for a hard-coded list of streaming services.
3. **Controlled test** (`diag-drm`, the learner watching the video, settings written per phase, store restored after):

| Phase | What screenpipe held | Video |
|---|---|---|
| A | ScreenCaptureKit screen frames, no audio | **black** |
| B | system audio via ScreenCaptureKit, no screen | visible* |
| C | system audio via CoreAudio process tap, no screen | visible |
| D | running, screen and audio off (UI taps only) | visible |
| E | vision on with `disableScreenshots` (text only) + audio | **black** |

E shows that "text-only" mode still opens screen-capture APIs, so a settings change alone can't fix it: capture must fully release ScreenCaptureKit while the player is focused — exactly the upstream DRM-pause path.

## Decision
Use the upstream DRM pause, extended in our engine branch (`feat/lecture-capture`):
- `SCREENPIPE_DRM_URLS` — extra DRM pages as `host` or `host/path-prefix` (ours: `learn.example.edu/player`). Other course portal pages are still captured.
- `SCREENPIPE_DRM_KEEP_AUDIO=1` — don't stop audio during the pause (phases B/C show audio is safe; the transcript is what matters).
- `sp-start` passes `--pause-on-drm-content` and both variables when `SP_DRM_URLS` is set in `.env`.
- `sp-autoswitch` logs the focused player page's URL + title to `~/.screenpipe/study-markers.jsonl`; `lecture_kit` turns these into sessions, since no frames exist while paused. The player page title is generic, so `build` names the lecture from its transcript.
- The app's Screen light shows "paused on protected video — audio still recording" (from `/health` `drm_content_paused`).

We do **not** attempt to capture protected frames (no hardware-acceleration or similar workarounds): the protection works as intended and the video stays watchable.

## Consequences
- Recorded course portal lectures: transcript + notes + cards from audio; **no slide images** (they would be black anyway). Slide text must come from course PDFs/decks if needed.
- Live Zoom classes are unaffected (not DRM) and keep full screen capture.
- `--pause-on-drm-content` persists in screenpipe's settings store once passed.
- Found along the way: screenpipe persists *every* CLI flag into `~/.screenpipe/store.bin` and boolean flags can't be reset from the CLI — never run ad-hoc `screenpipe record --disable-*` experiments without restoring the store (diag-drm does).

## Regression 2026-09-30 01:19 — and the guard
The black screen came back after an app rebuild. `sp-start.log`: `permission monitor
started screen=true … accessibility=false`; tccd: `Failed to match existing code
requirement … kTCCServiceAccessibility`. The DRM pause reads Chrome's URL through
Accessibility; with the grant stale (pinned to the previous ad-hoc cdhash) it never
recognises the player page, so capture runs and Chrome blanks the video.

- **Guard:** the app reads its own `AXIsProcessTrusted()` / `CGPreflightScreenCaptureAccess()`
  (screenpipe's checks are attributed to the app, so these are screenpipe's real grants),
  shows an *Access* light when either isn't effective, and refuses **Start capture** while
  `SP_DRM_URLS` is set and Accessibility is missing (alert with *Open Accessibility
  Settings* / *Start anyway*).
- **Root fix:** sign the app with a stable identity (self-signed "Lecture Lens Local"
  code-signing certificate); `build_app.sh` uses it automatically, so grants survive rebuilds.

## Regression 2026-09-30 02:00 — "paused on protected video", yet black
`sp-start.log` showed the pause flag set at 01:59:54 but *"stopping all vision monitors
to release SCK handles"* only at 02:00:44. Two engine gaps:
1. **Late release.** The pre-capture check sets the flag and skips captures, but the
   ScreenCaptureKit streams are released by the monitor watcher, which sleeps up to its
   60 s backstop between iterations. The app showed "paused" while SCK was still open.
   Fix: a `Notify` fired on every pause/resume transition wakes the watcher at once.
2. **Focus-only detection.** The pause cleared whenever another app was focused (Screenpipe
   Study, Claude) because "DRM window on screen" only matched built-in service names in
   window titles — the course portal's title is generic — so capture resumed with the lecture still
   visible. Fix: for `SCREENPIPE_DRM_URLS`, read on-screen browser windows' URLs over
   Accessibility (bounded timeouts) both before capturing and before clearing the pause.

## Regression 2026-09-30 02:15 — the audio half
With the engine pausing correctly (SCK screen streams released in milliseconds), the video
was still black: during the pause `SCREENPIPE_DRM_KEEP_AUDIO` kept **System Audio**
recording through **ScreenCaptureKit**, and any SCK session keeps protected video black —
the reason upstream stops output devices on DRM. (*Phase B's "visible" was not reproducible:
its log was overwritten, and the audio device may not have started within the 40 s phase.)
Fix: `sp-start` adds `--experimental-coreaudio-system-audio` with `SP_DRM_URLS`, so system
audio uses a CoreAudio process tap (no SCK), like the App Tap. Verified live: video visible,
audio still transcribed.
