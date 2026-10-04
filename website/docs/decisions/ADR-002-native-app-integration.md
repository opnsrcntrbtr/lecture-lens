# ADR-002: Native SwiftUI app that launches the toolkit and reads its files

**Status:** Accepted
**Date:** 2026-09-29
**Deciders:** project maintainer

## Context
The toolkit works from the terminal, but capture has produced **0 frames**:
macOS grants Screen & System Audio Recording to the *responsible app* that
launched screenpipe, and terminal launches never received it. the learner wants a native
macOS interface to control, inspect, give feedback on and evaluate the system.
Constraints: one user, Swift 6.4 / Xcode 27 on macOS 27, no Apple Development
certificate yet, everything local.

## Decision
A SwiftUI app (menu bar + window), built as a **Swift Package and bundled into a
`.app` by a script**, that:
1. **launches** `sp-start` / `sp-stop` itself, so screenpipe and its descendants
   belong to the app for permission purposes;
2. calls the existing Python/zsh tools through a **`--json` contract**
   (one JSON document on stdout, progress on stderr);
3. reads lecture folders, eval summaries and the feedback log **as files**, and
   appends feedback to `feedback/feedback.jsonl`;
4. talks to screenpipe (:3030) and oMLX (:8001) over **localhost HTTP** for health.

## Options considered

### A. App launches tools + reads files (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low: no new service |
| Reuse | Full: every capability stays in the already-tested tools |
| Latency | ~0.1–0.3 s process spawn per call; fine for everything except typing-speed features |
| Permissions | App is the responsible process, which fixes the 0-frames problem |

### B. Local API daemon (FastAPI) the app calls
Cleaner typed boundary, but one more long-running process to supervise and one
more thing that can be down. It also doesn't solve the permission question by
itself: the daemon would need to be the app's child anyway.
**Revisit** if an interactive feature needs sub-100 ms responses.

### C. Rewrite the core in Swift
Most native, but re-implements and re-tests working, evaluated code, and forks
the logic that the eval gate protects. **Rejected.**

### D. Xcode project instead of SwiftPM
Needs Xcode's GUI or xcodegen (not installed) to manage. SwiftPM builds from the
command line, diffs cleanly and is fully scriptable; the bundle script adds
Info.plist and signing. **Revisit** for asset catalogs or App Store packaging.

## Consequences
- **Easier:** permissions; ambient health; one-keystroke feedback; one implementation per capability.
- **Harder:** until an Apple Development certificate exists, each rebuild is a
  new ad-hoc identity, so permissions must be re-granted after rebuilds.
  Mitigation: rebuild rarely, and sign into Xcode to get a free certificate.
- **Contract discipline:** a tool's JSON shape is now an API. The app decodes
  with tolerant `Codable` (optional fields), and `check.sh` exercises each `--json` command.

## Action items
1. [ ] Add `--json` to `lecture doctor|list|build`, `study ask`, `sp-mode`.
2. [ ] Phase A: menu bar + health + start/stop; verify frames > 0 after granting.
3. [ ] Phase B/C screens per 02-system-design.md.
4. [ ] Maintainer: sign into Xcode (Settings → Accounts) to get an Apple Development certificate.
