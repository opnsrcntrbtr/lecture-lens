// Flashcards: parse the model's reply, drop near-duplicates, write cards.tsv.
import { tokens, jaccard, salvageJsonObjects, cleanReply } from "./text.js";
import { CARD_KINDS } from "./prompts.js";

/** Card count scales with the material, as in lecture_kit.make_cards. */
export const cardCount = (source, n = 20) => Math.max(4, Math.min(n, Math.round(String(source).split(/\s+/).filter(Boolean).length / 120)));

export function parseCards(raw) {
  const text = cleanReply(raw);
  let items = [];
  try {
    const v = JSON.parse(text);
    items = Array.isArray(v) ? v : Array.isArray(v?.cards) ? v.cards : [];
  } catch {
    items = salvageJsonObjects(text);
  }
  return items
    .filter((c) => c && typeof c === "object" && String(c.q ?? "").trim() && String(c.a ?? "").trim())
    .map((c) => ({
      q: String(c.q).trim(), a: String(c.a).trim(),
      at: /^\d{2}:\d{2}:\d{2}$/.test(String(c.at ?? "")) ? c.at : "",
      kind: CARD_KINDS.includes(c.kind) ? c.kind : "recall",
    }));
}

/** Drop a card that asks what an earlier card asks: same question words, or the same
 *  answer under another question (lecture_kit.dedupe_cards). */
export function dedupeCards(cards, threshold = 0.6) {
  const kept = [], seen = [];
  for (const c of cards) {
    const q = tokens(c.q), a = tokens(c.a);
    if (seen.some(([q2, a2]) => jaccard(q, q2) >= threshold || jaccard(a, a2) >= threshold)) continue;
    kept.push(c); seen.push([q, a]);
  }
  return kept;
}

const cell = (s) => String(s ?? "").replace(/[\t\r\n]+/g, " ");

export const cardsTsv = (cards, title) =>
  cards.map((c) => `${cell(c.q)}\t${cell(c.a)}\t${cell(title)} ${c.at || ""}`).join("\n") + (cards.length ? "\n" : "");
