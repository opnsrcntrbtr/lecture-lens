#!/usr/bin/env python3
"""Generate the synthetic lecture-slide test set with exact ground truth.

Deterministic: same code, same images, same truth. Content is written to look
like an AI-product-management course (the real use case) so the model is tested
on the vocabulary and layouts it will actually meet.

    python3 gen_dataset.py [outdir]     # default: ./cases
"""
import json, math, random, subprocess, sys, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "cases")
W, H = 1280, 720
random.seed(7)

FONT_CANDIDATES = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
BOLD_CANDIDATES = ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] + FONT_CANDIDATES

def font(size, bold=False):
    for p in (BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        try: return ImageFont.truetype(p, size)
        except Exception: continue
    return ImageFont.load_default()

def canvas(bg="white"):
    img = Image.new("RGB", (W, H), bg); return img, ImageDraw.Draw(img)

def title(d, text, color="#1a202c"):
    d.text((60, 36), text, fill=color, font=font(44, True))
    d.line([(60, 100), (W - 60, 100)], fill="#cbd5e0", width=3)

def arrow(d, x1, y1, x2, y2, color="#2d3748"):
    d.line([(x1, y1), (x2, y2)], fill=color, width=4)
    ang = math.atan2(y2 - y1, x2 - x1)
    for s in (-0.45, 0.45):
        d.line([(x2, y2), (x2 - 18 * math.cos(ang + s), y2 - 18 * math.sin(ang + s))], fill=color, width=4)

def box(d, x, y, w, h, text, fill="#ebf8ff", size=24):
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=fill, outline="#2b6cb0", width=3)
    f = font(size, True); tw = d.textlength(text, font=f)
    d.text((x + (w - tw) / 2, y + h / 2 - size / 2 - 2), text, fill="#1a202c", font=f)

CASES = []
DESCRIBE = "describe"   # production prompt from lecture_kit, filled in by the runner

def add(cid, dim, kind, img=None, prompt=DESCRIBE, media=None, **truth):
    path = None
    if img is not None:
        path = OUT / f"{cid}.jpg"; img.save(path, quality=90)
    t = {"phrases": [], "numbers": [], "order": [], "pairs": [], "expect_skip": False,
         "expect_absent": False, "forbidden": []}
    t.update(truth)
    CASES.append({"id": cid, "dim": dim, "kind": kind,
                  "media": [str(p.name) for p in (media or ([path] if path else []))],
                  "prompt": prompt, "truth": t})

