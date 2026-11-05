// Plan item S2, rejected on this evidence: can a lexical screen (share of a claim's
// content words found in the source) flag unsupported sentences from a small model?
// Runs on the judge benchmark's hand-labelled pairs (tests/evals/judge_pairs.json,
// invented content; 96 pairs, 48/48). No model.   node scripts/support_bench.mjs [--json]
//
// Result (2026-11-05): precision about 0.5 at every threshold, i.e. chance. Wrong-number,
// swapped and half-true claims reuse the context's words; correct paraphrases avoid them.
// As Honovich et al. (2022, TRUE) found for overlap metrics, it does not measure
// faithfulness. Not shipped; the local judge decides (ralph/fix_plan.md S2).
import { readFileSync } from "node:fs";
import { contentWords } from "../src/core/text.js";

const CITE = /\[[^\]]*\]|\(S\d+\)/g;
/** Share of a claim's content words that occur in the source (1 when it has none). */
function support(claim, source) {
  const words = contentWords(String(claim).replace(CITE, " "));
  if (!words.size) return 1;
  const src = contentWords(source);
  let hit = 0;
  for (const w of words) if (src.has(w) || src.has(w.replace(/s$/, "")) || src.has(`${w}s`)) hit++;
  return hit / words.size;
}

const ev = new URL("../../tests/evals/", import.meta.url);
const contexts = JSON.parse(readFileSync(new URL("contexts.json", ev)));
const { pairs } = JSON.parse(readFileSync(new URL("judge_pairs.json", ev)));
const rows = pairs.map((p) => ({ ...p, s: support(p.claim, contexts[p.ctx].join("\n\n")) }));

function at(min) {   // positive = flagged as unsupported
  const tp = rows.filter((r) => r.s < min && r.label === 0).length, fp = rows.filter((r) => r.s < min && r.label === 1).length;
  const fn = rows.filter((r) => r.s >= min && r.label === 0).length;
  return { min, flagged: tp + fp, precision: tp + fp ? tp / (tp + fp) : 0, recall: tp / (tp + fn), false_flags_on_supported: fp / rows.filter((r) => r.label === 1).length };
}
const kinds = {};
for (const r of rows) (kinds[r.kind] ??= []).push(r.s);
const out = { pairs: rows.length, thresholds: [0.3, 0.4, 0.5, 0.6, 0.7, 0.8].map(at),
  mean_support_by_kind: Object.fromEntries(Object.entries(kinds).map(([k, v]) => [k, +(v.reduce((a, b) => a + b, 0) / v.length).toFixed(3)])) };
if (process.argv.includes("--json")) console.log(JSON.stringify(out, null, 1));
else {
  console.log(`${out.pairs} labelled pairs. Flag = support below the threshold. Positive class = unsupported.`);
  console.log("threshold | flagged | precision | recall | supported claims flagged");
  for (const t of out.thresholds) console.log(`${t.min.toFixed(1)} | ${t.flagged} | ${t.precision.toFixed(2)} | ${t.recall.toFixed(2)} | ${t.false_flags_on_supported.toFixed(2)}`);
  console.log("mean support by kind:", out.mean_support_by_kind);
}
