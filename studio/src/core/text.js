// Small text helpers shared by every job. Kept identical in behaviour to the Python
// toolkit (live_class.tokens/jaccard, live_qa.content_words/grounded) so the two
// paths filter the same way; test/text.test.js pins them.

export const tokens = (text) => new Set(String(text ?? "").toLowerCase().match(/[a-z0-9]{3,}/g) ?? []);

export function jaccard(a, b) {
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  const union = a.size + b.size - inter;
  return union ? inter / union : 1;
}

const STOP = new Set(("this that with from have what when where which will would could should about into your their there these those " +
  "they them then than also just like more most some such very been being does doing done here only over same " +
  "other each make made using used want need know think").split(" "));

export const contentWords = (text) =>
  new Set((String(text ?? "").toLowerCase().match(/[a-z][a-z0-9-]{3,}/g) ?? []).filter((w) => !STOP.has(w)));

const ATTRIBUTION = /\b(you (mentioned|said|noted|showed)|the (lecture|professor|slide) (said|mentioned|showed)|as (discussed|mentioned))\b/i;

/** The question's content words must appear in the quoted context; one that attributes
 *  a claim to the lecture needs one more shared word. */
export function grounded(q, context, minShared = 2) {
  const need = minShared + (ATTRIBUTION.test(q) ? 1 : 0);
  const ctx = contentWords(context);
  let n = 0;
  for (const w of contentWords(q)) if (ctx.has(w)) n++;
  return n >= need;
}

export function hhmmss(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}

/** Pack lines into consecutive parts of at most `budget` characters (lecture_kit._split_lines). */
export function splitLines(lines, budget) {
  const parts = [];
  let cur = [], size = 0;
  for (const ln of lines) {
    if (cur.length && size + ln.length + 1 > budget) { parts.push(cur.join("\n")); cur = []; size = 0; }
    cur.push(ln); size += ln.length + 1;
  }
  if (cur.length) parts.push(cur.join("\n"));
  return parts;
}

/** Every complete {...} object in a reply: survives a truncated or chatty answer. */
export function salvageJsonObjects(raw) {
  const out = [];
  const s = String(raw ?? "");
  for (let i = s.indexOf("{"); i !== -1;) {
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let j = i; j < s.length; j++) {
      const c = s[j];
      if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
      if (c === '"') inStr = true;
      else if (c === "{") depth++;
      else if (c === "}" && --depth === 0) { end = j; break; }
    }
    if (end === -1) break;
    try {
      const obj = JSON.parse(s.slice(i, end + 1));
      if (obj && typeof obj === "object" && !Array.isArray(obj)) out.push(obj);
      i = s.indexOf("{", end + 1);
    } catch {
      i = s.indexOf("{", i + 1);
    }
  }
  return out;
}

/** Remove a reasoning model's <think>…</think> block and code fences around JSON. */
export function cleanReply(text) {
  return String(text ?? "").replace(/<think>[\s\S]*?<\/think>/g, "").trim().replace(/^```(?:json|markdown|md)?\s*|\s*```$/g, "").trim();
}

/** Markdown sections keyed by their `## ` heading. */
export function sections(md) {
  const out = {};
  for (const part of String(md).split(/^## /m).slice(1)) {
    const nl = part.indexOf("\n");
    if (nl > 0) out[part.slice(0, nl).trim()] = part.slice(nl + 1).trim();
  }
  return out;
}
