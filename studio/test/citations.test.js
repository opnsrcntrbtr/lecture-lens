// Citation checks (ALCE-style). Fixtures are shared with the Python tests.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { checkCitations, knownRefs, dropUnknownRefs } from "../src/core/citations.js";

const fx = JSON.parse(readFileSync(new URL("./fixtures/citations.json", import.meta.url)));

test("answer citation checks match the Python fixtures", () => {
  for (const c of fx.answers) assert.deepEqual(checkCitations(c.answer, c.n), { warnings: c.warnings, coverage: c.coverage }, c.answer);
});

test("citations to slides or decks never given are removed and counted", () => {
  const refs = knownRefs(fx.refs.source);
  assert.deepEqual([...refs.slide].sort(), [1, 3]);
  assert.deepEqual([...refs.deck], [2]);
  assert.deepEqual(dropUnknownRefs(fx.refs.text, refs), { text: fx.refs.expected, removed: fx.refs.removed });
  assert.deepEqual(dropUnknownRefs("no refs here", refs), { text: "no refs here", removed: 0 });
});