def gen():
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # ---------------- fidelity ----------------
    img, d = canvas(); title(d, "What Makes a Product AI-Native?")
    bullets = ["Learns from every user interaction", "Output quality improves with data volume",
               "Uncertainty is surfaced, not hidden", "Humans stay in the loop for high-stakes calls"]
    for i, b in enumerate(bullets): d.text((90, 150 + i * 80), "•  " + b, fill="#2d3748", font=font(34))
    add("fid_bullets_01", "fidelity", "bullets", img, phrases=["What Makes a Product AI-Native?"] + bullets)

    img, d = canvas("#1a202c"); title(d, "Three Horizons of AI Adoption", "#f7fafc")
    items = [("H1", "Automate existing workflows", "6-12 months"), ("H2", "Augment decision making", "12-24 months"),
             ("H3", "Create new AI-first offerings", "24-36 months")]
    for i, (h, t_, dur) in enumerate(items):
        d.text((90, 160 + i * 120), h, fill="#63b3ed", font=font(44, True))
        d.text((190, 165 + i * 120), t_, fill="#f7fafc", font=font(34))
        d.text((190, 210 + i * 120), dur, fill="#a0aec0", font=font(26))
    add("fid_dark_horizons", "fidelity", "dark_theme", img, phrases=["Three Horizons of AI Adoption"] + [x[1] for x in items],
        numbers=["6-12", "12-24", "24-36"])

    img, d = canvas(); title(d, "Model Evaluation: Precision vs Recall")
    vals = [("Precision", 0.82, "#2b6cb0"), ("Recall", 0.64, "#c05621"), ("F1", 0.72, "#2f855a")]
    for i, (lab, v, col) in enumerate(vals):
        x = 160 + i * 340; h = int(v * 440)
        d.rectangle([x, 650 - h, x + 180, 650], fill=col)
        d.text((x + 20, 660), lab, fill="#1a202c", font=font(28))
        d.text((x + 50, 610 - h), f"{v:.2f}", fill="#1a202c", font=font(30, True))
    add("fid_bar_chart", "fidelity", "bar_chart", img, phrases=["Model Evaluation: Precision vs Recall", "Precision", "Recall", "F1"],
        numbers=["0.82", "0.64", "0.72"])

    img, d = canvas(); title(d, "Cost per 1K Requests by Model Tier")
    rows = [("Tier", "Latency (ms)", "Cost ($)", "Accuracy"), ("Small", "120", "0.04", "78%"),
            ("Medium", "340", "0.21", "86%"), ("Large", "910", "1.35", "92%")]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            x, y = 90 + c * 280, 140 + r * 110
            d.rectangle([x, y, x + 280, y + 110], outline="#4a5568", width=2, fill="#edf2f7" if r == 0 else "white")
            d.text((x + 24, y + 38), cell, fill="#1a202c", font=font(30, r == 0))
    add("fid_table", "fidelity", "table", img, phrases=["Cost per 1K Requests by Model Tier", "Small", "Medium", "Large"],
        numbers=["120", "340", "910", "0.04", "0.21", "1.35", "78", "86", "92"],
        pairs=[["Small", "0.04"], ["Medium", "340"], ["Large", "92"]])

    img, d = canvas(); title(d, "North-Star Metric Dashboard")
    kpis = [("Weekly Active Users", "48,250"), ("Task Success Rate", "91.4%"), ("Cost per Task", "$0.037"), ("CSAT", "4.6 / 5")]
    for i, (k, v) in enumerate(kpis):
        x, y = 80 + (i % 2) * 580, 150 + (i // 2) * 260
        d.rounded_rectangle([x, y, x + 540, y + 220], radius=16, fill="#f0fff4", outline="#2f855a", width=3)
        d.text((x + 30, y + 30), k, fill="#276749", font=font(30))
        d.text((x + 30, y + 100), v, fill="#1a202c", font=font(64, True))
    add("fid_kpi_tiles", "fidelity", "kpi_tiles", img, phrases=[k for k, _ in kpis], numbers=["48,250", "91.4", "0.037", "4.6"])

    img, d = canvas(); title(d, "Offline vs Online Evaluation")
    small = ["Offline: held-out test set, F1, AUC-ROC, calibration error (ECE)",
             "Online: A/B test on conversion, guardrail metrics, p95 latency < 800 ms",
             "Human review: 200 sampled outputs per week, rubric of 5 criteria",
             "Drift monitoring: PSI > 0.2 on any top-10 feature triggers retraining",
             "Shadow mode: new model runs for 14 days before any traffic shift",
             "Rollback: automatic if error rate exceeds 1.5% for 10 minutes"]
    for i, s in enumerate(small): d.text((70, 140 + i * 58), s, fill="#2d3748", font=font(22))
    add("fid_small_dense", "fidelity", "small_font_dense", img, phrases=["Offline vs Online Evaluation"],
        numbers=["200", "5", "0.2", "10", "14", "1.5", "800"])

    img, d = canvas(); title(d, "Expected Value of an AI Feature")
    d.text((90, 180), "EV = P(success) × Value − Cost", fill="#1a202c", font=font(52, True))
    d.text((90, 300), "P(success) = 0.35", fill="#2d3748", font=font(36))
    d.text((90, 360), "Value = $1.2M / year", fill="#2d3748", font=font(36))
    d.text((90, 420), "Cost = $180K", fill="#2d3748", font=font(36))
    d.text((90, 500), "EV = $240K", fill="#c53030", font=font(44, True))
    add("fid_formula", "fidelity", "formula", img, phrases=["Expected Value of an AI Feature"], numbers=["0.35", "1.2", "180", "240"])

    img, d = canvas("#f7f7f7"); title(d, "Data Flywheel", "#4a4a4a")
    for i, s in enumerate(["More users", "More interaction data", "Better model", "Better product"]):
        d.text((110, 160 + i * 90), f"{i+1}. {s}", fill="#9a9a9a", font=font(34))   # low contrast
    add("fid_low_contrast", "fidelity", "low_contrast", img, phrases=["Data Flywheel", "More users", "More interaction data", "Better model", "Better product"])

    img, d = canvas(); title(d, "Model Accuracy by Quarter")
    q = [("Q1", 71), ("Q2", 76), ("Q3", 83), ("Q4", 79)]
    pts = [(180 + i * 300, 640 - (v - 60) * 16) for i, (_, v) in enumerate(q)]
    d.line(pts, fill="#2b6cb0", width=6)
    for (lab, v), (x, y) in zip(q, pts):
        d.ellipse([x - 10, y - 10, x + 10, y + 10], fill="#2b6cb0")
        d.text((x - 20, y - 60), f"{v}%", fill="#1a202c", font=font(30, True)); d.text((x - 20, 660), lab, fill="#1a202c", font=font(28))
    add("fid_line_chart", "fidelity", "line_chart", img, phrases=["Model Accuracy by Quarter", "Q1", "Q4"], numbers=["71", "76", "83", "79"])

    # ---------------- structure ----------------
    img, d = canvas(); title(d, "ML Product Lifecycle")
    steps = ["Problem framing", "Data collection", "Model training", "Evaluation", "Deployment"]
    for i, s in enumerate(steps):
        x = 40 + i * 250; box(d, x, 300, 210, 110, s, size=22)
        if i < len(steps) - 1: arrow(d, x + 212, 355, x + 248, 355)
    add("str_flowchart", "structure", "flowchart", img, phrases=steps, order=steps)

    img, d = canvas(); title(d, "Prioritisation: Impact vs Effort")
    d.line([(640, 130), (640, 690)], fill="#2d3748", width=3); d.line([(120, 410), (1180, 410)], fill="#2d3748", width=3)
    quads = {"Quick Wins": (140, 140, ["Smart autocomplete", "FAQ chatbot"]),
             "Big Bets": (660, 140, ["Personalised tutor", "Voice assistant"]),
             "Fill-ins": (140, 430, ["Tag suggestions"]),
             "Money Pits": (660, 430, ["Custom foundation model"])}
    for q_, (x, y, its) in quads.items():
        d.text((x, y), q_, fill="#c05621", font=font(32, True))
        for j, it in enumerate(its): d.text((x + 10, y + 60 + j * 50), "– " + it, fill="#1a202c", font=font(28))
    d.text((40, 400), "Impact", fill="#4a5568", font=font(22)); d.text((1110, 680), "Effort →", fill="#4a5568", font=font(22))
    add("str_2x2_matrix", "structure", "matrix_2x2", img, phrases=list(quads),
        pairs=[[q_, it] for q_, (_, _, its) in quads.items() for it in its])

    img, d = canvas(); title(d, "Onboarding Funnel")
    stages = [("Visited", "10,000"), ("Signed up", "2,400"), ("Activated", "1,150"), ("Paid", "310")]
    for i, (s, n) in enumerate(stages):
        w = 1000 - i * 200; x = (W - w) // 2; y = 140 + i * 135
        d.rectangle([x, y, x + w, y + 110], fill=["#bee3f8", "#90cdf4", "#63b3ed", "#3182ce"][i])
        d.text((x + 30, y + 35), f"{s}: {n}", fill="#1a202c", font=font(34, True))
    add("str_funnel", "structure", "funnel", img, phrases=[s for s, _ in stages], numbers=[n for _, n in stages],
        order=[s for s, _ in stages], pairs=[[s, n] for s, n in stages])

    img, d = canvas(); title(d, "Responsible AI Review Cycle")
    cyc = ["Identify risks", "Measure harms", "Mitigate", "Monitor"]
    cx, cy, r = 640, 420, 220
    pos = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in [-math.pi / 2, 0, math.pi / 2, math.pi]]
    for i, (x, y) in enumerate(pos): box(d, int(x - 120), int(y - 45), 240, 90, cyc[i], fill="#fefcbf", size=22)
    for i in range(4):
        (x1, y1), (x2, y2) = pos[i], pos[(i + 1) % 4]
        arrow(d, int(x1 + (x2 - x1) * 0.35), int(y1 + (y2 - y1) * 0.35), int(x1 + (x2 - x1) * 0.62), int(y1 + (y2 - y1) * 0.62))
    add("str_cycle", "structure", "cycle", img, phrases=cyc, order=cyc)

    img, d = canvas(); title(d, "AI Product Team Structure")
    box(d, 520, 130, 240, 80, "Head of AI", size=24)
    kids = ["Product Manager", "ML Engineering", "Data Science"]
    for i, k in enumerate(kids):
        x = 120 + i * 400; box(d, x, 330, 280, 80, k, fill="#f0fff4", size=22); arrow(d, 640, 212, x + 140, 328)
    grand = {"ML Engineering": ["MLOps", "Inference"], "Data Science": ["Experimentation"]}
    for i, k in enumerate(kids):
        for j, g in enumerate(grand.get(k, [])):
            x = 120 + i * 400 + j * 175 - 30; box(d, x, 540, 170, 70, g, fill="#faf5ff", size=20); arrow(d, 120 + i * 400 + 140, 412, x + 70, 538)
    add("str_org_tree", "structure", "hierarchy", img, phrases=["Head of AI"] + kids + ["MLOps", "Inference", "Experimentation"],
        pairs=[["ML Engineering", "MLOps"], ["ML Engineering", "Inference"], ["Data Science", "Experimentation"]])

    img, d = canvas(); title(d, "Roadmap 2027")
    ms = [("Jan", "Discovery"), ("Apr", "MVP launch"), ("Jul", "Beta"), ("Oct", "General availability")]
    d.line([(100, 400), (1180, 400)], fill="#2d3748", width=5)
    for i, (m, e) in enumerate(ms):
        x = 150 + i * 320; d.ellipse([x - 14, 386, x + 14, 414], fill="#805ad5")
        d.text((x - 25, 340), m, fill="#1a202c", font=font(30, True)); d.text((x - 60, 440), e, fill="#1a202c", font=font(26))
    add("str_timeline", "structure", "timeline", img, phrases=[e for _, e in ms], order=[e for _, e in ms],
        pairs=[[m, e] for m, e in ms])

    # ---------------- hallucination guard ----------------
    add("hal_no_image", "hallucination", "no_image", None, expect_skip=True, forbidden=["precision", "recall", "revenue", "roadmap"])

    img, _ = canvas(); add("hal_blank_white", "hallucination", "blank", img, expect_skip=True)

    img = Image.effect_noise((W, H), 40).convert("RGB").filter(ImageFilter.GaussianBlur(3))
    add("hal_noise", "hallucination", "noise", img, expect_skip=True)

    img, d = canvas("#2d3748")       # talking head: silhouette + player chrome, no text content
    d.ellipse([520, 140, 760, 400], fill="#e2c9a6"); d.rounded_rectangle([420, 380, 860, 720], radius=80, fill="#2b6cb0")
    d.rectangle([0, 660, W, H], fill="#000000"); d.polygon([(30, 675), (30, 705), (55, 690)], fill="white")
    d.rectangle([80, 686, 1100, 694], fill="#718096"); d.rectangle([80, 686, 520, 694], fill="#e53e3e")
    add("hal_talking_head", "hallucination", "talking_head", img, expect_skip=True)

    img, d = canvas(); title(d, "Model Evaluation: Precision vs Recall")
    for i, (lab, v, col) in enumerate(vals[:2]):
        x = 260 + i * 440; h = int(v * 440); d.rectangle([x, 650 - h, x + 200, 650], fill=col)
        d.text((x + 20, 660), lab, fill="#1a202c", font=font(28)); d.text((x + 60, 610 - h), f"{v:.2f}", fill="#1a202c", font=font(30, True))
    add("hal_absent_value", "hallucination", "absent_value", img,
        prompt="What is the F1 score shown on this slide? If it is not shown, say exactly: NOT SHOWN.",
        expect_absent=True, forbidden=["0.72", "0.73", "0.71"])

    # ---------------- video ----------------
    frames = []
    vid_titles = ["Week 3: Data Strategy", "Build vs Buy vs Partner", "Data Moats Are Overrated", "Key Takeaways"]
    for i, t_ in enumerate(vid_titles):
        img, d = canvas(); title(d, t_); d.text((90, 200), f"Slide {i+1} of 4", fill="#4a5568", font=font(30))
        p = OUT / f"vid_frame_{i+1}.jpg"; img.save(p, quality=88); frames.append(p)
    add("vid_frames_multi", "video", "multi_image", None, media=frames,
        prompt="These are frames sampled in order from one lecture video. List the title of each slide in order, one per line.",
        phrases=vid_titles, order=vid_titles)
    mp4 = OUT / "vid_clip.mp4"
    if shutil.which("ffmpeg"):
        lst = OUT / "frames.txt"
        lst.write_text("".join(f"file '{p.name}'\nduration 2\n" for p in frames) + f"file '{frames[-1].name}'\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", "fps=2,format=yuv420p", "-c:v", "libx264", str(mp4)], check=True, cwd=OUT)
        CASES.append({"id": "vid_native_mp4", "dim": "video", "kind": "native_video", "media": [], "video": mp4.name,
                      "prompt": "This is a short lecture video. List the title of each slide in order, one per line.",
                      "truth": {"phrases": vid_titles, "numbers": [], "order": vid_titles, "pairs": [],
                                "expect_skip": False, "expect_absent": False, "forbidden": []}})

    (OUT / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in CASES) + "\n")
    by = {}
    for c in CASES: by[c["dim"]] = by.get(c["dim"], 0) + 1
    print(f"{len(CASES)} cases -> {OUT}  {by}")

if __name__ == "__main__":
    gen()
