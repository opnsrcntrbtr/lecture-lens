// The eval page: pick tests/evals, run the suite on the chosen engine, write the outputs next to it.
import { probe, pick, WEBLLM_MODELS } from "./engines/index.js";
import { FolderIO } from "./io/folder.js";
import { runEval } from "./eval.js";

const $ = (id) => document.getElementById(id);
const log = (m) => { $("log").textContent = `${new Date().toLocaleTimeString()}  ${m}\n` + $("log").textContent; };
const caps = await probe();
$("engine-pref").append(new Option(`Gemini Nano (${caps.nano})`, "nano"), ...WEBLLM_MODELS.filter((m) => !m.vision).map((m) => new Option(`WebGPU: ${m.label}`, `webllm:${m.id}`)));

$("go").onclick = async () => {
  try {
    const io = new FolderIO(await window.showDirectoryPicker({ id: "evals", mode: "readwrite" }));
    const goldens = JSON.parse(await io.readText(".dataset.json")), contexts = JSON.parse(await io.readText("contexts.json"));
    const engine = await pick($("engine-pref").value, caps).init({ onProgress: (p, t) => log(`${t} ${Math.round(p * 100)}%`) });
    const out = await runEval(engine, { goldens, contexts, limit: +$("limit").value || 0, onProgress: log });
    const name = `.outputs.${engine.name.toLowerCase().replace(/[^a-z0-9.]+/g, "-")}.json`;
    await io.writeText(name, JSON.stringify(out, null, 1));
    log(`wrote ${name}: ${out.ask.length} answers, ${out.notes.length} notes, ${out.cards.length} card sets in ${out.seconds} s`);
  } catch (e) { log(`error: ${e.message}`); }
};
