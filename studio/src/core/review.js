// Spaced review in the browser: a port of review.py (FSRS-5; Ye, Su & Cao 2022), reading the
// lecture's cards.tsv and appending to the same reviews.jsonl as `./lecture review`, so the
// CLI and the Studio share one schedule. No model. studio/test/fixtures/review.json is
// generated from review.replay; the node tests require the same numbers.
import { pyIso, jsonl } from "./live.js";

export const W = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
  1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621];
const DECAY = -0.5, FACTOR = 19 / 81, AGAIN = 1, HARD = 2, GOOD = 3, EASY = 4;
export const RETENTION = 0.9, MAX_INTERVAL_DAYS = 365, LEECH_LAPSES = 3;
const DAY = 86400 * 1000;

export const retrievability = (days, s) => (1 + FACTOR * Math.max(0, days) / s) ** DECAY;
// Python's round() rounds halves to even; Math.round rounds them up. Match Python.
const pyRound = (x) => { const f = Math.floor(x), d = x - f; return d > 0.5 || (d === 0.5 && f % 2 === 1) ? f + 1 : f; };
export const intervalDays = (s, r = RETENTION) =>
  Math.trunc(Math.min(MAX_INTERVAL_DAYS, Math.max(1, pyRound(s / FACTOR * (r ** (1 / DECAY) - 1)))));
const clampD = (d) => Math.min(10, Math.max(1, d));
const initS = (g) => W[g - 1];
const initD = (g) => clampD(W[4] - Math.exp(W[5] * (g - 1)) + 1);
const nextD = (d, g) => clampD(W[7] * initD(EASY) + (1 - W[7]) * (d + (-W[6] * (g - 3)) * (10 - d) / 9));
const sRecall = (d, s, r, g) => s * (Math.exp(W[8]) * (11 - d) * s ** -W[9] * (Math.exp(W[10] * (1 - r)) - 1)
  * (g === HARD ? W[15] : 1) * (g === EASY ? W[16] : 1) + 1);
const sLapse = (d, s, r) => Math.min(W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * Math.exp(W[14] * (1 - r)), s);
const sSameDay = (s, g) => s * Math.exp(W[17] * (g - 3 + W[18]));

/** Memory state of one card from its log events, as review.replay; null if never reviewed. */
export function replay(events) {
  let st = null;
  for (const e of [...events].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0))) {
    const t = Date.parse(e.ts), g = e.pass ? GOOD : AGAIN;
    if (!st) st = { stability: initS(g), difficulty: initD(g), reps: 0, lapses: 0 };
    else {
      const days = (t - st.last) / DAY, s = st.stability, d = st.difficulty;
      st.stability = days < 1 ? sSameDay(s, g) : g >= HARD ? sRecall(d, s, retrievability(days, s), g) : sLapse(d, s, retrievability(days, s));
      st.difficulty = nextD(d, g);
    }
    st.reps += 1; st.lapses += e.pass ? 0 : 1; st.last = t;
    st.due = t + intervalDays(st.stability) * DAY;
  }
  return st;
}

/** review.card_id: first 12 hex of SHA-1 of the question, lower-cased, spaces collapsed. */
export async function cardId(question) {
  const norm = String(question).toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(norm));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
}

export async function parseCards(tsv, folder) {
  const out = [];
  for (const line of String(tsv ?? "").split("\n")) {
    const [q, a, ref = ""] = line.split("\t");
    if (q?.trim() && a !== undefined) out.push({ id: await cardId(q), q: q.trim(), a: a.trim(), ref: ref.trim(), folder });
  }
  return out;
}

export function logByCard(text) {
  const by = new Map();
  for (const e of jsonl(text)) if (e.card && e.ts) (by.get(e.card) ?? by.set(e.card, []).get(e.card)).push(e);
  return by;
}

/** { due, fresh, leeches } across lectures, as review.queue: due most overdue first, new
 *  cards in lecture order up to newLimit. lectures: [{ folder, cardsTsv, log }]. */
export async function queue(lectures, nowMs, newLimit = 10) {
  const due = [], fresh = [], leeches = [];
  for (const { folder, cardsTsv, log } of lectures) {
    const by = logByCard(log);
    for (const c of await parseCards(cardsTsv, folder)) {
      const st = replay(by.get(c.id) ?? []);
      if (!st) { fresh.push(c); continue; }
      const card = { ...c, ...st };
      if (st.lapses >= LEECH_LAPSES) leeches.push(card);
      if (st.due <= nowMs) due.push(card);
    }
  }
  due.sort((a, b) => a.due - b.due);
  return { due, fresh: fresh.slice(0, Math.max(0, newLimit)), leeches };
}

/** One reviews.jsonl line, in the shape review.log_answer writes. */
export const logLine = (card, passed, nowMs = Date.now()) => JSON.stringify({ card, ts: pyIso(nowMs), pass: Boolean(passed) }) + "\n";
