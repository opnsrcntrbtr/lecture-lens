// Recall-first notes: three questions above the notes, answers after them. A port of
// lecture_kit.recall_first; studio/test/fixtures/recall.json is generated from the Python
// function, so both paths write the same block. Answering before reading is retrieval
// practice (Roediger & Karpicke 2006); a failed attempt still helps (Richland et al. 2009).
const RECALL_KINDS = ["why", "apply", "compare"];

export function recallFirst(notes, cards, k = 3) {
  const ranked = cards.map((c, i) => [c, i])
    .sort(([a, i], [b, j]) => (RECALL_KINDS.includes(String(a.kind ?? "").toLowerCase()) ? 0 : 1)
      - (RECALL_KINDS.includes(String(b.kind ?? "").toLowerCase()) ? 0 : 1) || i - j)
    .map(([c]) => c);
  const picked = ranked.slice(0, k).filter((c) => c.q && c.a);
  if (!picked.length) return notes;
  const qs = picked.map((c, i) => `${i + 1}. ${c.q}`).join("\n");
  const ans = picked.map((c, i) => `${i + 1}. ${c.a}` + (c.at ? ` [${c.at}]` : "")).join("\n");
  return "## Before you read\n\nAnswer these from memory first. The answers are at the end.\n\n" +
    `${qs}\n\n---\n\n${notes}\n\n---\n\n## Answers to the opening questions\n\n${ans}`;
}
