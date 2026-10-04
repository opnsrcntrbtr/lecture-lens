---
title: Tech debt
---

# Tech debt register

Scored as (impact + risk) × (6 − effort), each factor 1 to 5. Higher means sooner.

| # | Item | Type | Impact | Risk | Effort | Score | Resolution |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | Settings live in four places (`.env`, flags, the recorder's store, the mode pin) with partial reconciliation | Architecture | 5 | 5 | 2 | 40 | Rewrite every mode-owned setting before each start; a command that prints the settings in effect |
| D2 | Health lights trust process status, not data written | Code | 4 | 5 | 2 | 36 | Watchdog on written counts |
| D3 | `lecture_kit.py` holds database access, vision, prompts and the command line in one file | Code | 4 | 4 | 3 | 24 | Split into `db`, `sessions`, `build`, `cli`; tests exist for the pure parts |
| D4 | Eval jobs cannot be cancelled; model restore relies on a shell trap | Code | 3 | 4 | 2 | 28 | Process-group cancel and an explicit restore step |
| D5 | Thresholds were set before any complete baseline | Test | 4 | 3 | 3 | 21 | Reset after the next judged run |
| D6 | Judge benchmark pairs are too easy | Test | 3 | 4 | 3 | 21 | Add derived-claim and partial-support pairs |
| D7 | The recorder reports every skipped frame the same way | Dependency | 3 | 3 | 3 | 18 | Needs a recorder change; propose upstream |
| D8 | Behaviour depends on local recorder changes that cannot be published | Dependency | 4 | 4 | 4 | 16 | Offer them upstream; document the stock-recorder behaviour |
| D9 | Capture cannot be started by a test (permissions belong to the app) | Test | 3 | 3 | 4 | 12 | Replay tests on recorded health data; a manual preflight |
| D10 | Transcripts lose about a quarter of spoken words | Dependency | 5 | 4 | 4 | 18 | Re-transcribe saved audio after class; raise upstream |
| D11 | Private windows of the meeting app are captured in live mode | Code | 2 | 4 | 2 | 24 | Window-title filter |
| D12 | Two code bases: the public repository and the author's private working copy | Infrastructure | 3 | 3 | 2 | 24 | Make the public repository the only source; keep private files in ignored folders |
| D13 | The card metric rejects recall cards that the card prompt allows | Test | 2 | 2 | 1 | 20 | Change the metric: recall cards are supported by the evidence |

## Order

1. With the capture guard and eval controller: D1, D2, D4.
2. Next: D12, D11, D13, D5, D6.
3. With upstream: D7, D8, D10.
4. Opportunistic: D3, D9.
