# Research-backed improvement plan

The loop reads this file at the start of every iteration, does the top open item
it can run where it is, and updates this file before it commits. One item per
iteration.

Status: `[ ]` open · `[x]` done · `[~]` blocked (reason given) · `[m]` needs the Mac
(local models or the app; the cloud loop skips these).

Every item cites the research it rests on. Full references are in
`website/docs/project/research-basis.md`; add a reference there in the same commit
as the code that relies on it.

## Grounded Q&A

- [x] **R1 Rank retrieved captures by relevance before packing the context.**
  `study.answer_from` packs rows in timestamp order, so with many hits the
  earliest captures fill the budget, not the most relevant. Score with Okapi
  BM25 (Robertson & Zaragoza 2009) against the question, pack the best first,
  and put the strongest source first (Liu et al. 2024, "Lost in the Middle").
  Done when: a unit test shows a late, relevant capture beats early irrelevant
  ones under a tight budget.
- [x] **R2 Check the citations in an answer.** Following ALCE (Gao et al. 2023),
  citations are part of the answer's correctness. Detect citations to sources
  that were never sent, and answers with no citation at all; return them as
  `citation_warnings` in `--json` and print them on the CLI. Optional field in
  `Contracts.swift` + a contract test.
  Done when: unit tests cover a valid, an out-of-range and an uncited answer.
- [m] **R3 Measure retrieval separately from generation.** Add a context-recall
  check to the smoke eval (RAGAS, Es et al. 2024) so a bad answer can be traced to
  retrieval or to the model.

## Learning science

- [x] **L1 Spaced review with a pass/fail log.** Roadmap "Next" item. Practice
  testing and distributed practice are the two high-utility techniques
  (Dunlosky et al. 2013); schedule with the FSRS memory model (Ye, Su & Cao 2022)
  using pass = Good, fail = Again, desired retention 0.9. `./lecture review`
  asks due cards, logs every answer to `lectures/<id>/reviews.jsonl` (ignored
  folder), and reports cards that keep failing, since a failed card is also
  feedback on the card.
  Done when: unit tests cover the forgetting curve, intervals growing on pass and
  shrinking on fail, due selection, and the log round trip.
