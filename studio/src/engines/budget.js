// Character budgets from a context window in tokens. A transcript part takes 40% of the
// window and a notes source 69%, leaving room for the instructions and up to ~1,500 output
// tokens. charsPerToken defaults to 3.5 (English lecture text); an engine that can count
// tokens measures it on the lecture's own text instead (calibrateBudget).
export const DEFAULT_CPT = 3.5;

export function budgetFor(windowTokens, charsPerToken = DEFAULT_CPT) {
  const w = Math.max(2048, windowTokens || 4096), c = Math.min(6, Math.max(2, charsPerToken || DEFAULT_CPT));
  return { windowTokens: w, charsPerToken: c, partChars: Math.floor(w * c * 0.4), sourceChars: Math.floor(w * c * (2.4 / DEFAULT_CPT)) };
}

/** Re-derive an engine's budget from its own token count of `sample` (S3). Engines without
 *  countTokens keep the default. A sample under 1,000 characters is too short to measure. */
export async function calibrateBudget(engine, sample) {
  const text = String(sample ?? "").slice(0, 6000);
  if (typeof engine.countTokens !== "function" || text.length < 1000) return engine.budget;
  try {
    const tokens = await engine.countTokens(text);
    if (tokens > 0) engine.budget = budgetFor(engine.budget.windowTokens, text.length / tokens);
  } catch { /* keep the default budget */ }
  return engine.budget;
}

/** True for the errors engines raise when an input does not fit the window. */
export const isOverflow = (e) => e?.name === "QuotaExceededError" || /too long|window|context length|quota/i.test(String(e?.message ?? ""));

/** A JSON Schema whose top level is an array, wrapped in an object (some engines only
 *  constrain objects), and the matching unwrap of the reply. */
export const wrapArraySchema = (schema) =>
  schema?.type === "array" ? { type: "object", additionalProperties: false, required: ["items"], properties: { items: schema } } : schema;

export function unwrapArrayReply(schema, text) {
  if (schema?.type !== "array") return text;
  try { const v = JSON.parse(text); return JSON.stringify(Array.isArray(v) ? v : v?.items ?? []); } catch { return text; }
}
