// Test doubles: an in-memory lectures folder and a scripted engine. Fixtures are invented.
import { budgetFor } from "../src/engines/budget.js";

export class MemIO {
  constructor(files = {}) { this.files = new Map(Object.entries(files)); this.name = "lectures"; }
  async exists(p) { return this.files.has(p); }
  async readText(p) { if (!this.files.has(p)) throw new Error(`missing ${p}`); return String(this.files.get(p)); }
  async readBlob(p) { if (!this.files.has(p)) throw new Error(`missing ${p}`); return { path: p }; }
  async writeText(p, t) { this.files.set(p, t); }
  async appendText(p, t) { this.files.set(p, (this.files.get(p) ?? "") + t); }
  async remove(p) { this.files.delete(p); }
  async mkdir() {}
  async list(dir = "") {
    const pre = dir ? `${dir}/` : "", seen = new Map();
    for (const k of this.files.keys()) {
      if (!k.startsWith(pre)) continue;
      const rest = k.slice(pre.length), [head, ...tail] = rest.split("/");
      seen.set(head, tail.length ? "directory" : "file");
    }
    return [...seen].map(([name, kind]) => ({ name, kind })).sort((a, b) => a.name.localeCompare(b.name));
  }
}

/** Replies by the first matching rule; records every call. */
export class FakeEngine {
  constructor(rules, { window = 9216, images = true } = {}) {
    this.rules = rules; this.calls = []; this.name = "fake"; this.canSeeImages = images; this.budget = budgetFor(window);
  }
  async prompt(system, user, opts = {}) {
    this.calls.push({ system, user, opts });
    for (const [re, reply] of this.rules) if (re.test(user)) return typeof reply === "function" ? reply(user, opts) : reply;
    return "";
  }
  async describeImage(blob, text) { this.calls.push({ image: blob.path, text }); return blob.path.endsWith("02_000500.jpg") ? "SKIP" : `## Heading for ${blob.path}\n- a bullet`; }
}
