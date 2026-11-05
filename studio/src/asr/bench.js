// Speech-to-text bench: the same audio through each engine, word error rate against a
// reference text, and wall time. Used by eval.html and by the measurements in ADR-005.
import { wer } from "../core/wer.js";

export async function benchAsr(engines, samples, reference, onProgress = () => {}) {
  const out = [];
  for (const asr of engines) {
    onProgress(`${asr.name}…`);
    const t0 = performance.now();
    let text = "", error = null;
    try { text = (await asr.transcribe(samples)).text ?? ""; } catch (e) { error = e.message; }
    out.push({ engine: asr.name, device: asr.device ?? null, seconds: +((performance.now() - t0) / 1000).toFixed(1),
      audio_seconds: +(samples.length / 16000).toFixed(1), wer: error ? null : +wer(reference, text).toFixed(3), error, text });
  }
  return out;
}
