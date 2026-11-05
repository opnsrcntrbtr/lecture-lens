// Character budgets from a context window in tokens. About 3.5 characters per token for
// English lecture text, leaving room for the instructions and up to ~1,500 output tokens.
export function budgetFor(windowTokens) {
  const w = Math.max(2048, windowTokens || 4096);
  return { windowTokens: w, partChars: Math.floor(w * 1.4), sourceChars: Math.floor(w * 2.4) };
}

/** A JSON Schema whose top level is an array, wrapped in an object (some engines only
 *  constrain objects), and the matching unwrap of the reply. */
export const wrapArraySchema = (schema) =>
  schema?.type === "array" ? { type: "object", additionalProperties: false, required: ["items"], properties: { items: schema } } : schema;

export function unwrapArrayReply(schema, text) {
  if (schema?.type !== "array") return text;
  try { const v = JSON.parse(text); return JSON.stringify(Array.isArray(v) ? v : v?.items ?? []); } catch { return text; }
}
