// Deterministic citation checks, no model. A port of study.check_citations (ALCE, Gao et
// al. 2023: a citation is part of an answer's correctness). studio/test/fixtures/
// citations.json is read by both the node tests and tests/unit, so the two stay equal.

const SOURCES_TAIL = /\n[ \t>*#_-]*sources used\b[\s\S]*$/i;
const CITE = /\[(\d+(?:\s*[-–,]\s*\d+)*)\]/g;
const REFUSAL = /(sources|captures|lecture|material)s? (do not|don't|does not|doesn't) (contain|say|cover|mention)/i;

/** The answer without the trailing "Sources used" list. */
export const answerBody = (answer) => String(answer ?? "").replace(SOURCES_TAIL, "").trimEnd();

function cited(group) {
  const out = new Set();
  for (const part of group.split(/\s*,\s*/)) {
    const m = part.trim().match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (m) {
      const lo = +m[1], hi = +m[2];
      if (hi - lo > 0 && hi - lo < 50) for (let n = lo; n <= hi; n++) out.add(n);
      else { out.add(lo); out.add(hi); }
    } else out.add(+part);
  }
  return out;
}

const words = (s) => (s.match(/\w+/g) ?? []).length;

/** { warnings, coverage }: citations to sources never given, an answer with no citation,
 *  and the share of substantive sentences (6+ words) that cite something. */
export function checkCitations(answer, nSources) {
  const body = answerBody(answer);
  const all = new Set();
  for (const m of body.matchAll(CITE)) for (const n of cited(m[1])) all.add(n);
  const warnings = [];
  const bad = [...all].filter((n) => !(n >= 1 && n <= nSources)).sort((a, b) => a - b);
  if (bad.length) warnings.push(`cites source(s) ${bad.join(", ")} but only ${nSources} were given`);
  const sentences = body.split(/(?<=[.!?])\s+|\n+/).filter((x) => words(x.replace(CITE, "")) >= 6);
  const withCite = sentences.filter((x) => new RegExp(CITE.source).test(x)).length;
  const coverage = sentences.length ? Math.round((withCite / sentences.length) * 1000) / 1000 : null;
  if (sentences.length && !withCite && !REFUSAL.test(body)) warnings.push("the answer cites no source");
  return { warnings, coverage };
}

const REF = /\[(slide|deck)\s+(\d+)(?:\s*@[^\]]*)?\]/gi;

/** Slide and deck numbers a source actually contains, from its own labels
 *  ("[slide 3 @ 00:01:00]", "[deck 2]"). */
export function knownRefs(source) {
  const refs = { slide: new Set(), deck: new Set() };
  for (const m of String(source ?? "").matchAll(REF)) refs[m[1].toLowerCase()].add(+m[2]);
  return refs;
}

/** Remove citations to slides or decks the model was never given. Returns { text, removed }.
 *  A citation that points nowhere misleads more than no citation. */
export function dropUnknownRefs(text, refs) {
  let removed = 0;
  const out = String(text ?? "").replace(/\s?\[(slide|deck)\s+(\d+)(?:\s*@[^\]]*)?\]/gi, (all, kind, n) => {
    if (refs[kind.toLowerCase()].has(+n)) return all;
    removed++;
    return "";
  });
  return { text: out, removed };
}
