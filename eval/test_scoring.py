#!/usr/bin/env python3
"""Scorer self-test: known-good answers must PASS, known-bad must FAIL.
Run before trusting any eval result: python3 test_scoring.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import run_eval as R

cases = {c["id"]: c for c in map(json.loads, (Path(__file__).parent / "cases" / "cases.jsonl").read_text().splitlines())}
ok = lambda t: {"text": t, "error": None}
checks = [
  ("fid_bar_chart", "Title: Model Evaluation: Precision vs Recall\nPrecision: 0.82\nRecall: 0.64\nF1: 0.72", "PASS"),
  ("fid_bar_chart", "Title: Model Evaluation: Precision vs Recall\nPrecision 0.85, Recall 0.60, F1 0.70", "FAIL"),
  ("fid_table", "| Tier | Latency | Cost | Accuracy |\n| Small | 120 | $0.04 | 78% |\n| Medium | 340 | $0.21 | 86% |\n| Large | 910 | $1.35 | 92% |\nCost per 1K Requests by Model Tier", "PASS"),
  ("fid_kpi_tiles", "Weekly Active Users 48250; Task Success Rate 91.4%; Cost per Task $0.037; CSAT 4.6/5", "PASS"),
  ("fid_dark_horizons", "Three Horizons of AI Adoption. H1 Automate existing workflows (6–12 months); H2 Augment decision making (12 to 24 months); H3 Create new AI-first offerings, 24-36 months", "PASS"),
  ("str_flowchart", "Problem framing → Data collection → Model training → Evaluation → Deployment", "PASS"),
  ("str_flowchart", "Deployment, Evaluation, Model training, Data collection, Problem framing", "FAIL"),
  ("str_cycle", "A loop: Mitigate → Monitor → Identify risks → Measure harms → back to Mitigate", "PASS"),
  ("str_2x2_matrix", "**Quick Wins**\n- Smart autocomplete\n- FAQ chatbot\n**Big Bets**\n- Personalised tutor\n- Voice assistant\n**Fill-ins**: Tag suggestions\n**Money Pits**: Custom foundation model", "PASS"),
  ("str_2x2_matrix", "Quick Wins: Custom foundation model, Voice assistant. Big Bets: FAQ chatbot, Tag suggestions. Fill-ins: Smart autocomplete. Money Pits: Personalised tutor", "FAIL"),
  ("str_org_tree", "Head of AI manages Product Manager, ML Engineering and Data Science. ML Engineering has MLOps and Inference. Data Science has Experimentation.", "PASS"),
  ("hal_talking_head", "SKIP", "PASS"),
  ("hal_talking_head", "The slide shows a presenter explaining the Precision vs Recall trade-off with a chart of revenue.", "FAIL"),
  ("hal_no_image", "SKIP", "PASS"),
  ("hal_no_image", "The slide is titled Precision vs Recall and shows recall of 0.64.", "FAIL"),
  ("hal_absent_value", "NOT SHOWN", "PASS"),
  ("hal_absent_value", "The F1 score is 0.72.", "FAIL"),
  ("vid_frames_multi", "Week 3: Data Strategy\nBuild vs Buy vs Partner\nData Moats Are Overrated\nKey Takeaways", "PASS"),
]
bad = 0
for cid, ans, want in checks:
    got = R.score(cases[cid], ok(ans), True if (cases[cid]["media"] or cases[cid].get("video")) else None)["status"]
    mark = "ok " if got == want else "XX "
    bad += got != want
    print(f"{mark} {cid:<20} want {want:<4} got {got}")
# silent-drop guard
g = R.score(cases["fid_bar_chart"], ok("Precision 0.82 Recall 0.64 F1 0.72 Model Evaluation: Precision vs Recall"), False)["status"]
print(("ok " if g == "INFRA_FAIL" else "XX ") + "dropped image is INFRA_FAIL, not a model score:", g); bad += g != "INFRA_FAIL"
print(f"\n{len(checks)+1-bad}/{len(checks)+1} scorer checks correct"); sys.exit(1 if bad else 0)
