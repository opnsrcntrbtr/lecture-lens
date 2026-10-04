#!/usr/bin/env python3
"""Stress variant: each clean slide as screenpipe would actually capture it.

A 3024x1964 (Retina) screenshot of Chrome: tab strip, address bar, a course
sidebar full of navigation text, the lecture player at ~55% of the screen with
controls and a caption line, then blur + JPEG q=60 (video + capture compression).
Ground truth is unchanged: the model must describe the slide, not the page.

    python3 gen_stress.py            # reads cases/, writes cases_stress/
"""
import json, shutil, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
sys.path.insert(0, str(Path(__file__).parent))
from gen_dataset import font

SRC = Path(__file__).parent / "cases"
MAX_EDGE = int(sys.argv[sys.argv.index("--max-edge") + 1]) if "--max-edge" in sys.argv else 0
DST = Path(__file__).parent / ("cases_stress" + (f"_{MAX_EDGE}" if MAX_EDGE else ""))
SW, SH = 3024, 1964

def chrome_frame(slide):
    img = Image.new("RGB", (SW, SH), "#f1f3f4"); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SW, 90], fill="#dee1e6")
    for i, t in enumerate(["course portal | Module 4 · AI Evaluation", "Gmail", "Course Calendar"]):
        d.rounded_rectangle([20 + i * 520, 18, 500 + i * 520, 90], radius=14, fill="#ffffff" if i == 0 else "#e8eaed")
        d.text((50 + i * 520, 38), t, fill="#3c4043", font=font(30))
    d.rounded_rectangle([200, 110, SW - 200, 180], radius=34, fill="#ffffff")
    d.text((250, 125), "learn.example.edu/course/sample/module-4/lecture-12", fill="#5f6368", font=font(32))
    d.rectangle([0, 200, 620, SH], fill="#ffffff")                     # course nav sidebar
    nav = ["Module 1  Foundations of AI Products", "Module 2  Data Strategy", "Module 3  Model Development",
           "Module 4  AI Evaluation", "   12. Precision, recall and product risk", "   13. Online experimentation",
           "Module 5  Responsible AI", "Assignments (2 due)", "Discussion forum", "Download slides (PDF)"]
    for i, t in enumerate(nav):
        d.text((40, 240 + i * 90), t, fill="#202124" if i != 4 else "#c5221f", font=font(30, i in (3, 4)))
    pw = int(SW * 0.55); ph = int(pw * 9 / 16); px, py = 760, 260  # player
    s = slide.resize((pw, ph), Image.LANCZOS)
    img.paste(s, (px, py))
    d.rectangle([px, py + ph, px + pw, py + ph + 90], fill="#111111")
    d.rectangle([px + 120, py + ph + 40, px + pw - 400, py + ph + 50], fill="#5f6368")
    d.rectangle([px + 120, py + ph + 40, px + 700, py + ph + 50], fill="#e41f1f")
    d.text((px + pw - 360, py + ph + 25), "14:32 / 41:05   1.25x   CC", fill="#ffffff", font=font(30))
    d.text((px, py + ph + 130), "Lecture 12 · Prof. (course faculty) · 41 min", fill="#202124", font=font(36, True))
    d.text((px, py + ph + 190), "Notes · Transcript · Resources · Q&A (38)", fill="#1a73e8", font=font(32))
    return img.filter(ImageFilter.GaussianBlur(0.8))

def main():
    if DST.exists(): shutil.rmtree(DST)
    DST.mkdir()
    out = []
    for c in map(json.loads, (SRC / "cases.jsonl").read_text().splitlines()):
        if c.get("video"): continue                                  # server rejects video
        media = []
        for m in c["media"]:
            framed = chrome_frame(Image.open(SRC / m).convert("RGB"))
            if MAX_EDGE:   # what lecture_kit would send after downscaling
                framed.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            framed.save(DST / m, quality=60); media.append(m)
        if not c["media"] and c["kind"] != "no_image": continue
        out.append({**c, "media": media})
    (DST / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in out) + "\n")
    print(f"{len(out)} stress cases -> {DST}")

if __name__ == "__main__":
    main()
