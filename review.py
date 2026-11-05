#!/usr/bin/env python3
"""Spaced review of a lecture's flashcards, with a pass/fail log.

    lecture review                 ask the cards that are due, oldest lecture first
    lecture review --stats         due counts and cards that keep failing
    lecture review --json --stats  the same, machine-readable

Why: practice testing and distributed practice are the two techniques rated
high utility in Dunlosky et al. (2013); cards are only useful if they are
reviewed on a schedule. The schedule is the FSRS-5 memory model (Ye, Su & Cao
2022, KDD; formulas and default weights from the open-spaced-repetition
"The Algorithm" page, FSRS-5 section). Answers are binary, as the study-method
loop asks: pass = Good (3), fail = Again (1).

State is never stored, only the log: lectures/<lecture>/reviews.jsonl, one
line per answer. The memory state of a card is the log replayed, so the log
is the single source of truth and a changed scheduler re-reads history.
Everything stays in the ignored lectures/ folder.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LECTURES = Path(os.path.expanduser(os.environ.get("STUDY_LECTURE_DIR", str(HERE / "lectures"))))

# ------------------------------------------------------------------ FSRS-5
# Default parameters w0..w18 of FSRS-5, as published.
W = (0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
     1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621)
DECAY = -0.5
FACTOR = 19 / 81          # so that R(S, S) = 0.9
AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RETENTION = float(os.environ.get("STUDY_REVIEW_RETENTION", "0.9"))
MAX_INTERVAL_DAYS = 365
LEECH_LAPSES = 3          # a card failed this often is probably a bad card, not a weak memory


def retrievability(days: float, stability: float) -> float:
    """Probability of recall after `days`: R(t, S) = (1 + FACTOR * t / S) ** DECAY."""
    return (1 + FACTOR * max(0.0, days) / stability) ** DECAY


def interval_days(stability: float, retention: float = RETENTION) -> int:
    """Days until R falls to `retention`: I = S / FACTOR * (r ** (1 / DECAY) - 1)."""
    i = stability / FACTOR * (retention ** (1 / DECAY) - 1)
    return int(min(MAX_INTERVAL_DAYS, max(1, round(i))))


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def init_stability(g: int) -> float:
    return W[g - 1]


def init_difficulty(g: int) -> float:
    """D0(G) = w4 - e^(w5 * (G - 1)) + 1."""
    return _clamp_d(W[4] - math.exp(W[5] * (g - 1)) + 1)


def next_difficulty(d: float, g: int) -> float:
    """Linear damping toward 10, then mean reversion to D0(Easy) (FSRS-5)."""
    d1 = d + (-W[6] * (g - 3)) * (10 - d) / 9
    return _clamp_d(W[7] * init_difficulty(EASY) + (1 - W[7]) * d1)


def stability_after_recall(d: float, s: float, r: float, g: int) -> float:
    hard = W[15] if g == HARD else 1.0
    easy = W[16] if g == EASY else 1.0
    return s * (math.exp(W[8]) * (11 - d) * s ** -W[9] * (math.exp(W[10] * (1 - r)) - 1) * hard * easy + 1)


def stability_after_lapse(d: float, s: float, r: float) -> float:
    s_f = W[11] * d ** -W[12] * ((s + 1) ** W[13] - 1) * math.exp(W[14] * (1 - r))
    return min(s_f, s)    # our guard: forgetting must never lengthen the interval


def stability_same_day(s: float, g: int) -> float:
    """A second review within a day: S' = S * e^(w17 * (G - 3 + w18))."""
    return s * math.exp(W[17] * (g - 3 + W[18]))


def replay(events: list[dict]) -> dict | None:
    """Memory state of one card from its log events (oldest first).

    Returns None for a card never reviewed, else
    {stability, difficulty, last, due, reps, lapses}. Stability is updated with
    the difficulty the card had at the review, then difficulty is updated.
    """
    state = None
    for e in sorted(events, key=lambda e: e["ts"]):
        t = dt.datetime.fromisoformat(e["ts"])
        g = GOOD if e["pass"] else AGAIN
        if state is None:
            state = {"stability": init_stability(g), "difficulty": init_difficulty(g),
                     "reps": 0, "lapses": 0}
        else:
            days = (t - state["last"]).total_seconds() / 86400
            s, d = state["stability"], state["difficulty"]
            if days < 1:
                state["stability"] = stability_same_day(s, g)
            else:
                r = retrievability(days, s)
                state["stability"] = stability_after_recall(d, s, r, g) if g >= HARD else stability_after_lapse(d, s, r)
            state["difficulty"] = next_difficulty(d, g)
        state["reps"] += 1
        state["lapses"] += 0 if e["pass"] else 1
        state["last"] = t
        state["due"] = t + dt.timedelta(days=interval_days(state["stability"]))
    return state


