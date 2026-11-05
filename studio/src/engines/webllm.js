// Fallback engine: an open model on WebGPU through WebLLM (MLC). The library is vendored
// into vendor/web-llm.js by the build (no remote code). Weights download once from the
// model's hub and are cached by the browser for this origin.
import { budgetFor, wrapArraySchema, unwrapArrayReply } from "./budget.js";

export const WEBLLM_MODELS = [
  { id: "Qwen3-4B-q4f16_1-MLC", label: "Qwen3 4B (about 2.6 GB of GPU memory)" },
  { id: "Qwen3-1.7B-q4f16_1-MLC", label: "Qwen3 1.7B (smaller, faster)" },
  { id: "Qwen3.5-4B-q4f16_1-MLC", label: "Qwen3.5 4B" },
  { id: "Phi-3.5-vision-instruct-q4f16_1-MLC", label: "Phi-3.5 vision (reads slides; short window)", vision: true },
];
export const DEFAULT_WEBLLM = WEBLLM_MODELS[0].id;
const VENDOR = new URL("../../vendor/web-llm.js", import.meta.url).href;
const WINDOW = 8192;   // above the 4,096 default: lecture parts need the room

export class WebLLMEngine {
  static id = "webllm";
  static label = "Open model on WebGPU (WebLLM)";
  static availability() { return typeof navigator !== "undefined" && "gpu" in navigator ? "available" : "unsupported"; }

  constructor(model = DEFAULT_WEBLLM) {
    this.model = model;
    this.name = model.replace(/-MLC$/, "");
    this.canSeeImages = !!WEBLLM_MODELS.find((m) => m.id === model)?.vision;
    this.budget = budgetFor(this.canSeeImages ? 1024 : WINDOW);
  }

  async init({ onProgress = () => {} } = {}) {
    if (!("gpu" in navigator)) throw new Error("this browser has no WebGPU");
    let webllm;
    try { webllm = await import(VENDOR); } catch { throw new Error("vendor/web-llm.js is missing: run `npm run build` in studio/"); }
    this.engine = await webllm.CreateMLCEngine(this.model,
      { initProgressCallback: (p) => onProgress(p.progress ?? 0, p.text ?? "loading") },
      this.canSeeImages ? undefined : { context_window_size: WINDOW });
    return this;
  }

  async prompt(system, user, { schema, maxTokens = 1024, temperature = 0.3 } = {}) {
    const thinkOff = /qwen3/i.test(this.model) ? " /no_think" : "";
    const wrapped = wrapArraySchema(schema);
    const r = await this.engine.chat.completions.create({
      messages: [{ role: "system", content: system }, { role: "user", content: user + thinkOff }],
      max_tokens: maxTokens, temperature,
      ...(wrapped ? { response_format: { type: "json_object", schema: JSON.stringify(wrapped) } } : {}),
    });
    const text = String(r.choices?.[0]?.message?.content ?? "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    return unwrapArrayReply(schema, text);
  }

  async describeImage(blob, text) {
    if (!this.canSeeImages) return null;
    const url = await new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = rej; fr.readAsDataURL(blob); });
    const r = await this.engine.chat.completions.create({
      messages: [{ role: "user", content: [{ type: "text", text }, { type: "image_url", image_url: { url } }] }], max_tokens: 500, temperature: 0.1,
    });
    return r.choices?.[0]?.message?.content ?? null;
  }

  destroy() { this.engine?.unload?.(); }
}
