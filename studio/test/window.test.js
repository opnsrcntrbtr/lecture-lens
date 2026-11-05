// S3: fitting the model's window. Invented content; no model.
import { test } from "node:test";
import assert from "node:assert/strict";
import { FakeEngine } from "./helpers.js";
import { BM25 } from "../src/core/ask.js";
import { budgetFor, calibrateBudget, isOverflow } from "../src/engines/budget.js";
import { digestPart } from "../src/core/notes.js";

test("BM25 packing skips a passage that does not fit and keeps a shorter one below it", () => {
  const docs = [
    { label: "a", text: "Comet tails point away from the sun: comet dust tails curve, comet ion tails stay straight, so comet tails show the solar wind." },
    { label: "b", text: "A comet has tails." },
    { label: "c", text: "The moon has craters." },
  ];
  const idx = new BM25(docs), s = idx.score("comet tails");
  assert.ok(s[0] > s[1] && s[1] > 0, "a ranks first, b second");           // the case the bug needs
  assert.deepEqual(idx.top("comet tails", 100).map((d) => d.label), ["b"]);  // a (136 chars) is over budget; before the fix: []
  assert.deepEqual(idx.top("comet tails", 1000).map((d) => d.label), ["a", "b"]);
});

test("budgets follow the measured characters per token", async () => {
  const eng = new FakeEngine([]);
  assert.equal(eng.budget.charsPerToken, 3.5);
  eng.countTokens = async (t) => Math.round(t.length / 2.5);             // a model that splits words finer
  const text = "Photosynthesis converts light energy into chemical energy in chloroplasts. ".repeat(40);
  const b = await calibrateBudget(eng, text);
  assert.ok(Math.abs(b.charsPerToken - 2.5) < 0.01);
  assert.ok(b.partChars < budgetFor(9216).partChars);                    // fewer characters fit
  const short = new FakeEngine([]); short.countTokens = async () => 1;
  assert.equal((await calibrateBudget(short, "too short")).charsPerToken, 3.5);   // < 1,000 chars: not measured
  assert.equal(budgetFor(9216, 50).charsPerToken, 6);                    // clamped to a plausible range
});

test("a part that overflows the window is split and digested in halves", async () => {
  const lines = Array.from({ length: 40 }, (_, i) => `[00:${String(i).padStart(2, "0")}:00] Bees pollinate flowers, point ${i}.`);
  const eng = new FakeEngine([[/TRANSCRIPT/, (u) => {
    if (u.length > 1200) { const e = new Error("input too long for Gemini Nano's window"); throw e; }
    return `- digest of ${u.match(/point (\d+)\./)[1]}`;
  }]]);
  const out = await digestPart(eng, "Pollination", 1, 1, lines.join("\n"));
  assert.ok(eng.calls.length > 1, "it retried on smaller parts");
  assert.match(out, /^- digest of 0/);
  assert.equal(out.split("\n").length >= 2, true);
  const other = new FakeEngine([[/TRANSCRIPT/, () => { throw new Error("network down"); }]]);
  await assert.rejects(digestPart(other, "Pollination", 1, 1, lines.join("\n")), /network down/);   // only overflows are split
  assert.ok(isOverflow({ name: "QuotaExceededError" }) && !isOverflow(new Error("boom")));
});
