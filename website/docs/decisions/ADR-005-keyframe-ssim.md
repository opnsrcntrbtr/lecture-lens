# ADR-005: confirm keyframe repeats with structural similarity

**Status:** Accepted on synthetic evidence; confirm on the next real build · **Date:** 2026-11-04 · **Deciders:** project maintainer

## Problem
`keyframes()` keeps one frame per slide. It called a frame a repeat when its 64-bit difference hash was within 12 bits of a kept frame, unless the on-screen text showed a new slide. Slides of one deck share a template, so the hash calls most of them the same, and the text check needs at least 40 distinct words, which few slides have. Each missed slide is missing from `slides.md` and from the notes' sources.

## Evidence
`eval/keyframe_bench.py` renders invented decks (12 slides from one template, astronomy and biology) and shows each slide three times as a recorder sees it: the cursor moves, a webcam inset changes, the frame is re-encoded at a random JPEG quality. Two harder conditions add a large talking-head inset whose content changes every view, and an animation build (each slide first appears with only its title and first bullet). Recall is the share of slides kept; precision is the share of kept frames that are not repeats.

| Condition (2 decks each) | v1: hash + text | SSIM ≥ 0.95 alone (mean) | v2: hash + text + changed cells |
| --- | --- | --- | --- |
| Short slide text | recall 0.17–0.42 | recall 1.00, precision 1.00 | recall 1.00, precision 1.00 |
| Long slide text | recall 0.42–0.58 | 1.00, 1.00 | 1.00, 1.00 |
| Large changing webcam | recall 0.33 | 1.00, precision 0.67–0.71 | 1.00, 1.00 |
| Builds + large webcam | recall 0.42, precision 0.83–1.00 | 1.00, precision 0.40–0.41 | 1.00, precision 0.50 |
| Time per candidate frame (pure Python) | 4 ms | 103 ms | 49 ms |

### How SSIM is pooled matters more than the threshold

A first version repeated a frame when its *mean* SSIM to a kept frame was at least 0.90, and scored perfectly on the table above. Measuring the distributions (`keyframe_bench.py --margin`) showed that was luck: the same slide under a large webcam scored as low as 0.84, and two different slides of one deck as high as 0.87 (0.90 with Pillow's built-in font). No mean-SSIM threshold separates them, at 160×90 or at 320×180.

Wang et al. (2004) compute SSIM locally and treat the map as the result; the mean is only one way to pool it. Counting *changed cells* instead (6×6 grid on a 192×108 grey thumbnail, a cell changed when its SSIM is below 0.8) separates every pair. A webcam inset changes at most 5 of the 36 cells, and a new slide changes at least 6. Margins on the light template and on a held-out dark (inverted) template, with DejaVu and with Pillow's built-in font:

| Deck | Same slide | Different slides | Margin (cells) |
| --- | --- | --- | --- |
| Light, DejaVu | 3–5 | 8–20 | +3 to +7 |
| Dark (held out), DejaVu | 3–4 | 11–21 | +7 to +8 |
| Light, built-in font | 3–5 | 6–18 | +1 to +3 |
| Dark (held out), built-in font | 3–4 | 6–18 | +2 to +4 |

The worst case is a one-cell margin. The cut-off is `STUDY_KEYFRAME_MAX_CHANGED` (default 5).

## Decision
Keep the hash as the first, cheap test. A frame the hash calls the same is a repeat only if at most 5 of its 36 grid cells changed (local SSIM below 0.8) against one of the last three kept frames that the hash also calls near. A slide shown again later in the session is kept again. Code: `lecture_kit.is_repeat`, `changed_cells`, `ssim` (keyframes-v2).

## Consequences
- More slides are kept, so more vision calls per build. The cap of 40 slides per session still holds.
- Animation builds now yield two frames per slide (the partial and the full one). That costs a vision call and loses nothing. Replacing the partial frame with the fuller one was tried and rejected: with a webcam inset, 3–8 cells change content-to-content during a build against 9–14 on a real slide change, too thin a margin when an error loses a slide.
- About 50 ms per candidate frame, against roughly 30 s per vision call.
- The evidence is synthetic: perfect on-screen text, two templates, invented content, a one-cell worst-case margin. Recall on a real class is the check that matters (plan item T5, on the Mac).

## References
Wang, Z., Bovik, A. C., Sheikh, H. R. and Simoncelli, E. P. (2004). Image quality assessment: from error visibility to structural similarity. *IEEE Transactions on Image Processing* 13(4), 600–612. doi:10.1109/TIP.2003.819861
