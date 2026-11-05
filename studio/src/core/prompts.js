// Prompts for small in-browser models. They keep the rules of the Python prompts
// (lecture_kit.make_notes notes-v3, make_cards cards-v3, live_class.make_questions)
// in fewer words: a 3-4B model follows short, concrete instructions better.
// Bump a version whenever its wording changes; the version is written to every output.

export const VERSIONS = {
  digest: "digest-nano-v1", notes: "notes-nano-v1", cards: "cards-nano-v1", slide: "slide-nano-v1",
  ask: "ask-nano-v1", outline: "outline-nano-v1", questions: "questions-nano-v1", recap: "recap-nano-v1",
};

export const SYS_TUTOR =
  "You are a rigorous study tutor. Use ONLY the material given (the learner's own lecture transcript, slides " +
  "and notes). If the material does not contain something, say so. Never invent facts, names or numbers.";

export const digestPrompt = (title, i, n, part) =>
  `Lecture: ${title} (part ${i} of ${n})\n\nTRANSCRIPT (timecoded):\n${part}\n\n` +
  "List every substantive point made in this part as short bullets: facts, names, numbers, dates, definitions, " +
  "frameworks, examples, commitments and answers to audience questions. Keep the timecode of each point like " +
  "[00:12:30]. Use only what is said; mark an unclear name (unclear). No preamble.";

export const reducePrompt = (title, a, b) =>
  `Lecture: ${title}\n\nTWO DIGESTS OF CONSECUTIVE PARTS:\n${a}\n\n${b}\n\n` +
  "Merge them into one bullet list. Keep every distinct point with its timecode, drop exact repeats, keep the order. No preamble.";

const NOTES_RULES =
  "Cite timecodes like [00:12:30] and slides like [slide 4]. Ignore player and navigation text. Speech-to-text " +
  "misspells names and numbers: use the spelling a slide shows.\nRules:\n" +
  "- If the material gives two different values for one fact, give both and who gave each.\n" +
  "- If a question was deferred, say it was not answered. Never infer the answer.\n" +
  "- A statistic on a slide without a source is a claim: write 'the slide claims'.";

/** Notes in two calls (a small model writes a fuller page this way); the sections and their
 *  order match the oMLX notes, so the app, the vault note and the notes eval read either. */
export const notesPromptA = (title, source) =>
  `Lecture: ${title}\n\n${source}\n\nWrite the first half of the study notes in Markdown with exactly these ` +
  "sections: ## TL;DR (5 bullets), ## Key concepts (bold each concept name), ## Frameworks & models.\n" + NOTES_RULES;

export const notesPromptB = (title, source, firstHalf) =>
  `Lecture: ${title}\n\n${source}\n\nTHE FIRST HALF OF THE NOTES (do not repeat it):\n${firstHalf.slice(0, 1500)}\n\n` +
  "Write the second half in Markdown with exactly these sections: ## Worked examples and numbers, " +
  "## How this applies to building AI products, ## Action items for the learner, ## Open questions " +
  "(only what the session left unanswered or contradictory).\n" + NOTES_RULES;

export const CARD_KINDS = ["why", "compare", "apply", "recall"];

export const cardsSchema = (n) => ({
  type: "array", maxItems: n,
  items: {
    type: "object", additionalProperties: false, required: ["q", "a", "at", "kind"],
    properties: { q: { type: "string" }, a: { type: "string" }, at: { type: "string" }, kind: { type: "string", enum: CARD_KINDS } },
  },
});

export const cardsPrompt = (title, source, n) =>
  `Lecture: ${title}\n\n${source}\n\nCreate ${n} flashcards from this material only. Rules:\n` +
  "- Each card tests a DIFFERENT idea; no two cards share an answer.\n" +
  `- Mix kinds: why (reasoning), compare (two things), apply (a short new scenario), recall (a definition or number); at most ${Math.max(1, Math.floor(n / 3))} recall.\n` +
  "- A plain question, answerable without the lecture's wording; do not mention 'the lecture'.\n" +
  "- The answer is under 40 words and states only what the material supports.\n" +
  '- "at" is the timecode of the point, like "00:10:00", or "" if none.\n' +
  'Return a JSON array of {"q","a","at","kind"}.';

