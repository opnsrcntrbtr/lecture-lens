#!/usr/bin/env python3
"""Find the lecture player inside full-screen captures by what changes.

Across keyframes of one viewing session, the browser tabs, address bar and
course sidebar are static; the video player is what changes. The union of
per-pair difference boxes is therefore the player (plus its controls). Cropping
to it removes page chrome from what the vision model sees — the source of the
'sidebar text in slide notes' pollution the stress eval found — and cuts image
tokens.
"""
from PIL import Image, ImageChops

def player_box(paths, thresh=28, work=800, min_frac=0.12, max_frac=0.92, margin=0.015):
    """Bounding box (in original pixels) of the region that changes across frames,
    or None when it cannot be trusted (too few frames, nothing or everything moved)."""
    if len(paths) < 2:
        return None
    ims = [Image.open(p).convert("L") for p in paths]
    W, H = ims[0].size
    if any(im.size != (W, H) for im in ims):
        return None
    scale = work / max(W, H)
    small = [im.resize((max(1, int(W * scale)), max(1, int(H * scale)))) for im in ims]
    box = None
    for a, b in zip(small, small[1:]):
        bb = ImageChops.difference(a, b).point(lambda v: 255 if v > thresh else 0).getbbox()
        if bb:
            box = bb if box is None else (min(box[0], bb[0]), min(box[1], bb[1]), max(box[2], bb[2]), max(box[3], bb[3]))
    if box is None:
        return None
    x0, y0, x1, y1 = (int(v / scale) for v in box)
    frac = (x1 - x0) * (y1 - y0) / (W * H)
    if not (min_frac <= frac <= max_frac):
        return None
    mx, my = int(W * margin), int(H * margin)
    return (max(0, x0 - mx), max(0, y0 - my), min(W, x1 + mx), min(H, y1 + my))

def crop_to(path, box, dest):
    Image.open(path).crop(box).save(dest, quality=90)
    return dest

if __name__ == "__main__":
    # self-test against the stress geometry: player at x=760,y=260, 1663x935 (+90px controls)
    import sys, tempfile
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from gen_stress import chrome_frame
    src = Path(__file__).parent / "cases"
    tmp = Path(tempfile.mkdtemp()); frames = []
    # a realistic session: consecutive slides with different layouts and themes
    for i, name in enumerate(["fid_bullets_01", "fid_dark_horizons", "fid_bar_chart", "fid_table", "str_flowchart", "str_2x2_matrix"]):
        f = chrome_frame(Image.open(src / f"{name}.jpg").convert("RGB")); p = tmp / f"s{i}.jpg"; f.save(p, quality=60); frames.append(p)
    b = player_box(frames); print("detected box:", b)
    px, py, pw, ph = 760, 260, int(3024 * 0.55), int(int(3024 * 0.55) * 9 / 16)
    ok = b and b[0] <= px + 40 and b[1] <= py + 200 and b[2] >= px + pw - 40 and (b[2] - b[0]) < 3024 * 0.75
    print("player enclosed, sidebar excluded:", bool(ok)); sys.exit(0 if ok else 1)
