---
title: Research basis
---

# Research basis

Every design decision that rests on published research is listed here with the reference and where it is used in the code. A change that relies on a new study adds it to this page in the same commit. The improvement loop in `ralph/` works through these items one at a time.

Each entry gives the claim we rely on and how far we rely on it. Most of these studies used other learners, other material or other models than ours, so applying them here is an inference, and the evaluation suite is how we check it.

## Grounded question answering

| Decision | Where | Reference |
| --- | --- | --- |
| Rank retrieved captures by Okapi BM25 before packing the context, instead of packing them in time order | `study.bm25_rank`, `study.answer_from` (`bm25-v1`) | Robertson, S. and Zaragoza, H. (2009). The probabilistic relevance framework: BM25 and beyond. *Foundations and Trends in Information Retrieval* 3(4), 333–389. doi:10.1561/1500000019 |
| Put the most relevant source first | `study.answer_from` | Liu, N. F., Lin, K., Hewitt, J., Paranjape, A., Bevilacqua, M., Petroni, F. and Liang, P. (2024). Lost in the middle: how language models use long contexts. *Transactions of the ACL* 12, 157–173. doi:10.1162/tacl_a_00638 |
| Check every `[n]` citation: it must point at a source that was sent, and substantive sentences should carry one; report `citation_warnings` and `citation_coverage` | `study.check_citations`, `study ask --json` | Gao, T., Yen, H., Yu, J. and Chen, D. (2023). Enabling large language models to generate text with citations. *Proceedings of EMNLP 2023*, 6465–6488. doi:10.18653/v1/2023.emnlp-main.398 |

## Learning science

| Decision | Where | Reference |
| --- | --- | --- |
| Review cards by testing yourself, on a spaced schedule; not by rereading notes | `review.py`, [study method](../guides/study-method.md) | Dunlosky, J., Rawson, K. A., Marsh, E. J., Nathan, M. J. and Willingham, D. T. (2013). Improving students' learning with effective learning techniques. *Psychological Science in the Public Interest* 14(1), 4–58. doi:10.1177/1529100612453266 |
| Spacing helps recall across many studies; the best gap grows with how long you need to remember | `review.py` | Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T. and Rohrer, D. (2006). Distributed practice in verbal recall tasks: a review and quantitative synthesis. *Psychological Bulletin* 132(3), 354–380. doi:10.1037/0033-2909.132.3.354 |
| Schedule each card with the FSRS-5 memory model (difficulty, stability, retrievability), target recall 0.9, pass = Good, fail = Again | `review.py` | Ye, J., Su, J. and Cao, Y. (2022). A stochastic shortest path algorithm for optimizing spaced repetition scheduling. *Proceedings of KDD 2022*, 4381–4390. doi:10.1145/3534678.3539081. Formulas and default weights: open-spaced-repetition, "The Algorithm", FSRS-5 section (awesome-fsrs wiki). |
| Open each lecture's notes with three questions to answer from memory, answers after the notes | `lecture_kit.recall_first` | Roediger, H. L. and Karpicke, J. D. (2006). Test-enhanced learning: taking memory tests improves long-term retention. *Psychological Science* 17(3), 249–255. doi:10.1111/j.1467-9280.2006.01693.x |
| A failed attempt before study still helps: ask before showing the notes | `lecture_kit.recall_first` | Richland, L. E., Kornell, N. and Kao, L. S. (2009). The pretesting effect: do unsuccessful retrieval attempts enhance learning? *Journal of Experimental Psychology: Applied* 15(3), 243–257. doi:10.1037/a0016496 |

## Evaluation

