#!/usr/bin/env python3
"""transcript_clean: drop whisper hallucinations and tidy OCR slide entries.

Whisper invents text over silence. With a vocabulary prompt it repeats the prompt
back ("<name> <term>: <term>?") and otherwise falls back to stock phrases
("Thank you.", "you"). These rules are content-free so they work for any class:

  - echo:    the segment's words are almost all prompt words (>= 0.8 contained)
  - stock:   one of whisper's known silence phrases, standing alone
  - repeat:  the same text 3+ times within `window` seconds (a loop)
  - dup:     an identical (time, text) pair from an overlapping batch

Slides from the Vision OCR fallback are labelled rather than dropped:
  - chrome:     the meeting app's own text ("... has started screen sharing", "Type message here")
  - whiteboard: handwriting OCR, few real words or garbled characters; kept, but not a slide title

    transcript_clean.py segments <whisper_segments.jsonl> [--prompt TEXT] [--write]
    transcript_clean.py slides   <slides.jsonl> [--write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

STOCK = {
    "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!", "-", "okay.", "bye.",
    "i'm going to go ahead and see you.", "i'll see you next time.", "see you next time.",
    "please subscribe", "subtitles by the amara.org community",
}
CHROME = re.compile(r"(has started screen sharing|has stopped screen sharing|type message here|"
                    r"you are viewing|is talking|recording\.\.\.|raise hand|leave webinar)", re.I)
WORD = re.compile(r"[A-Za-z]{2,}")
_DICT: set[str] | None = None


def dictionary() -> set[str]:
    """System word list (macOS ships one); empty if absent, which disables the check."""
    global _DICT
    if _DICT is None:
        try:
            _DICT = {w.strip().lower() for w in open("/usr/share/dict/words", encoding="utf-8", errors="ignore")}
        except OSError:
            _DICT = set()
    return _DICT


def real_word_share(ws: list[str]) -> float | None:
    d = dictionary()
    ws = [w for w in ws if len(w) >= 3]
    if not d or not ws:
        return None
    return sum(w in d or w.rstrip("s") in d or w[:-2] in d or w[:-3] in d for w in ws) / len(ws)


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text)]


def is_echo(text: str, prompt_words: set[str], threshold: float = 0.8) -> bool:
    """Most of the segment's words come from the whisper prompt (prompt bleed-through)."""
    ws = words(text)
    if len(ws) < 2 or not prompt_words:
        return False
    return sum(w in prompt_words for w in ws) / len(ws) >= threshold


def is_stock(text: str) -> bool:
    return text.strip().lower() in STOCK


def _t(s: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(s["t"].replace("Z", "+00:00"))


def clean_segments(segs: list[dict], prompt: str = "", window: float = 180.0, repeats: int = 3) -> tuple[list[dict], Counter]:
    """Return (kept segments in time order, counts of dropped segments by reason)."""
    pw = set(words(prompt))
    dropped: Counter = Counter()
    seen, rows = set(), []
    for s in sorted(segs, key=_t):
        key = (s["t"], s["text"].strip())
        if key in seen:
            dropped["dup"] += 1
            continue
        seen.add(key)
        rows.append(s)
    # loops: identical normalised text 3+ times inside a sliding window
    by_text: dict[str, list[dt.datetime]] = defaultdict(list)
    for s in rows:
        by_text[" ".join(words(s["text"]))].append(_t(s))
    looping = set()
    for txt, ts in by_text.items():
        if not txt:
            continue
        for i in range(len(ts) - repeats + 1):
            if (ts[i + repeats - 1] - ts[i]).total_seconds() <= window:
                looping.add(txt)
                break
    kept = []
    for s in rows:
        text = s["text"].strip()
        norm = " ".join(words(text))
        if is_stock(text):
            dropped["stock"] += 1
        elif is_echo(text, pw):
            dropped["echo"] += 1
        elif norm in looping and len(by_text[norm]) >= repeats:
            dropped["repeat"] += 1
        else:
            kept.append(s)
    return kept, dropped


def ocr_fix(title: str) -> str:
    """Vision reads a sans-serif 'AI' as 'Al'."""
    return re.sub(r"\bAl\b", "AI", title)


def slide_kind(row: dict) -> str:
    """chrome, whiteboard or slide. Whiteboard OCR (handwriting) has a short body and a
    fragment for a title, or garbled characters; typeset slides have neither."""
    title, text = row.get("title", ""), row.get("text", "")
    if CHROME.search(title) or (CHROME.search(text) and len(text) < 200):
        return "chrome"
    if row.get("src") != "vision":
        return "slide"
    garbled = sum(ord(c) > 127 and not c.isspace() and c not in "•–—×’‘“”…·" for c in title)
    body, head = words(text), words(title)
    if garbled >= 2 or len(body) < 12 or (len(body) < 40 and len(head) < 4):
        return "whiteboard"
    share = real_word_share(head)
    if share is not None and share < 0.7 and len(body) < 120:
        return "whiteboard"
    return "slide"


def label_slides(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        r["title"] = ocr_fix(r.get("title", ""))
        r["text"] = ocr_fix(r.get("text", ""))
        r["kind"] = slide_kind(r)
        out.append(r)
    return out


def _rewrite(path: Path, rows: list[dict]) -> Path:
    bak = path.with_suffix(path.suffix + ".raw")
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return bak


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("segments")
    s.add_argument("path", type=Path)
    s.add_argument("--prompt", default="")
    s.add_argument("--write", action="store_true", help="rewrite in place (the original is kept as .raw once)")
    sl = sub.add_parser("slides")
    sl.add_argument("path", type=Path)
    sl.add_argument("--write", action="store_true")
    a = p.parse_args()
    rows = [json.loads(l) for l in a.path.read_text().splitlines() if l.strip()]
    if a.cmd == "segments":
        kept, dropped = clean_segments(rows, a.prompt)
        print(json.dumps({"in": len(rows), "kept": len(kept), "dropped": dict(dropped)}))
        if a.write:
            print("original kept at", _rewrite(a.path, kept))
    else:
        out = label_slides(rows)
        print(json.dumps(dict(Counter(r["kind"] for r in out))))
        for r in out:
            print(r["kind"][:5], r["t"][11:16], r["title"][:60])
        if a.write:
            print("original kept at", _rewrite(a.path, out))


if __name__ == "__main__":
    main()
