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
