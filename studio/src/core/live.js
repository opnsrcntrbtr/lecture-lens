// Live class: the 10-minute outline and questions-to-ask, written to the same files the
// Python follower writes (lectures/live/<yyyymmdd>/outline.jsonl, questions.jsonl), so the
// app's Live tab shows them unchanged. In browser mode the Python follower keeps doing the
// deterministic work (whisper, slide log, Q&A threads) and leaves these two to the Studio,
// which also answers the app's Sync now requests (sync_request.json -> sync_done.json).
import { tokens, jaccard, grounded, cleanReply, salvageJsonObjects } from "./text.js";
import { SYS_TUTOR, VERSIONS, outlinePrompt, questionsPrompt, questionsSchema, QUESTION_TYPES } from "./prompts.js";

export const WINDOW_MS = 10 * 60 * 1000;

/** Milliseconds from Python's isoformat (microseconds, +00:00) or JS ISO strings. */
export const ms = (iso) => Date.parse(String(iso).replace(/(\.\d{3})\d+/, "$1"));

/** Python-style UTC timestamp, so string comparisons in the Python code still order correctly. */
export const pyIso = (t) => new Date(t).toISOString().replace(/\.\d{3}Z$/, "+00:00");

export const jsonl = (text) => String(text ?? "").split("\n").filter((l) => l.trim()).flatMap((l) => { try { return [JSON.parse(l)]; } catch { return []; } });

/** Outline a window only once whisper has transcribed past its end, or once it is too late
 *  to wait (live_class.window_ready). */
export const windowReady = (segs, toMs, nowMs, maxWaitMs = 1500 * 1000) =>
  (segs.length > 0 && ms(segs[segs.length - 1].t) >= toMs) || nowMs - toMs >= maxWaitMs;

/** Questions from the model's JSON: well-formed items only, sources limited to the verified
 *  evidence bank, near-repeats of earlier or attendee questions dropped (live_class.parse_questions). */
export function parseQuestions(raw, evidenceIds, asked = []) {
  const text = cleanReply(raw);
  let items;
  try { const v = JSON.parse(text); items = Array.isArray(v) ? v : []; } catch { items = salvageJsonObjects(text); }
  const seen = asked.map(tokens), out = [];
  for (const it of items) {
    if (!it || typeof it !== "object" || !String(it.q ?? "").trim() || !String(it.answer ?? "").trim()) continue;
    const tk = tokens(it.q);
    if (seen.some((s) => jaccard(tk, s) >= 0.5)) continue;
    const answer = String(it.answer).replace(/\s*\[\d+(?:\s*,\s*\d+)*\]/g, "").trim();
    const named = new Set([...answer.matchAll(/\bS\d+\b/g)].map((m) => m[0]));
    const sources = [...named].filter((s) => evidenceIds.has(s)).sort((a, b) => +a.slice(1) - +b.slice(1));
    out.push({ type: QUESTION_TYPES.includes(it.type) ? it.type : "clarify", q: String(it.q).trim(), answer, sources });
    seen.push(tk);
  }
  return out;
}

const SYNC_KINDS = ["slides", "outline", "questions"];

/** Consume the app's on-demand sync request, if any. */
export async function takeSync(io, dir) {
  const f = `${dir}/sync_request.json`;
  if (!(await io.exists(f))) return new Set();
  let what = [];
  try { what = JSON.parse((await io.readText(f)) || "{}").what ?? []; } catch { what = []; }
  await io.remove(f);
  const set = new Set(typeof what === "string" ? [what] : what);
  return set.has("all") ? new Set(SYNC_KINDS) : new Set([...set].filter((k) => SYNC_KINDS.includes(k)));
}

const read = async (io, f) => ((await io.exists(f)) ? io.readText(f) : "");
const append = async (io, f, rows) => { if (rows.length) await io.appendText(f, rows.map((r) => JSON.stringify(r) + "\n").join("")); };

