#!/usr/bin/env python3
"""Compare a session transcript with a reference transcript (Zoom's own export)
and merge the two. No model calls.

    transcript_ref.py <lecture folder> <zoom transcript.txt> [--start HH:MM:SS]

Writes into the lecture folder:
  transcript_merged.md   reference text (with speakers) while it lasts, then ours
  transcript_eval.json   word error rate of our transcript on the overlap

WER here counts the reference as truth. Zoom's captions are not perfect either,
so read it as "how far we are from Zoom", mostly a measure of dropped words.
"""
import json, re, sys
from pathlib import Path

# Only true disfluencies. "so", "right", "you know" are often real words, and
# dropping them hid real deletions (the earlier list removed "you" and "all").
DISFLUENCIES = {"uh", "um", "uhm", "umm", "hmm", "hm", "mm", "mhm", "er", "erm", "ah", "eh"}

_CONTRACTIONS = [(r"\bwon't\b", "will not"), (r"\bcan't\b", "can not"), (r"\blet's\b", "let us"),
                 (r"n't\b", " not"), (r"'re\b", " are"), (r"'ve\b", " have"), (r"'ll\b", " will"),
                 (r"'m\b", " am"), (r"'d\b", " would"),
                 (r"\b(it|that|there|what|here|he|she|who|where)'s\b", r"\1 is")]
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}


def _numbers(tokens):
    """Spelled-out cardinals to digits: 'twenty five' -> '25',
    'one hundred and twenty' -> '120', 'two thousand' -> '2000'."""
    out, i = [], 0
    while i < len(tokens):
        if tokens[i] not in _ONES and tokens[i] not in _TENS:
            out.append(tokens[i]); i += 1; continue
        total, cur, j, prev = 0, 0, i, None
        while j < len(tokens):
            t = tokens[j]
            # "twenty five" joins; "nine eleven" or "eight two" are two numbers
            if t in _ONES and prev in ("ones", "teen"): break
            if t in _TENS and prev in ("ones", "teen", "tens"): break
            if t in _ONES:
                cur += _ONES[t]; prev = "teen" if _ONES[t] >= 10 else "ones"
            elif t in _TENS:
                cur += _TENS[t]; prev = "tens"
            elif t == "hundred" and prev != "hundred":
                cur = max(1, cur) * 100; prev = "hundred"
            elif t in _SCALES and t != "hundred":
                total += max(1, cur) * _SCALES[t]; cur = 0; prev = "scale"
            elif (t == "and" and tokens[j - 1] in _SCALES and j + 1 < len(tokens)
                  and (tokens[j + 1] in _ONES or tokens[j + 1] in _TENS)):
                pass    # "one hundred and twenty"; but "one and two" stays two numbers
            else:
                break
            j += 1
        out.append(str(total + cur)); i = j
    return out


