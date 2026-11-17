"""CT-5 labels: pre-label kept frames with the vision model for the learner to correct.

Classifies every kept frame of the given lectures with the vision model (image only, fixed
label set), then keeps up to 60: every frame it did not call a slide (up to 30) plus slides
drawn at random to fill. Written to eval/golden/scene_labels.jsonl with source
"vlm_prelabel" and confirmed false; correct them in the app (Evals, Scene labels) or with
`lecture scene-labels set`.

  python3 tools/scene_prelabel.py LECTURE [LECTURE ...] [-n 60]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("lectures", nargs="+")
    ap.add_argument("-n", type=int, default=60)
    a = ap.parse_args(argv)
    import lecture_kit as lk
    vlm = lk.scene_vlm()
    if vlm is None:
        sys.exit("OMLX_VL_MODEL is not set")
    rows = []
    for ident in a.lectures:
        folder = lk.find_folder(ident)
        for fr in lk.scene_frames(folder):
            if not fr["image"]:
                continue
            rows.append({"lecture": folder.name, "slide_id": fr["slide_id"], "slide_file": fr["slide_file"],
                         "t_abs": fr["t_abs"], "thumb": fr["image"], "label": vlm(fr["image"]) or "other",
                         "source": "vlm_prelabel", "confirmed": False})
            print(json.dumps({k: rows[-1][k] for k in ("slide_id", "label")}), flush=True)
    rng = random.Random(7)
    non = [r for r in rows if r["label"] != "slide"][: a.n // 2]
    sl = [r for r in rows if r["label"] == "slide"]
    pick = non + rng.sample(sl, min(len(sl), a.n - len(non)))
    pick.sort(key=lambda r: r["slide_id"])
    out = HERE / "eval" / "golden" / "scene_labels.jsonl"
    old = {}
    if out.exists():
        for l in out.read_text().splitlines():
            r = json.loads(l)
            if r.get("confirmed"):
                old[r["slide_id"]] = r          # never overwrite the learner's corrections
    pick = [old.get(r["slide_id"], r) for r in pick] + [r for k, r in old.items() if k not in {p["slide_id"] for p in pick}]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in pick))
    from collections import Counter
    print(json.dumps({"frames": len(rows), "kept": len(pick), "labels": Counter(r["label"] for r in pick)}))


if __name__ == "__main__":
    main()
