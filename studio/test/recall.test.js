import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { recallFirst } from "../src/core/recall.js";

const fx = JSON.parse(readFileSync(new URL("./fixtures/recall.json", import.meta.url)));

test("S4: recall-first block is the same as lecture_kit.recall_first", () => {
  for (const c of fx.cases) assert.equal(recallFirst(c.notes, c.cards), c.expected);
});