def normalize(text):
    """Text normalisation for word error rate, after the English normaliser
    published with Whisper (Radford et al. 2023): lower case, drop bracketed
    annotations, expand contractions, spelled-out numbers to digits, strip
    punctuation (keeping decimals such as 0.82), drop disfluencies. Reference
    and hypothesis go through the same function, so WER counts words, not
    formatting differences."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    for pat, rep in _CONTRACTIONS:
        t = re.sub(pat, rep, t)
    t = t.replace("%", " percent ").replace("-", " ")
    t = re.sub(r"(?<!\d)[.,](?!\d)|[^a-z0-9., ]", " ", t)          # keep 0.82 and 10,000
    toks = [w.replace(",", "") for w in t.split() if w.strip(".,")]
    toks = [w.strip(".") for w in toks]
    return [w for w in _numbers(toks) if w and w not in DISFLUENCIES]


def sec(h):
    a, b, c = map(int, h.split(":")); return a * 3600 + b * 60 + c

def hms(s):
    return "%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)

def words(text, drop_fillers=True):
    """Tokens for WER: normalised (normalize) or, with drop_fillers=False, only
    lower-cased with punctuation removed (the raw view, kept for comparison)."""
    if drop_fillers:
        return normalize(text)
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", " ")).split()

def align(ref, hyp):
    """Levenshtein alignment -> (substitutions, deletions, insertions)."""
    n, m = len(ref), len(hyp)
    prev = [(j, 0, 0, j) for j in range(m + 1)]          # (cost, S, D, I)
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                best = prev[j - 1]
            else:
                c, s, d, k = prev[j - 1]; best = (c + 1, s + 1, d, k)
            c, s, d, k = prev[j]
            if c + 1 < best[0]: best = (c + 1, s, d + 1, k)
            c, s, d, k = cur[j - 1]
            if c + 1 < best[0]: best = (c + 1, s, d, k + 1)
            cur.append(best)
        prev = cur
    return prev[m][1:]

def parse_zoom(path):
    t = Path(path).read_text()
    return [(sec(a), sec(b), s.strip(), x.strip()) for a, b, s, x in
            re.findall(r"(\d\d:\d\d:\d\d) --> (\d\d:\d\d:\d\d)\n([^:\n]+): (.*)", t)]

def parse_ours(path):
    out = []
    for l in Path(path).read_text().splitlines():
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] (.*)", l)
        if m: out.append((sec(m.group(1)), m.group(2)))
    return out

def main():
    folder, zoom = Path(sys.argv[1]), sys.argv[2]
    man = json.loads((folder / "manifest.json").read_text())
    import datetime as dt
    t0 = dt.datetime.fromisoformat(man["start"]).astimezone()
    T0 = t0.hour * 3600 + t0.minute * 60 + t0.second      # session start, local clock seconds
    z, ours = parse_zoom(zoom), parse_ours(folder / "transcript.md")
    z_end = z[-1][1]
    # overlap: from our first segment to the end of the reference, minus capture gaps > 75 s
    gaps = [(ours[i][0] + T0, ours[i + 1][0] + T0) for i in range(len(ours) - 1) if ours[i + 1][0] - ours[i][0] > 75]
    lo = ours[0][0] + T0 + 30
    def in_gap(s): return any(a - 5 <= s <= b + 30 for a, b in gaps)
    ref_txt = " ".join(x for s, e, _, x in z if s >= lo and e <= z_end - 15 and not in_gap(s))
    hyp_txt = " ".join(x for t, x in ours if lo - 15 <= t + T0 < z_end - 30 and not in_gap(t + T0))
    res = {"reference": Path(zoom).name, "overlap": [hms(lo), hms(z_end)],
           "capture_gaps": [[hms(a), hms(b)] for a, b in gaps]}
    # "normalized" is the headline number (Whisper-style normaliser); "all_words" is the
    # raw view. Files written before this change had "content_words" instead.
    for name, drop in (("normalized", True), ("all_words", False)):
        r, h = words(ref_txt, drop), words(hyp_txt, drop)
        s, d, i = align(r, h)
        res[name] = {"ref_words": len(r), "hyp_words": len(h), "substitutions": s, "deletions": d,
                     "insertions": i, "wer": round((s + d + i) / max(1, len(r)), 3),
                     "deletion_rate": round(d / max(1, len(r)), 3)}
    (folder / "transcript_eval.json").write_text(json.dumps(res, indent=2))
    # merged transcript: reference (speaker-labelled) to its end, then ours
    lines = [f"# Transcript (merged) — {man['title']}", "",
             f"_Zoom's own transcript until {hms(z_end)}, then this Mac's capture. Timecodes are clock time (IST)._", ""]
    for s, e, who, x in z:      # every line carries its own time and speaker, so notes can cite it
        lines.append(f"[{hms(s)}] {who}: {x}")
    lines.append(f"\n---\n_From {hms(z_end)}: screenpipe capture (no speaker labels; words may be missing)._\n")
    for t, x in ours:
        if t + T0 >= z_end - 10: lines.append(f"[{hms(t + T0)}] {x}")
    (folder / "transcript_merged.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(res))

if __name__ == "__main__":
    main()
