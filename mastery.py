"""LS-7 calibration and mastery: what you know without help, and where confidence is wrong.

A read-only view over practice/attempts.jsonl (LS-1) and practice/reviews.jsonl (LS-3);
nothing new is stored. Per module (the manifest's module, else the lecture):
  predicted_recall      mean FSRS recall now over the module's started cards
  closed_book_accuracy  unassisted items only; also at Apply level or above
  confidence grid       sure / unsure / guess x right / wrong, with "sure and wrong" listed first
and the plan's projected weekly workload. A number appears only with MIN_OBS observations
behind it; every figure is an estimate.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import spaced

MIN_OBS = 5
APPLY_UP = ("apply", "analyse")


def _rows(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def module_of(lectures: Path) -> dict[str, str]:
    out = {}
    for f in Path(lectures).glob("*/manifest.json"):
        try:
            m = json.loads(f.read_text())
        except ValueError:
            continue
        out[f.parent.name] = m.get("module") or m.get("title") or f.parent.name
    return out


def view(practice_dir: Path, lectures: Path, now: dt.datetime | None = None,
         levels: dict[str, str] | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    rv = spaced.Reviews(practice_dir, lectures)
    mod = module_of(lectures)
    states = rv.states()
    by: dict[str, dict] = {}

    def m(key):
        return by.setdefault(key, {"module": key, "recall": [], "closed": [], "closed_apply": [],
                                   "grid": {c: {"right": 0, "wrong": 0} for c in ("sure", "unsure", "guess")},
                                   "sure_wrong": [], "lectures": set()})

    for c in rv.cards():
        st = states.get(c["card_id"])
        if st:
            x = m(c["module"])
            x["recall"].append(spaced.retrievability((now - st["last"]).total_seconds() / 86400, st["s"]))
            x["lectures"].add(c["lecture"])
    for r in _rows(Path(practice_dir) / "attempts.jsonl"):
        if r.get("assisted") or r.get("kind") != "quiz_item" or r.get("correct") is None:
            continue   # unassisted (closed-book) results only
        x = m(mod.get(r.get("lecture_id"), r.get("lecture_id") or "?"))
        x["lectures"].add(r.get("lecture_id"))
        ok = bool(r["correct"])
        x["closed"].append(ok)
        lvl = r.get("level") or (levels or {}).get(r.get("item_id"))
        if lvl in APPLY_UP:
            x["closed_apply"].append(ok)
        conf = r.get("confidence")
        if conf in x["grid"]:
            x["grid"][conf]["right" if ok else "wrong"] += 1
        if conf == "sure" and not ok:
            x["sure_wrong"].append({"item_id": r["item_id"], "stem": r.get("stem"), "lecture": r.get("lecture_id"),
                                    "t": r.get("t")})
    share = lambda xs: round(sum(xs) / len(xs), 3) if len(xs) >= MIN_OBS else None
    out = []
    for x in by.values():
        out.append({"module": x["module"], "lectures": sorted(l for l in x["lectures"] if l),
                    "cards_started": len(x["recall"]),
                    "predicted_recall": round(sum(x["recall"]) / len(x["recall"]), 3) if len(x["recall"]) >= MIN_OBS else None,
                    "closed_book_items": len(x["closed"]), "closed_book_accuracy": share(x["closed"]),
                    "apply_items": len(x["closed_apply"]), "apply_accuracy": share(x["closed_apply"]),
                    "confidence_grid": x["grid"], "sure_and_wrong": x["sure_wrong"][::-1],
                    "enough_data": len(x["recall"]) >= MIN_OBS or len(x["closed"]) >= MIN_OBS})
    out.sort(key=lambda r: (-len(r["sure_and_wrong"]), r["module"]))
    plan = rv.current_plan(now.astimezone().date())
    grid = {c: {k: sum(r["confidence_grid"][c][k] for r in out) for k in ("right", "wrong")} for c in ("sure", "unsure", "guess")}
    return {"modules": out, "min_observations": MIN_OBS, "confidence_grid": grid,
            "sure_and_wrong": [s for r in out for s in r["sure_and_wrong"]][:20],
            "projected_weekly_minutes": plan["projected_weekly_minutes"], "weekly_budget": plan["budget"],
            "note": "Estimates from unassisted results only; modules under the minimum show not enough data."}
