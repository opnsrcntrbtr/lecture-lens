// After-class job for one lecture folder: slide descriptions, digests, notes, cards.
// Map-reduce sized to the engine's context window (Gemini Nano: 9,216 tokens).
import { splitLines, cleanReply, sections } from "./text.js";
import { SYS_TUTOR, VERSIONS, digestPrompt, reducePrompt, notesPromptA, notesPromptB, cardsPrompt, cardsSchema, slidePrompt } from "./prompts.js";
import { parseCards, dedupeCards, cardCount, cardsTsv } from "./cards.js";
import { slidesMarkdown, slideSource, cleanDescription } from "./slides.js";

const NOTE_HEADINGS = ["TL;DR", "Key concepts", "Frameworks & models", "Worked examples and numbers",
  "How this applies to building AI products", "Action items for the learner", "Open questions"];

/** Keep only `## ` sections and drop a heading the model repeated across the two halves. */
export function joinNoteHalves(a, b) {
  const seen = new Set(), out = [];
  for (const md of [a, b]) {
    for (const [h, body] of Object.entries(sections(cleanReply(md)))) {
      const key = h.toLowerCase();
      if (seen.has(key) || !body) continue;
      seen.add(key); out.push(`## ${h}\n${body}`);
    }
  }
  if (!out.length) return [cleanReply(a), cleanReply(b)].filter(Boolean).join("\n\n");   // no ## headings: keep the text
  out.sort((x, y) => rank(x) - rank(y));
  return out.join("\n\n");
  function rank(s) {
    const h = s.slice(3, s.indexOf("\n")).toLowerCase();
    const i = NOTE_HEADINGS.findIndex((k) => h.startsWith(k.toLowerCase().slice(0, 8)));
    return i === -1 ? 99 : i;
  }
}

/** Notes, digests and cards for a lecture from transcript lines and the slide source. */
export async function makeNotes(engine, { title, lines, slideSrc, cards = 20 }, say = () => {}) {
  const { partChars, sourceChars } = engine.budget;
  const transcript = lines.join("\n");
  const parts = transcript.length + slideSrc.length > sourceChars ? splitLines(lines, partChars) : [];
  let digests = [];
  if (parts.length) {
    for (const [i, part] of parts.entries()) {
      say(`digesting part ${i + 1}/${parts.length}…`);
      digests.push(cleanReply(await engine.prompt(SYS_TUTOR, digestPrompt(title, i + 1, parts.length, part), { maxTokens: 1200 })));
    }
  }
  // reduce: merge neighbouring digests until digests + slides fit one prompt
  let reduced = digests.slice();
  while (reduced.length > 1 && reduced.join("\n\n").length + slideSrc.length > sourceChars) {
    say(`merging ${reduced.length} digests…`);
    const next = [];
    for (let i = 0; i < reduced.length; i += 2) {
      next.push(i + 1 < reduced.length
        ? cleanReply(await engine.prompt(SYS_TUTOR, reducePrompt(title, reduced[i], reduced[i + 1]), { maxTokens: 1400 }))
        : reduced[i]);
    }
    reduced = next;
  }
  const body = parts.length ? `DIGEST OF THE FULL TRANSCRIPT (timecoded):\n${reduced.join("\n\n")}` : `TRANSCRIPT (timecoded):\n${transcript}`;
  const source = `${body}\n\n${slideSrc}`.slice(0, sourceChars);
  say("writing notes…");
  const a = await engine.prompt(SYS_TUTOR, notesPromptA(title, source), { maxTokens: 1500, temperature: 0.3 });
  // the second call also quotes the first half, so it gets a shorter slice of the source
  const b = await engine.prompt(SYS_TUTOR, notesPromptB(title, source.slice(0, sourceChars - 1800), cleanReply(a)), { maxTokens: 1500, temperature: 0.3 });
  const notes = joinNoteHalves(a, b);

  let cardRows = [];
  if (cards) {
    const chunks = parts.length ? parts.map((p) => `TRANSCRIPT (timecoded):\n${p}`) : [source];
    if (parts.length && slideSrc.trim()) chunks.push(slideSrc.slice(0, sourceChars));
    const per = parts.length ? Math.max(3, Math.ceil(cards / parts.length)) : cardCount(source, cards);
    for (const [i, chunk] of chunks.entries()) {
      say(`cards ${i + 1}/${chunks.length}…`);
      const n = Math.min(per, cardCount(chunk, per));
      try {
        cardRows.push(...parseCards(await engine.prompt(SYS_TUTOR, cardsPrompt(title, chunk, n), { schema: cardsSchema(n), maxTokens: 300 + 160 * n, temperature: 0.2 })).slice(0, n));
      } catch (e) {
        say(`cards ${i + 1}: ${e.message}`);
      }
    }
    cardRows = dedupeCards(cardRows);
  }
  return { notes, digests, cards: cardRows };
}

