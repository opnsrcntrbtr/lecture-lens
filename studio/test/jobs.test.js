import { test } from "node:test";
import assert from "node:assert/strict";
import { MemIO, FakeEngine } from "./helpers.js";
import { runLectureJob, jobState, makeNotes, joinNoteHalves } from "../src/core/notes.js";
import { lecturePassages, BM25, askLecture } from "../src/core/ask.js";
import { liveRound, parseQuestions, windowReady, pyIso, ms, takeSync } from "../src/core/live.js";
import { lecturesByModule, makeRecap } from "../src/core/recap.js";

const F = "20300101-1000_Watering-the-garden";
const lines = Array.from({ length: 200 }, (_, i) => `[0${Math.floor(i / 60)}:${String(i % 60).padStart(2, "0")}:00] Drip irrigation and mulch keep raised beds moist, point ${i}.`);

const rules = [
  [/^Lecture: .*\(part \d+ of \d+\)/, "- [00:01:00] drip lines save water"],
  [/TWO DIGESTS/, "- merged digest"],
  [/first half of the study notes/, "## TL;DR\n- water early\n## Key concepts\n- **Mulch** holds moisture\n## Frameworks & models\n- none"],
  [/second half/, "## TL;DR\n- repeated\n## Worked examples and numbers\n- 40% less water [00:02:00]\n## Open questions\n- none"],
  [/flashcards/, (_, o) => JSON.stringify([{ q: `Why mulch? ${o.schema.maxItems}`, a: "Holds moisture.", at: "00:01:00", kind: "why" },
    { q: "Compare drip and spray", a: "Drip wastes less.", at: "", kind: "compare" }])],
];

function lectureFolder(extra = {}) {
  return new MemIO({
    [`${F}/studio_job.json`]: JSON.stringify({ version: 1, title: "Watering the garden", when: "Tue 01 Jan 2030 10:00", minutes: 60,
      created_at: "2020-01-01T11:00:00+00:00", cards: 6, transcript: lines, deck_src: "", qa_src: "" }),
    [`${F}/slides.json`]: JSON.stringify([{ n: 1, at: "00:00:10", file: "01_000010.jpg", screen_text: "Raised beds", description: null },
      { n: 2, at: "00:05:00", file: "02_000500.jpg", screen_text: "Presenter", description: null }]),
    [`${F}/slides/01_000010.jpg`]: "jpg", [`${F}/slides/02_000500.jpg`]: "jpg",
    [`${F}/manifest.json`]: JSON.stringify({ title: "Watering the garden", module: "Module 1", start: "2030-01-01T10:00:00+05:30" }),
    ...extra,
  });
}

