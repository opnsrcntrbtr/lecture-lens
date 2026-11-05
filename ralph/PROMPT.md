# Ralph loop prompt

You are one iteration of a loop. The same prompt runs again after you finish, with
a fresh context. Your memory between iterations is this repository: the plan
(`ralph/fix_plan.md`), the git log, and the code.

## Each iteration

1. Read `AGENTS.md` and `CLAUDE.md`. Their hard rules override this file.
2. Read `ralph/fix_plan.md`. Choose the **single** most important open item you
   can run here. `RALPH_HOST` tells you where you are: `cloud` (no models, no
   macOS app) skips `[m]` items; `mac` may do them, after checking no capture is
   running in live mode and that no other eval or build holds the model slot.
3. Search the code before you assume something is missing.
4. Implement it with the smallest change that meets its "Done when". Cite the
   research in the docstring or comment where the decision lives, and add the
   full reference to `website/docs/project/research-basis.md`.
5. Add or update unit tests (invented content only: biology, astronomy).
6. Run `./check.sh` (or, on Linux, `.venv/bin/python -m pytest tests/unit -q` and
   `python3 tools/content_guard.py --all`). Both must pass. If you change a
   `--json` field, update `Contracts.swift` and `ContractTests.swift` together.
7. If you change a prompt, bump its version, and on `mac` run the smoke eval and
   write the before/after numbers, with n and the interval, in the commit.
8. Update `ralph/fix_plan.md`: mark the item, note anything you discovered as a new
   item with a citation, or mark it blocked with the reason.
9. Commit with a message that names the item id and the reference. Do not push;
   pushing is `tools/publish.sh`, done by a person.

## Stop

When no open item can be run here, write `RALPH: plan empty for <host>` as the
last line of your reply and do nothing else.

## Never

- Read, quote or copy anything under `lectures/`, `notes/`, `private/` or the
  recorder's data folder into a tracked file, a prompt you send anywhere, or a
  commit message.
- Cite a source you have not checked. If you cannot verify a reference, leave the
  item open and say so in the plan.
- Weaken a test, a guard list or a threshold to make a check pass.
