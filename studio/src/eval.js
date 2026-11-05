// Generate eval outputs in the browser, in the shape tests/evals/generate_outputs.py writes
// (.outputs.json), so the existing local judge can score the in-browser engine:
//   EVAL_OUTPUTS=.outputs.nano.json ./run.sh --judge-only
// The inputs are the synthetic suite (tests/evals/.dataset.json and contexts.json).
import { askFromSources } from "./core/ask.js";
import { makeNotes } from "./core/notes.js";
import { SYS_TUTOR, VERSIONS, cardsPrompt, cardsSchema } from "./core/prompts.js";
import { parseCards, dedupeCards } from "./core/cards.js";
import { checkCitations } from "./core/citations.js";

const label = (c, i) => (c.startsWith("Transcript") ? `transcript, part ${i + 1}` : `slide, part ${i + 1}`);

export async function runEval(engine, { goldens, contexts, limit = 0, onProgress = () => {} }) {
  const g = limit ? goldens.slice(0, limit) : goldens, cx = limit ? contexts.slice(0, limit) : contexts;
  const out = { generator: engine.name, engine: "studio", prompt_versions: { ask: VERSIONS.ask, notes: VERSIONS.notes, cards: VERSIONS.cards, citations: VERSIONS.citations },
    ask: [], notes: [], cards: [] };
  const t0 = performance.now();
  for (const [i, gold] of g.entries()) {
    const ctx = gold.context || [], t = performance.now();
    let answer;
    try { answer = await askFromSources(engine, gold.input, ctx.map((c, j) => ({ label: label(c, j), text: c }))); }
    catch (e) { answer = `(error: ${e.message})`; }
    const cite = checkCitations(answer, ctx.length);
    out.ask.push({ input: gold.input, actual_output: answer, answer_with_sources: answer, expected_output: gold.expected_output,
      citation_warnings: cite.warnings, citation_coverage: cite.coverage,
      retrieval_context: ctx, latency_s: +((performance.now() - t) / 1000).toFixed(2) });
    onProgress(`ask ${i + 1}/${g.length}`);
  }
  for (const [i, ctx] of cx.entries()) {
    const t = performance.now(), title = `Lecture segment ${i + 1}`, source = ctx.join("\n\n");
    let notes = "", cards = [];
    let citationsRemoved = 0;
    try { ({ notes, citationsRemoved } = await makeNotes(engine, { title, lines: ctx, slideSrc: "", cards: 0 })); } catch (e) { notes = `(error: ${e.message})`; }
    try {
      cards = dedupeCards(parseCards(await engine.prompt(SYS_TUTOR, cardsPrompt(title, source, 4), { schema: cardsSchema(4), maxTokens: 900, temperature: 0.2 }))).slice(0, 4);
    } catch { cards = []; }
    out.notes.push({ input: `notes for lecture segment ${i + 1}`, actual_output: notes, retrieval_context: ctx, citations_removed: citationsRemoved, latency_s: +((performance.now() - t) / 1000).toFixed(2) });
    out.cards.push({ input: `flashcards for lecture segment ${i + 1}`,
      actual_output: cards.map((c) => `Q: ${c.q}\nA: ${c.a}`).join("\n") || "(no cards)", n_cards: cards.length, retrieval_context: ctx });
    onProgress(`notes+cards ${i + 1}/${cx.length}`);
  }
  out.seconds = +((performance.now() - t0) / 1000).toFixed(1);
  return out;
}
