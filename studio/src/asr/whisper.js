// Whisper in the browser: transformers.js on WebGPU (WASM when WebGPU is missing).
// The library and its ONNX runtime are vendored by the build (vendor/transformers.js,
// vendor/ort/), so neither packaging loads code from another origin. Model weights
// download once from the Hugging Face hub and are cached by the browser.
const VENDOR = new URL("../../vendor/transformers.js", import.meta.url).href;
const ORT = new URL("../../vendor/ort/", import.meta.url).href;

export const WHISPER_MODELS = [
  { id: "onnx-community/whisper-base", label: "Whisper base (fast, about 150 MB)" },
  { id: "onnx-community/whisper-small.en", label: "Whisper small, English (about 500 MB)" },
  { id: "onnx-community/whisper-large-v3-turbo", label: "Whisper large-v3 turbo (best, about 1 GB, needs WebGPU)" },
];

export class WhisperASR {
  constructor(model = WHISPER_MODELS[0].id) { this.model = model; this.name = model.split("/").pop(); }

  async init({ onProgress = () => {} } = {}) {
    let tf;
    try { tf = await import(VENDOR); } catch { throw new Error("vendor/transformers.js is missing: run `npm run build` in studio/"); }
    tf.env.allowLocalModels = false;
    tf.env.backends.onnx.wasm.wasmPaths = { mjs: `${ORT}ort-wasm-simd-threaded.asyncify.mjs`, wasm: `${ORT}ort-wasm-simd-threaded.asyncify.wasm` };
    const webgpu = typeof navigator !== "undefined" && "gpu" in navigator && !!(await navigator.gpu.requestAdapter().catch(() => null));
    this.device = webgpu ? "webgpu" : "wasm";
    this.pipe = await tf.pipeline("automatic-speech-recognition", this.model, {
      device: this.device,
      dtype: webgpu ? { encoder_model: "fp16", decoder_model_merged: "q4" } : "q8",
      progress_callback: (p) => { if (p.status === "progress") onProgress((p.progress ?? 0) / 100, `loading ${p.file ?? this.name}`); },
    });
    return this;
  }

  /** 16 kHz mono samples -> {text, chunks:[{timestamp:[s,e], text}]}. */
  async transcribe(samples) {
    const english = /\.en$/.test(this.model);
    return this.pipe(samples, { chunk_length_s: 30, stride_length_s: 5, return_timestamps: true,
      ...(english ? {} : { language: "english", task: "transcribe" }) });
  }
}

/** Gemini Nano's audio input (needs a GPU), for comparison with Whisper on the same audio. */
export class NanoASR {
  constructor() { this.name = "gemini-nano-audio"; }
  async init() {
    if (typeof LanguageModel === "undefined") throw new Error("this Chrome has no Prompt API");
    const opts = { expectedInputs: [{ type: "audio" }, { type: "text", languages: ["en"] }], expectedOutputs: [{ type: "text", languages: ["en"] }] };
    if ((await LanguageModel.availability(opts)) === "unavailable") throw new Error("Gemini Nano cannot take audio on this device");
    this.base = await LanguageModel.create({ ...opts, initialPrompts: [{ role: "system", content: "You transcribe speech exactly, word for word. Output only the words spoken." }] });
    return this;
  }
  /** Nano takes at most ~30 s of audio per prompt here; longer audio is cut into 30 s pieces. */
  async transcribe(samples) {
    const piece = 16000 * 30, chunks = [];
    for (let i = 0; i < samples.length; i += piece) {
      const s = await this.base.clone();
      try {
        const ctx = new AudioContext({ sampleRate: 16000 });
        const buf = ctx.createBuffer(1, Math.min(piece, samples.length - i), 16000);
        buf.copyToChannel(samples.subarray(i, i + piece), 0); ctx.close();
        const text = await s.prompt([{ role: "user", content: [{ type: "text", value: "Transcribe this audio verbatim." }, { type: "audio", value: buf }] }]);
        chunks.push({ timestamp: [i / 16000, Math.min(samples.length, i + piece) / 16000], text: String(text).trim() });
      } finally { s.destroy(); }
    }
    return { text: chunks.map((c) => c.text).join(" "), chunks };
  }
}
