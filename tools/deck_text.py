"""deck_text: slide text and speaker notes from a .pptx, with the standard library only.

A .pptx is a zip of XML parts. Slide order comes from ppt/presentation.xml and its
relationships; each slide's notes page is found through the slide's own relationships.
Text runs (<a:t>) are joined per paragraph (<a:p>), paragraphs per shape.

    deck_text.py deck.pptx > deck.json      # [{"n", "title", "text", "notes"}]
"""
from __future__ import annotations

import json
import posixpath
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
NOTES_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
SLIDE_NUMBER = re.compile(r"^\s*(‹#›|<#>|\d{1,3})\s*$")


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    d, f = posixpath.split(part)
    path = posixpath.join(d, "_rels", f + ".rels")
    if path not in z.namelist():
        return {}
    root = ET.fromstring(z.read(path))
    return {r.get("Id"): (r.get("Type"), posixpath.normpath(posixpath.join(d, r.get("Target"))))
            for r in root.findall("rel:Relationship", NS)}


def _shapes_text(xml: bytes) -> list[dict]:
    """[{text, is_title}] per shape, in document order."""
    root = ET.fromstring(xml)
    out = []
    for sp in root.iter("{%s}sp" % NS["p"]):
        ph = sp.find(".//p:nvPr/p:ph", NS)
        is_title = ph is not None and ph.get("type") in ("title", "ctrTitle")
        paras = []
        for p in sp.iter("{%s}p" % NS["a"]):
            t = "".join(x.text or "" for x in p.iter("{%s}t" % NS["a"])).strip()
            if t and not SLIDE_NUMBER.match(t):
                paras.append(t)
        if paras:
            out.append({"text": "\n".join(paras), "is_title": is_title})
    return out


def _label(line: str) -> bool:
    """Small caps labels such as "1 OF 3 · WHERE VALUE SITS" or "CHAT"."""
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8


def read_deck(path: str) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        pres = "ppt/presentation.xml"
        rels = _rels(z, pres)
        root = ET.fromstring(z.read(pres))
        slides = [rels[s.get("{%s}id" % NS["r"])][1] for s in root.findall("p:sldIdLst/p:sldId", NS)]
        parsed = [(part, _shapes_text(z.read(part))) for part in slides]
        # chrome = lines on a quarter or more of the slides (navigation, footers)
        freq: dict[str, int] = {}
        for _, shapes in parsed:
            for line in {l for s in shapes for l in s["text"].split("\n")}:
                freq[line] = freq.get(line, 0) + 1
        chrome = {l for l, c in freq.items() if c >= max(3, len(parsed) // 4)}
        out = []
        for n, (part, shapes) in enumerate(parsed, 1):
            title = next((s["text"] for s in shapes if s["is_title"] and len(s["text"]) <= 90), "")
            if not title:   # decks built without title placeholders: first line that is not chrome or a label
                for s in shapes:
                    line = next((l for l in s["text"].split("\n") if l not in chrome), "")
                    if len(line) >= 6 and not _label(line):
                        title = line
                        break
            notes = ""
            for typ, target in _rels(z, part).values():
                if typ == NOTES_REL and target in z.namelist():
                    notes = "\n".join(s["text"] for s in _shapes_text(z.read(target)))
            text = "\n".join(l for s in shapes for l in s["text"].split("\n") if l not in chrome)
            out.append({"n": n, "title": title.replace("\n", " ").strip(), "text": text, "notes": notes.strip()})
        return out


if __name__ == "__main__":
    json.dump(read_deck(sys.argv[1]), sys.stdout, ensure_ascii=False, indent=1)