- [x] **L2 Recall-first notes.** Roadmap "Next" item. Put three questions at the
  top of `notes.md`, answers folded, before the notes: retrieval before review
  (Roediger & Karpicke 2006) and the pretesting effect (Richland, Kornell & Kao
  2009). Prefer why/apply/compare cards over recall. (Answers go after the notes:
  the app's Markdown view does not fold `<details>`.)
  Done when: a unit test on the pure function; notes text unchanged below the block.
- [x] **L3 D13: the card metric must accept recall cards.** The card prompt allows
  up to a third recall cards, and recall itself is supported by the testing-effect
  evidence (Roediger & Karpicke 2006). Rewrite the "Card quality" criterion to
  judge the mix, not to reject recall.
  Done when: criterion changed; tech-debt register updated.

## Evaluation rigour

- [x] **E1 Confidence intervals on every pass rate.** Six smoke cases cannot show a
  small change. Report a Wilson score interval with each pass rate (Wilson 1927;
  recommended over the normal interval by Brown, Cai & DasGupta 2001) in
  `summarize.py` and the judge benchmark.
  Done when: unit tests check known Wilson values; summary.json carries `ci95`.
- [x] **E2 Compare two runs case by case.** Roadmap "results show the change against
  the previous run". Store per-case outcomes and compare runs with McNemar's exact
  test on the discordant pairs (McNemar 1947; Dietterich 1998).
  Done when: `tests/evals/compare_runs.py A B` prints per-metric change and p-value;
  unit tests on the test statistic.
- [x] **E3 Agreement, not only accuracy, for the judge.** Add Cohen's kappa
  (Cohen 1960) between judge and labels, with a bootstrap interval for AUROC
  (Efron & Tibshirani 1993), so "the judge scored perfectly on 72 pairs" comes with
  its uncertainty (Zheng et al. 2023 on judge reliability).
  Done when: unit tests on kappa; report.md shows both.
- [x] **E4 D6: harder judge pairs.** Add `partial` pairs (one supported and one
  unsupported atomic fact in one claim, after FActScore, Min et al. 2023) and more
  `derived` pairs, all on invented content.
  Done when: pairs added, balance kept, `about` updated.
- [m] **L4 Confirm L3 on a judged run.** Smoke eval before/after the new card criterion;
  report card-quality pass rate with n and the E1 interval.
- [m] **E6 Run the judge benchmark on the 96 pairs** and record per-kind accuracy for
  `partial` and `derived` with intervals; only then let the judge gate a build (roadmap risk 1).
- [m] **E5 D5: re-baseline thresholds** after the next full judged run, using the
  interval from E1 rather than the point estimate.

## Transcript and slides

- [x] **T1 Standard text normalisation for word error rate.** The "content words"
  list drops real words (`you`, `all`, `right`, `know`). Normalise like Whisper's
  English normaliser (Radford et al. 2023): case, punctuation, contractions,
  number words, and only true disfluencies (`uh`, `um`, `hmm`, `mm`, `er`, `ah`).
  Keep S/D/I counts.
  Done when: unit tests show "twenty five" = "25", "don't" = "do not", and that
  "you" is kept.
- [x] **T2 Measure keyframe de-duplication before changing it.** Build a synthetic
  benchmark from `eval/gen_dataset.py` slides (same slide + cursor/noise vs.
  different slides of one template). Score the current dHash+text rule and an SSIM
  rule (Wang et al. 2004). Switch only if the numbers say so.
  Done when: benchmark script + unit test; result recorded in an ADR.
- [~] **T4 Prefer the fuller frame of an animation build. Rejected on evidence.**
  Tried: replace a kept frame when every changed grid cell went from blank to
  content. With a webcam inset, 3-8 cells also change content-to-content during a
  build against 9-14 on a real slide change: a one-cell margin on our own synthetic
  layout, and an error drops a real slide to save one vision call. Kept both frames
  (ADR-005). Revisit only with real build examples.
- [m] **T5 Check v2 keyframes on a real class**: slides kept vs a hand count, before/after.
- [m] **T3 Re-transcribe saved audio after class** (D10) and report WER against the
  reference with T1's normaliser.

## Docs

- [x] **D1 Research basis page** listing every reference above with what it is used
  for, linked from the study-method, evaluation and testing pages.

## Lecture Lens Studio (in-browser model, ADR-006)

Tests run with `cd studio && node --test test/*.test.js` and the fake engine in
`studio/test/helpers.js`; the real model is on the Mac (Chrome 148+).

- [x] **S1 Check citations in Studio answers and notes.** The PR #29 read-through
  found Nano citing `[slide 4]` with no slide source. Port `study.check_citations`
  (ALCE, Gao et al. 2023): answers report `citation_warnings`; notes and cards drop
  citations to slides that were never given and count them in `studio_result.json`,
  since a citation that points nowhere misleads more than none.
  Done when: node tests cover valid, out-of-range and uncited answers and a notes
  page citing a missing slide; Python and JS agree on shared fixtures.
- [~] **S2 Flag answer sentences the sources do not support. Rejected on evidence.**
  Measured before shipping (`studio/scripts/support_bench.mjs`) on the judge benchmark's
  96 labelled pairs: a content-word overlap screen has precision about 0.5 at every
  threshold (0.3-0.8), i.e. chance. Wrong-number, swapped and half-true claims reuse the
  context's words (mean support 0.67-0.76); correct paraphrases avoid them (0.57). Even
  for unmentioned padding alone, catching 6 of 11 also flags 10 of 48 correct claims.
  Overlap does not measure faithfulness (Honovich et al. 2022); the judge decides (S7).
- [x] **S3 Fit the window by tokens, not a guess.** `BM25.top` stops at the first
  passage that does not fit, leaving smaller later ones out (fix: skip it and keep
  going). Budgets assume 3.5 characters per token; calibrate the ratio from the
  engine's own `measureContextUsage` on a sample when it exists. On a window overflow
  in a digest, split the part in two and retry instead of failing the lecture.
  Best source first is kept (Liu et al. 2024).
  Done when: tests for the packing bug, calibration and split-and-retry.
- [x] **S4 Recall-first notes in the Studio.** The oMLX path opens notes with three
  questions (L2; Roediger & Karpicke 2006; Richland et al. 2009); Studio notes do not.
  Port `recall_first`, write notes after cards.
  Done when: Python and JS give the same block on a shared fixture.
- [ ] **S5 Spaced review in the Studio.** A Review tab that needs no model: FSRS-5
  (Ye, Su & Cao 2022) ported to JS, reading `cards.tsv` and appending to the same
  `reviews.jsonl` as `./lecture review`, so the two share one schedule.
  Done when: JS replay equals Python `review.replay` on golden logs; a headless
  Chromium smoke test loads the tab.
- [ ] **S6 Nano vs Qwen with uncertainty.** The PR's model-free table has no
  intervals (e.g. recall 0.734 vs 0.776 on 36 cases). `tests/evals/compare_outputs.py
  A.json B.json` computes the same checks per case, pairs them, and reports a paired
  bootstrap interval for the mean difference (Efron & Tibshirani 1993) and an exact
  sign test (Dixon & Mood 1946), plus Wilson intervals for pass counts and the rate of
  citations to sources that do not exist.
  Done when: unit tests with known answers; docs show how to run it on the PR's outputs.
- [m] **S7 Judge Nano's outputs.** `EVAL_OUTPUTS=.outputs.nano.json ./run.sh --judge-only`,
  then `compare_runs.py` against the Qwen run: cases are keyed by input, so the two
  generators pair case by case (McNemar).
