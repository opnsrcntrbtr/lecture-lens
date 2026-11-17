"""CT-5 acceptance on the labelled keyframes (eval/golden/scene_labels.jsonl):
macro-F1 >= 0.90, zero break frames classed as slides, vision model on <= 20% of frames.
Labels still marked unconfirmed (vision pre-labels) make the result provisional, and the
vision stage is then partly graded against itself.

  python3 tools/scene_bench.py [--no-vision]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-vision", action="store_true")
    a = ap.parse_args(argv)
    import lecture_kit as lk, scenes
    labels = [json.loads(l) for l in (HERE / "eval" / "golden" / "scene_labels.jsonl").read_text().splitlines() if l.strip()]
    by_lecture = {}
    for r in labels:
        by_lecture.setdefault(r["lecture"], []).append(r)
    vlm = None if a.no_vision else lk.scene_vlm()
    gold, pred, src = [], [], []
    for lec, rs in by_lecture.items():
        frames = {f["slide_id"]: f for f in lk.scene_frames(lk.LECTURES / lec)}
        for r in rs:
            f = frames.get(r["slide_id"])
            if not f:
                continue
            c = scenes.classify(f, vlm)
            gold.append(r["label"]); pred.append(c["class"]); src.append(c["class_source"])
    rep = scenes.macro_f1(gold, pred)
    rep.update({"n": len(gold), "confirmed": sum(bool(r.get("confirmed")) for r in labels),
                "vision_share": round(src.count("vision") / len(src), 3) if src else None,
                "by_stage": {s: src.count(s) for s in set(src)},
                "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    rep["provisional"] = rep["confirmed"] < len(labels)
    rep["ok"] = bool(rep["macro_f1"] is not None and rep["macro_f1"] >= 0.90 and rep["break_as_slide"] == 0
                     and (rep["vision_share"] or 0) <= 0.20)
    (HERE / "eval" / "results" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-scenes-ct5.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
