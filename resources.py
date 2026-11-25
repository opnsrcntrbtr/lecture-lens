"""Class resources added after a lecture (slides, agenda, the answered Q&A sheet, readings).

The portal posts a class's files some days after it: the deck (.pptx or .pdf), the agenda and
pre-read (.docx), an answered Q&A sheet (.xlsx). `lecture resources add <id> FILE...` (and
the app's Add resources… button) decides what each file is from its content and writes the
form the build reads:

  deck       .pptx, or a .pdf of slides     resources/decks/<stem>.deck.json  [{n, title, text, notes}]
  agenda     .docx/.txt/.md with Part lines resources/agenda.txt (and live/<day>/agenda.txt if missing)
  qa_sheet   .xlsx with Question + Answer   qa_sheet.json  {rows: [{no, type, q, a}]}
  reading    any other .pdf/.docx/.txt/.md  a reading (study_integrations.import_reading)

Decks and the agenda are also saved as readings, so packs and exports include their text.
Privacy: a Q&A sheet's asked-by, email and name columns never reach an output. Their values
are used only to scrub those names from the question and answer text, and the original
sheet is kept in the private live folder, not beside the lecture. Chat and logistics rows
are dropped. resources/index.json lists what was added; the same file twice is a no-op
(SHA-256). Standard library only (the app runs the system Python): .pptx/.docx/.xlsx are
zipped XML, a .pdf is read with pdftotext (poppler) or macOS PDFKit.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# same shape as agenda.PART: 'Part 2 · Title (about 55 minutes)'
PART = re.compile(r"^\s*(?:\d+[.)]\s*)?part\s+(\w+)\s*[·:.\-–—)]*\s*(.+)$", re.I)
SLIDE_NUMBER = re.compile(r"^\s*\d{1,3}\s*(?:/\s*\d{1,3})?\s*$")
# columns that identify people: never copied, only used to scrub names from the text
PERSON_COL = re.compile(r"asked|e-?mail|name|author|from|attendee|learner|student", re.I)
QUESTION_COL = re.compile(r"^\s*(?:question|query)s?\b", re.I)
ANSWER_COL = re.compile(r"answer|response|reply", re.I)
FINAL_COL = re.compile(r"final", re.I)
TYPE_COL = re.compile(r"^\s*(?:type|category|kind)\b", re.I)
NO_COL = re.compile(r"^\s*(?:no\.?|#|s\.?\s*no\.?|sr\.?\s*no\.?|id)\s*$", re.I)
# what an export puts in a name column when there is no name: not a person, never learned or scrubbed
PLACEHOLDER = re.compile(r"\b(?:anonymous|attendees?|participants?|guests?|unknown|users?|hosts?|panelists?|team|n/?a)\b", re.I)
SKIP_TYPE = re.compile(r"chat|logistic|admin|greeting|thanks", re.I)
KINDS = ("deck", "agenda", "qa_sheet", "reading")


# ---------- readers ----------

def docx_paragraphs(path) -> list[str]:
    """Paragraph texts of a .docx in order; list items get a '- ' prefix, tabs and breaks kept."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(f"{W}p"):
        parts = []
        for el in p.iter():
            if el.tag == f"{W}t":
                parts.append(el.text or "")
            elif el.tag == f"{W}tab":
                parts.append("\t")
            elif el.tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
        t = "".join(parts).strip()
        if not t:
            continue
        listed = p.find(f"{W}pPr/{W}numPr") is not None
        out.append(("- " + t) if listed and not t.startswith(("-", "•")) else t)
    return out


def _col_index(ref: str) -> int:
    n = 0
    for c in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(c) - 64
    return n - 1


