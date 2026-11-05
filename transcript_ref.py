#!/usr/bin/env python3
"""Compare a session transcript with a reference transcript (Zoom's own export)
and merge the two. No model calls.

    transcript_ref.py <lecture folder> <zoom transcript.txt|.md>

Two reference formats are read:
  - Zoom's caption export (.txt/.vtt): "HH:MM:SS --> HH:MM:SS" then "Speaker: text"
  - Zoom's AI summary export (.md): per recording part, "## Summary" then "## Transcript"
    with "[MMM:SS] Speaker N: text" lines whose clock restarts at 0 in each part.
    Each part is placed on the clock by matching its word 4-grams against our
    transcript (a vote over time offsets), so no start time has to be typed in.

Writes into the lecture folder:
  transcript_merged.md   reference text (with speakers) while it lasts, then ours
  transcript_eval.json   word error rate of our transcript on the overlap

WER here counts the reference as truth. Zoom's captions are not perfect either,
so read it as "how far we are from Zoom", mostly a measure of dropped words.
"""
import json, re, sys
from pathlib import Path

FILLERS = {"uh", "um", "you", "know", "right", "so", "like", "okay", "yeah", "alright", "all"}

def sec(h):
    a, b, c = map(int, h.split(":")); return a * 3600 + b * 60 + c

def hms(s):
    return "%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)

def words(text, drop_fillers=True):
    w = re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", " ")).split()
    return [x for x in w if not drop_fillers or x not in FILLERS]

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

def _msec(stamp):
    parts = list(map(int, stamp.split(":")))
    return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_zoom_md(text):
    """Zoom AI summary markdown -> (parts, summaries). A part is a list of (rel_s, speaker, text);
    a new part starts at each "## Transcript" heading. summaries: [(part heading, summary markdown)]."""
    parts, summaries, cur, heading, buf, mode = [], [], None, "", [], None
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            heading = line[2:].strip()
        if line.strip() == "## Summary":
            mode, buf = "summary", []
            continue
        if line.strip() == "## Transcript":
            if mode == "summary":
                summaries.append((heading, "\n".join(buf).strip()))
            mode, cur = "transcript", []
            parts.append(cur)
            continue
        m = re.match(r"\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s*([^:]{1,40}):\s*(.*)", line)
        if mode == "transcript" and m:
            cur.append((_msec(m.group(1)), m.group(2).strip(), m.group(3).strip()))
        elif mode == "summary":
            buf.append(line)
    return [p for p in parts if p], summaries


def _grams(text, n=4):
    w = words(text)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def align_offset(part, ours, bucket=10):
    """Clock offset (s) that best maps a reference part onto our (t, text) lines: every shared
    word 4-gram votes for (our time - reference time), rounded to `bucket` s; the mode wins.
    Returns (offset, votes); votes == 0 means no overlap was found."""
    index = {}
    for t, x in ours:
        for g in _grams(x):
            index.setdefault(g, []).append(t)
    votes = {}
    for r, _, x in part:
        for g in _grams(x):
            ts = index.get(g, ())
            if 0 < len(ts) <= 3:                       # common phrases vote everywhere; skip them
                for t in ts:
                    k = round((t - r) / bucket)
                    votes[k] = votes.get(k, 0) + 1
    if not votes:
        return 0, 0
    k = max(votes, key=lambda k: votes[k] + 0.5 * (votes.get(k - 1, 0) + votes.get(k + 1, 0)))
    return k * bucket, votes[k]


def zoom_md_entries(text, ours):
    """Place every part on our clock and return (start, end, speaker, text) like parse_zoom."""
    parts, _ = parse_zoom_md(text)
    out = []
    for part in parts:
        off, n = align_offset(part, ours)
        if n == 0:
            continue
        for i, (r, who, x) in enumerate(part):
            end = part[i + 1][0] if i + 1 < len(part) else r + 10
            out.append((r + off, end + off, who, x))
    return sorted(out)


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
    ours = parse_ours(folder / "transcript.md")
    raw = Path(zoom).read_text()
    if re.search(r"^\[\d{1,3}:\d{2}\]", raw, re.M):
        z = zoom_md_entries(raw, [(t + T0, x) for t, x in ours])
        _, summaries = parse_zoom_md(raw)
        if summaries:                       # the meeting app's own summary, kept beside our notes
            (folder / "zoom_summary.md").write_text("\n\n".join(f"## {h}\n\n{b}" for h, b in summaries) + "\n")
    else:
        z = parse_zoom(zoom)
    z_end = z[-1][1]
    # overlap: from our first segment to the end of the reference, minus capture gaps > 75 s
    gaps = [(ours[i][0] + T0, ours[i + 1][0] + T0) for i in range(len(ours) - 1) if ours[i + 1][0] - ours[i][0] > 75]
    lo = ours[0][0] + T0 + 30
    def in_gap(s): return any(a - 5 <= s <= b + 30 for a, b in gaps)
    ref_txt = " ".join(x for s, e, _, x in z if s >= lo and e <= z_end - 15 and not in_gap(s))
    hyp_txt = " ".join(x for t, x in ours if lo - 15 <= t + T0 < z_end - 30 and not in_gap(t + T0))
    res = {"reference": Path(zoom).name, "overlap": [hms(lo), hms(z_end)],
           "capture_gaps": [[hms(a), hms(b)] for a, b in gaps]}
    for name, drop in (("all_words", False), ("content_words", True)):
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
