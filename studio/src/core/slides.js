// Slide records (slides.json, written by `lecture build` in browser mode) and the two
// texts made from them: slides.md for reading, and the slide source for notes and cards.
// Both match lecture_kit.build so either engine's output reads the same.

export function slidesMarkdown(title, slides) {
  const body = slides.map((s) =>
    `## ${s.n}. at ${s.at}\n\n![[${s.file}]]\n\n` +
    (s.description ? `${s.description}\n\n` : "") +
    (s.screen_text ? `> On-screen text: ${s.screen_text}\n` : "")).join("\n");
  return `# Slides & visuals — ${title}\n\n${body || "_no keyframes captured_"}\n`;
}

/** The slide part of the notes source: keyframe descriptions (or OCR text), unless a
 *  class deck was supplied, which replaces them; then the class Q&A, if any. */
export function slideSource(slides, { deckSrc = "", qaSrc = "" } = {}) {
  const line = (s, cap) => `[slide ${s.n} @ ${s.at}] ${(s.description || s.screen_text || "").slice(0, cap)}`;
  let src = deckSrc
    ? `${deckSrc}\n\nKEYFRAME TIMES (when each captured slide was on screen):\n` + slides.map((s) => line(s, 90)).join("\n")
    : "SLIDES:\n" + slides.map((s) => line(s, 1500)).join("\n");
  if (qaSrc) src += `\n\n${qaSrc}`;
  return src;
}

/** A model's slide description, or null when it says the frame has no slide. */
export function cleanDescription(text) {
  const t = String(text ?? "").trim();
  if (!t || /^skip\b/i.test(t) || t.length < 12) return null;
  return t.replace(/^(here is|this (frame|slide|image) shows)[^:\n]*:\s*/i, "");
}
