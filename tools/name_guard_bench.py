#!/usr/bin/env python3
"""M-9: does roster matching catch names as speech-to-text spells them, without touching
domain terms? Everything here is invented: names are built from syllables, sentences are
templates, and variants imitate common ASR spellings of Indian names. Prints recall and
false hits for a threshold grid and the chosen thresholds; writes eval/results/<ts>-names-m9.json.

    tools/name_guard_bench.py [--n 200] [--seed 7]
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import name_guard as ng  # noqa: E402

ONSETS = ["Pra", "Vi", "Ka", "Su", "Ra", "Dhi", "Sha", "Ni", "Ha", "Ja", "Bha", "Tri", "Ma", "Ke", "Gau", "Chi",
          "Ana", "Ishi", "Lo", "Ru", "Sri", "Thi", "Va", "Yo", "De", "Pa", "Na", "Si", "Ama", "Kri"]
MIDS = ["van", "lak", "dhar", "mesh", "rish", "thik", "nand", "jay", "vind", "shan", "kum", "rav", "lin", "tes",
        "deep", "mit", "rup", "sant", "kanth", "vas"]
ENDS = ["a", "i", "an", "esh", "ini", "ya", "u", "ita", "ika", "endra", "arth", "ali", "ak", "iya"]
SURN = ["Rao", "Iyer", "Naidu", "Menon", "Pillai", "Chatter", "Banerji", "Kalvekar", "Deshpan", "Reddy", "Bhatna",
        "Sharvani", "Mukher", "Gowda", "Trived", "Chauhar", "Nambi", "Sethur", "Khanol", "Venkatar"]
DOMAIN = ["PRD", "timestamp", "dependencies", "panelists", "Klarna", "Waymo", "Zomato", "Firefly", "Sarvam",
          "ONDC", "UPI", "NBFC", "Hinglish", "Tanglish", "golden", "guardrails", "orchestrator", "Copilot",
          "Autopilot", "Gemini", "Qwen", "WhatsApp", "MakeMyTrip", "Jugalbandi", "RBI", "DPDP", "FSRS"]
TEMPLATES = ["Thanks, {n}. The {d} slide covers that.", "{n} asked whether the {d} needs a threshold.",
             "So as {n} said, the {d} matters here.", "I think {n} has a point about {d}.",
             "Yes {n}, the {d} example is in the deck.", "Over to you, {n}, on the {d} question."]


def invent_names(n: int, rnd: random.Random) -> list[str]:
    out = set()
    while len(out) < n:
        first = rnd.choice(ONSETS) + rnd.choice(MIDS) + rnd.choice(ENDS)
        last = rnd.choice(SURN) + rnd.choice(["", "a", "an", "kar", "i"])
        out.add(f"{first.capitalize()} {last.capitalize()}")
    return sorted(out)


SWAPS = [("sh", "s"), ("s", "sh"), ("v", "w"), ("w", "v"), ("th", "t"), ("t", "th"), ("ee", "i"), ("i", "ee"),
         ("a", "aa"), ("aa", "a"), ("dh", "d"), ("bh", "b"), ("k", "c"), ("c", "k"), ("ph", "f"), ("y", "i"),
         ("u", "oo"), ("j", "z"), ("rr", "r"), ("nn", "n")]


def asr_variant(word: str, rnd: random.Random) -> str:
    """One or two spelling changes a recogniser makes, or the word split/merged oddly."""
    w = word.lower()
    for _ in range(rnd.choice([1, 1, 2])):
        opts = [(a, b) for a, b in SWAPS if a in w]
        if not opts:
            break
        a, b = rnd.choice(opts)
        i = w.find(a)
        w = w[:i] + b + w[i + len(a):]
    if rnd.random() < 0.15 and len(w) > 5:      # a dropped letter
        i = rnd.randrange(1, len(w) - 1)
        w = w[:i] + w[i + 1:]
    return w.capitalize()


def run(n: int, seed: int) -> dict:
    rnd = random.Random(seed)
    names = invent_names(n, rnd)
    sents, truth = [], []
    for nm in names:
        first, last = nm.split()
        for target in (first, last):
            for spelled in (target, asr_variant(target, rnd)):
                sents.append(rnd.choice(TEMPLATES).format(n=spelled, d=rnd.choice(DOMAIN)))
                truth.append(spelled)
    grid = itertools.product([0.65, 0.70, 0.75, 0.80], [0.82, 0.84, 0.86, 0.88, 0.90], [0.74, 0.78])
    results = []
    for ph, ed, rv in grid:
        r = ng.Roster(names, dictionary=ng.load_dictionary(), thresholds={"phonetic": ph, "edit": ed, "review": rv})
        hit = sum(1 for t in truth if (m := r.match(t)) and m[0] == "remove")
        hit_or_review = sum(1 for t in truth if r.match(t))
        dom = sorted({d for d in DOMAIN if (m := r.match(d)) and m[0] == "remove"})
        dom_review = sorted({d for d in DOMAIN if (m := r.match(d)) and m[0] == "review"})
        words = {w for s in sents for w in s.replace(",", " ").replace(".", " ").split()} - set(truth) - set(DOMAIN)
        other = sorted(w for w in words if (m := r.match(w)) and m[0] == "remove")
        results.append({"phonetic": ph, "edit": ed, "review": rv, "recall": round(hit / len(truth), 4),
                        "recall_with_review": round(hit_or_review / len(truth), 4), "domain_removed": len(dom),
                        "domain_review": len(dom_review), "other_words_removed": len(other)})
    ok = [x for x in results if x["recall"] >= 0.98 and x["domain_removed"] == 0 and x["other_words_removed"] == 0]
    chosen = max(ok, key=lambda x: (x["edit"], x["phonetic"], -x["domain_review"])) if ok else \
        max(results, key=lambda x: (x["domain_removed"] == 0, x["recall"]))
    variants = [t for i, t in enumerate(truth) if i % 2 == 1]
    r = ng.Roster(names, dictionary=ng.load_dictionary(), thresholds=chosen)
    miss = [t for t in variants if not ((m := r.match(t)) and m[0] == "remove")]
    return {"date": dt.date.today().isoformat(), "seed": seed, "names": len(names), "mentions": len(truth),
            "variant_mentions": len(variants), "chosen": chosen, "met_target": bool(ok),
            "variant_recall": round(1 - len(miss) / len(variants), 4), "grid": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rep = run(a.n, a.seed)
    out = Path(__file__).resolve().parents[1] / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{dt.datetime.now():%Y%m%d-%H%M%S}-names-m9.json"
    f.write_text(json.dumps(rep, indent=1))
    c = rep["chosen"]
    print(f"M-9: {rep['names']} invented names, {rep['mentions']} mentions ({rep['variant_mentions']} ASR variants)")
    print(f"chosen thresholds phonetic={c['phonetic']} edit={c['edit']} review={c['review']}: recall {c['recall']}, "
          f"variant recall {rep['variant_recall']}, domain terms removed {c['domain_removed']} (to review {c['domain_review']}), "
          f"other words removed {c['other_words_removed']}; target met: {rep['met_target']}")
    print(f"report: {f}")


if __name__ == "__main__":
    main()
