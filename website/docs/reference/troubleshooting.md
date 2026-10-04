---
title: Troubleshooting
---

# Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Capture "running", but no slides for a live class | A browser-site allowlist left from portal mode makes the recorder skip native windows | Stop and start capture once from the app; check `frames_db_written` rises |
| Capture never starts after a restart from a terminal | The shell lacks microphone or screen permission; the recorder waits | Stop it; start from the app |
| Protected lecture video is black | Screen capture is active on a protected page | Check Accessibility permission and `SP_DRM_URLS` |
| macOS asks for recording permission on every start | The app's signature changed with a rebuild | Use a stable signing identity; see Install |
| App says a permission is "not effective" | macOS reports a new grant only after relaunch | Use **Relaunch app** |
| A long class built with only a few slides | Frame extraction from compacted video failed | Read the build log for a warning; check `ffmpeg` is installed |
| `database is locked` | Capture is running | Nothing to do: the toolkit falls back to the recorder's local API |
| Notes stop partway through a long class | An old build without part digests | Rebuild |
| Eval run fails with timeouts | Per-metric budget too small for a local judge | Use `tests/evals/run.sh`, which raises it |
| Eval refuses to start | Live capture is running | Wait until class ends |
| `git push` says the push URL is disabled | By design | Use `tools/publish.sh` |
| The content guard blocks a harmless word | It contains a blocked word | Add the harmless word to `tools/guard/allow_tokens.txt` with a reason |
