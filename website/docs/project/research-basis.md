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
