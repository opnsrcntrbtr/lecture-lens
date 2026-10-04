---
title: Roadmap
---

# Roadmap

The job to be done: *when I attend a class, I want it captured and turned into material I can trust and will remember, without watching the tooling.*

## Now

| Item | Why | Done when |
| --- | --- | --- |
| Capture guard in the app | A class was once "capturing" for ten minutes with nothing saved | Any minute with attempts but no frames or audio written raises an alert within 60 s, with the cause named |
| Settings reconciliation | A setting left from one mode silently blocked another | Every mode-owned setting is rewritten before each start; a test starts portal then live and asserts the result |
| Eval controller | Runs take up to two hours and cannot be cancelled | Cancel stops the run and restores the generator; results show the change against the previous run |
| Transcript completeness | About a quarter of spoken words were missing in a measured class | Words per minute is a health signal; saved audio can be re-transcribed after class |
| Eval checks from real failures | Notes hid contradictions and answered a deferred question | Checks for contradiction reporting, deferred questions and name fidelity |

## Next

| Item | Why |
| --- | --- |
| Recall-first notes | Show three questions before revealing the notes; see [study method](../guides/study-method.md) |
| Card review with a pass/fail log | Spacing and testing are the two best-supported techniques; a failed card is also feedback on the card |
| Class schedule | Remind, run preflight and start in the right mode before a class |
| Import the course's slide deck | A deck settles what speech-to-text garbles: names, numbers, tables |
| Build from a time range in the app | Today it needs the command line |
| Window-title filter in live mode | Keep the meeting app's own screens out of the capture |

## Later

- **A local MCP server** exposing `list lectures`, `build`, `ask` and `health` as tools, so an assistant can work with your lectures without shell access. Read-only by default; nothing leaves the machine.
- A per-claim checker for notes using a small, fast judge, once it is accurate enough on derived claims.
- Speaker labels.
- Nightly full evaluation when no capture is active.

## Considered and dropped

| Idea | Why not |
| --- | --- |
| Record everything, filter later | Breaks the privacy rule and the protected-video constraint |
| Longer, richer notes and weekly digests | Feels productive; the evidence says summaries you did not write do little |
| Hosted evaluation dashboards | Captured text would leave the machine |
| A single capture mode | Would remove the settings-leak class of bug, but the site allowlist is what keeps unrelated browsing out |

## Riskiest assumptions

1. **The local judge is reliable enough for a gate.** It scored perfectly on the first benchmark, which more likely means the benchmark is easy. Harder pairs come before the gate is allowed to block.
2. **The recorder's behaviour stays stable.** Lecture Lens depends on a separate product it does not control.
3. **The owner will review cards.** If not, the highest-value artefact is unused; the review log exists to find out.
