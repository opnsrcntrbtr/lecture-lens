// Module recap: one page per module from the notes of its lectures.
import { SYS_TUTOR, VERSIONS, recapPrompt } from "./prompts.js";
import { sections, cleanReply } from "./text.js";

const KEEP = ["TL;DR", "Key concepts", "Frameworks", "Worked examples"];

/** The parts of a lecture's notes worth carrying into a recap, capped at `cap` characters. */
export function recapExtract(title, notesMd, cap) {
  const secs = sections(notesMd);
  const body = Object.entries(secs).filter(([h]) => KEEP.some((k) => h.startsWith(k))).map(([h, b]) => `### ${h}\n${b}`).join("\n");
  return `## ${title}\n${(body || notesMd).slice(0, cap)}`;
}

/** Lectures grouped by the module in their manifest ("" when untagged). */
export async function lecturesByModule(io) {
  const groups = {};
  for (const e of await io.list("")) {
    if (e.kind !== "directory" || !/^\d{8}-\d{4}_/.test(e.name)) continue;
    let man = {};
    try { man = JSON.parse(await io.readText(`${e.name}/manifest.json`)); } catch { continue; }
    const mod = man.module || man.calendar?.module || "";
    (groups[mod] ??= []).push({ folder: e.name, title: man.title || e.name, start: man.start || "" });
  }
  for (const g of Object.values(groups)) g.sort((a, b) => a.start.localeCompare(b.start));
  return groups;
}

export async function makeRecap(engine, io, module, lectures, say = () => {}) {
  const per = Math.floor(engine.budget.sourceChars / Math.max(1, lectures.length));
  const parts = [];
  for (const l of lectures) {
    if (!(await io.exists(`${l.folder}/notes.md`))) continue;
    parts.push(recapExtract(l.title, await io.readText(`${l.folder}/notes.md`), per));
  }
  if (!parts.length) throw new Error(`no notes yet for ${module || "untagged lectures"}`);
  say(`recap of ${parts.length} lecture(s)…`);
  const md = cleanReply(await engine.prompt(SYS_TUTOR, recapPrompt(module, parts.join("\n\n")), { maxTokens: 1500, temperature: 0.3 }));
  const name = (module || "Untagged").replace(/[^\w.-]+/g, "-");
  const out = `# Module recap — ${module || "untagged lectures"}\n\n_${parts.length} lecture(s) · ${engine.name} in Chrome · ${VERSIONS.recap}_\n\n${md}\n`;
  await io.mkdir("recaps");
  await io.writeText(`recaps/${name}.md`, out);
  return `recaps/${name}.md`;
}