def xlsx_sheets(path) -> dict[str, list[list[str]]]:
    """{sheet name: rows of cell strings} from an .xlsx (shared and inline strings, numbers)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{S}t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{PKG}Relationship"):
                t = r.get("Target").lstrip("/")
                rels[r.get("Id")] = t if t.startswith("xl/") else "xl/" + t
        out = {}
        for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{S}sheet"):
            part = rels.get(sh.get(R_ID))
            if not part or part not in names:
                continue
            rows = []
            for row in ET.fromstring(z.read(part)).iter(f"{S}row"):
                cells: dict[int, str] = {}
                for c in row.iter(f"{S}c"):
                    t, v = c.get("t"), c.find(f"{S}v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{S}t"))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    if c.get("r"):
                        cells[_col_index(c.get("r"))] = val
                    else:
                        cells[len(cells)] = val
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
            out[sh.get("name")] = rows
    return out


def _pdftotext() -> str | None:
    return shutil.which("pdftotext") or next(
        (p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.path.exists(p)), None)


PDFKIT_JS = ('ObjC.import("PDFKit"); function run(a) { var d = $.PDFDocument.alloc.initWithURL('
             '$.NSURL.fileURLWithPath(a[0])); var o = []; for (var i = 0; i < d.pageCount; i++) '
             '{ o.push(ObjC.unwrap(d.pageAtIndex(i).string) || ""); } return JSON.stringify(o); }')


def pdf_pages(path) -> list[str]:
    """Text per page: pdftotext when installed, else macOS PDFKit (through JavaScript for
    Automation)."""
    exe = _pdftotext()
    if exe:
        r = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=180)
        if r.returncode == 0:
            pages = r.stdout.decode("utf-8", "replace").split("\f")
            return pages[:-1] if pages and not pages[-1].strip() else pages
    if sys.platform == "darwin":
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", PDFKIT_JS, str(path)],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    raise RuntimeError("no PDF text reader found (install poppler for pdftotext)")


def pdf_landscape(path) -> bool | None:
    """Is the first page wider than tall (slides)? None when the size can't be read."""
    head = Path(path).read_bytes()[:400_000]
    m = re.search(rb"/MediaBox\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]", head)
    if not m:
        return None
    x0, y0, x1, y1 = (float(v) for v in m.groups())
    return (x1 - x0) > (y1 - y0)


# ---------- conversions ----------

