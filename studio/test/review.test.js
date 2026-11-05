// S5: the Studio's spaced review reproduces review.py exactly. Invented content.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replay, intervalDays, cardId, queue, logLine, LEECH_LAPSES } from "../src/core/review.js";

const fx = JSON.parse(readFileSync(new URL("./fixtures/review.json", import.meta.url)));
const close = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} vs ${b}`);

test("FSRS-5 replay equals review.replay on the golden logs", () => {
  for (const [name, g] of Object.entries(fx.logs)) {
    const st = replay(g.events);
    close(st.stability, g.stability, `${name} stability`);
    close(st.difficulty, g.difficulty, `${name} difficulty`);
    assert.equal(st.reps, g.reps, name); assert.equal(st.lapses, g.lapses, name);
    assert.equal(st.due, Date.parse(g.due), `${name} due`);
    assert.equal(intervalDays(st.stability), g.interval, `${name} interval`);
  }
  for (const [s, days] of Object.entries(fx.intervals)) assert.equal(intervalDays(+s), days, `interval for S=${s}`);
  assert.equal(replay([]), null);
});

test("card ids match review.card_id", async () => {
  for (const [q, id] of Object.entries(fx.card_ids)) assert.equal(await cardId(q), id, q);
});

test("queue: due most overdue first, new cards capped, leeches flagged; log lines read back", async () => {
  const tsv = "Why do bees visit flowers?\tFor nectar\tPollination 00:01:00\nWhat carries pollen?\tBees and wind\t\nWhy is pollen sticky?\tTo cling to insects\t\n";
  const id0 = await cardId("Why do bees visit flowers?"), id1 = await cardId("What carries pollen?");
  const t0 = Date.parse("2030-03-01T09:00:00+00:00"), day = 86400e3;
  let log = "";
  for (let d = 0; d < LEECH_LAPSES; d++) log += logLine(id0, false, t0 + d * 2 * day);
  log += logLine(id1, true, t0) + "not json\n";
  const { due, fresh, leeches } = await queue([{ folder: "f", cardsTsv: tsv, log }], t0 + 30 * day);
  assert.deepEqual(new Set(due.map((c) => c.id)), new Set([id0, id1]));
  assert.deepEqual(due.map((c) => c.due), [...due.map((c) => c.due)].sort((a, b) => a - b));
  assert.deepEqual(fresh.map((c) => c.q), ["Why is pollen sticky?"]);
  assert.deepEqual(leeches.map((c) => c.id), [id0]);
  assert.equal((await queue([{ folder: "f", cardsTsv: tsv, log: "" }], t0, 0)).fresh.length, 0);
  assert.match(logLine("abc", true, t0), /^\{"card":"abc","ts":"2030-03-01T09:00:00\+00:00","pass":true\}\n$/);
});
