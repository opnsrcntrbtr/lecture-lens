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

- [ ] **T1 Standard text normalisation for word error rate.** The "content words"
  list drops real words (`you`, `all`, `right`, `know`). Normalise like Whisper's
  English normaliser (Radford et al. 2023): case, punctuation, contractions,
  number words, and only true disfluencies (`uh`, `um`, `hmm`, `mm`, `er`, `ah`).
  Keep S/D/I counts.
  Done when: unit tests show "twenty five" = "25", "don't" = "do not", and that
  "you" is kept.
- [ ] **T2 Measure keyframe de-duplication before changing it.** Build a synthetic
  benchmark from `eval/gen_dataset.py` slides (same slide + cursor/noise vs.
  different slides of one template). Score the current dHash+text rule and an SSIM
  rule (Wang et al. 2004). Switch only if the numbers say so.
  Done when: benchmark script + unit test; result recorded in an ADR.
- [m] **T3 Re-transcribe saved audio after class** (D10) and report WER against the
  reference with T1's normaliser.

## Docs

- [ ] **D1 Research basis page** listing every reference above with what it is used
  for, linked from the study-method, evaluation and testing pages.