def _label(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def deck_from_pages(pages: list[str]) -> list[dict]:
    """Slides from PDF page texts: lines on a quarter or more of the pages are chrome
    (footers, navigation) and dropped; the title is the first line that is not a small-caps
    label, else the first line."""
    split = [[l.strip() for l in p.splitlines() if l.strip() and not SLIDE_NUMBER.match(l)] for p in pages]
    freq: dict[str, int] = {}
    for ls in split:
        for l in set(ls):
            freq[l] = freq.get(l, 0) + 1
    chrome = {l for l, c in freq.items() if len(split) >= 6 and c >= max(3, len(split) // 4)}
    out = []
    for n, ls in enumerate(split, 1):
        ls = [l for l in ls if l not in chrome]
        title = next((l for l in ls if len(l) >= 6 and not _label(l) and len(l) <= 90), ls[0] if ls else "")
        body = [l for l in ls if l != title]
        out.append({"n": n, "title": title, "text": "\n".join(body), "notes": ""})
    return out


def agenda_text(paragraphs: list[str]) -> str:
    return "\n".join(paragraphs).strip() + "\n"


def is_agenda(lines: list[str]) -> bool:
    return sum(1 for l in lines if PART.match(l)) >= 2 or (
        any(re.search(r"\bagenda\b", l, re.I) for l in lines[:8]) and len(lines) <= 200)


def _header(rows: list[list[str]]) -> tuple[int, dict] | None:
    """(row index, {role: column}) for the first row naming a question column and an answer column."""
    for i, row in enumerate(rows[:10]):
        cols = {"person": []}
        for j, h in enumerate(row):
            h = (h or "").strip()
            if not h:
                continue
            if PERSON_COL.search(h):
                cols["person"].append(j)
            elif QUESTION_COL.search(h) and "q" not in cols:
                cols["q"] = j
            elif ANSWER_COL.search(h):
                # a 'Final answer' column beats a plain one; an 'Original note' is neither
                if "a" not in cols or (FINAL_COL.search(h) and not cols.get("a_final")):
                    cols["a"], cols["a_final"] = j, bool(FINAL_COL.search(h))
            elif TYPE_COL.search(h):
                cols["type"] = j
            elif NO_COL.search(h):
                cols["no"] = j
        if "q" in cols and "a" in cols:
            return i, cols
    return None


def norm_q(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def qa_from_sheets(sheets: dict[str, list[list[str]]]) -> tuple[list[dict], set[str], dict]:
    """Questions and answers from every sheet with Question and Answer columns, de-duplicated
    by question text (the longer answer wins). Returns (rows, the values of person columns
    for scrubbing only, counts)."""
    by_q: dict[str, dict] = {}
    people: set[str] = set()
    counts = {"sheets": 0, "skipped_type": 0, "unanswered": 0}
    for _name, rows in sheets.items():
        h = _header(rows)
        if not h:
            continue
        counts["sheets"] += 1
        hi, cols = h
        cell = lambda r, k: (r[cols[k]] if k in cols and cols[k] < len(r) else "") or ""
        for r in rows[hi + 1:]:
            for j in cols["person"]:
                if j < len(r) and (r[j] or "").strip() and not PLACEHOLDER.search(r[j]) and "@" not in r[j]:
                    people.add(r[j].strip())
            q, a, kind = cell(r, "q").strip(), cell(r, "a").strip(), cell(r, "type").strip()
            if not q:
                continue
            if kind and SKIP_TYPE.search(kind):
                counts["skipped_type"] += 1
                continue
            if not a:
                counts["unanswered"] += 1
                continue
            k = norm_q(q)
            row = {"no": cell(r, "no").strip(), "type": kind or "Question", "q": q, "a": a}
            if k not in by_q or len(a) > len(by_q[k]["a"]):
                by_q[k] = row
    out = list(by_q.values())
    num = lambda r: int(r["no"]) if r["no"].isdigit() else 10 ** 6
    out.sort(key=num)
    return out, people, counts


def scrub_people(texts: list[str], people: set[str], keep: set[str]) -> tuple[list[str], int]:
    """Whole-word removal of each listed person's name tokens (staff in `keep` stay)."""
    toks = set()
    for p in people:
        for t in re.findall(r"[A-Za-z][A-Za-z'’.-]+", p):
            t = t.strip(".'’-")
            if len(t) >= 3 and t.lower() not in {k.lower() for k in keep}:
                toks.add(t)
    if not toks:
        return texts, 0
    rx = re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(toks, key=len, reverse=True)) + r")\b", re.I)
    n = 0
    out = []
    for t in texts:
        new, k = rx.subn("[name]", t)
        n += k
        out.append(new)
    return out, n


# ---------- classification ----------

def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def classify(path) -> str:
    """deck, agenda, qa_sheet or reading, from the file's content."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pptx":
        return "deck"
    if ext == ".xlsx":
        return "qa_sheet" if any(_header(rows) for rows in xlsx_sheets(p).values()) else "reading"
    if ext == ".pdf":
        land = pdf_landscape(p)
        if land:
            return "deck"
        pages = pdf_pages(p)
        words = sorted(len(x.split()) for x in pages) or [0]
        if land is None and len(pages) <= 150 and words[len(words) // 2] <= 120:
            return "deck"
        return "agenda" if is_agenda([l for x in pages[:3] for l in x.splitlines()]) else "reading"
    if ext == ".docx":
        return "agenda" if is_agenda(docx_paragraphs(p)) else "reading"
    if ext in (".txt", ".md"):
        return "agenda" if is_agenda(p.read_text(encoding="utf-8", errors="replace").splitlines()) else "reading"
    raise ValueError(f"unsupported file type: {ext or 'none'} (pptx, pdf, docx, xlsx, txt or md)")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", Path(name).stem).strip("-")[:80] or "file"


def index_path(folder: Path) -> Path:
    return folder / "resources" / "index.json"


def load_index(folder: Path) -> list[dict]:
    p = index_path(folder)
    try:
        return json.loads(p.read_text()) if p.exists() else []
    except ValueError:
        return []


def deck_markdown(name: str, slides: list[dict]) -> str:
    md = [f"# Slides: {name}", ""]
    for s in slides:
        md += [f"## {s['n']}. {s['title']}".rstrip(), ""]
        if s.get("text"):
            md += [s["text"], ""]
        if s.get("notes"):
            md += ["> Speaker notes: " + re.sub(r"\s+", " ", s["notes"]).strip(), ""]
    return "\n".join(md) + "\n"


def add(folder: Path, files, live_dir: Path | None = None, read_deck=None, import_reading=None,
        guard=None, learn=None, keep: set[str] | None = None, now: str | None = None) -> dict:
    """Add files to a built lecture. read_deck(path) -> slides for a .pptx (tools/deck_text);
    import_reading(folder, path, title) -> dict saves a reading. For a Q&A sheet, guard(lines)
    -> lines removes names from its text (name_guard) and learn(people) may add the sheet's
    asked-by names to the private roster; `keep` are staff names left in. Decks and agendas
    are the faculty's own documents and are not name-filtered. Returns {added, skipped, rebuild}."""
    folder = Path(folder)
    res = folder / "resources"
    (res / "files").mkdir(parents=True, exist_ok=True)
    idx = load_index(folder)
    seen = {e["sha256"]: e for e in idx}
    guard = guard or (lambda lines: lines)
    now = now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    report = {"lecture": folder.name, "added": [], "skipped": [], "rebuild": False}
    for f in files:
        src = Path(f).expanduser()
        if not src.is_file():
            report["skipped"].append({"file": src.name, "reason": "not found"})
            continue
        digest = sha256(src)
        if digest in seen:
            report["skipped"].append({"file": src.name, "reason": "already added", "kind": seen[digest]["kind"]})
            continue
        try:
            kind = classify(src)
        except (ValueError, RuntimeError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
            report["skipped"].append({"file": src.name, "reason": str(e)})
            continue
        entry = {"file": src.name, "kind": kind, "sha256": digest, "added_at": now, "outputs": []}
        stem = slug(src.name)
        if kind == "qa_sheet":
            # the original names people: it goes to the private live folder, never beside the lecture
            keep_dir = live_dir or (res / "private")
            keep_dir.mkdir(parents=True, exist_ok=True)
            raw = keep_dir / f"qa_sheet_{digest[:8]}{src.suffix.lower()}"
            shutil.copy2(src, raw)
            rows, people, counts = qa_from_sheets(xlsx_sheets(src))
            if learn and people:
                learn(people)
            texts, n = scrub_people([x for r in rows for x in (r["q"], r["a"])], people, keep or set())
            texts = guard(texts)
            for i, r in enumerate(rows):
                r["q"], r["a"] = texts[2 * i], texts[2 * i + 1]
            out = {"source": f"Q&A sheet added {now[:10]}", "rows": rows,
                   "counts": {**counts, "rows": len(rows), "names_scrubbed": n}}
            (folder / "qa_sheet.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
            entry.update(outputs=["qa_sheet.json"], original="live folder", people=len(people),
                         summary=f"{len(rows)} answered questions; asked-by and email columns dropped")
            report["rebuild"] = True
        else:
            dest = res / "files" / src.name
            shutil.copy2(src, dest)
            entry["original"] = f"resources/files/{src.name}"
            if kind == "deck":
                if src.suffix.lower() == ".pptx" and read_deck is None:
                    raise RuntimeError("read_deck is required for .pptx")
                slides = read_deck(str(src)) if src.suffix.lower() == ".pptx" else deck_from_pages(pdf_pages(src))
                (res / "decks").mkdir(exist_ok=True)
                dj = res / "decks" / f"{stem}.deck.json"
                dj.write_text(json.dumps(slides, indent=1, ensure_ascii=False))
                entry["outputs"].append(f"resources/decks/{dj.name}")
                entry["slides"] = len(slides)
                entry["summary"] = f"{len(slides)} slides" + (
                    f", speaker notes on {sum(1 for s in slides if s.get('notes'))}" if any(s.get("notes") for s in slides) else "")
                if import_reading:
                    md = res / "decks" / f"{stem}.md"
                    md.write_text(deck_markdown(src.stem, slides))
                    r = import_reading(folder, str(md), f"Slides: {src.stem}")
                    if r.get("ok"):
                        entry["outputs"].append(r.get("saved_path", ""))
                report["rebuild"] = True
            elif kind == "agenda":
                if src.suffix.lower() == ".docx":
                    text = agenda_text(docx_paragraphs(src))
                elif src.suffix.lower() == ".pdf":
                    text = "\n".join(pdf_pages(src)).strip() + "\n"
                else:
                    text = src.read_text(encoding="utf-8", errors="replace")
                (res / "agenda.txt").write_text(text)
                entry["outputs"].append("resources/agenda.txt")
                parts = sum(1 for l in text.splitlines() if PART.match(l))
                if live_dir is not None and not (live_dir / "agenda.txt").exists():
                    live_dir.mkdir(parents=True, exist_ok=True)
                    (live_dir / "agenda.txt").write_text(text)
                    entry["outputs"].append("live agenda.txt")
                entry["summary"] = f"agenda, {parts} parts" if parts else "agenda"
                if import_reading:
                    md = res / f"{stem}_agenda.md"
                    md.write_text(f"# {src.stem}\n\n{text}")
                    r = import_reading(folder, str(md), src.stem)
                    if r.get("ok"):
                        entry["outputs"].append(r.get("saved_path", ""))
                report["rebuild"] = True
            else:
                text_src = src
                if src.suffix.lower() == ".docx":
                    text_src = res / f"{stem}.md"
                    text_src.write_text(f"# {src.stem}\n\n" + "\n\n".join(docx_paragraphs(src)) + "\n")
                elif src.suffix.lower() == ".xlsx":
                    text_src = res / f"{stem}.md"
                    text_src.write_text(f"# {src.stem}\n\n" + "\n".join(
                        " | ".join(c for c in row if c) for rows in xlsx_sheets(src).values() for row in rows if any(row)) + "\n")
                r = import_reading(folder, str(text_src), src.stem) if import_reading else {"ok": False}
                if r.get("ok"):
                    entry["outputs"].append(r.get("saved_path", ""))
                    entry["summary"] = f"reading, {r.get('word_count', 0)} words"
                else:
                    entry["summary"] = "reading (stored only)" + (f": {r['error']}" if r.get("error") else "")
        idx.append(entry)
        seen[digest] = entry
        report["added"].append({k: v for k, v in entry.items() if k != "sha256"})
    index_path(folder).write_text(json.dumps(idx, indent=1, ensure_ascii=False))
    return report


# ---------- what the build reads ----------

def decks(folder: Path) -> list[tuple[str, list[dict]]]:
    """(name, slides) for each deck added as a resource, in the order added."""
    out = []
    for e in load_index(folder):
        for o in e.get("outputs", []):
            if o.endswith(".deck.json") and (folder / o).exists():
                out.append((Path(e["file"]).stem, json.loads((folder / o).read_text())))
    return out


def combined_deck(folder: Path, own: list[dict]) -> list[dict]:
    """The lecture's own deck (deck.json or a .pptx in the folder) followed by the resource
    decks, numbered 1..N across all of them; each slide keeps its deck's name."""
    out = []
    for name, slides in ([("class deck", own)] if own else []) + decks(folder):
        for s in slides:
            out.append({**s, "n": len(out) + 1, "deck": name, "deck_n": s.get("n")})
    return out


def load_sheet(folder: Path) -> list[dict]:
    p = Path(folder) / "qa_sheet.json"
    try:
        return json.loads(p.read_text()).get("rows", []) if p.exists() else []
    except ValueError:
        return []


def in_sheet(question: str, rows: list[dict]) -> bool:
    k = norm_q(question)
    if not k:
        return False
    for r in rows:
        c = norm_q(r["q"])
        if c == k or (min(len(c), len(k)) >= 24 and (c.startswith(k[:60]) or k.startswith(c[:60]))):
            return True
    return False


def sheet_markdown(rows: list[dict], title: str) -> list[str]:
    md = [f"# Class Q&A — {title}", "",
          f"_From the answered Q&A sheet added after class ({len(rows)} questions). Asked-by and email "
          "columns are not copied; names in the text are removed._", ""]
    for r in rows:
        md += [f"## {('Q' + r['no'] + ' · ') if r['no'] else ''}{r['type']}", "", r["q"], "",
               "> " + r["a"].replace("\n", "\n> "), ""]
    return md


def sheet_source(rows: list[dict], budget: int = 9000) -> str:
    """Notes source block: course questions with their answers (programme rows left out)."""
    lines = [f"Q{(' ' + r['no']) if r['no'] else ''}: {r['q'][:300]} | A: {re.sub(chr(10) + '+', ' / ', r['a'])[:600]}"
             for r in rows if not re.search(r"programme|program|admin", r["type"], re.I)]
    head = "CLASS Q&A SHEET (quoted; questions from attendees with the answers given, not the lecturer's words):\n"
    return (head + "\n".join(lines))[:budget] if lines else ""


def deck_index_markdown(deck: list[dict]) -> str:
    """deck_index.md: which slide each [deck N] in the notes and cards is, deck by deck."""
    md = ["# Class decks", "", "_Notes and cards cite slides as [deck N]; N runs on across the decks below._"]
    cur = object()
    for s in deck:
        if s.get("deck") != cur:
            cur = s.get("deck")
            md += ["", f"## {cur}", ""]
        own = s.get("deck_n")
        md.append(f"- **[deck {s['n']}]** {s.get('title', '')}" + (f" (its slide {own})" if own and own != s["n"] else ""))
    return "\n".join(md) + "\n"
