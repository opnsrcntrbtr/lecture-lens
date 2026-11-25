"""Rename a built lecture, or consolidate lectures into one (the file side; no model).

  rename(folder, title, vault_dir)      the new title everywhere: manifest, file headings, card
                                        tags, the vault note (renamed in place). The folder keeps
                                        its id, so links, review history and sync records still work.
  unmerge(dest, superseded, ...)        undo the latest consolidation into dest: the destination as
                                        it was and every source come back; the merged folder moves
                                        to _superseded/ (…merge-undone-…). `leave` keeps named
                                        sources out (e.g. a partial copy of a class already covered).
  merge(dest, sources, ...)             the sources' transcript, slides, Q&A, page text, decks,
                                        resources and readings join the destination, on one
                                        timeline in start order. The sources (and a copy of the
                                        destination as it was) move to _superseded/, so it can be
                                        undone by moving them back. Notes, cards and study aids
                                        are rebuilt afterwards from the merged files.

Timecodes in every file are offsets from a lecture's start. Merged, each part keeps its order and
its offset is the running total of the earlier parts' lengths, so [00:01:00] in the second part of
a 40 + 30 minute pair becomes [00:41:00].
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

TC = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")
LINE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
SLIDE_HEAD = re.compile(r"^## (\d+)\. at (\d{1,2}:\d{2}(?::\d{2})?)\s*$", re.M)
APPENDED = ("qa.md", "page_text.md", "deck_index.md")          # text joined part by part
DECKS = (".pptx", ".pdf", ".key", ".docx", ".xlsx")              # class files kept beside the lecture


def secs(tc: str) -> int:
    p = [int(x) for x in tc.split(":")]
    return p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else p[0] * 60 + p[1]


def hhmmss(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def shift_text(text: str, offset_s: float) -> str:
    """Every [mm:ss] / [hh:mm:ss] timecode in a text moved by offset_s."""
    if not offset_s:
        return text
    return TC.sub(lambda m: f"[{hhmmss(secs(m[0][1:-1]) + offset_s)}]", text)


def _manifest(folder: Path) -> dict:
    try:
        return json.loads((Path(folder) / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def _parse_t(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def vault_note(vault_dir: Path, man: dict) -> Path:
    """Where build() writes a lecture's vault note."""
    return Path(vault_dir) / f"{man.get('id')} {man.get('title')}.md".replace("/", "-")