export const slidePrompt = (title) =>
  "This is one frame from a lecture video." + (title ? ` The lecture is titled "${title}".` : "") + "\n" +
  "If it shows slide content (a heading, text, numbers, chart, table or diagram), describe it so a learner who " +
  "cannot see it loses nothing: the heading, each bullet or label as written, every number, and what a chart or " +
  "diagram shows. Plain Markdown, no preamble. If it shows only a person, a video player or app chrome, reply SKIP.";

export const ASK_INSTRUCTIONS =
  "Answer clearly and briefly from the numbered sources only, citing them inline like [2]. " +
  "If they do not contain the answer, say so and suggest what to review.";

export const askPrompt = (question, sourcesText) =>
  `Sources:\n${sourcesText}\n\nQuestion: ${question}\n\n${ASK_INSTRUCTIONS}`;

export const outlinePrompt = (titles, text) =>
  `SLIDE TITLES (quoted): ${JSON.stringify(titles)}\n\nTRANSCRIPT, 10 minutes of a live class (quoted; ` +
  `speech-to-text, may have errors):\n${text}\n\nWrite exactly 3 short bullets ('- ') on what was taught: ` +
  "concepts, frameworks, numbers. Only what the material says. No citations, no brackets, no preamble.";

export const QUESTION_TYPES = ["clarify", "challenge", "practical", "capstone"];

export const questionsSchema = {
  type: "array", maxItems: 3,
  items: {
    type: "object", additionalProperties: false, required: ["type", "q", "answer", "sources"],
    properties: {
      type: { type: "string", enum: QUESTION_TYPES }, q: { type: "string" }, answer: { type: "string" },
      sources: { type: "array", items: { type: "string" } },
    },
  },
};

export const questionsPrompt = ({ title, titles, earlier, text, side, bank, idea }) =>
  `LIVE CLASS: ${title}\nSLIDES IN THIS WINDOW (quoted): ${JSON.stringify(titles)}\nEARLIER OUTLINE (quoted): ${earlier}\n\n` +
  `LAST 10 MINUTES OF TRANSCRIPT (quoted; speech-to-text, may have errors):\n${text}\n\n` +
  `ATTENDEE Q&A ALREADY VISIBLE (do not repeat these):\n${side || "(none)"}\n\n` +
  `VERIFIED EVIDENCE BANK (the only outside sources you may cite, by id):\n${bank || "(empty)"}\n\n` +
  (idea ? `LEARNER'S CAPSTONE IDEA: ${idea}\n\n` : "") +
  "Write 3 questions the learner could type into the meeting Q&A now, about what was just taught. Types: clarify " +
  "(how a framework works, its criteria or cut-offs), challenge (contrast the lecture with an evidence-bank fact), " +
  "practical (tools, costs, build or buy, evals)" + (idea ? ", capstone (apply it to the learner's idea)" : "") + ".\n" +
  "One question mark per question, under 40 words, using only terms in the quoted text. A good question names a " +
  "criterion, threshold, example or trade-off the lecturer can answer in two minutes.\n" +
  "Each draft answer under 80 words: first what the lecture said, then outside evidence as (S#) only if it truly " +
  "applies; list those ids in sources. Use only numbers in the transcript or the evidence bank.";

export const recapPrompt = (module, notes) =>
  `MODULE: ${module}\n\nNOTES OF EACH LECTURE IN THE MODULE (quoted):\n${notes}\n\n` +
  "Write a one-page module recap in Markdown with these sections: ## The module in 5 bullets, ## How the ideas " +
  "connect (one paragraph), ## Frameworks to know, ## Numbers worth remembering, ## Questions to test yourself (5), " +
  "## Gaps (what the notes leave unclear). Keep the timecodes and [slide n] references the notes give. " +
  "Use only the notes.";
