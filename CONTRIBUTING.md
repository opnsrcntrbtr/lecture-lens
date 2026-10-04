# Contributing

## Set up

```sh
git clone <this repo> ~/lecture-lens && cd ~/lecture-lens
tools/setup_hooks.sh            # turns on the content guard; required
cp .env.example .env            # then edit
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
./check.sh                      # unit tests and contract checks
```

## Rules for every change

1. **No course material, ever.** No slides, transcripts, notes, screenshots of
   classes, assignment text, or names of institutions, platforms, courses or
   teachers. Use generic words: "the institute", "the course portal",
   `learn.example.edu`. The content guard blocks pushes that break this, and
   CI runs it again.
2. **No screenpipe source.** screenpipe has its own licence. Do not copy its
   code or patches into this repository; describe behaviour in words.
3. **Nothing leaves the machine.** No telemetry, no cloud calls, no hosted
   eval dashboards.
4. Push with `tools/publish.sh`, not `git push`.
5. Run `./check.sh` before you push. Add a unit test for any bug you fix.

## Adding a blocked term

Add it to your own `.guard/terms.local.txt`, run
`tools/guard/make_hashes.py .guard/terms.local.txt`, and commit the updated
`tools/guard/blocked_terms.sha256`. The plaintext file is never committed.

## Licence of contributions

By contributing you agree that your contribution is licensed under Apache-2.0.
