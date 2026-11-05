// Gemini Nano through Chrome's built-in Prompt API (LanguageModel).
// Web pages: Chrome 148+. Extensions: Chrome 138+, where temperature/topK are also allowed.
// Every call runs in a fresh clone of a base session that holds only the system prompt,
// so jobs never share context and the 9K window is never consumed by earlier calls.
import { budgetFor } from "./budget.js";

const LANG = ["en"];
const opts = (image) => ({
  expectedInputs: image ? [{ type: "text", languages: LANG }, { type: "image" }] : [{ type: "text", languages: LANG }],
  expectedOutputs: [{ type: "text", languages: LANG }],
});

export class NanoEngine {
  static id = "nano";
  static label = "Gemini Nano (built into Chrome)";

  constructor() {
    this.name = "gemini-nano";
    this.canSeeImages = false;
    this.budget = budgetFor(9216);
    this.base = new Map();
  }

  /** "unsupported" when this Chrome has no Prompt API; otherwise the API's own state. */
  static async availability(image = false) {
    if (typeof LanguageModel === "undefined") return "unsupported";
    try { return await LanguageModel.availability(opts(image)); } catch { return "unavailable"; }
  }

  /** Needs a user gesture when the model still has to be downloaded. */
  async init({ onProgress = () => {}, image = true } = {}) {
    if (typeof LanguageModel === "undefined") throw new Error("this Chrome has no Prompt API (Chrome 148+ for pages, 138+ for extensions)");
    const imgState = image ? await NanoEngine.availability(true) : "unavailable";
    this.canSeeImages = imgState === "available" || imgState === "downloadable" || imgState === "downloading";
    this.sampling = {};
    if (typeof LanguageModel.params === "function") {   // extensions only
      try { const p = await LanguageModel.params(); this.sampling = { topK: p.defaultTopK }; } catch { /* web page */ }
    }
    const s = await LanguageModel.create({
      ...opts(this.canSeeImages),
      monitor(m) { m.addEventListener("downloadprogress", (e) => onProgress(e.loaded, "downloading Gemini Nano")); },
    });
    this.budget = budgetFor(s.contextWindow ?? s.inputQuota ?? 9216);
    s.destroy();
    return this;
  }

  async #session(system, temperature) {
    const key = `${temperature ?? ""}|${system}`;
    if (!this.base.has(key)) {
      const sampling = this.sampling.topK != null && temperature != null ? { topK: this.sampling.topK, temperature } : {};
      this.base.set(key, await LanguageModel.create({ ...opts(this.canSeeImages), ...sampling,
        initialPrompts: [{ role: "system", content: system }] }));
    }
    return this.base.get(key).clone();
  }

  async prompt(system, user, { schema, temperature } = {}) {
    const s = await this.#session(system, temperature);
    try {
      const measure = s.measureContextUsage ?? s.measureInputUsage;
      let need = null;
      if (measure) {
        try { need = await measure.call(s, user, schema ? { responseConstraint: schema } : undefined); } catch { need = null; }
      }
      const window = s.contextWindow ?? s.inputQuota, used = s.contextUsage ?? s.inputUsage ?? 0;
      if (need != null && window && used + need > window) throw new Error(`input is ${used + need} tokens; the model window is ${window}`);
      return await s.prompt(user, schema ? { responseConstraint: schema } : undefined);
    } catch (e) {
      if (e?.name === "QuotaExceededError") throw new Error(`input too long for Gemini Nano's window (${e.message})`);
      throw e;
    } finally {
      s.destroy();
    }
  }

  async describeImage(blob, text) {
    if (!this.canSeeImages) return null;
    const s = await this.#session("You describe lecture slides for a learner. Be exact; never invent text.");
    try {
      return await s.prompt([{ role: "user", content: [{ type: "text", value: text }, { type: "image", value: blob }] }]);
    } finally {
      s.destroy();
    }
  }

  destroy() { for (const s of this.base.values()) s.destroy(); this.base.clear(); }
}
