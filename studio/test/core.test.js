import { test } from "node:test";
import assert from "node:assert/strict";
import { tokens, jaccard, grounded, hhmmss, splitLines, salvageJsonObjects, cleanReply, sections } from "../src/core/text.js";
import { parseCards, dedupeCards, cardsTsv, cardCount } from "../src/core/cards.js";
import { slidesMarkdown, slideSource, cleanDescription } from "../src/core/slides.js";
import { budgetFor, wrapArraySchema, unwrapArrayReply } from "../src/engines/budget.js";
import { pick } from "../src/engines/index.js";

test("tokens and jaccard match the Python helpers", () => {
  assert.deepEqual([...tokens("Drip lines, 40% water; a b")].sort(), ["drip", "lines", "water"]);
  assert.equal(jaccard(new Set(), new Set()), 1);
  assert.equal(jaccard(new Set(["a", "b"]), new Set(["b", "c"])), 1 / 3);
});

test("grounded needs shared content words, one more when the question attributes a claim", () => {
  const ctx = "Mulch keeps garden soil moist; drip irrigation saves water in raised beds";
  assert.ok(grounded("How much water does drip irrigation save?", ctx));
  assert.ok(!grounded("What did the lecture say about pricing tiers?", ctx));
  assert.ok(!grounded("You mentioned drip irrigation, why?", "drip irrigation"));
  assert.ok(grounded("You mentioned drip irrigation saves water, why?", ctx));
});

test("hhmmss, splitLines", () => {
  assert.equal(hhmmss(3725.9), "01:02:05");
  assert.deepEqual(splitLines(["aaaa", "bbbb", "cc"], 9), ["aaaa", "bbbb\ncc"]);
  assert.deepEqual(splitLines([], 10), []);
});

test("salvage complete objects from a truncated or chatty reply", () => {
  const raw = 'Sure! [{"q":"Why mulch?","a":"It holds moisture {mostly}."}, {"q":"Cut off';
  assert.deepEqual(salvageJsonObjects(raw), [{ q: "Why mulch?", a: "It holds moisture {mostly}." }]);
  assert.equal(cleanReply("<think>hmm</think>\n```json\n[1]\n```"), "[1]");
  assert.deepEqual(Object.keys(sections("# T\n\n## TL;DR\n- a\n## Key concepts\n- b\n")), ["TL;DR", "Key concepts"]);
});

test("cards: parse, normalise, dedupe, TSV", () => {
  const raw = JSON.stringify([
    { q: "Why mulch a bed?", a: "To keep soil moist.", at: "00:01:00", kind: "why" },
    { q: "Why mulch the bed?", a: "Keeps soil moist.", at: "x", kind: "odd" },
    { q: "Compare drip and sprinklers", a: "Drip wastes less water.", at: "", kind: "compare" },
    { q: "", a: "no question" },
  ]);
  const cards = dedupeCards(parseCards(raw));
  assert.equal(cards.length, 2);
  assert.equal(cards[1].kind, "compare");
  assert.equal(parseCards('{"cards":[{"q":"A?","a":"B","at":"bad"}]}')[0].at, "");
  assert.equal(cardsTsv([{ q: "Tab\there?", a: "Line\nbreak", at: "00:02:00" }], "Garden"), "Tab here?\tLine break\tGarden 00:02:00\n");
  assert.equal(cardCount("word ".repeat(100)), 4);
  assert.equal(cardCount("word ".repeat(5000), 20), 20);
});

test("slides: markdown and the notes source match lecture_kit", () => {
  const slides = [{ n: 1, at: "00:00:10", file: "01_000010.jpg", screen_text: "Raised beds", description: "## Raised beds\n- drain well" },
    { n: 2, at: "00:05:00", file: "02_000500.jpg", screen_text: "Drip lines", description: null }];
  const md = slidesMarkdown("Garden", slides);
  assert.match(md, /^# Slides & visuals — Garden/);
  assert.match(md, /## 2\. at 00:05:00\n\n!\[\[02_000500\.jpg\]\]\n\n> On-screen text: Drip lines/);
  assert.match(slideSource(slides), /^SLIDES:\n\[slide 1 @ 00:00:10\] ## Raised beds/);
  assert.match(slideSource(slides, { deckSrc: "DECK:\n1. Beds", qaSrc: "CLASS Q&A:\nQ" }), /KEYFRAME TIMES[\s\S]*CLASS Q&A/);
  assert.equal(cleanDescription("SKIP"), null);
  assert.equal(cleanDescription("This slide shows: ## Beds\n- one"), "## Beds\n- one");
});

test("budgets, array-schema wrapping, engine choice", () => {
  assert.deepEqual(budgetFor(9216), { windowTokens: 9216, partChars: 12902, sourceChars: 22118 });
  const s = { type: "array", items: { type: "string" } };
  assert.equal(wrapArraySchema(s).properties.items, s);
  assert.equal(unwrapArrayReply(s, '{"items":["a"]}'), '["a"]');
  assert.equal(unwrapArrayReply({ type: "object" }, "x"), "x");
  assert.equal(pick("auto", { nano: "available", webgpu: "available" }).name, "gemini-nano");
  assert.equal(pick("auto", { nano: "unavailable", webgpu: "available" }).name, "Qwen3-4B-q4f16_1");
  assert.throws(() => pick("auto", { nano: "unsupported", webgpu: "unsupported" }), /neither/);
});