test("notes halves are merged without repeated sections, in the usual order", () => {
  const md = joinNoteHalves(rules[2][1], rules[3][1]);
  assert.deepEqual(md.match(/^## .+$/gm), ["## TL;DR", "## Key concepts", "## Frameworks & models", "## Worked examples and numbers", "## Open questions"]);
  assert.ok(!md.includes("repeated"));
  assert.equal(joinNoteHalves("plain text", ""), "plain text");
});

test("a long lecture is digested part by part and reduced to fit a small window", async () => {
  const eng = new FakeEngine(rules, { window: 2048 });
  const r = await makeNotes(eng, { title: "Garden", lines, slideSrc: "SLIDES:\n[slide 1 @ 00:00:10] Beds", cards: 6 });
  assert.ok(r.digests.length > 1);
  assert.match(r.notes, /## Key concepts/);
  for (const c of eng.calls) assert.ok(c.user.length < eng.budget.sourceChars + 1200, "every prompt fits the window budget");
  assert.ok(eng.calls.some((c) => /flashcards/.test(c.user) && c.opts.schema?.type === "array"), "cards use a JSON schema");
  assert.equal(r.cards.length, 2, "duplicate cards across parts are dropped");
});

test("the lecture job writes the same files as the oMLX path and a result", async () => {
  const io = lectureFolder(), eng = new FakeEngine(rules);
  assert.equal(await jobState(io, F), "pending");
  const r = await runLectureJob(eng, io, F);
  assert.equal(r.status, "done");
  assert.equal(r.slides_described, 1);
  const slides = JSON.parse(io.files.get(`${F}/slides.json`));
  assert.match(slides[0].description, /Heading/); assert.equal(slides[1].skipped, true);
  assert.match(io.files.get(`${F}/slides.md`), /## 1\. at 00:00:10/);
  assert.match(io.files.get(`${F}/notes.md`), /^# Watering the garden\n\n_Tue 01 Jan 2030 10:00 · 60 min · notes by fake in Chrome/);
  assert.match(io.files.get(`${F}/cards.tsv`), /\tWatering the garden 00:01:00\n/);
  assert.equal(await jobState(io, F), "done");
  assert.ok(eng.calls.some((c) => /\[slide 1 @ 00:00:10\] ## Heading/.test(c.user ?? "")), "notes use the new slide descriptions");
});

test("ask: BM25 finds the right passage and the answer cites numbered sources", async () => {
  const tx = "# Transcript\n\n[00:00:10] Raised beds drain well.\n[00:01:00] Speaker: Drip irrigation saves water.\n[00:02:00] Prune in winter.\n[00:03:00] Tomatoes need sun.";
  const idx = new BM25(lecturePassages(tx, [{ n: 1, at: "00:00:10", screen_text: "Compost ratios: 3 brown to 1 green" }], 1));
  assert.equal(idx.top("how much water does drip irrigation save", 5000)[0].label, "00:01:00");
  assert.equal(idx.top("compost ratio", 5000)[0].label, "slide 1 @ 00:00:10");
  const eng = new FakeEngine([[/Question:/, "Drip saves water [1]."]]);
  const r = await askLecture(eng, idx, "drip irrigation water?");
  assert.equal(r.answer, "Drip saves water [1].");
  assert.match(eng.calls[0].user, /^Sources:\n\[1\] \(00:01:00\)/);
  assert.match((await askLecture(eng, idx, "xylophone")).answer, /Nothing in this lecture/);
});

test("questions: evidence ids only, no repeats, bracket numbers removed", () => {
  const raw = JSON.stringify([
    { type: "challenge", q: "Does drip irrigation beat sprinklers on water use?", answer: "The lecture said yes [3]; S2 agrees, S9 too.", sources: ["S9"] },
    { type: "odd", q: "Does drip irrigation beat sprinklers on water usage?", answer: "dup" },
    { type: "clarify", q: "Which mulch depth was suggested?", answer: "Five centimetres.", sources: [] },
  ]);
  const qs = parseQuestions(raw, new Set(["S1", "S2"]), ["Which mulch depth was suggested today?"]);
  assert.equal(qs.length, 1);
  assert.deepEqual(qs[0].sources, ["S2"]);
  assert.equal(qs[0].answer, "The lecture said yes; S2 agrees, S9 too.");
});

test("window readiness and Python-compatible timestamps", () => {
  const t = Date.parse("2030-01-01T10:10:00Z");
  assert.ok(!windowReady([{ t: "2030-01-01T10:05:00.123456+00:00" }], t, t + 60_000));
  assert.ok(windowReady([{ t: "2030-01-01T10:10:01+00:00" }], t, t + 60_000));
  assert.ok(windowReady([], t, t + 1500_000));
  assert.equal(pyIso(t), "2030-01-01T10:10:00+00:00");
  assert.equal(ms("2030-01-01T10:10:00.999999+00:00"), t + 999);
});

function liveFolder() {
  const D = "live/20300101", start = Date.parse("2030-01-01T10:00:00Z");
  const segs = Array.from({ length: 24 }, (_, i) => JSON.stringify({ t: pyIso(start + i * 30_000), text: "drip irrigation saves water in raised beds and mulch keeps soil moist" }));
  return { D, start, io: new MemIO({
    [`${D}/whisper_segments.jsonl`]: segs.join("\n") + "\n",
    [`${D}/slides.jsonl`]: JSON.stringify({ t: pyIso(start + 60_000), title: "Drip irrigation", kind: "slide" }) + "\n",
    [`${D}/evidence.json`]: JSON.stringify([{ id: "S1", title: "Water study", date: "2029", fact: "Drip uses less water." }]),
  }) };
}

const liveRules = [
  [/exactly 3 short bullets/, "- drip irrigation\n- mulch\n- raised beds"],
  [/Write 3 questions/, JSON.stringify([{ type: "clarify", q: "How much water does drip irrigation save in raised beds?", answer: "The lecture said a lot (S1).", sources: ["S1"] },
    { type: "practical", q: "Which tomato variety resists blight best?", answer: "Not covered.", sources: [] }])],
];

test("live: one outline and grounded questions per finished 10-minute window", async () => {
  const { D, start, io } = liveFolder(), eng = new FakeEngine(liveRules), state = { lastOutline: null };
  await liveRound(eng, io, D, state, { title: "Garden", now: start + 5 * 60_000 });
  assert.ok(!io.files.has(`${D}/outline.jsonl`), "nothing before the window ends");
  await liveRound(eng, io, D, state, { title: "Garden", now: start + 11 * 60_000 });
  const outl = io.files.get(`${D}/outline.jsonl`).trim().split("\n").map(JSON.parse);
  assert.equal(outl.length, 1);
  assert.equal(outl[0].from, "2030-01-01T10:00:00+00:00");
  const qs = io.files.get(`${D}/questions.jsonl`).trim().split("\n").map(JSON.parse);
  assert.equal(qs.length, 1, "the ungrounded question is dropped");
  assert.deepEqual(qs[0].sources, ["S1"]);
  assert.equal(state.lastOutline, start + 10 * 60_000);
});

test("live: Sync now consumes the request and answers it", async () => {
  const { D, start, io } = liveFolder(), eng = new FakeEngine(liveRules), state = { lastOutline: null };
  io.files.set(`${D}/sync_request.json`, JSON.stringify({ what: ["outline"] }));
  await liveRound(eng, io, D, state, { title: "Garden", now: start + 13 * 60_000 - 1 });
  assert.ok(!io.files.has(`${D}/sync_request.json`));
  const done = JSON.parse(io.files.get(`${D}/sync_done.json`));
  assert.deepEqual(done.what, ["outline"]);
  assert.equal(done.by, "studio");
  const outl = io.files.get(`${D}/outline.jsonl`).trim().split("\n").map(JSON.parse);
  assert.equal(outl.length, 1);
  io.files.set(`${D}/sync_request.json`, JSON.stringify({ what: "all" }));
  assert.deepEqual([...(await takeSync(io, D))].sort(), ["outline", "questions", "slides"]);
});

test("recap groups lectures by module and writes recaps/<module>.md", async () => {
  const io = lectureFolder({ [`${F}/notes.md`]: "# W\n\n## TL;DR\n- water early\n## Action items for the learner\n- skip me" });
  const groups = await lecturesByModule(io);
  assert.deepEqual(Object.keys(groups), ["Module 1"]);
  const eng = new FakeEngine([[/MODULE: Module 1/, "## The module in 5 bullets\n- water"]]);
  const path = await makeRecap(eng, io, "Module 1", groups["Module 1"]);
  assert.equal(path, "recaps/Module-1.md");
  assert.match(io.files.get(path), /^# Module recap — Module 1/);
  assert.ok(!eng.calls[0].user.includes("skip me"));
});

test("S1: notes and cards drop citations to slides that were never given, and count them", async () => {
  const cite = [
    [/first half of the study notes/, "## TL;DR\n- water early [slide 1]\n## Key concepts\n- **Mulch** holds moisture [slide 4]"],
    [/second half/, "## Worked examples and numbers\n- 40% less water [00:02:00] [deck 3]"],
    [/flashcards/, JSON.stringify([{ q: "Why mulch?", a: "It holds moisture [slide 9].", at: "", kind: "why" }])],
    ...rules,
  ];
  const io = lectureFolder(), eng = new FakeEngine(cite);
  const r = await runLectureJob(eng, io, F);
  const notes = io.files.get(`${F}/notes.md`);
  assert.match(notes, /water early \[slide 1\]/);                     // slide 1 exists: kept
  assert.doesNotMatch(notes, /\[slide 4\]|\[deck 3\]/);                // never given: removed
  assert.match(notes, /40% less water \[00:02:00\]/);                  // timecodes untouched
  assert.doesNotMatch(io.files.get(`${F}/cards.tsv`), /slide 9/);
  assert.equal(r.citations_removed, 3);
  assert.equal(JSON.parse(io.files.get(`${F}/studio_result.json`)).prompt_versions.citations, "cite-v1");
});

test("S1: an Ask answer carries its citation check", async () => {
  const io = lectureFolder();
  const idx = new BM25(lecturePassages(lines.join("\n")));
  const eng = new FakeEngine([[/Question:/, "Drip irrigation keeps raised beds moist through the season [99]."]]);
  const r = await askLecture(eng, idx, "how does drip irrigation help raised beds?");
  assert.ok(r.sources.length >= 1);
  assert.deepEqual(r.warnings, [`cites source(s) 99 but only ${r.sources.length} were given`]);
});

test("S4: Studio notes open with recall questions; the answers follow the notes", async () => {
  const io = lectureFolder(), eng = new FakeEngine(rules);
  await runLectureJob(eng, io, F);
  const md = io.files.get(`${F}/notes.md`);
  assert.match(md, /_\n\n## Before you read\n/);
  const [head, , tail] = md.split("\n\n---\n\n");
  assert.doesNotMatch(head, /Holds moisture/);                 // no answer before the notes
  assert.match(tail, /^## Answers to the opening questions/);
});
