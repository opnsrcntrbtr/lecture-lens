// Engine choice: Gemini Nano when Chrome has it, otherwise an open model on WebGPU.
import { NanoEngine } from "./nano.js";
import { WebLLMEngine, DEFAULT_WEBLLM, WEBLLM_MODELS } from "./webllm.js";

export { NanoEngine, WebLLMEngine, WEBLLM_MODELS, DEFAULT_WEBLLM };

const USABLE = new Set(["available", "downloadable", "downloading"]);

/** { nano, nanoImage, webgpu }: what this browser can run. */
export async function probe() {
  const [nano, nanoImage] = await Promise.all([NanoEngine.availability(false), NanoEngine.availability(true)]);
  return { nano, nanoImage, webgpu: WebLLMEngine.availability() };
}

/** pref: "auto" | "nano" | "webllm:<model id>". Returns an engine that still needs init(). */
export function pick(pref, caps) {
  if (pref === "nano" || (pref === "auto" && USABLE.has(caps.nano))) return new NanoEngine();
  const model = pref.startsWith("webllm:") ? pref.slice(7) : DEFAULT_WEBLLM;
  if (caps.webgpu !== "available") throw new Error("neither Gemini Nano nor WebGPU is available in this browser");
  return new WebLLMEngine(model);
}
