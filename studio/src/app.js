// Lecture Lens Studio UI. All text read from the lectures folder is shown with
// textContent, never as HTML: captured text is untrusted.
import { probe, pick, WEBLLM_MODELS } from "./engines/index.js";
import { supported as fsSupported, pickFolder, savedFolder } from "./io/folder.js";
import { runLectureJob, jobState } from "./core/notes.js";
import { lecturePassages, BM25, askLecture } from "./core/ask.js";
import { liveRound } from "./core/live.js";
import { lecturesByModule, makeRecap } from "./core/recap.js";

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
const PREF_KEY = "studio.engine";

let engine = null, io = null, caps = null, busy = false, liveTimer = null;
const liveState = { lastOutline: null };

function log(msg) {
  const line = `${new Date().toLocaleTimeString()}  ${msg}\n`;
  $("log").textContent = (line + $("log").textContent).slice(0, 20000);
}

function guard(fn) {
  return async (...a) => {
    if (busy) { log("busy: wait for the current job"); return; }
    busy = true;
    try { await fn(...a); } catch (e) { log(`error: ${e.message}`); console.error(e); } finally { busy = false; }
  };
}

async function needEngine() {
  if (!engine) throw new Error("prepare a model first (step 1)");
  return engine;
}
async function needFolder() {
  if (!io) throw new Error("choose the lectures folder first (step 2)");
  return io;
}

// ---------------------------------------------------------------- engine
async function setupEngine() {
  caps = await probe();
  const sel = $("engine-pref");
  sel.replaceChildren(el("option", { value: "auto", textContent: "Auto: Gemini Nano, else WebGPU" }),
    el("option", { value: "nano", textContent: `Gemini Nano (${caps.nano})` }),
    ...WEBLLM_MODELS.map((m) => el("option", { value: `webllm:${m.id}`, textContent: `WebGPU: ${m.label}` })));
  try { sel.value = localStorage.getItem(PREF_KEY) || "auto"; } catch { sel.value = "auto"; }
  $("caps").textContent = `Gemini Nano: ${caps.nano} (images: ${caps.nanoImage}) · WebGPU: ${caps.webgpu}` +
    (caps.nano === "unsupported" ? " · Prompt API needs Chrome 148+ for pages, 138+ for the extension" : "");
}

const initEngine = guard(async () => {
  const pref = $("engine-pref").value;
  try { localStorage.setItem(PREF_KEY, pref); } catch { /* private window */ }
  engine?.destroy?.();
  const e = pick(pref, caps), bar = $("engine-progress");
  bar.hidden = false; bar.value = 0;
  log(`preparing ${e.name}…`);
  await e.init({ onProgress: (p, text) => { bar.value = p; if (text) $("caps").textContent = `${text} ${Math.round(p * 100)}%`; } });
  bar.hidden = true;
  engine = e;
  $("engine-chip").textContent = `engine: ${e.name}${e.canSeeImages ? " · reads images" : ""} · ${e.budget.windowTokens} tokens`;
  $("engine-chip").classList.add("ready");
  log(`${e.name} ready (window ${e.budget.windowTokens} tokens)`);
});

// ---------------------------------------------------------------- folder
async function setupFolder() {
  if (!fsSupported()) { $("folder-name").textContent = "this browser cannot open folders (use Chrome)"; $("folder-pick").disabled = true; return; }
  const { io: saved, permission } = await savedFolder();
  if (!saved) return;
  if (permission === "granted") { io = saved; await folderReady(); }
  else { $("folder-regrant").hidden = false; $("folder-regrant").textContent = `Reconnect ${saved.name}`; $("folder-regrant").onclick = guard(async () => {
    if (await saved.regrant()) { io = saved; $("folder-regrant").hidden = true; await folderReady(); }
  }); }
}

async function folderReady() {
  $("folder-name").textContent = io.name;
  await refreshLectures();
  await refreshLive();
  await refreshModules();
}

async function lectureFolders() {
  return (await io.list("")).filter((e) => e.kind === "directory" && /^\d{8}-\d{4}_/.test(e.name)).map((e) => e.name).reverse();
}

// ---------------------------------------------------------------- after class
async function refreshLectures() {
  const rows = [], askSel = $("ask-lecture");
  askSel.replaceChildren();
  for (const f of await lectureFolders()) {
    const state = await jobState(io, f);
    const run = el("button", { textContent: state === "done" ? "Run again" : "Run", disabled: state === "none" });
    run.onclick = guard(() => runOne(f));
    rows.push(el("tr", {}, el("td", { textContent: f }), el("td", { textContent: state, className: `state-${state}` }), el("td", {}, run)));
    askSel.append(el("option", { value: f, textContent: f }));
  }
  $("lectures").replaceChildren(...rows);
}

async function runOne(folder) {
  const e = await needEngine();
  log(`${folder}: started with ${e.name}`);
  const r = await runLectureJob(e, io, folder, { describe: $("describe").checked, say: (m) => log(`${folder}: ${m}`) });
  log(`${folder}: done in ${r.seconds} s (${r.cards} cards, ${r.slides_described} slides described). Run ./lecture finish ${folder.slice(0, 13)} to update the vault note.`);
  await refreshLectures();
}

