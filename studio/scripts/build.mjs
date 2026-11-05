// Build the Studio into one folder that works both as an unpacked Chrome extension and as
// a static page:  node scripts/build.mjs [--out dist]
// Copies the plain ES modules as they are and bundles WebLLM into vendor/web-llm.js, so
// neither packaging loads code from another origin.
import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const i = process.argv.indexOf("--out");
const out = resolve(root, i > 0 ? process.argv[i + 1] : "dist");

await rm(out, { recursive: true, force: true });
await mkdir(join(out, "vendor"), { recursive: true });
for (const f of ["index.html", "eval.html", "style.css", "manifest.json", "background.js", "src"]) {
  await cp(join(root, f), join(out, f), { recursive: true });
}
const { build } = await import("esbuild");
await build({
  entryPoints: [join(root, "node_modules/@mlc-ai/web-llm/lib/index.js")],
  bundle: true, format: "esm", platform: "browser", target: "chrome120", minify: true,
  outfile: join(out, "vendor/web-llm.js"), logLevel: "warning",
});
await writeFile(join(out, "BUILD.txt"), `Lecture Lens Studio, built ${new Date().toISOString()}\n` +
  "Load this folder in chrome://extensions (Developer mode > Load unpacked), or serve it as static files.\n");
console.log(`built ${out}`);
