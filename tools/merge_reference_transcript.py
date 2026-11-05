"""Merge a reference transcript without timecodes (e.g. a dictation app's 'Speaker N: text'
export) onto this Mac's timed whisper segments, giving lines with clock times.

Usage: merge_reference_transcript.py <live_dir> <reference.txt> <out.md> --keep 1,5 --label 1="Instructor A" ...
Speakers not in --keep become 'Attendee'. Where the reference has no coverage, whisper lines fill in.
"""
import argparse, datetime as dt, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def word_opcodes(a, b):
    """(tag, a_start, a_end, b_start, b_end) edit opcodes between two word lists:
    rapidfuzz when installed (fast on a 4-hour class), else the standard library."""
    try:
        from rapidfuzz.distance import Levenshtein
        return [(o.tag, o.src_start, o.src_end, o.dest_start, o.dest_end) for o in Levenshtein.opcodes(a, b)]
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


LOCAL_TZ = dt.datetime.now().astimezone().tzinfo   # times are shown in this Mac's time zone
MIN_RUN = 3          # equal words in a row before a match counts as an anchor
GAP_S = 90           # whisper fills in where no reference line is within this many seconds


def covered(t, line_times, gap_s=GAP_S):
    """True when some reference line is within gap_s seconds of t."""
    import bisect
    ts = sorted(x for x in line_times if x)
    i = bisect.bisect_left(ts, t)
    near = [ts[j] for j in (i - 1, i) if 0 <= j < len(ts)]
    return any(abs((x - t).total_seconds()) <= gap_s for x in near)


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("live_dir"); ap.add_argument("reference"); ap.add_argument("out")
    ap.add_argument("--keep", default="1"); ap.add_argument("--label", action="append", default=[])
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    keep = set(a.keep.split(","))
    labels = dict(x.split("=", 1) for x in a.label)
    segs = [json.loads(l) for l in open(Path(a.live_dir) / "whisper_segments.jsonl")]
    # whisper words with times
    ww, wt = [], []
    for s in segs:
        t = dt.datetime.fromisoformat(s["t"])
        for w in s["text"].split():
            n = norm(w)
            if n: ww.append(n); wt.append(t)
    # reference lines -> words
    ref = []
    for line in open(a.reference, encoding="utf-8"):
        m = re.match(r"\s*Speaker\s+(\d+)\s*:\s*(.*)", line)
        if m and m.group(2).strip():
            ref.append((m.group(1), m.group(2).strip()))
    rw, owner = [], []
    for i, (_, text) in enumerate(ref):
        for w in text.split():
            n = norm(w)
            if n: rw.append(n); owner.append(i)
    # align reference words to whisper words
    t_of = [None] * len(rw)
    for tag, i1, i2, j1, _ in word_opcodes(rw, ww):
        # short "equal" runs are mostly chance matches of common words far away; trust runs of 3+
        if tag == "equal" and i2 - i1 >= MIN_RUN:
            for k in range(i2 - i1):
                t_of[i1 + k] = wt[j1 + k]
    # line time = first matched word in the line; interpolate gaps from neighbours
    line_t = [None] * len(ref)
    for j, i in enumerate(owner):
        if line_t[i] is None and t_of[j] is not None:
            line_t[i] = t_of[j]
    known = [i for i, t in enumerate(line_t) if t]
    for i in range(len(ref)):
        if line_t[i] is None and known:
            prev = max((k for k in known if k < i), default=None); nxt = min((k for k in known if k > i), default=None)
            line_t[i] = line_t[prev] if prev is not None else line_t[nxt]
    # keep time order monotonic (alignment noise)
    for i in range(1, len(ref)):
        if line_t[i] and line_t[i - 1] and line_t[i] < line_t[i - 1]:
            line_t[i] = line_t[i - 1]
    start, end = (min(t for t in line_t if t), max(t for t in line_t if t)) if known else (None, None)
    rows = []
    for s in segs:   # whisper wherever the reference has no line nearby (before, after, or a gap inside it)
        t = dt.datetime.fromisoformat(s["t"])
        if not covered(t, line_t):
            rows.append((t, "Speaker", s["text"].strip()))
    for (spk, text), t in zip(ref, line_t):
        rows.append((t, labels.get(spk, f"Speaker {spk}") if spk in keep else "Attendee", text))
    rows.sort(key=lambda r: r[0])
    matched = sum(1 for t in t_of if t) / max(1, len(t_of))
    head = (f"# Transcript (merged) — {a.title}\n\n_Reference transcript (dictation export, speakers relabelled; "
            f"non-instructors shown as Attendee) aligned to this Mac's capture "
            f"({matched:.0%} of its words matched, {start.astimezone(LOCAL_TZ):%H:%M}–{end.astimezone(LOCAL_TZ):%H:%M}); "
            f"this Mac's capture (Speaker) fills any gap over {GAP_S} s. Timecodes are this Mac's clock time._\n\n")
    Path(a.out).write_text(head + "\n".join(f"[{t.astimezone(LOCAL_TZ):%H:%M:%S}] {who}: {txt}" for t, who, txt in rows) + "\n")
    print(json.dumps({"lines": len(rows), "reference_lines": len(ref), "matched_words": round(matched, 3),
                      "span": [start.astimezone(LOCAL_TZ).strftime("%H:%M:%S"), end.astimezone(LOCAL_TZ).strftime("%H:%M:%S")]}))


if __name__ == "__main__":
    main()
