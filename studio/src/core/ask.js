// Ask your lectures: BM25 over transcript windows and slides, then a cited answer.
import { SYS_TUTOR, askPrompt } from "./prompts.js";
import { cleanReply } from "./text.js";
import { checkCitations } from "./citations.js";

const WORD = /[a-z0-9][a-z0-9-]*/g;
const STOP = new Set("the a an and or of to in on for is are was were be it this that with as at by from what how why which who when does do did can could should would about into than then there their they them its your you we our".split(" "));
export const terms = (text) => (String(text).toLowerCase().match(WORD) ?? []).filter((w) => w.length > 1 && !STOP.has(w));

/** Passages from a lecture: transcript lines in windows of `size` lines, and one per slide. */
export function lecturePassages(transcriptMd, slides = [], size = 3) {
  const lines = String(transcriptMd).split("\n").map((l) => l.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$/)).filter(Boolean);
  const out = [];
  for (let i = 0; i < lines.length; i += size) {
    const w = lines.slice(i, i + size);
    out.push({ label: w[0][1], text: w.map((m) => m[2]).join(" ") });
  }
  for (const s of slides) {
    const text = s.description || s.screen_text;
    if (text) out.push({ label: `slide ${s.n} @ ${s.at}`, text: text.slice(0, 1500) });
  }
  return out;
}

export class BM25 {
  constructor(docs, { k1 = 1.4, b = 0.75 } = {}) {
    this.docs = docs; this.k1 = k1; this.b = b;
    this.tf = docs.map((d) => { const m = new Map(); for (const t of terms(d.text)) m.set(t, (m.get(t) ?? 0) + 1); return m; });
    this.len = this.tf.map((m) => [...m.values()].reduce((a, c) => a + c, 0));
    this.avg = this.len.reduce((a, c) => a + c, 0) / (docs.length || 1);
    this.df = new Map();
    for (const m of this.tf) for (const t of m.keys()) this.df.set(t, (this.df.get(t) ?? 0) + 1);
  }
  score(query) {
    const q = [...new Set(terms(query))], N = this.docs.length;
    return this.tf.map((m, i) => {
      let s = 0;
      for (const t of q) {
        const f = m.get(t); if (!f) continue;
        const idf = Math.log(1 + (N - this.df.get(t) + 0.5) / (this.df.get(t) + 0.5));
        s += idf * (f * (this.k1 + 1)) / (f + this.k1 * (1 - this.b + this.b * this.len[i] / (this.avg || 1)));
      }
      return s;
    });
  }
  /** Best passages first, as many as fit `budget` characters; ties keep lecture order. */
  top(query, budget, max = 10) {
    const s = this.score(query);
    const order = s.map((v, i) => [v, i]).filter(([v]) => v > 0).sort((x, y) => y[0] - x[0] || x[1] - y[1]);
    const out = []; let used = 0;
    for (const [, i] of order) {
      const d = this.docs[i], n = d.text.length + d.label.length + 8;
      if (out.length >= max) break;
      if (used + n > budget) continue;   // too long here; a shorter, lower-ranked passage may still fit
      out.push(d); used += n;
    }
    return out;
  }
}

export const numberedSources = (sources) => sources.map((s, i) => `[${i + 1}] (${s.label})\n${s.text}`).join("\n\n");

/** Answer from fixed sources (what the eval feeds; the Ask tab feeds retrieved passages). */
export async function askFromSources(engine, question, sources) {
  return cleanReply(await engine.prompt(SYS_TUTOR, askPrompt(question, numberedSources(sources)), { maxTokens: 900, temperature: 0.2 }));
}

export async function askLecture(engine, index, question) {
  const sources = index.top(question, Math.floor(engine.budget.sourceChars * 0.8));
  if (!sources.length) return { answer: "Nothing in this lecture matches the question's words. Try other words, or another lecture.", sources, warnings: [] };
  const answer = await askFromSources(engine, question, sources);
  const { warnings, coverage } = checkCitations(answer, sources.length);
  return { answer, sources, warnings, coverage };
}