const runPending = guard(async () => {
  await needEngine(); await needFolder();
  for (const f of await lectureFolders()) if ((await jobState(io, f)) === "pending") await runOne(f);
  log("no more pending lectures");
});

// ---------------------------------------------------------------- ask
const indexCache = new Map();
async function lectureIndex(folder) {
  if (indexCache.has(folder)) return indexCache.get(folder);
  const tx = (await io.exists(`${folder}/transcript_merged.md`)) ? `${folder}/transcript_merged.md` : `${folder}/transcript.md`;
  const transcript = (await io.exists(tx)) ? await io.readText(tx) : "";
  const slides = (await io.exists(`${folder}/slides.json`)) ? JSON.parse(await io.readText(`${folder}/slides.json`)) : [];
  const idx = new BM25(lecturePassages(transcript, slides));
  indexCache.set(folder, idx);
  return idx;
}

const ask = guard(async () => {
  const e = await needEngine(); await needFolder();
  const q = $("ask-q").value.trim(), folder = $("ask-lecture").value;
  if (!q || !folder) return;
  $("ask-answer").textContent = "thinking…"; $("ask-sources").replaceChildren(); $("ask-warnings").replaceChildren();
  const { answer, sources, warnings = [], coverage = null } = await askLecture(e, await lectureIndex(folder), q);
  $("ask-answer").textContent = answer;
  $("ask-warnings").replaceChildren(...warnings.map((w) => el("li", { textContent: `Citation check: ${w}` })));
  $("ask-sources").replaceChildren(...sources.map((s) => el("li", { textContent: `${s.label}: ${s.text.slice(0, 240)}` })));
  await io.appendText(`${folder}/studio_ask.jsonl`, JSON.stringify({ t: new Date().toISOString(), q, answer, engine: e.name,
    sources: sources.map((s) => s.label), citation_warnings: warnings, citation_coverage: coverage }) + "\n");
});

// ---------------------------------------------------------------- live
async function refreshLive() {
  const days = (await io.list("live")).filter((e) => e.kind === "directory" && /^\d{8}$/.test(e.name)).map((e) => e.name).reverse();
  $("live-day").replaceChildren(...days.map((d) => el("option", { value: d, textContent: d })));
  await loadLiveTitle();
}
async function loadLiveTitle() {
  const day = $("live-day").value;
  if (!day) return;
  try { $("live-title").value = JSON.parse(await io.readText(`live/${day}/studio_live_job.json`)).title || ""; } catch { /* none yet */ }
}

function startLive() {
  if (liveTimer) return;
  const day = $("live-day").value;
  if (!engine || !io || !day) { log("live: prepare a model, choose the folder and pick a day first"); return; }
  liveState.lastOutline = null;
  const tick = async () => {
    if (busy) return;   // an after-class job is running; try again next minute
    busy = true;
    try {
      let idea = "";
      try { idea = JSON.parse(await io.readText(`live/${day}/studio_live_job.json`)).idea || ""; } catch { /* optional */ }
      await liveRound(engine, io, `live/${day}`, liveState, { title: $("live-title").value || "Live class", idea, say: (m) => log(`live: ${m}`) });
      $("live-status").textContent = `following ${day} · next window from ${new Date(liveState.lastOutline).toLocaleTimeString()}`;
    } catch (e) { log(`live error: ${e.message}`); } finally { busy = false; }
  };
  liveTimer = setInterval(tick, 60_000); tick();
  $("live-start").disabled = true; $("live-stop").disabled = false;
  log(`live: following ${day}`);
}
function stopLive() {
  clearInterval(liveTimer); liveTimer = null;
  $("live-start").disabled = false; $("live-stop").disabled = true; $("live-status").textContent = "stopped";
}

// ---------------------------------------------------------------- recap
let modules = {};
async function refreshModules() {
  modules = await lecturesByModule(io);
  $("recap-module").replaceChildren(...Object.keys(modules).sort().map((m) => el("option", { value: m, textContent: `${m || "untagged"} (${modules[m].length})` })));
}
const recap = guard(async () => {
  const e = await needEngine(); await needFolder();
  const m = $("recap-module").value;
  const path = await makeRecap(e, io, m, modules[m] || [], (x) => log(`recap: ${x}`));
  $("recap-out").textContent = await io.readText(path);
  log(`recap written to ${path}`);
});

// ---------------------------------------------------------------- wiring
for (const b of document.querySelectorAll(".tabs button")) {
  b.onclick = () => {
    for (const x of document.querySelectorAll(".tabs button")) x.setAttribute("aria-selected", String(x === b));
    for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== `tab-${b.dataset.tab}`;
  };
}
$("engine-init").onclick = initEngine;
$("folder-pick").onclick = guard(async () => { io = await pickFolder(); $("folder-regrant").hidden = true; await folderReady(); });
$("refresh").onclick = guard(async () => { await needFolder(); await refreshLectures(); });
$("run-pending").onclick = runPending;
$("ask-go").onclick = ask;
$("live-day").onchange = () => io && loadLiveTitle();
$("live-start").onclick = startLive;
$("live-stop").onclick = stopLive;
$("recap-go").onclick = recap;

await setupEngine();
await setupFolder();
