#!/usr/bin/env python3
"""Run the VLM evaluation and write results + a report.

    python3 run_eval.py [--model M] [--configs think_off,think_on] [--only fid_,str_]

Scoring is deterministic string matching against ground truth from
gen_dataset.py — see DESIGN.md §3.2. A case whose image did not reach the
model is INFRA_FAIL, never a model score (DESIGN.md §3.3).
"""
import argparse, datetime as dt, difflib, json, os, re, statistics, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vlm_client as vc

CASES = HERE / "cases"
PASS = {"fidelity": 0.9, "structure": 0.8, "hallucination": 1.0, "video": 0.8}

# ---------------------------------------------------------------- normalizing
def norm(s):
    s = s.lower().replace("–", "-").replace("—", "-").replace("−", "-")
    s = s.replace("’", "'").replace("×", "x").replace("*", "").replace("`", "")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)          # 48,250 -> 48250
    s = re.sub(r"\s+-\s+|\s+to\s+", "-", s)              # "6 to 12" / "6 - 12" -> 6-12
    s = re.sub(r"[^a-z0-9.%$\-\s']", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def find(needle, hay):
    """Index of needle in hay (normalized), exact first, then fuzzy >= 0.9."""
    n, h = norm(needle), hay
    i = h.find(n)
    if i >= 0 or len(n) < 8:
        return i
    L = len(n); best, at = 0.0, -1
    for j in range(0, max(1, len(h) - L + 1), max(1, L // 6)):
        r = difflib.SequenceMatcher(None, n, h[j:j + L]).ratio()
        if r > best: best, at = r, j
    return at if best >= 0.9 else -1

def number_found(num, hay):
    n = norm(num).strip("$%")
    return re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", hay) is not None

# ---------------------------------------------------------------- scorers
def _hit(v):
    # find() returns an index (0 is a hit!), number_found() a bool. Never use
    # `v not in (-1, False, None)`: 0 == False, so a title at position 0 would miss.
    return v is True or (type(v) is int and v >= 0)

def recall(items, hay, fn):
    return (sum(1 for x in items if _hit(fn(x, hay))) / len(items)) if items else None

def order_score(labels, hay, cyclic=False):
    pos = [find(l, hay) for l in labels]
    seq = [i for i, p in sorted(enumerate(pos), key=lambda t: t[1]) if pos[i] >= 0]
    if len(seq) < 2: return 0.0
    def agree(s): return sum(1 for a, b in zip(s, s[1:]) if b == a + 1) / (len(s) - 1)
    if not cyclic: return agree(seq)
    n = len(labels); best = 0.0
    for k in range(n):   # a cycle can be read from any starting node
        best = max(best, agree([(i - k) % n for i in seq]))
    return best

def pair_score(pairs, hay_raw):
    """Member belongs to group if the nearest preceding group mention is the
    right one, or they share a line (tables, 'Quick Wins: FAQ chatbot')."""
    hay = norm(hay_raw); groups = sorted({g for g, _ in pairs})
    lines = [norm(l) for l in hay_raw.splitlines()]
    ok = 0
    for g, m in pairs:
        # Same-line evidence only counts when that line names exactly one group;
        # "Quick Wins: X. Big Bets: Y" on one line proves nothing about X or Y.
        def single_group_line(l):
            return sum(1 for gg in groups if find(gg, l) >= 0) == 1
        if any(single_group_line(l) and find(g, l) >= 0 and
               (number_found(m, l) if re.fullmatch(r"[\d.,%$]+", m) else find(m, l) >= 0) for l in lines):
            ok += 1; continue
        mpos = hay.find(norm(m)) if re.fullmatch(r"[\d.,%$]+", m) else find(m, hay)
        if mpos < 0: continue
        prev = [(hay.rfind(norm(gg), 0, mpos), gg) for gg in groups]
        prev = [t for t in prev if t[0] >= 0]
        if prev and max(prev)[1] == g: ok += 1
    return ok / len(pairs) if pairs else None

# Text that exists only in the browser/page around the lecture player (gen_stress.py).
# A slide description that repeats it is polluted even when it is otherwise right.
CHROME_MARKERS = ["gmail", "course calendar", "learn.example.edu", "module 1 foundations", "discussion forum",
                  "download slides", "14:32", "assignments (2 due)", "address bar", "sidebar"]

def chrome_leak(text):
    t = (text or "").lower()
    return any(m in t for m in CHROME_MARKERS)

SKIP_MARKERS = ["skip", "no image", "not see", "cannot see", "can't see", "no slide", "blank",
                "no informational", "no visible text", "no text", "not provided", "no content"]

def score(case, res, received):
    t, text = case["truth"], res["text"]
    hay = norm(text)
    out = {"received": received}
    has_media = bool(case["media"]) or bool(case.get("video"))
    if has_media and not received:
        out.update(status="INFRA_FAIL", score=0.0, note="media did not reach the model"); return out
    if res.get("error"):
        out.update(status="ERROR", score=0.0, note=res["error"]); return out

    if t["expect_skip"]:
        said_skip = any(k in hay for k in SKIP_MARKERS) and len(hay) < 400
        invented = [f for f in t["forbidden"] if norm(f) in hay]
        s = 1.0 if said_skip and not invented else 0.0
        out.update(score=s, note=("invented: " + ", ".join(invented)) if invented else ("" if said_skip else "described a non-slide"))
    elif t["expect_absent"]:
        invented = [f for f in t["forbidden"] if norm(f) in hay]
        s = 1.0 if ("not shown" in hay and not invented) else 0.0
        out.update(score=s, note=("invented: " + ", ".join(invented)) if invented else ("" if s else "did not say NOT SHOWN"))
    else:
        parts = {}
        parts["phrases"] = recall(t["phrases"], hay, find)
        parts["numbers"] = recall(t["numbers"], hay, number_found)
        if t["order"]: parts["order"] = order_score(t["order"], hay, cyclic=case["kind"] == "cycle")
        if t["pairs"]: parts["pairs"] = pair_score(t["pairs"], text)
        present = {k: v for k, v in parts.items() if v is not None}
        if case["dim"] == "fidelity":
            s = statistics.mean([v for k, v in present.items() if k in ("phrases", "numbers")] or [0])
            if "pairs" in present: s = 0.8 * s + 0.2 * present["pairs"]
        else:
            lab = statistics.mean([v for k, v in present.items() if k in ("phrases", "numbers")] or [0])
            rel = statistics.mean([v for k, v in present.items() if k in ("order", "pairs")] or [lab])
            s = 0.5 * lab + 0.5 * rel
        missing = [p for p in t["phrases"] if find(p, hay) < 0][:4] + [n for n in t["numbers"] if not number_found(n, hay)][:4]
        out.update(score=round(s, 3), parts={k: round(v, 3) for k, v in present.items()},
                   note=("missing: " + ", ".join(missing)) if missing else "")
    out["status"] = "PASS" if out["score"] >= PASS[case["dim"]] else "FAIL"
    return out

# ---------------------------------------------------------------- running
def health():
    try:
        with vc._OPENER.open(vc.BASE.rsplit("/v1", 1)[0] + "/health", timeout=5) as r:
            return json.loads(r.read()).get("engine_pool", {})
    except Exception:
        return {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("OMLX_VL_MODEL") or "Qwen3.6-35B-A3B-oQ4e-mtp-XL-mlx")
    ap.add_argument("--configs", default="think_off,think_on")
    ap.add_argument("--only", default="", help="comma-separated id prefixes")
    ap.add_argument("--cases", default="cases", help="case folder: cases | cases_stress")
    ap.add_argument("--context", default="Lecture 12: Precision, recall and product risk",
                    help="lecture title passed to the slide prompt ('' to omit)")
    a = ap.parse_args()
    global CASES
    CASES = HERE / a.cases
    cases = [json.loads(l) for l in (CASES / "cases.jsonl").read_text().splitlines() if l.strip()]
    if a.only: cases = [c for c in cases if any(c["id"].startswith(p) for p in a.only.split(","))]
    out = HERE / "results" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ("" if a.cases == "cases" else "-" + a.cases))
    out.mkdir(parents=True)
    mem_before = health().get("current_model_memory")
    baselines = {}
    rows = []
    for cfg in a.configs.split(","):
        think = cfg == "think_on"
        infra_streak = 0
        for c in cases:
            # Pass a lecture title like production does: a title in the prompt once
            # flipped good slides to SKIP, and only a context-bearing eval catches that.
            prompt = vc.slide_prompt(a.context) if c["prompt"] == "describe" else c["prompt"]
            if prompt not in baselines: baselines[prompt] = vc.text_baseline(a.model, prompt)
            imgs = [CASES / m for m in c["media"]]
            vid = CASES / c["video"] if c.get("video") else None
            res = vc.ask(a.model, prompt, images=imgs, video=vid, think=think,
                         max_tokens=4000 if think else 1200)
            n_media = len(imgs) + (1 if vid else 0)
            rec = vc.image_received(res["prompt_tokens"], baselines[prompt], n_media) if n_media else None
            sc = score(c, res, rec)
            row = {"config": cfg, "id": c["id"], "dim": c["dim"], "kind": c["kind"], **sc,
                   "latency_s": res["latency_s"], "prompt_tokens": res["prompt_tokens"],
                   "completion_tokens": res["completion_tokens"], "reasoning_chars": res.get("reasoning_chars"),
                   "baseline_tokens": baselines[prompt], "answer": res["text"],
                   "chrome_leak": chrome_leak(res["text"]) if n_media else None}
            rows.append(row)
            print(f"[{cfg}] {c['id']:<22} {sc['status']:<10} {sc['score']:.2f}  {res['latency_s'] or 0:>6.1f}s  {sc.get('note','')[:70]}", flush=True)
            if n_media and c["kind"] != "native_video":
                infra_streak = infra_streak + 1 if sc["status"] == "INFRA_FAIL" else 0
                if infra_streak >= 3:
                    print("ABORT: 3 image cases in a row never reached the model — engine is text-only. Reload the model as VLM.")
                    (out / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n"); sys.exit(2)
    (out / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summary = report(rows, a.model, mem_before, health().get("current_model_memory"), out)
    print(f"\nreport: {out/'report.md'}")
    print(json.dumps(summary["by_config"], indent=1))

def pct(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))] if v else None

def report(rows, model, mem0, mem1, out):
    cfgs = sorted({r["config"] for r in rows}, key=lambda x: x != "think_off")
    dims = ["hallucination", "fidelity", "structure", "video"]
    summ = {"model": model, "memory_gb": {"before": round(mem0 / 1e9, 1) if mem0 else None,
                                         "after": round(mem1 / 1e9, 1) if mem1 else None}, "by_config": {}}
    L = [f"# VLM evaluation — {model}", "", f"_{dt.datetime.now():%Y-%m-%d %H:%M} · {len(rows)} runs · "
         f"model memory {summ['memory_gb']['before']} → {summ['memory_gb']['after']} GB_", ""]
    L += ["| Dimension | " + " | ".join(cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for d in dims:
        cells = []
        for c in cfgs:
            rs = [r for r in rows if r["config"] == c and r["dim"] == d]
            if not rs: cells.append("—"); continue
            p = sum(r["status"] == "PASS" for r in rs); inf = sum(r["status"] == "INFRA_FAIL" for r in rs)
            ms = statistics.mean(r["score"] for r in rs if r["status"] != "INFRA_FAIL") if len(rs) > inf else 0
            cells.append(f"{p}/{len(rs)} pass · mean {ms:.2f}" + (f" · {inf} dropped" if inf else ""))
            summ["by_config"].setdefault(c, {})[d] = {"pass": p, "n": len(rs), "mean": round(ms, 3), "dropped": inf}
        L.append(f"| {d} | " + " | ".join(cells) + " |")
    L += ["", "| Speed | " + " | ".join(cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    for lab, fn in [("latency p50 (s)", lambda rs: pct([r["latency_s"] for r in rs], 50)),
                    ("latency p95 (s)", lambda rs: pct([r["latency_s"] for r in rs], 95)),
                    ("output tok/s (median)", lambda rs: statistics.median([r["completion_tokens"] / r["latency_s"]
                        for r in rs if r["completion_tokens"] and r["latency_s"]] or [0])),
                    ("answers leaking page chrome", lambda rs: sum(1 for r in rs if r.get("chrome_leak"))),
                    ("image tokens (median)", lambda rs: statistics.median([r["prompt_tokens"] - r["baseline_tokens"]
                        for r in rs if r["received"] and r["prompt_tokens"] and r["baseline_tokens"]] or [0]))]:
        vals = []
        for c in cfgs:
            v = fn([r for r in rows if r["config"] == c])
            vals.append(f"{v:.1f}" if isinstance(v, (int, float)) and v is not None else "—")
            summ["by_config"].setdefault(c, {})[lab] = v
        L.append(f"| {lab} | " + " | ".join(vals) + " |")
    L += ["", "## Every case", "", "| config | case | status | score | s | note |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['config']} | {r['id']} | {r['status']} | {r['score']:.2f} | {r['latency_s'] or '—'} | {(r.get('note') or '').replace('|','/')[:90]} |")
    L += ["", "## Answers to failed cases", ""]
    for r in rows:
        if r["status"] != "PASS":
            L += [f"### {r['config']} · {r['id']} ({r['status']})", "", "```", (r["answer"] or "(empty)")[:1500], "```", ""]
    (out / "report.md").write_text("\n".join(L) + "\n")
    (out / "summary.json").write_text(json.dumps(summ, indent=2))
    return summ

if __name__ == "__main__":
    main()
