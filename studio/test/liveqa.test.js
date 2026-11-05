import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { MemIO, FakeEngine } from "./helpers.js";
import { threadKind, needsFollowup, needsProspective, parseProspective, cleanAnswer, relevantSources, qaTime, qaRound, doneFromFiles } from "../src/core/liveqa.js";
import { liveRound, pyIso } from "../src/core/live.js";

const fx = JSON.parse(readFileSync(new URL("./fixtures/liveqa.json", import.meta.url)));

test("thread rules match live_qa.py on the shared fixture", () => {
  assert.deepEqual(fx.threads.map(threadKind), fx.kind);
  assert.deepEqual(fx.threads.map((t) => needsFollowup(t, fx.done)), fx.needs_followup);
  assert.deepEqual(fx.threads.map((t) => needsProspective(t, new Set())), fx.needs_prospective);
  for (const p of fx.prospective) assert.deepEqual(parseProspective(p.raw, new Set(["S1", "S2"])), p.parsed);
  for (const c of fx.clean) assert.equal(cleanAnswer(c.text), c.out);
  for (const r of fx.relevant) assert.deepEqual(relevantSources(r.ids, r.text, fx.bank), r.out);
});

test("panel times map onto the same day", () => {
  const now = new Date(2030, 0, 1, 14, 0).getTime();
  assert.equal(new Date(qaTime("10:05 AM", now)).getHours(), 10);
  assert.equal(new Date(qaTime("2:30PM", now)).getHours(), 14);
  assert.equal(qaTime("soon", now), now);
});

function qaFolder() {
  const D = "live/20300101", now = new Date(2030, 0, 1, 10, 20).getTime();
  const segs = Array.from({ length: 30 }, (_, i) => JSON.stringify({ t: pyIso(now - (30 - i) * 30_000),
    text: "drip irrigation saves water in vegetable beds and mulch depth matters for tomato plants" }));
  return { D, now, io: new MemIO({
    [`${D}/whisper_segments.jsonl`]: segs.join("\n") + "\n",
    [`${D}/qa_threads.json`]: JSON.stringify(fx.threads),
    [`${D}/evidence.json`]: JSON.stringify(Object.entries(fx.bank).map(([id, e]) => ({ id, ...e }))),
  }) };
}

const rules = [
  [/NOT YET ANSWERED/, '{"answer": "Five centimetres of mulch [lecture] (S2).", "confidence": "medium", "listen_for": "depth"}'],
  [/LOGISTICS/, JSON.stringify([{ type: "practical", q: "When will tonight's upload of the recording appear in the portal?", answer: "Yes, by tonight." }])],
  [/Write 2 follow-up/, JSON.stringify([{ type: "clarify", q: "How much mulch keeps drip irrigation effective in hot weather?", answer: "Usually yes (S1)." },
    { type: "challenge", q: "Which tomato variety sells best at markets?", answer: "not covered yet" }])],
];

test("qaRound drafts one prospective answer and grounded follow-ups, in the Python file formats", async () => {
  const { D, now, io } = qaFolder(), eng = new FakeEngine(rules), state = { qaDone: { t4: 1 } };
  const r = await qaRound(eng, io, D, state, { title: "Garden", now });
  assert.deepEqual(r, { followups: 1, prospective: 1 });
  const pro = io.files.get(`${D}/qa_prospective.jsonl`).trim().split("\n").map(JSON.parse);
  assert.equal(pro.length, 1); assert.equal(pro[0].thread, "t3"); assert.equal(pro[0].answer, "Five centimetres of mulch (S2).");
  assert.deepEqual(pro[0].sources, ["S2"]); assert.equal(pro[0].prompt, "prospective-v2");
  const fus = io.files.get(`${D}/qa_followups.jsonl`).trim().split("\n").map(JSON.parse);
  assert.equal(fus.length, 1, "only the admin thread t2 needed follow-ups (t4 was done); its one follow-up is grounded");
  assert.equal(fus[0].thread, "t2");
  assert.deepEqual(state.qaDone, { t4: 1, t2: 1 });
  // a second round has nothing new to do
  assert.deepEqual(await qaRound(eng, io, D, state, { title: "Garden", now }), { followups: 0, prospective: 0 });
});

test("follow-ups are redone only when a thread gets a new reply; state rebuilds from files", async () => {
  const { D, now, io } = qaFolder(), eng = new FakeEngine(rules), state = { qaDone: {} };
  await qaRound(eng, io, D, state, { title: "Garden", now });
  const fus = io.files.get(`${D}/qa_followups.jsonl`).trim().split("\n").map(JSON.parse);
  assert.ok(fus.some((f) => f.thread === "t4"), "t4 is followed up when nothing was done before");
  assert.ok(!fus.some((f) => /tomato variety/.test(f.q)), "the ungrounded follow-up is dropped");
  assert.deepEqual(doneFromFiles(fx.threads, fus), { t2: 1, t4: 1 });
});

test("liveRound runs the Q&A round and can skip it", async () => {
  const { D, now, io } = qaFolder(), eng = new FakeEngine(rules);
  const r = await liveRound(eng, io, D, { lastOutline: now }, { title: "Garden", now });
  assert.deepEqual(r.qa, { followups: 2, prospective: 1 }, "t2 and t4 each get one grounded follow-up");
  const { D: D2, io: io2 } = qaFolder();
  const r2 = await liveRound(eng, io2, D2, { lastOutline: now }, { title: "Garden", now, qa: false });
  assert.equal(r2.qa, undefined); assert.ok(!io2.files.has(`${D2}/qa_prospective.jsonl`));
});