# ------------------------------------------------------------------ cards and log
def card_id(question: str) -> str:
    return hashlib.sha1(" ".join(question.lower().split()).encode()).hexdigest()[:12]


def load_cards(folder: Path) -> list[dict]:
    f = folder / "cards.tsv"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            out.append({"id": card_id(parts[0]), "q": parts[0].strip(), "a": parts[1].strip(),
                        "ref": parts[2].strip() if len(parts) > 2 else "", "folder": folder})
    return out


def load_log(folder: Path) -> dict[str, list[dict]]:
    by_card: dict[str, list[dict]] = {}
    f = folder / "reviews.jsonl"
    if f.exists():
        for n, line in enumerate(f.read_text().splitlines(), 1):
            try:
                e = json.loads(line)
                by_card.setdefault(e["card"], []).append(e)
            except (json.JSONDecodeError, KeyError):
                print(f"  warning: {f}:{n} is not a review record, ignored", file=sys.stderr)
    return by_card


def log_answer(folder: Path, card: str, passed: bool, now: dt.datetime) -> None:
    with (folder / "reviews.jsonl").open("a") as fh:
        fh.write(json.dumps({"card": card, "ts": now.isoformat(), "pass": bool(passed)}) + "\n")


def queue(folders: list[Path], now: dt.datetime, new_limit: int = 10) -> tuple[list[dict], list[dict], list[dict]]:
    """(due reviews, new cards up to new_limit, leeches). Due cards come most
    overdue first; new cards in lecture order, so earlier lectures start first."""
    due, new, leeches = [], [], []
    for folder in folders:
        log = load_log(folder)
        for c in load_cards(folder):
            st = replay(log.get(c["id"], []))
            if st is None:
                new.append(c)
                continue
            c = {**c, **st}
            if st["lapses"] >= LEECH_LAPSES:
                leeches.append(c)
            if st["due"] <= now:
                due.append(c)
    due.sort(key=lambda c: c["due"])
    return due, new[:max(0, new_limit)], leeches


def lecture_folders(root: Path = LECTURES) -> list[Path]:
    return sorted(p for p in root.glob("*") if (p / "cards.tsv").exists()) if root.exists() else []


# ------------------------------------------------------------------ command
def cmd_review(a) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    folders = lecture_folders()
    if a.lecture:
        folders = [f for f in folders if a.lecture in f.name]
    due, new, leeches = queue(folders, now, a.new)
    if a.stats:
        info = {"lectures": len(folders), "due": len(due), "new": len(new), "retention_target": RETENTION,
                "leeches": [{"q": c["q"], "lapses": c["lapses"], "lecture": c["folder"].name} for c in leeches]}
        if a.json:
            print(json.dumps(info)); return
        print(f"{len(due)} due, {len(new)} new across {len(folders)} lecture(s); target recall {RETENTION:.0%}")
        for c in leeches:
            print(f"  keeps failing ({c['lapses']}×): {c['q']}  — rewrite or split this card")
        return
    cards = (due + new)[: a.limit]
    if not cards:
        print("Nothing due. Come back tomorrow."); return
    passed = done = 0
    for i, c in enumerate(cards, 1):
        print(f"\n[{i}/{len(cards)}] {c['q']}")
        try:
            input("  (answer from memory, then press Enter) ")
            print(f"  → {c['a']}" + (f"   ({c['ref']})" if c["ref"] else ""))
            ans = ""
            while ans not in ("p", "f"):
                ans = input("  pass or fail? [p/f] ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print("\nStopped; answers so far are saved."); break
        log_answer(c["folder"], c["id"], ans == "p", dt.datetime.now(dt.timezone.utc))
        passed += ans == "p"; done += 1
        st = replay(load_log(c["folder"]).get(c["id"], []))
        print(f"  next review in {interval_days(st['stability'])} day(s)")
    print(f"\n{passed} passed of {done}.")


def add_parser(sub) -> None:
    p = sub.add_parser("review", help="spaced review of flashcards with a pass/fail log")
    p.add_argument("--limit", type=int, default=30, help="cards this session (default %(default)s)")
    p.add_argument("--new", type=int, default=10, help="new cards this session (default %(default)s)")
    p.add_argument("--lecture", help="only lectures whose folder name contains this")
    p.add_argument("--stats", action="store_true", help="counts and failing cards, no questions")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_review)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="review", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest="cmd", required=True)
    add_parser(s)
    args = ap.parse_args(["review", *sys.argv[1:]])
    args.fn(args)