/** Describe the keyframes that have no description yet. Returns how many were described. */
export async function describeSlides(engine, io, folder, title, slides, say = () => {}) {
  if (!engine.canSeeImages) return 0;
  let n = 0;
  for (const s of slides) {
    if (s.description || s.skipped) continue;
    say(`describing slide ${s.n}/${slides.length}…`);
    try {
      const blob = await io.readBlob(`${folder}/slides/${s.file}`);
      const d = cleanDescription(await engine.describeImage(blob, slidePrompt(title)));
      if (d) { s.description = d; n++; } else s.skipped = true;
    } catch (e) {
      say(`slide ${s.n}: ${e.message}`);
    }
  }
  return n;
}

/** Run every pending step for one lecture folder (relative to the lectures root). */
export async function runLectureJob(engine, io, folder, { describe = true, say = () => {} } = {}) {
  const job = JSON.parse(await io.readText(`${folder}/studio_job.json`));
  const slides = (await io.exists(`${folder}/slides.json`)) ? JSON.parse(await io.readText(`${folder}/slides.json`)) : [];
  const t0 = Date.now();
  const described = describe ? await describeSlides(engine, io, folder, job.title, slides, say) : 0;
  if (slides.length) {
    await io.writeText(`${folder}/slides.json`, JSON.stringify(slides, null, 1));
    await io.writeText(`${folder}/slides.md`, slidesMarkdown(job.title, slides));
  }
  const slideSrc = slideSource(slides, { deckSrc: job.deck_src || "", qaSrc: job.qa_src || "" });
  const { notes, digests, cards } = await makeNotes(engine, { title: job.title, lines: job.transcript || [], slideSrc, cards: job.cards ?? 20 }, say);
  const when = job.when ? `${job.when} · ` : "";
  await io.writeText(`${folder}/notes.md`,
    `# ${job.title}\n\n_${when}${job.minutes ?? "?"} min · notes by ${engine.name} in Chrome (Lecture Lens Studio) from this Mac's own capture_\n\n${notes}\n`);
  if (digests.length) {
    await io.writeText(`${folder}/digest.md`, `# Part digests — ${job.title}\n\n` + digests.map((d, i) => `## Part ${i + 1}\n\n${d}`).join("\n\n") + "\n");
  }
  if (cards.length) await io.writeText(`${folder}/cards.tsv`, cardsTsv(cards, job.title));
  const result = {
    status: "done", engine: engine.name, finished_at: new Date().toISOString(), seconds: Math.round((Date.now() - t0) / 1000),
    cards: cards.length, slides_described: described, digest_parts: digests.length,
    prompt_versions: { digest: VERSIONS.digest, notes: VERSIONS.notes, cards: VERSIONS.cards, slide: VERSIONS.slide },
  };
  await io.writeText(`${folder}/studio_result.json`, JSON.stringify(result, null, 1));
  return result;
}

/** A lecture folder is pending when its job exists and no result is newer. */
export async function jobState(io, folder) {
  if (!(await io.exists(`${folder}/studio_job.json`))) return "none";
  if (!(await io.exists(`${folder}/studio_result.json`))) return "pending";
  const job = JSON.parse(await io.readText(`${folder}/studio_job.json`));
  const res = JSON.parse(await io.readText(`${folder}/studio_result.json`));
  return res.finished_at && job.created_at && Date.parse(res.finished_at) < Date.parse(job.created_at) ? "pending" : "done";
}