/** One round of the live loop. `state.lastOutline` is the start of the next window (ms). */
export async function liveRound(engine, io, dir, state, { title, now = Date.now(), idea = "", say = () => {} }) {
  const segs = jsonl(await read(io, `${dir}/whisper_segments.jsonl`));
  const slides = jsonl(await read(io, `${dir}/slides.jsonl`));
  const sync = await takeSync(io, dir);
  if (state.lastOutline == null) state.lastOutline = segs.length ? ms(segs[0].t) : now;
  const due = now - state.lastOutline >= WINDOW_MS && windowReady(segs, state.lastOutline + WINDOW_MS, now);
  const forced = (sync.has("outline") || sync.has("questions")) && !due;
  const result = {};
  if (due || forced) {
    const frm = state.lastOutline;
    const to = forced ? Math.min(segs.length ? ms(segs[segs.length - 1].t) : now, now) : frm + WINDOW_MS;
    const win = segs.filter((s) => ms(s.t) >= frm && ms(s.t) < to);
    const cap = Math.floor(engine.budget.sourceChars * 0.5);
    const text = win.map((s) => s.text).join(" ").slice(-cap);
    const titles = slides.filter((s) => ms(s.t) >= frm && ms(s.t) < to && (s.kind ?? "slide") === "slide").map((s) => s.title);
    let wrote = false;
    if (text.split(/\s+/).length > 40) {
      try {
        const bullets = cleanReply(await engine.prompt(SYS_TUTOR, outlinePrompt(titles, text), { maxTokens: 260, temperature: 0.2 }));
        await append(io, `${dir}/outline.jsonl`, [{ from: pyIso(frm), to: pyIso(to), bullets, engine: engine.name, prompt_version: VERSIONS.outline,
          ...(forced && !sync.has("questions") ? { partial: true } : {}) }]);
        result.outline_window = [pyIso(frm), pyIso(to)];
        say(`outline ${new Date(frm).toLocaleTimeString()}`);
      } catch (e) { say(`outline error: ${e.message}`); }
      if (!forced || sync.has("questions")) {
        try {
          const n = await makeQuestions(engine, io, dir, { title, frm, to, text, titles, idea });
          result.questions = n;
          wrote = n > 0 || !forced;   // an empty sync doesn't use up the window
          say(`questions +${n}`);
        } catch (e) { say(`questions error: ${e.message}`); }
      }
    }
    if (forced) {
      result.window = [pyIso(frm), pyIso(to)];
      if (wrote) state.lastOutline = to; else result.note = "not enough new transcript yet";
    } else {
      state.lastOutline = now - to < 1800 * 1000 ? to : now - WINDOW_MS;
    }
  }
  if (sync.size) {
    const outl = jsonl(await read(io, `${dir}/outline.jsonl`));
    await io.writeText(`${dir}/sync_done.json`, JSON.stringify({ what: [...sync].sort(), at: pyIso(Date.now()), slides: slides.length,
      outline: outl.length, by: "studio", ...result }));
  }
  await io.writeText(`${dir}/studio_live.json`, JSON.stringify({ at: pyIso(Date.now()), engine: engine.name, last_outline: pyIso(state.lastOutline) }));
  return result;
}

async function makeQuestions(engine, io, dir, { title, frm, to, text, titles, idea }) {
  const bank = (await io.exists(`${dir}/evidence.json`)) ? JSON.parse(await io.readText(`${dir}/evidence.json`)) : [];
  const bankTxt = bank.map((e) => `${e.id}: ${e.title} (${e.date ?? ""}): ${e.fact}`).join("\n");
  const outline = jsonl(await read(io, `${dir}/outline.jsonl`));
  const earlier = outline.slice(-4).map((o) => o.bullets.replace(/\n/g, " ")).join(" | ").slice(0, 2000);
  const prior = jsonl(await read(io, `${dir}/questions.jsonl`)).map((q) => q.q);
  const threads = (await io.exists(`${dir}/qa_threads.json`)) ? JSON.parse(await io.readText(`${dir}/qa_threads.json`)) : [];
  const side = threads.filter((t) => t.q).map((t) => t.q).slice(-12).join(" / ");
  const raw = await engine.prompt(SYS_TUTOR, questionsPrompt({ title, titles, earlier, text, side, bank: bankTxt, idea }),
    { schema: questionsSchema, maxTokens: 900, temperature: 0.3 });
  const ground = [text, JSON.stringify(titles), earlier].join(" ");
  const qs = parseQuestions(raw, new Set(bank.map((e) => e.id)), [...prior, ...threads.map((t) => t.q).filter(Boolean)])
    .filter((q) => grounded(q.q, ground));
  await append(io, `${dir}/questions.jsonl`, qs.map((q) => ({ t: pyIso(to), ...q, engine: engine.name })));
  return qs.length;
}