| Decision | Where | Reference |
| --- | --- | --- |
| Report every pass rate with a 95% Wilson score interval, not the normal interval, because our suites are small and often at 0% or 100% | `tests/evals/stats.py`, `summarize.py` (`ci95`) | Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the American Statistical Association* 22(158), 209–212. doi:10.1080/01621459.1927.10502953 · Brown, L. D., Cai, T. T. and DasGupta, A. (2001). Interval estimation for a binomial proportion. *Statistical Science* 16(2), 101–133. doi:10.1214/ss/1009213286 |
| Compare two runs case by case with McNemar's exact test on the discordant pairs | `tests/evals/compare_runs.py`, `run.sh` (`compare.md`) | McNemar, Q. (1947). Note on the sampling error of the difference between correlated proportions or percentages. *Psychometrika* 12(2), 153–157. doi:10.1007/BF02295996 · Dietterich, T. G. (1998). Approximate statistical tests for comparing supervised classification learning algorithms. *Neural Computation* 10(7), 1895–1923. doi:10.1162/089976698300017197 |
| Validate the LLM judge against hand labels before trusting it as a gate; report agreement (Cohen's kappa) and intervals, not accuracy alone | `tests/evals/judge_bench.py` | Zheng, L., Chiang, W.-L., Sheng, Y. and others (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *NeurIPS 2023 Datasets and Benchmarks*. arXiv:2306.05685 · Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement* 20(1), 37–46. doi:10.1177/001316446002000104 |
| Bootstrap interval for AUROC, seeded for reproducible reports | `tests/evals/stats.py` | Efron, B. and Tibshirani, R. J. (1993). *An Introduction to the Bootstrap*. Chapman & Hall. doi:10.1201/9780429246593 |
| Judge benchmark includes *partial* claims (one true and one false atomic fact) and multi-step *derived* claims, so a judge that checks only the first fact, or only word overlap, is caught | `tests/evals/judge_pairs.json` | Min, S., Krishna, K., Lyu, X., Lewis, M., Yih, W., Koh, P. W., Iyyer, M., Zettlemoyer, L. and Hajishirzi, H. (2023). FActScore: fine-grained atomic evaluation of factual precision in long form text generation. *Proceedings of EMNLP 2023*, 12076–12100. doi:10.18653/v1/2023.emnlp-main.741 |

## Transcripts and slides

| Decision | Where | Reference |
| --- | --- | --- |
| Normalise reference and hypothesis the same way before word error rate: case, punctuation, contractions, spelled-out numbers, and only true disfluencies (`uh`, `um`, `hmm`) removed | `transcript_ref.normalize` (`normalized` in `transcript_eval.json`) | Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C. and Sutskever, I. (2023). Robust speech recognition via large-scale weak supervision. *Proceedings of ICML 2023*, PMLR 202, 28492–28518 (text normaliser, appendix). arXiv:2212.04356 |
| Confirm a keyframe repeat with local structural similarity, pooled by counting changed grid cells, not by the mean | `lecture_kit.is_repeat`, `changed_cells`; [ADR-005](../decisions/ADR-005-keyframe-ssim.md); `eval/keyframe_bench.py` | Wang, Z., Bovik, A. C., Sheikh, H. R. and Simoncelli, E. P. (2004). Image quality assessment: from error visibility to structural similarity. *IEEE Transactions on Image Processing* 13(4), 600–612. doi:10.1109/TIP.2003.819861 |

## Lecture Lens Studio (in-browser model)

| Decision | Where | Reference |
| --- | --- | --- |
| Check Studio answers' `[n]` citations the same way as `study ask` (shared fixtures pin Python and JS together), and remove `[slide n]` / `[deck n]` citations to sources the model was never given, counting them as `citations_removed` | `studio/src/core/citations.js`, `notes.js`, `ask.js` (`cite-v1`) | Gao, T., Yen, H., Yu, J. and Chen, D. (2023). Enabling large language models to generate text with citations. *Proceedings of EMNLP 2023*, 6465–6488. doi:10.18653/v1/2023.emnlp-main.398 |
| **Rejected:** a lexical screen that flags answer sentences whose words are absent from the sources. On the 96 labelled judge pairs its precision was about 0.5 (chance) at every threshold: wrong numbers and swapped facts reuse the source's words, correct paraphrases avoid them | `studio/scripts/support_bench.mjs` (kept as the record) | Honovich, O., Aharoni, R., Herzig, J. and others (2022). TRUE: re-evaluating factual consistency evaluation. *Proceedings of NAACL 2022*, 3905–3920. doi:10.18653/v1/2022.naacl-main.287 · Maynez, J., Narayan, S., Bohnet, B. and McDonald, R. (2020). On faithfulness and factuality in abstractive summarization. *Proceedings of ACL 2020*, 1906–1919. doi:10.18653/v1/2020.acl-main.173 |
