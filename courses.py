#!/usr/bin/env python3
"""Courses: several programmes side by side, each kept apart.

A course has its own lecture folder (index, calendar, glossary, names, live folders, exports),
its own vault folder, its own sites, its own NotebookLM Drive folder and Anki deck. Capture is
shared: one engine records every course's sites, and each course's tools only see frames from
its own sites.

The registry is course material, so it lives in the gitignored courses/ folder
(courses/courses.json, or STUDY_COURSES). Without a registry nothing changes: the toolkit runs
as one course, exactly as before.

    {"default": "main",
     "courses": [
       {"id": "main", "name": "My programme", "kind": "live"},
       {"id": "path-1", "name": "A self-paced path", "kind": "self_paced",
        "sites": ["learn.example.com"], "title_strip": ["Example Learn"], "drm": "auto",
        "focus": "applying these ideas at work"}]}

Every tool picks its course from `--course <id>` (removed from argv before argparse) or
STUDY_COURSE, and activate() rewrites the course-scoped environment before the modules read
it. The default course keeps the environment it started with.

    courses.py list [--json]        the registry, with each course's folder and lecture count
    courses.py sites                capture sites of every course (comma-separated)
    courses.py drm                  DRM page patterns of courses whose player blanks video
    courses.py which <url>          the course a page belongs to
    courses.py check-drm [--course <id>] [--hours 24] [--json]
                                    are this course's captured frames blanked by DRM?
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = ("live", "self_paced")
# What a course may change. The base values (before any course) are kept in STUDY_COURSE_BASE so a
# child process that switches back to the default course can restore them.
SCOPED = ("STUDY_LECTURE_DIR", "STUDY_CALENDAR", "STUDY_VAULT_SUBDIR", "STUDY_LECTURE_SITE", "STUDY_LIVE_APPS",
          "STUDY_TITLE_STRIP", "STUDY_GENERIC_TITLES", "STUDY_NLM_DRIVE_PARENT", "STUDY_DECK_ROOT",
          "STUDY_COURSE_FOCUS", "STUDY_COURSE_NAME", "STUDY_PRACTICE_DIR")
DRM_DARK_LUMA = 6          # a DRM-blanked video region is pure black
DRM_MIN_FRAMES = 5
DRM_DARK_SHARE = 0.25      # share of a frame's pixels that are pure black
DRM_FRAME_SHARE = 0.6      # share of frames that look blanked


def registry_path(environ=None) -> Path:
    env = os.environ if environ is None else environ
    return Path(os.path.expanduser(env.get("STUDY_COURSES", str(HERE / "courses" / "courses.json"))))


def state_path(environ=None) -> Path:
    return registry_path(environ).with_name("state.json")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48] or "course"


def load(path: Path | None = None, environ=None) -> dict:
    """The registry, checked: ids are unique slugs, kinds known, one default. Missing or broken:
    one implicit course that changes nothing."""
    try:
        reg = json.loads(Path(path or registry_path(environ)).read_text())
    except (OSError, ValueError):
        return {"default": "default", "courses": [{"id": "default", "name": "", "kind": "live", "implicit": True}]}
    seen, out = set(), []
    for c in reg.get("courses") or []:
        cid = slug(c.get("id") or c.get("name") or "")
        if cid in seen:
            continue
        seen.add(cid)
        out.append({**c, "id": cid, "kind": c.get("kind") if c.get("kind") in KINDS else "live"})
    if not out:
        return load(Path("/nonexistent"))
    default = slug(reg.get("default") or "") if reg.get("default") else out[0]["id"]
    if default not in seen:
        default = out[0]["id"]
    return {"default": default, "courses": out}


def load_state(environ=None) -> dict:
    try:
        return json.loads(state_path(environ).read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict, environ=None) -> Path:
    p = state_path(environ)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    os.replace(tmp, p)
    return p


def get(reg: dict, cid: str | None) -> dict | None:
    cid = slug(cid) if cid else reg["default"]
    return next((c for c in reg["courses"] if c["id"] == cid), None)


def is_default(reg: dict, c: dict) -> bool:
    return c["id"] == reg["default"]


def _base(environ) -> dict:
    """The course-scoped environment as it was before any course was applied (None = unset)."""
    raw = environ.get("STUDY_COURSE_BASE")
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return {k: environ.get(k) for k in SCOPED}


def root(reg: dict, c: dict, environ=None) -> Path:
    """A course's lecture folder: `root` from the registry, else the base folder for the default
    course and courses/<id> beside the registry for the others."""
    env = os.environ if environ is None else environ
    if c.get("root"):
        return Path(os.path.expanduser(c["root"]))
    base = _base(env).get("STUDY_LECTURE_DIR") or str(HERE / "lectures")
    if is_default(reg, c):
        return Path(os.path.expanduser(base))
    return registry_path(env).parent / c["id"]


def overlay(reg: dict, c: dict, environ=None) -> dict:
    """The course-scoped variables for a course: {name: value or None (unset)}."""
    env = os.environ if environ is None else environ
    base = _base(env)
    if is_default(reg, c):
        out = dict(base)
        if c.get("root"):
            out["STUDY_LECTURE_DIR"] = c["root"]
    else:
        r = root(reg, c, env)
        out = {k: None for k in SCOPED}
        out.update({"STUDY_LECTURE_DIR": str(r), "STUDY_CALENDAR": str(r / "calendar.json"),
                    "STUDY_PRACTICE_DIR": str(r / "practice"),   # review schedule, attempts, mastery
                    "STUDY_VAULT_SUBDIR": c.get("vault_subdir") or f"Courses/{c.get('short') or c.get('name') or c['id']}",
                    "STUDY_LECTURE_SITE": (c.get("sites") or [""])[0],
                    "STUDY_LIVE_APPS": ",".join(c.get("live_apps") or []),
                    "STUDY_DECK_ROOT": c.get("deck") or c.get("short") or c.get("name") or c["id"]})
        # STUDY_NLM_DRIVE_PARENT stays unset unless the course (or `courses nlm-folder`) names one:
        # another course's packs never land in the default course's Drive folder.
    for key, field in (("STUDY_COURSE_NAME", "name"), ("STUDY_COURSE_FOCUS", "focus"),
                       ("STUDY_NLM_DRIVE_PARENT", "nlm_drive_parent")):
        if c.get(field):
            out[key] = c[field]
    for key, field in (("STUDY_TITLE_STRIP", "title_strip"), ("STUDY_GENERIC_TITLES", "generic_titles")):
        if c.get(field):
            out[key] = "|".join(c[field])
    if not is_default(reg, c):
        st = load_state(env).get("nlm_drive_parent", {}).get(c["id"])
        if st and not out.get("STUDY_NLM_DRIVE_PARENT"):
            out["STUDY_NLM_DRIVE_PARENT"] = st
    return out


def activate(argv: list[str] | None = None, environ=None) -> str | None:
    """Apply the course chosen by `--course <id>` in argv (removed in place) or STUDY_COURSE.
    Returns the active course id, or None when there is no registry. Unknown ids exit."""
    env = os.environ if environ is None else environ
    argv = sys.argv if argv is None else argv
    chosen = None
    for i, a in enumerate(list(argv[1:]), 1):
        if a == "--course" and i + 1 < len(argv):
            chosen = argv[i + 1]
            del argv[i:i + 2]
            break
        if a.startswith("--course="):
            chosen = a.split("=", 1)[1]
            del argv[i]
            break
    chosen = chosen or env.get("STUDY_COURSE") or None
    reg = load(environ=env)
    if reg["courses"][0].get("implicit"):
        if chosen and slug(chosen) != "default":
            sys.exit(f"no course registry ({registry_path(env)}): --course {chosen} is unknown")
        return None
    c = get(reg, chosen)
    if c is None:
        sys.exit(f"unknown course '{chosen}': one of {', '.join(x['id'] for x in reg['courses'])}")
    # The base is what the environment says when no other course is applied: a parent process may
    # have set STUDY_LECTURE_DIR on purpose, and the default course keeps it. Only a switch back
    # from another course restores the saved base.
    applied = env.get("STUDY_COURSE_APPLIED")
    if not applied or applied == reg["default"] or "STUDY_COURSE_BASE" not in env:
        env["STUDY_COURSE_BASE"] = json.dumps({k: env.get(k) for k in SCOPED})
    for k, v in overlay(reg, c, env).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    env["STUDY_COURSE"] = c["id"]
    env["STUDY_COURSE_ID"] = c["id"]
    env["STUDY_COURSE_APPLIED"] = c["id"]
    env["STUDY_COURSE_DEFAULT"] = reg["default"]
    return c["id"]


def active(environ=None) -> dict | None:
    """The active course record (after activate()), or None without a registry."""
    env = os.environ if environ is None else environ
    reg = load(environ=env)
    if reg["courses"][0].get("implicit"):
        return None
    return get(reg, env.get("STUDY_COURSE_ID") or env.get("STUDY_COURSE"))


# ------------------------------------------------------------------ capture scope
def host_of(url: str) -> str:
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#:]+)", (url or "").strip(), re.I)
    return (m[1] if m else (url or "").split("/")[0]).lower()


def site_matches(site: str, url: str) -> bool:
    """`site` is 'host', 'host:subdomains' (the engine's allowlist form) or a URL prefix."""
    site = (site or "").strip().lower()
    if not site:
        return False
    if "/" in site:
        return site in (url or "").lower()
    h, sub = (site[:-len(":subdomains")], True) if site.endswith(":subdomains") else (site, False)
    host = host_of(url)
    return host == h or (sub and host.endswith("." + h))


def for_url(url: str, reg: dict | None = None) -> str | None:
    """The course whose sites match a page (the longest match wins); None when none does."""
    reg = reg or load()
    best = None
    for c in reg["courses"]:
        for s in c.get("sites") or []:
            if site_matches(s, url) and (best is None or len(s) > best[0]):
                best = (len(s), c["id"])
    return best[1] if best else None


def capture_sites(reg: dict | None = None) -> list[str]:
    """Allowlist entries for portal capture: every course's sites, as the engine accepts them."""
    reg = reg or load()
    out = []
    for c in reg["courses"]:
        for s in c.get("sites") or []:
            s = s.strip()
            if s and "/" not in s and s not in out:
                out.append(s)
    return out


def drm_on(c: dict, state: dict | None = None) -> bool:
    d = c.get("drm", False)
    if d == "auto":
        return bool(((state or {}).get("drm") or {}).get(c["id"], {}).get("blanked"))
    return bool(d)


def drm_patterns(reg: dict | None = None, state: dict | None = None) -> list[str]:
    """URL patterns of pages whose player blanks under screen capture (engine pauses the screen there)."""
    reg = reg or load()
    state = load_state() if state is None else state
    out = []
    for c in reg["courses"]:
        if drm_on(c, state):
            for p in c.get("drm_urls") or [host_of("https://" + s.split(":")[0]) for s in c.get("sites") or []]:
                if p and p not in out:
                    out.append(p)
    return out


def dark_share(img, luma: int = DRM_DARK_LUMA) -> float:
    """Share of an image's pixels that are pure black (PIL image)."""
    g = img.convert("L").resize((160, 100))
    hist = g.histogram()
    return sum(hist[:luma + 1]) / max(1, sum(hist))


def drm_verdict(shares: list[float]) -> dict:
    """Decide from per-frame pure-black shares whether the course's player is DRM-blanked.
    A slide deck with a dark theme is grey, not pure black; a blanked player is a black block
    in most frames while the video plays."""
    n = len(shares)
    if n < DRM_MIN_FRAMES:
        return {"frames": n, "blanked": None, "reason": f"need {DRM_MIN_FRAMES}+ frames of a playing video"}
    dark = sum(1 for s in shares if s >= DRM_DARK_SHARE)
    srt = sorted(shares)
    median = srt[n // 2]
    blanked = dark / n >= DRM_FRAME_SHARE
    return {"frames": n, "blanked": blanked, "dark_frames": dark, "median_dark_share": round(median, 3),
            "reason": ("most frames show a pure-black block where the video plays" if blanked
                       else "frames show the video picture")}


def record_drm(cid: str, verdict: dict, environ=None, now: dt.datetime | None = None) -> dict:
    """Keep a verdict (only a decided one) for drm: auto courses."""
    if verdict.get("blanked") is None:
        return load_state(environ)
    st = load_state(environ)
    st.setdefault("drm", {})[cid] = {**verdict, "checked": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")}
    save_state(st, environ)
    return st


# ------------------------------------------------------------------ CLI
def summary(reg: dict | None = None, environ=None) -> dict:
    reg = reg or load(environ=environ)
    state = load_state(environ)
    out = []
    for c in reg["courses"]:
        r = root(reg, c, environ)
        built = len(list(r.glob("*/manifest.json"))) if r.exists() else 0
        ov = overlay(reg, c, environ)
        out.append({"id": c["id"], "name": c.get("name") or c["id"], "short": c.get("short") or c.get("name") or c["id"],
                    "kind": c["kind"], "default": is_default(reg, c), "sites": c.get("sites") or [],
                    "root": str(r), "vault_subdir": ov.get("STUDY_VAULT_SUBDIR") or "Lectures",
                    "url": c.get("url"), "lectures": built,
                    "drm": c.get("drm", False), "drm_on": drm_on(c, state),
                    "drm_check": (state.get("drm") or {}).get(c["id"]),
                    "nlm_drive_parent": ov.get("STUDY_NLM_DRIVE_PARENT")})
    return {"ok": True, "registry": str(registry_path(environ)), "implicit": bool(reg["courses"][0].get("implicit")),
            "default": reg["default"], "courses": out}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="courses")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--json", action="store_true")
    sub.add_parser("sites")
    sub.add_parser("drm")
    p = sub.add_parser("which"); p.add_argument("url")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        s = summary()
        if a.json:
            print(json.dumps(s, indent=1))
        else:
            for c in s["courses"]:
                print(f"{'*' if c['default'] else ' '} {c['id']:<28} {c['kind']:<10} {c['lectures']:>3} lectures  {c['root']}")
    elif a.cmd == "sites":
        print(",".join(capture_sites()))
    elif a.cmd == "drm":
        print(",".join(drm_patterns()))
    elif a.cmd == "which":
        print(for_url(a.url) or "")


if __name__ == "__main__":
    main()