# ------------------------------------------------------------------ rename
def rename(folder: Path, title: str, vault_dir: Path | None = None) -> dict:
    folder = Path(folder)
    title = re.sub(r"\s+", " ", (title or "")).strip()
    if not title:
        return {"ok": False, "error": "the new title is empty"}
    man = _manifest(folder)
    if not man:
        return {"ok": False, "error": f"not a built lecture: {folder}"}
    old = man.get("title") or ""
    if old == title:
        return {"ok": True, "lecture": folder.name, "title": title, "changed": []}
    changed = []

    def edit(name, fn):
        p = folder / name
        if p.exists():
            s = p.read_text()
            n = fn(s)
            if n != s:
                p.write_text(n)
                changed.append(name)

    head = lambda prefix: (lambda s: re.sub(rf"^# {re.escape(prefix)}{re.escape(old)}\s*$", f"# {prefix}{title}", s, count=1, flags=re.M))
    edit("notes.md", head(""))
    edit("transcript.md", head("Transcript — "))
    edit("transcript_merged.md", head("Transcript — "))
    edit("slides.md", head("Slides & visuals — "))
    edit("digest.md", head("Part digests — "))
    # card tags: the third column starts with the lecture title
    edit("cards.tsv", lambda s: "\n".join(
        "\t".join(c[:2] + [title + c[2][len(old):]] + c[3:]) if len(c := l.split("\t")) >= 3 and old and c[2].startswith(old) else l
        for l in s.split("\n")))
    vault = None
    if vault_dir:
        src = vault_note(vault_dir, man)
        dst = vault_note(vault_dir, {**man, "title": title})
        if src.exists():
            s = re.sub(rf"^# {re.escape(old)}\s*$", f"# {title}", src.read_text(), count=1, flags=re.M)
            dst.write_text(s)
            if src != dst:
                src.unlink()
            vault = str(dst)
    man.update({"title": title, "title_source": "renamed", "renamed_from": man.get("renamed_from") or old,
                "renamed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    (folder / "manifest.json").write_text(json.dumps(man, indent=2, ensure_ascii=False))
    return {"ok": True, "lecture": folder.name, "title": title, "old_title": old,
            "changed": ["manifest.json"] + changed, "vault_note": vault}


# ------------------------------------------------------------------ merge
def plan(dest: Path, sources: list[Path]) -> list[dict]:
    """The parts in start order with their timeline offsets (seconds)."""
    parts = []
    for f in [Path(dest)] + [Path(s) for s in sources]:
        m = _manifest(f)
        start = _parse_t(m.get("start"))
        end = _parse_t(m.get("end"))
        mins = m.get("minutes")
        if mins is None and start and end:
            mins = (end - start).total_seconds() / 60
        parts.append({"folder": f, "id": m.get("id") or f.name, "title": m.get("title") or f.name,
                      "start": start, "end": end, "minutes": float(mins or 0), "dest": f == Path(dest),
                      "consolidated": m.get("consolidated") or []})
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    parts.sort(key=lambda p: (p["start"] or epoch, not p["dest"]))
    off = 0.0
    for p in parts:
        p["offset_s"] = off
        off += p["minutes"] * 60
    return parts


def _timed_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text().splitlines() if LINE.match(l)]


def _slides(folder: Path) -> list[dict]:
    """Slide blocks of slides.md: {n, at, body} (body keeps image link, description, screen text)."""
    p = folder / "slides.md"
    if not p.exists():
        return []
    s = p.read_text()
    heads = list(SLIDE_HEAD.finditer(s))
    out = []
    for i, h in enumerate(heads):
        body = s[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(s)].strip("\n")
        out.append({"n": int(h[1]), "at": h[2], "body": body})
    return out


def _merge_json(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge_json(a[k], v) if k in a else v
        return out
    return a


def merge(dest: Path, sources: list[Path], superseded: Path, vault_dir: Path | None = None,
          now: dt.datetime | None = None) -> dict:
    dest, sources = Path(dest), [Path(s) for s in sources]
    if not _manifest(dest):
        return {"ok": False, "error": f"not a built lecture: {dest}"}
    sources = [s for s in dict.fromkeys(sources) if s != dest]
    if not sources:
        return {"ok": False, "error": "choose at least one other lecture to consolidate"}
    bad = [s.name for s in sources if not _manifest(s)]
    if bad:
        return {"ok": False, "error": f"not built lectures: {', '.join(bad)}"}
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    superseded = Path(superseded)
    superseded.mkdir(parents=True, exist_ok=True)
    backup = superseded / f"{dest.name}.before-merge-{stamp}"
    shutil.copytree(dest, backup)
    if vault_dir and vault_note(vault_dir, _manifest(dest)).exists():   # so an undo restores it too
        shutil.copy2(vault_note(vault_dir, _manifest(dest)), backup / "_vault_note.md")
    parts = plan(dest, sources)
    dman = _manifest(dest)
    title = dman.get("title") or dest.name

    # transcripts: our own, and a merged reference wherever a part has one
    lines, ref_lines, any_ref = [], [], any((p["folder"] / "transcript_merged.md").exists() for p in parts)
    for p in parts:
        own = _timed_lines(p["folder"] / "transcript.md")
        lines += [shift_text(l, p["offset_s"]) for l in own]
        if any_ref:
            ref = _timed_lines(p["folder"] / "transcript_merged.md") or own
            ref_lines += [shift_text(l, p["offset_s"]) for l in ref]
    start = min((p["start"] for p in parts if p["start"]), default=None)
    end = max((p["end"] for p in parts if p["end"]), default=None)
    minutes = round(sum(p["minutes"] for p in parts), 1)
    when = f"{start.astimezone():%a %d %b %Y %H:%M} · " if start else ""
    meta = f"_{when}{minutes} min · {{n}} segments · consolidated from {len(parts)} lectures_"
    (dest / "transcript.md").write_text(f"# Transcript — {title}\n\n{meta.format(n=len(lines))}\n\n" + "\n".join(lines) + "\n")
    if any_ref:
        (dest / "transcript_merged.md").write_text(f"# Transcript — {title}\n\n{meta.format(n=len(ref_lines))}\n" + "\n".join(ref_lines) + "\n")

    # slides: renumbered on the joint timeline, images copied under their new names
    tmp = dest / "slides.merging"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    blocks, cache, n = [], {}, 0
    for p in parts:
        src_dir = (backup if p["dest"] else p["folder"]) / "slides"
        try:
            cache.update(json.loads((src_dir / ".descriptions.json").read_text()))
        except (OSError, ValueError):
            pass
        for s in _slides(backup if p["dest"] else p["folder"]):
            n += 1
            at = hhmmss(secs(s["at"]) + p["offset_s"])
            body = s["body"]
            m = re.search(r"!\[\[([^\]]+)\]\]", body)
            if m and (src_dir / m[1]).exists():
                new = f"{n:02d}_{at.replace(':', '')}{Path(m[1]).suffix}"
                shutil.copy2(src_dir / m[1], tmp / new)
                body = body.replace(m[0], f"![[{new}]]")
            blocks.append(f"## {n}. at {at}\n\n{shift_text(body, p['offset_s'])}\n")
    if (dest / "slides").exists():
        shutil.rmtree(dest / "slides")
    tmp.rename(dest / "slides")
    if cache:
        (dest / "slides" / ".descriptions.json").write_text(json.dumps(cache))
    (dest / "slides.md").write_text(f"# Slides & visuals — {title}\n\n" + ("\n".join(blocks) or "_no keyframes captured_") + "\n")

    # scene rows carry absolute times: joined and kept in time order
    rows = []
    for p in parts:
        f = p["folder"] / "slides.jsonl"
        if f.exists():
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    if rows:
        rows.sort(key=lambda r: str(r.get("t") or r.get("t0") or ""))
        (dest / "slides.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    # text joined part by part (headed by the part's title), decks and resources copied in
    for name in APPENDED:
        texts = [(p, (p["folder"] / name).read_text()) for p in parts if (p["folder"] / name).exists()]
        if len(texts) > 1 or (texts and (not texts[0][0]["dest"] or texts[0][0]["offset_s"])):
            (dest / name).write_text("\n\n".join(f"## {p['title']}\n\n{shift_text(t, p['offset_s'])}" for p, t in texts) + "\n")
    for p in parts:
        if p["dest"]:
            continue
        for f in p["folder"].iterdir():
            if f.is_file() and f.suffix.lower() in DECKS and not (dest / f.name).exists():
                shutil.copy2(f, dest / f.name)
        res = p["folder"] / "resources"
        if res.is_dir():
            out = dest / "resources"
            out.mkdir(exist_ok=True)
            for f in res.rglob("*"):
                if not f.is_file():
                    continue
                rel = f.relative_to(res)
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if f.suffix == ".json" and target.exists():
                    try:
                        target.write_text(json.dumps(_merge_json(json.loads(target.read_text()), json.loads(f.read_text())),
                                                     indent=1, ensure_ascii=False))
                    except ValueError:
                        pass
                elif not target.exists():
                    shutil.copy2(f, target)

    # cards and notes joined as a fallback; the rebuild replaces both
    cards = []
    for p in parts:
        f = p["folder"] / "cards.tsv"
        if f.exists():
            cards += [shift_text(l, p["offset_s"]) for l in f.read_text().splitlines() if l.strip()]
    if cards:
        (dest / "cards.tsv").write_text("\n".join(cards) + "\n")
    notes = [f"## {p['title']}\n\n" + shift_text(re.sub(r"^# .*\n+(_.*_\n+)?", "", (p["folder"] / "notes.md").read_text(), count=1), p["offset_s"])
             for p in parts if (p["folder"] / "notes.md").exists()]
    if notes:
        (dest / "notes.md").write_text(f"# {title}\n\n_consolidated from {len(parts)} lectures; rebuild pending_\n\n" + "\n\n".join(notes) + "\n")

    # the sources leave the library (and the vault) but stay recoverable
    moved, vault_moved = [], []
    for p in parts:
        if p["dest"]:
            continue
        target = superseded / f"{p['folder'].name}.merged-into-{dest.name.split('_', 1)[0]}-{stamp}"
        shutil.move(str(p["folder"]), target)
        moved.append(str(target))
        if vault_dir:
            note = vault_note(vault_dir, _manifest(target))
            if note.exists():
                (Path(vault_dir) / "_superseded").mkdir(exist_ok=True)
                shutil.move(str(note), Path(vault_dir) / "_superseded" / note.name)
                vault_moved.append(note.name)

    prior = [c for p in parts for c in p["consolidated"]]
    record = [{"id": p["id"], "title": p["title"], "folder": p["folder"].name,
               "start": p["start"].isoformat() if p["start"] else None, "end": p["end"].isoformat() if p["end"] else None,
               "minutes": p["minutes"], "offset_s": p["offset_s"], "destination": p["dest"]} for p in parts]
    dman.update({"start": start.isoformat() if start else dman.get("start"), "end": end.isoformat() if end else dman.get("end"),
                 "minutes": minutes, "transcript_segments": len(lines), "slides": n,
                 "cards": len(cards), "consolidated": prior + [r for r in record if not r["destination"]],
                 "timeline": record, "consolidated_at": now.isoformat(timespec="seconds"), "rebuild_pending": True})
    (dest / "manifest.json").write_text(json.dumps(dman, indent=2, ensure_ascii=False))
    return {"ok": True, "lecture": dest.name, "title": title, "parts": len(parts), "minutes": minutes,
            "transcript_lines": len(lines), "slides": n, "moved": moved, "vault_moved": vault_moved,
            "backup": str(backup), "source_ids": [r["id"] for r in record if not r["destination"]]}


def unmerge(dest: Path, superseded: Path, vault_dir: Path | None = None, leave=(),
            now: dt.datetime | None = None) -> dict:
    """Undo the latest consolidation into `dest` from the copies merge() kept in _superseded/."""
    dest, superseded = Path(dest), Path(superseded)
    man = _manifest(dest)
    if not man.get("consolidated_at"):
        return {"ok": False, "error": f"{dest.name} was not consolidated"}
    t = _parse_t(man["consolidated_at"])
    stamp = t.strftime("%Y%m%d-%H%M%S") if t else ""
    backup = superseded / f"{dest.name}.before-merge-{stamp}"
    if not backup.is_dir():
        found = sorted(superseded.glob(f"{dest.name}.before-merge-*"))
        if not found:
            return {"ok": False, "error": f"no copy of {dest.name} from before the merge in {superseded}"}
        backup = found[-1]
        stamp = backup.name.rsplit(".before-merge-", 1)[1]
    sources = sorted(superseded.glob(f"*.merged-into-{dest.name.split('_', 1)[0]}-{stamp}"))
    leave = set(leave or ())
    back = [(src, dest.parent / src.name.split(".merged-into-")[0]) for src in sources]
    clash = [orig.name for _, orig in back if orig.name not in leave and orig.exists()]
    if clash:
        return {"ok": False, "error": f"already in the library: {', '.join(clash)}"}
    now = now or dt.datetime.now(dt.timezone.utc)
    nstamp = now.strftime("%Y%m%d-%H%M%S")
    undone = superseded / f"{dest.name}.merge-undone-{nstamp}"
    old_note = vault_note(vault_dir, man) if vault_dir else None
    shutil.move(str(dest), undone)
    shutil.move(str(backup), dest)
    restored, left, vault_back = [], [], []
    for src, orig in back:
        if orig.name in leave:
            target = superseded / f"{orig.name}.left-out-{nstamp}"
            shutil.move(str(src), target)
            left.append(target.name)
            hide_sessions(dest.parent, [_manifest(target).get("id")], f"left out when undoing the merge into {dest.name}")
            continue
        shutil.move(str(src), orig)
        restored.append(orig.name)
        if vault_dir:
            note = vault_note(vault_dir, _manifest(orig))
            kept = Path(vault_dir) / "_superseded" / note.name
            if kept.exists() and not note.exists():
                shutil.move(str(kept), note)
                vault_back.append(note.name)
    note_restored = False
    if vault_dir:
        if old_note and old_note.exists():
            shutil.move(str(old_note), undone / "_vault_note.md")   # the merged note goes with the merged folder
        kept = dest / "_vault_note.md"
        if kept.exists():
            shutil.move(str(kept), vault_note(vault_dir, _manifest(dest)))
            note_restored = True
    return {"ok": True, "lecture": dest.name, "title": _manifest(dest).get("title"), "restored": restored,
            "left_out": left, "merged_copy": str(undone), "vault_restored": vault_back,
            "vault_note_restored": note_restored}


HIDDEN = ".hidden_sessions.json"   # captures kept out of the library (a copy of a class already built)


def hide_sessions(lectures: Path, ids, reason: str) -> None:
    p = Path(lectures) / HIDDEN
    try:
        rows = json.loads(p.read_text())
    except (OSError, ValueError):
        rows = []
    have = {r.get("id") for r in rows}
    rows += [{"id": i, "reason": reason, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
             for i in dict.fromkeys(ids) if i and i not in have]
    p.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")


def library_check(lectures: Path) -> dict:
    """What looks wrong in the library, without changing it: two built lectures covering the same
    class (overlap over half the shorter), a lecture dated away from its own capture day, one id in
    two folders, a consolidation spanning days, and a rebuild left pending."""
    rows = []
    for m in sorted(Path(lectures).glob("*/manifest.json")) if Path(lectures).exists() else []:
        try:
            j = json.loads(m.read_text())
        except (OSError, ValueError):
            continue
        a, b = _parse_t(j.get("start")), _parse_t(j.get("end"))
        if a and not b and j.get("minutes"):
            b = a + dt.timedelta(minutes=float(j["minutes"]))
        rows.append({"folder": m.parent.name, "id": str(j.get("id") or ""), "title": j.get("title") or m.parent.name,
                     "start": a, "end": b, "man": j})
    issues = []
    def add(kind, folders, message):
        issues.append({"kind": kind, "folders": folders, "message": message})
    for i, x in enumerate(rows):
        for y in rows[i + 1:]:
            if not (x["start"] and x["end"] and y["start"] and y["end"]):
                continue
            ov = (min(x["end"], y["end"]) - max(x["start"], y["start"])).total_seconds()
            short = min((x["end"] - x["start"]).total_seconds(), (y["end"] - y["start"]).total_seconds())
            if short > 0 and ov > 0.5 * short:
                inner = x if (x["end"] - x["start"]) <= (y["end"] - y["start"]) else y
                outer = y if inner is x else x
                part = ov >= 0.95 * short
                add("copies", [x["folder"], y["folder"]],
                    f"{outer['start'].astimezone():%d %b}: “{inner['title']}” "
                    + (f"lies inside “{outer['title']}” (a part of the same class captured twice)" if part else
                       f"overlaps “{outer['title']}” by {int(ov // 60)} min (the same class captured twice)"))
    for x in rows:
        day = re.match(r"^(\d{8})", x["folder"])
        if day and x["start"]:
            cap = dt.datetime.strptime(day[1], "%Y%m%d").date()
            if abs((x["start"].astimezone().date() - cap).days) > 1:
                add("dated_away", [x["folder"]], f"“{x['title']}” was captured {cap:%d %b} but is dated "
                    f"{x['start'].astimezone():%d %b}, so the library lists it there")
        tl = [_parse_t(t.get("start")) for t in x["man"].get("timeline") or [] if t.get("start")]
        if len({t.astimezone().date() for t in tl if t}) > 1:
            add("multi_day_merge", [x["folder"]], f"“{x['title']}” joins lectures from different days; "
                "right-click › Undo consolidation if that was not meant")
        if x["man"].get("rebuild_pending"):
            add("rebuild_pending", [x["folder"]], f"“{x['title']}” still needs its notes and cards rebuilt")
    ids = {}
    for x in rows:
        if x["id"]:
            ids.setdefault(x["id"], []).append(x["folder"])
    for i, fs in ids.items():
        if len(fs) > 1:
            add("same_id", fs, f"{len(fs)} folders share the session id {i}: the app may show only one of them")
    return {"ok": True, "lectures": len(rows), "issues": issues,
            "counts": {k: sum(1 for t in issues if t["kind"] == k) for k in dict.fromkeys(t["kind"] for t in issues)}}


def consolidated_ids(lectures: Path) -> set[str]:
    """Session ids folded into another lecture, or hidden as a copy: the library and the watcher
    skip them as captures (a built lecture folder is still listed)."""
    try:
        out = {str(r["id"]) for r in json.loads((Path(lectures) / HIDDEN).read_text()) if r.get("id")}
    except (OSError, ValueError, TypeError, KeyError):
        out = set()
    for m in Path(lectures).glob("*/manifest.json") if Path(lectures).exists() else []:
        try:
            out |= {str(c.get("id")) for c in json.loads(m.read_text()).get("consolidated") or [] if c.get("id")}
        except (OSError, ValueError, AttributeError):
            continue
    return out
