import { test } from "node:test";
import assert from "node:assert/strict";
import { MemIO, FakeEngine } from "./helpers.js";
import { wer, words } from "../src/core/wer.js";
import { chunkLines, rms, transcribeLecture, transcriptMarkdown } from "../src/core/transcribe.js";
import { runLectureJob, digestPart, attachTimecodes } from "../src/core/notes.js";

test("word error rate", () => {
  assert.deepEqual(words("Drip lines, mulch!"), ["drip", "lines", "mulch"]);
  assert.equal(wer("water the beans early", "water the beans early"), 0);
  assert.equal(wer("water the beans early", "water beans early today"), 0.5);   // one deletion, one insertion
  assert.equal(wer("", ""), 0); assert.equal(wer("", "noise"), 1);
});

test("chunk lines are offset into the lecture and drop silence hallucinations", () => {
  const r = { chunks: [{ timestamp: [0, 4], text: " Mulch keeps soil moist." }, { timestamp: [4, 6], text: "Thank you." }, { timestamp: [6.5, 9], text: "Drip saves water." }] };
  assert.deepEqual(chunkLines(r, 3600), ["[01:00:00] Mulch keeps soil moist.", "[01:00:06] Drip saves water."]);
  assert.deepEqual(chunkLines({ text: "Raised beds." }, 30), ["[00:00:30] Raised beds."]);
  assert.equal(rms(new Float32Array([0, 0, 0])), 0);
  assert.ok(Math.abs(rms(new Float32Array([0.5, -0.5])) - 0.5) < 1e-9);
});

const F = "20300101-1000_Watering";
function folder() {
  return new MemIO({
    [`${F}/studio_job.json`]: JSON.stringify({ title: "Watering", minutes: 2, cards: 0, transcript: ["[00:00:01] old capture line"], transcript_source: "capture" }),
    [`${F}/audio.json`]: JSON.stringify([{ file: "a.mp4", offset_s: 0 }, { file: "b.mp4", offset_s: 30 }, { file: "c.mp4", offset_s: 60 }]),
    [`${F}/audio/a.mp4`]: "loud", [`${F}/audio/b.mp4`]: "quiet", [`${F}/audio/c.mp4`]: "loud",
  });
}
const decode = async (blob) => (blob.path.endsWith("b.mp4") ? new Float32Array(16000) : new Float32Array(16000).fill(0.1));
const asr = { name: "fake-whisper", calls: 0, async transcribe() { this.calls++; return { chunks: [{ timestamp: [1, 3], text: "Drip irrigation saves water in raised beds." }] }; } };

test("transcribeLecture skips silent chunks and keeps lecture time", async () => {
  const io = folder(), a = { ...asr, calls: 0 };
  const lines = await transcribeLecture(a, io, F, decode);
  assert.deepEqual(lines, ["[00:00:01] Drip irrigation saves water in raised beds.", "[00:01:01] Drip irrigation saves water in raised beds."]);
  assert.equal(a.calls, 2);
  assert.match(transcriptMarkdown("Watering", lines, "x"), /^# Transcript \(browser\) — Watering/);
});

test("the lecture job uses the browser transcript, but never over a merged reference transcript", async () => {
  const io = folder(), eng = new FakeEngine([[/study notes|second half/, "## TL;DR\n- water"]]);
  const r = await runLectureJob(eng, io, F, { describe: false, asr: { ...asr }, decode });
  assert.deepEqual(r.transcript, { engine: "fake-whisper", lines: 2 });
  assert.ok(io.files.has(`${F}/transcript_studio.md`));
  assert.ok(eng.calls.some((c) => c.user.includes("[00:01:01] Drip irrigation")) && !eng.calls.some((c) => c.user.includes("old capture line")));
  const io2 = folder();
  const job = JSON.parse(io2.files.get(`${F}/studio_job.json`)); job.transcript_source = "merged";
  io2.files.set(`${F}/studio_job.json`, JSON.stringify(job));
  const r2 = await runLectureJob(eng, io2, F, { describe: false, asr: { ...asr }, decode });
  assert.deepEqual(r2.transcript, { source: "merged" }); assert.ok(!io2.files.has(`${F}/transcript_studio.md`));
});

test("fast digests go through the Summarizer when the engine offers it", async () => {
  const eng = new FakeEngine([[/./, "prompt digest"]]);
  eng.summarize = async (text, ctx) => `- summary of ${text.length} chars (${ctx})`;
  assert.equal(await digestPart(eng, "G", 1, 2, "[00:00:01] a line"), "prompt digest");
  eng.fastDigests = true;
  assert.equal(await digestPart(eng, "G", 1, 2, "[00:00:01] a line"), "- summary of 17 chars (Lecture: G (part 1 of 2))");

});

test("digest bullets get back the timecode of the line they came from", () => {
  const part = "[00:01:00] Mulch keeps garden soil moist through summer.\n[00:02:30] Drip irrigation saves water in raised beds.";
  const d = "Key points:\n- Drip irrigation saves water in beds\n- [00:01:00] Mulch keeps soil moist\n- Prune in winter";
  assert.equal(attachTimecodes(d, part), "Key points:\n- [00:02:30] Drip irrigation saves water in beds\n- [00:01:00] Mulch keeps soil moist\n- Prune in winter");
  assert.equal(attachTimecodes("- anything", "no timecodes here"), "- anything");
});
