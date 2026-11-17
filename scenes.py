"""CT-5 scene classes for kept frames: slide, shared_document, gallery, break, other.

A cascade from cheap to costly, so the vision model sees only frames still ambiguous:
  1. title     the app and window title (a document site or PDF viewer, our own app)
  2. stability how long the frame stayed on screen, and how different it is from the
               last kept frame (slides dwell; galleries and video flicker)
  3. layout    the frame's OCR text with known meeting-app interface strings removed:
               break-screen wording, a grid of short name-like tiles (gallery), slide-shaped
               text (a heading and short lines) or document-shaped text (long lines, editor menus)
  4. vision    the local vision model, with a fixed label set, only when stages 1-3 do
               not settle it
Each frame records which stage decided (class_source). OCR text of gallery frames holds
people's names: it is used here only to count tile-shaped lines and is never stored.

Contract: <lecture>/slides.jsonl
  {slide_id, t0, t1, class, class_source, text_blocks, tables, thumb_path, vlm_desc}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CLASSES = ("slide", "shared_document", "gallery", "break", "other")

UI = re.compile(r"^(?:mute|unmute|start video|stop video|participants?|chat|share screen|stop share|record(?:ing)?|"
                r"reactions?|leave|end|security|apps|whiteboards?|more|view|gallery view|speaker view|raise hand|"
                r"lower hand|q&a|polls?|captions?|show captions|audio settings|video settings|webinar chat|"
                r"zoom (?:meeting|webinar|workplace)|you are viewing .* screen|view options|talking:.*|"
                r"recording\.\.\.|live transcript|breakout rooms|ai companion|summary|notes|docs|"
                r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?|\d+)$", re.I)
OTHER_UI = re.compile(r"my programs|academic planner|tab search|skip to main content|gmail|inbox|"
                      r"submit \d+ of \d+ answered|who can see your responses|option\+command|has started screen sharing|"
                      r"double-click to enter full screen|accessibility help|go to google|event details", re.I)
BREAK = re.compile(r"\bbreak\b|we(?:'ll| will) (?:be )?(?:back|resume)|resum(?:e|es|ing) (?:at|in)\b|back in \d+|"
                   r"\bstarting (?:soon|shortly)\b|will (?:begin|start) shortly|\bpaused\b|lunch", re.I)
DOC_TITLE = re.compile(r"google docs|google sheets|notion|confluence|microsoft word|\.docx?\b|\.pdf\b|preview|"
                       r"acrobat|github|readme|- docs\b", re.I)
DOC_MENU = re.compile(r"\bfile\s+edit\s+view\s+insert\b|\bcomments?\b.*\bshare\b|\bsuggesting\b|\bpage \d+ of \d+\b", re.I)
OWN_APPS = ("screenpipe study", "lecture lens")
TILE = re.compile(r"^(?:[A-Z][a-z]+|[A-Z]{2,})(?:\s+(?:[A-Z][a-z]+|[A-Z]\.?)){0,2}$")


MAIN = (0.02, 0.08, 0.78, 0.92)   # left, top, right, bottom: inside the meeting app's bars and side panel


def lines_from_json(text_json: str | list | None, region: tuple = MAIN) -> str:
    """OCR words (screenpipe text_json: normalised boxes) inside the main content region,
    joined into lines; the side panel (chat, participants) and the toolbars are left out."""
    if not text_json:
        return ""
    words = json.loads(text_json) if isinstance(text_json, str) else text_json
    l0, t0, r0, b0 = region
    by: dict[tuple, list] = {}
    for w in words:
        try:
            x, y, wd = float(w["left"]), float(w["top"]), float(w["width"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (l0 <= x and x + wd <= r0 and t0 <= y <= b0) or not str(w.get("text") or "").strip():
            continue
        key = (w.get("block_num"), w.get("par_num"), w.get("line_num"))
        by.setdefault(key, []).append((x, y, str(w["text"])))
    rows = sorted(by.values(), key=lambda ws: (round(min(y for _, y, _ in ws), 2), min(x for x, _, _ in ws)))
    return "\n".join(" ".join(t for _, _, t in sorted(ws)) for ws in rows)


def ocr_text(entries: list[dict], region: tuple = MAIN) -> str:
    """Lines from the image OCR (tools/ocr: Vision observations, normalised boxes from the
    top-left) inside the main content region, top to bottom."""
    l0, t0, r0, b0 = region
    keep = [e for e in entries if l0 <= e["left"] and e["left"] + e["width"] <= r0 and t0 <= e["top"] <= b0]
    return "\n".join(e["text"] for e in sorted(keep, key=lambda e: (round(e["top"], 2), e["left"])))


def ocr_images(paths: list[str], binary: Path) -> list[list[dict]]:
    """Run the on-device OCR helper once over many images."""
    import subprocess
    if not paths or not Path(binary).exists():
        return [[] for _ in paths]
    out = subprocess.run([str(binary), *paths], capture_output=True, text=True, timeout=600).stdout.splitlines()
    res = []
    for line in out:
        try:
            res.append(json.loads(line))
        except ValueError:
            res.append([])
    return res + [[] for _ in range(len(paths) - len(res))]


def clean_lines(text: str) -> list[str]:
    out = []
    for l in (text or "").splitlines():
        t = l.strip()
        if t and not UI.match(t):
            out.append(t)
    return out


def layout(text: str) -> tuple[str | None, str]:
    """(class or None, reason) from OCR text alone."""
    lines = clean_lines(text)
    words = sum(len(l.split()) for l in lines)
    joined = " ".join(lines)
    if BREAK.search(joined) and words < 80:
        return "break", "break wording"
    if OTHER_UI.search(joined):
        return "other", "portal, mail or poll screen"
    tiles = sum(1 for l in lines if TILE.match(l))
    if len(lines) >= 4 and tiles >= 4 and tiles / len(lines) >= 0.5 and words < 60:
        return "gallery", f"{tiles} tile-shaped lines"
    if DOC_MENU.search(joined):
        return "shared_document", "editor menus"
    if words < 6:
        return None, "almost no text"
    avg = words / max(1, len(lines))
    if words > 260 or (avg > 14 and len(lines) >= 6):
        return "shared_document", f"{words} words, {avg:.0f} per line"
    if 12 <= words <= 260 and avg <= 14:
        return "slide", f"{len(lines)} short lines"
    return None, "unclear layout"


def has_title(entries: list[dict], region: tuple = MAIN) -> bool:
    """A slide-like heading: one or more large text lines in the main region, and not a page
    full of text (OCR boxes; height is a share of the frame)."""
    l0, t0, r0, b0 = region
    k = [e for e in entries if l0 <= e["left"] and e["left"] + e["width"] <= r0 and t0 <= e["top"] <= b0]
    return bool(k) and len(k) <= 45 and max(e["height"] for e in k) >= 0.04


def classify(frame: dict, vlm=None) -> dict:
    """frame: {app_name, window_name, text, dwell_s, diff_prev, image}. vlm(image) -> class or None."""
    app = (frame.get("app_name") or "").lower()
    win = frame.get("window_name") or ""
    if any(a in app or a in win.lower() for a in OWN_APPS):
        return {"class": "other", "class_source": "title", "reason": "own app"}
    if DOC_TITLE.search(win):
        return {"class": "shared_document", "class_source": "title", "reason": "document window"}
    dwell = frame.get("dwell_s")
    if dwell is not None and dwell < 1.5 and (frame.get("diff_prev") or 0) > 0.6:
        cls, why = None, "transient frame"
    else:
        cls, why = layout(frame.get("text") or "")
    if cls in ("break", "gallery", "shared_document", "other"):
        return {"class": cls, "class_source": "layout", "reason": why}
    if cls == "slide" and (dwell is None or dwell >= 4) and frame.get("title", True):
        return {"class": "slide", "class_source": "layout" if dwell is None else "stability", "reason": why}
    if vlm and frame.get("image"):
        v = vlm(frame["image"])
        if v in CLASSES:
            return {"class": v, "class_source": "vision", "reason": why}
    return {"class": cls or "other", "class_source": "fallback", "reason": why}


VLM_PROMPT = ("Classify this screenshot from an online class into exactly one label:\n"
              "slide = a presentation slide is the main content;\n"
              "shared_document = a document, spreadsheet, website or code is being shared;\n"
              "gallery = video tiles of participants are the main content;\n"
              "break = a break or waiting screen, countdown or 'we will be back' notice;\n"
              "other = anything else (blank, app menus, the learner's own apps).\n"
              "Reply with the label only.")


def parse_label(text: str) -> str | None:
    t = (text or "").strip().lower()
    for c in CLASSES:
        if t.startswith(c) or re.search(rf"\b{c}\b", t):
            return c
    return "shared_document" if "document" in t else None


def macro_f1(gold: list[str], pred: list[str]) -> dict:
    out = {}
    for c in CLASSES:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        if tp + fp + fn == 0:
            continue
        pr = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        out[c] = {"precision": round(pr, 3), "recall": round(rc, 3), "f1": round(2 * pr * rc / (pr + rc), 3) if pr + rc else 0.0,
                  "n": tp + fn}
    present = [v["f1"] for c, v in out.items() if v["n"]]
    return {"per_class": out, "macro_f1": round(sum(present) / len(present), 3) if present else None,
            "break_as_slide": sum(g == "break" and p == "slide" for g, p in zip(gold, pred))}


def write_jsonl(folder: Path, rows: list[dict]) -> None:
    (Path(folder) / "slides.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
