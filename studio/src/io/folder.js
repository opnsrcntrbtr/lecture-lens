// The lectures folder, read and written through the File System Access API. The user
// picks the folder once; its handle is kept in IndexedDB so a reload only needs one
// click to grant access again. Paths are relative to that folder, with "/" separators.

const DB = "lecture-lens-studio", STORE = "handles", KEY = "lectures";

function idb(mode, fn) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction(STORE, mode), req = fn(tx.objectStore(STORE));
      req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error);
    };
  });
}

export const supported = () => typeof window !== "undefined" && "showDirectoryPicker" in window;

export async function pickFolder() {
  const handle = await window.showDirectoryPicker({ id: "lectures", mode: "readwrite" });
  await idb("readwrite", (s) => s.put(handle, KEY));
  return new FolderIO(handle);
}

/** The folder picked last time, and whether access is granted now ("granted" | "prompt" | null). */
export async function savedFolder() {
  const handle = await idb("readonly", (s) => s.get(KEY)).catch(() => null);
  if (!handle) return { io: null, permission: null };
  return { io: new FolderIO(handle), permission: await handle.queryPermission({ mode: "readwrite" }) };
}

export class FolderIO {
  constructor(root) { this.root = root; this.name = root.name; }

  async regrant() { return (await this.root.requestPermission({ mode: "readwrite" })) === "granted"; }

  async #dir(parts, create = false) {
    let d = this.root;
    for (const p of parts) if (p) d = await d.getDirectoryHandle(p, { create });
    return d;
  }

  async #file(path, create = false) {
    const parts = path.split("/").filter(Boolean), name = parts.pop();
    return (await this.#dir(parts, create)).getFileHandle(name, { create });
  }

  async exists(path) {
    try { await this.#file(path); return true; } catch { return false; }
  }

  async readText(path) { return (await (await this.#file(path)).getFile()).text(); }

  async readBlob(path) { return (await this.#file(path)).getFile(); }

  async writeText(path, text) {
    const w = await (await this.#file(path, true)).createWritable();
    await w.write(text); await w.close();
  }

  async appendText(path, text) {
    const fh = await this.#file(path, true), size = (await fh.getFile()).size;
    const w = await fh.createWritable({ keepExistingData: true });
    await w.seek(size); await w.write(text); await w.close();
  }

  async remove(path) {
    const parts = path.split("/").filter(Boolean), name = parts.pop();
    try { await (await this.#dir(parts)).removeEntry(name); } catch { /* already gone */ }
  }

  async mkdir(path) { await this.#dir(path.split("/"), true); }

  async list(path = "") {
    const out = [];
    try {
      for await (const [name, h] of (await this.#dir(path.split("/"))).entries()) out.push({ name, kind: h.kind });
    } catch { /* missing folder: empty */ }
    return out.sort((a, b) => a.name.localeCompare(b.name));
  }
}
