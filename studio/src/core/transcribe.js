// After-class transcription in the browser: the lecture's saved system-audio chunks
// (listed in audio.json by `lecture build` when STUDY_STUDIO_TRANSCRIBE=1) become timecoded
// transcript lines, the same "[hh:mm:ss] text" lines the notes job reads.
import { hhmmss } from "./text.js";

/** Drop whisper's usual hallucinations on silence and near-empty chunks. */
const HALLUCINATION = /^(thank you( for watching)?\.?|thanks for watching!?|you|\.|\[(music|silence|blank_audio)\]|subtitles by .*)$/i;

/** Lines from one chunk's timestamped chunks, offset by where the chunk starts in the lecture. */
export function chunkLines(result, offsetS) {
  const parts = result?.chunks?.length ? result.chunks : [{ timestamp: [0, null], text: result?.text ?? "" }];
  const out = [];
  for (const c of parts) {
    const text = String(c.text ?? "").trim();
    if (!text || HALLUCINATION.test(text)) continue;
    out.push(`[${hhmmss(offsetS + (c.timestamp?.[0] ?? 0))}] ${text}`);
  }
  return out;
}

/** Root-mean-square level of 16 kHz mono samples; below `floor` the chunk is treated as silence. */
export function rms(samples) {
  let s = 0;
  for (let i = 0; i < samples.length; i++) s += samples[i] * samples[i];
  return samples.length ? Math.sqrt(s / samples.length) : 0;
}

/** Transcribe the lecture's audio chunks. `asr.transcribe(Float32Array16k)` returns
 *  {text, chunks:[{timestamp:[s,e], text}]}; `decode(blob)` returns 16 kHz mono samples. */
export async function transcribeLecture(asr, io, folder, decode, { say = () => {}, floor = 0.004 } = {}) {
  const list = JSON.parse(await io.readText(`${folder}/audio.json`));
  const lines = [];
  let silent = 0;
  for (const [i, a] of list.entries()) {
    say(`transcribing audio ${i + 1}/${list.length}…`);
    let samples;
    try { samples = await decode(await io.readBlob(`${folder}/audio/${a.file}`)); }
    catch (e) { say(`audio ${a.file}: ${e.message}`); continue; }
    if (rms(samples) < floor) { silent++; continue; }
    lines.push(...chunkLines(await asr.transcribe(samples), a.offset_s ?? 0));
  }
  if (silent) say(`${silent} silent chunk(s) skipped`);
  return lines;
}

export const transcriptMarkdown = (title, lines, engine) =>
  `# Transcript (browser) — ${title}\n\n_${lines.length} lines · transcribed in Chrome by ${engine}_\n\n${lines.join("\n")}\n`;
