#!/usr/bin/env python3
"""Keyframe de-duplication benchmark on synthetic decks. No model, no capture.

    python3 eval/keyframe_bench.py [--json]

Each deck is N slides from one template (the hard case: a coarse image hash
calls two slides of one deck the same). Every slide is shown several times as a
recorder would see it: the cursor moves, a webcam inset changes, the frame is
re-encoded. A rule must keep exactly one frame per slide.

Rules compared, each fed the same frames in order, through the production code:
  v1          lecture_kit.is_repeat without thumbnails: dHash within 12 bits
              and no new on-screen text (text counts only with >= 40 tokens)
  ssim alone  repeat when structural similarity (Wang et al. 2004) to one of
              the last three kept frames is at least 0.95 (160x90 grey)
  v2          keyframe_step as keyframes() now calls it: v1, and at most 5 of
              36 grid cells changed (local SSIM < 0.8) against a recent
              hash-near kept frame

`--margin` prints, for each pooling, the gap between same-slide and
different-slide pairs, including a held-out dark template.

Scored per deck: slides recovered (recall) and kept frames that are duplicates
(precision). On-screen text is given exactly, as perfect OCR would; real OCR is
noisier, so the text check is if anything favoured here.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_kit as k  # noqa: E402

W, H = 1280, 720
TOPICS = {   # invented content only
    "astronomy": ["Main-sequence stars fuse hydrogen", "Red giants expand as the core contracts",
                  "White dwarfs cool over billions of years", "Neutron stars spin hundreds of times a second",
                  "Comets grow two tails near the sun", "Jupiter shields the inner planets",
                  "Saturn's rings are mostly water ice", "Exoplanets are found by transits",
                  "The Moon is moving away by 3.8 cm a year", "Mars has the tallest volcano known",
                  "Light from the Sun takes eight minutes", "Galaxies collide without stars colliding"],
    "biology": ["Mitochondria produce most ATP", "Ribosomes translate messenger RNA",
                "Enzymes lower activation energy", "Osmosis moves water across membranes",
                "Chloroplasts capture light energy", "DNA replication is semi-conservative",
                "Meiosis halves the chromosome number", "Antibodies bind specific antigens",
                "Neurons signal with action potentials", "Hormones act on distant targets",
                "Stem cells can differentiate", "Ecosystems cycle carbon and nitrogen"],
}


def _font(size, bold=False):
    for p in (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else []) + [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)   # Pillow >= 10.1: scalable built-in font


def slide(topic, i, rng, long_text=False):
    """One slide of a shared template: header bar, title, 3-5 bullets. Returns (image, text)."""
    facts = TOPICS[topic]
    img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill="#1f3a5f"); d.text((40, 25), f"{topic.title()} · Unit 2", fill="white", font=_font(34, True))
    title = facts[i]
    d.text((60, 130), title, fill="#1a202c", font=_font(44, True))
    bullets = [facts[(i + j) % len(facts)] for j in range(1, rng.randint(3, 5) + 1)]
    if long_text:
        bullets = [b + " — " + facts[(i * 3 + n) % len(facts)].lower() for n, b in enumerate(bullets)]
    for n, b in enumerate(bullets):
        d.text((90, 240 + n * 80), "• " + b, fill="#2d3748", font=_font(26 if long_text else 32))
    d.text((W - 120, H - 50), f"{i + 1}", fill="#718096", font=_font(24))
    return img, " ".join([title] + bullets)


def as_seen(img, rng, big_cam=False):
    """The slide as a recorder captures it: cursor, changing webcam inset, re-encoding.
    big_cam: a 400x225 talking-head inset whose content changes every view."""
    im = img.copy(); d = ImageDraw.Draw(im)
    if big_cam:
        x0, y0 = W - 420, H - 245
        d.rectangle([x0, y0, x0 + 400, y0 + 225], fill=(rng.randint(30, 90),) * 3)
        fx, fy = x0 + rng.randint(120, 220), y0 + rng.randint(30, 70)
        d.ellipse([fx, fy, fx + 90, fy + 110], fill=(225, 185, 155))
        d.rectangle([fx - 30, fy + 110, fx + 120, y0 + 225], fill=(rng.randint(40, 200), 60, 90))
    x, y = rng.randint(100, W - 100), rng.randint(150, H - 100)
    d.polygon([(x, y), (x + 18, y + 26), (x + 7, y + 24), (x, y + 34)], fill="black")
    shade = rng.randint(60, 200)
    d.rectangle([W - 260, H - 200, W - 20, H - 60], fill=(shade, shade - 20, shade - 40))
    d.ellipse([W - 175, H - 185, W - 105, H - 115], fill=(220, 180, 150))
    from io import BytesIO
    buf = BytesIO(); im.save(buf, "JPEG", quality=rng.randint(60, 90)); buf.seek(0)
    return Image.open(buf).convert("RGB")


def ssim(a, b):
    """SSIM of two images through the production code (lecture_kit.ssim)."""
    return k.ssim(k.grey_thumb(a), k.grey_thumb(b))


def deck(topic, n_slides=12, views=3, long_text=False, seed=0, big_cam=False, builds=False):
    """Frames in display order: [(image, text, slide_index)].
    builds: each slide first appears with only its title and first bullet, as an
    animation build; the build frames count as the same slide."""
    rng = random.Random(seed)
    frames = []
    for i in range(n_slides):
        img, text = slide(topic, i, rng, long_text)
        if builds:
            part = img.copy(); ImageDraw.Draw(part).rectangle([60, 320, W - 440, H - 60], fill="white")
            frames.append((as_seen(part, rng, big_cam), text.split(" • ")[0], i))
        frames += [(as_seen(img, rng, big_cam), text, i) for _ in range(views)]
    return frames


def run_rule(frames, rule):
    kept, state = [], {"hashes": [], "texts": [], "imgs": [], "thumbs": []}
    for img, text, idx in frames:
        if rule(img, text, state):
            continue
        kept.append(idx)
    return kept


def production(img, text, st, v1=False):
    """lecture_kit.keyframe_step, as keyframes() calls it; v1 = the rule before ADR-005."""
    h = k.dhash_image(img); toks = k._text_tokens(text)
    if v1:
        if k.is_repeat(h, toks, st["hashes"], st["texts"]):
            return True
        st["hashes"].append(h)
        if len(toks) >= k.MIN_SLIDE_TOKENS:
            st["texts"].append(toks)
        return False
    return k.keyframe_step(st, h, toks, k.grey_thumb(img))


def ssim_rule(threshold):
    def rule(img, text, st):
        th = k.grey_thumb(img)
        if any(k.ssim(th, prev) >= threshold for prev in st["imgs"][-3:]):   # a slide repeats in place
            return True
        st["imgs"].append(th)
        return False
    return rule


def score(kept, n_slides):
    distinct = len(set(kept))
    return {"kept": len(kept), "recall": distinct / n_slides, "precision": distinct / len(kept) if kept else 0.0}


RULES = {"v1 (dhash+text)": lambda i, t, s: production(i, t, s, v1=True),
         "ssim>=0.95 alone": ssim_rule(0.95),
         "v2 production": production}
CONDITIONS = {"short text": {}, "long text": {"long_text": True},
              "big webcam": {"big_cam": True}, "builds + big webcam": {"builds": True, "big_cam": True}}


def margins():
    """Separation of same-slide vs different-slide pairs per pooling, light and
    held-out dark (inverted) template. Positive margin = a threshold exists."""
    from PIL import ImageOps
    pool = {"mean SSIM (higher = same)": (lambda a, b: k.ssim(k.grey_thumb(a), k.grey_thumb(b)), True),
            "changed cells of 36 (lower = same)": (lambda a, b: k.changed_cells(k.grey_thumb(a), k.grey_thumb(b)), False)}
    out = {}
    for dark in (False, True):
        for topic in TOPICS:
            rng = random.Random(0)
            sl = [slide(topic, i, rng)[0] for i in range(10)]
            if dark:
                sl = [ImageOps.invert(s) for s in sl]
            views = [[as_seen(s, rng, big) for big in (False, True, True)] for s in sl]
            for name, (f, higher_same) in pool.items():
                same = [f(v[0], v[1]) for v in views] + [f(v[1], v[2]) for v in views]
                diff = [f(views[i][1], views[j][1]) for i in range(10) for j in range(i + 1, 10)]
                m = (min(same) - max(diff)) if higher_same else (min(diff) - max(same))
                out[f"{'dark' if dark else 'light'} {topic} / {name}"] = {
                    "same": [min(same), max(same)], "different": [min(diff), max(diff)], "margin": m}
    return out


def main(argv):
    import time
    if "--margin" in argv:
        for name, r in margins().items():
            print(f"{name:55} same {r['same'][0]:.3f}-{r['same'][1]:.3f}  different "
                  f"{r['different'][0]:.3f}-{r['different'][1]:.3f}  margin {r['margin']:+.3f}")
        return None
    results, timing = {}, {r: [0.0, 0] for r in RULES}
    for topic in TOPICS:
        for cond, kw in CONDITIONS.items():
            frames = deck(topic, seed=len(topic), **kw)
            res = {}
            for r, fn in RULES.items():
                t = time.perf_counter()
                res[r] = score(run_rule(frames, fn), 12)
                timing[r][0] += time.perf_counter() - t; timing[r][1] += len(frames)
            results[f"{topic} / {cond}"] = res
    ms = {r: round(1000 * t / n, 1) for r, (t, n) in timing.items()}
    if "--json" in argv:
        print(json.dumps({"results": results, "ms_per_frame": ms}, indent=1)); return results
    print("| deck (12 slides x 3 views) | " + " | ".join(RULES) + " |")
    print("|---|" + "---|" * len(RULES))
    for name, res in results.items():
        print(f"| {name} | " + " | ".join(f"R {v['recall']:.2f} · P {v['precision']:.2f}" for v in res.values()) + " |")
    print("| ms per frame | " + " | ".join(str(v) for v in ms.values()) + " |")
    return results


if __name__ == "__main__":
    main(sys.argv[1:])
