"""CC-6 copilot evaluation and over-reliance telemetry.

Three layers, all read from the workspace (nothing new is judged here):
  critique quality   per checker: the seeded-defect bench (M-11) plus the learner's own
                     labels of findings (valid or not, specific or vague), with Wilson intervals
  simulation conduct per panel session: questions per turn, grounded follow-ups, rubric
                     coverage, blocked concessions (from capstone_panel metrics)
  learner outcomes   unaided only: cold-mock checks passed, incident ratings, the gap between
                     self and critic ratings, unresolved Tigers, time to declare
plus telemetry: attempts before hints, hint levels, artefacts changed after a critique, and
days since the last cold mock.

Files: capstone/eval/finding_labels.jsonl, capstone/eval/checker_card.json
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import capstone as cs
import capstone_panel as cp
import cite

MIN_LABELS = 60          # EP-1: fewer labels than this is exploratory
P_SCORE = {"P": 3, "S": 2, "M": 1, "U": 0}


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def label_finding(ws: cs.Workspace, finding_id: str, valid: bool, specific: bool, by: str = "learner") -> dict:
    found = next((f for f in _jsonl(ws.root / "findings.jsonl") if f["finding_id"] == finding_id), None)
    if not found:
        raise KeyError("no such finding")
    row = {"finding_id": finding_id, "check_id": found["check_id"], "verdict": found["verdict"], "valid": bool(valid),
           "specific": bool(specific), "by": by, "t": cs.now_iso()}
    (ws.root / "eval").mkdir(exist_ok=True)
    rows = [r for r in _jsonl(ws.root / "eval" / "finding_labels.jsonl") if r["finding_id"] != finding_id] + [row]
    (ws.root / "eval" / "finding_labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return row


def checker_card(ws: cs.Workspace, bench: dict | None, checks: list[str]) -> list[dict]:
    """EP-1 shaped rows per checker. Status 'ok' needs the bench thresholds met and at least
    MIN_LABELS learner labels with valid share >= 0.85; otherwise 'exploratory'."""
    labels = _jsonl(ws.root / "eval" / "finding_labels.jsonl")
    bench_by = {c["check_id"]: c for c in (bench or {}).get("card", [])}
    out = []
    for check in checks:
        b = bench_by.get(check, {})
        ls = [l for l in labels if l["check_id"] == check]
        nv, ns = sum(l["valid"] for l in ls), sum(l["specific"] for l in ls)
        valid_share = round(nv / len(ls), 3) if ls else None
        bench_ok = b.get("status") == "ok"
        status = "ok" if bench_ok and len(ls) >= MIN_LABELS and (valid_share or 0) >= 0.85 else "exploratory"
        why = [] if status == "ok" else [m for m, bad in (
            ("bench below TPR 0.80 / TNR 0.90 or not run", not bench_ok),
            (f"{len(ls)} of {MIN_LABELS} learner labels", len(ls) < MIN_LABELS),
            ("valid share under 0.85", ls and (valid_share or 0) < 0.85)) if bad]
        out.append({"criterion": check, "n_labels": len(ls), "valid_share": valid_share,
                    "valid_ci": cite.wilson(nv, len(ls)), "specific_share": round(ns / len(ls), 3) if ls else None,
                    "tpr": b.get("tpr"), "tpr_ci": b.get("tpr_ci"), "tnr": b.get("tnr"), "tnr_ci": b.get("tnr_ci"),
                    "bench_at": (bench or {}).get("at"), "judge_model": (bench or {}).get("model"),
                    "status": status, "why": why})
    (ws.root / "eval").mkdir(exist_ok=True)
    (ws.root / "eval" / "checker_card.json").write_text(json.dumps(out, indent=1))
    return out


def _panels(ws: cs.Workspace) -> list[cp.Panel]:
    out = []
    for d in sorted((ws.root / "sessions").glob("*-panel-*")):
        if (d / "panel.json").exists():
            p = cp.Panel(ws, int(d.name.rsplit("-", 1)[1]))
            p.dir = d
            out.append(p)
    return out


def _drills(ws: cs.Workspace) -> list[Path]:
    return [d for d in sorted((ws.root / "sessions").glob("*-sim-*")) if (d / "aar.json").exists()]


def conduct(ws: cs.Workspace) -> list[dict]:
    return [{"session": p.dir.name, **p.metrics()} for p in _panels(ws)]


def outcomes(ws: cs.Workspace) -> dict:
    cold, gaps_panel = [], []
    for p in _panels(ws):
        f = p.dir / "score.json"
        if not f.exists():
            continue
        rows = json.loads(f.read_text())
        checks = [c for r in rows for c in r["checks"] if c["verdict"] in ("pass", "fail")]
        share = round(sum(c["verdict"] == "pass" for c in checks) / len(checks), 3) if checks else None
        if p.cfg()["cold"]:
            cold.append({"session": p.dir.name, "checks_passed": share})
        for r in rows:
            cs_ = [c for c in r["checks"] if c["verdict"] in ("pass", "fail")]
            if r.get("self_score") and cs_:
                critic = 1 + 3 * sum(c["verdict"] == "pass" for c in cs_) / len(cs_)   # onto the 1-4 scale
                gaps_panel.append(r["self_score"] - critic)
    drills, gaps_drill = [], []
    for d in _drills(ws):
        aar = json.loads((d / "aar.json").read_text())
        rated = [r for r in aar if r["critic_rating"] in P_SCORE]
        drills.append({"session": d.name, "mean_rating": round(sum(P_SCORE[r["critic_rating"]] for r in rated) / len(rated), 2)
                       if rated else None,
                       "time_to_declare": next((r["observation_quote"] for r in aar if r["objective_id"] == "time_to_declare"), None)})
        gaps_drill += [P_SCORE[r["self_rating"]] - P_SCORE[r["critic_rating"]] for r in rated
                       if r.get("self_rating") in P_SCORE]
    pm = ws.root / "premortem.json"
    tigers = [r for r in (json.loads(pm.read_text()) if pm.exists() else [])
              if r.get("class") == "tiger" and not (r.get("mitigation") or "").strip()]
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    return {"cold_mocks": cold, "drills": drills,
            "self_minus_critic": {"panel": mean(gaps_panel), "drill": mean(gaps_drill),
                                  "note": "positive means you rated yourself higher than the critic did"},
            "unresolved_tigers": len(tigers)}


def telemetry(ws: cs.Workspace, today: dt.date | None = None) -> dict:
    rows = _jsonl(ws.root / "telemetry.jsonl")
    hints = [r for r in rows if r.get("kind") == "hint"]
    levels = {str(k): sum(1 for r in hints if r.get("level") == k) for k in range(4)}
    arts = {a["artefact_id"]: a for a in ws.artefacts()}
    findings = _jsonl(ws.root / "findings.jsonl")
    changed = [f for f in findings if f["artefact_id"] in arts and arts[f["artefact_id"]]["sha256"] != f["artefact_sha"]]
    colds = [p.dir.name[:10] for p in _panels(ws) if p.cfg()["cold"]]
    last = max(colds) if colds else None
    days = ((today or dt.date.today()) - dt.date.fromisoformat(last)).days if last else None
    return {"hints": len(hints), "attempted_first_share": round(sum(bool(r.get("attempted_first")) for r in hints) / len(hints), 3)
            if hints else None, "hint_levels": levels,
            "findings": len(findings), "artefact_changed_after_share": round(len(changed) / len(findings), 3) if findings else None,
            "days_since_cold_mock": days}


def progress(ws: cs.Workspace, bench: dict | None, checks: list[str]) -> dict:
    return {"checker_card": checker_card(ws, bench, checks), "conduct": conduct(ws), "outcomes": outcomes(ws),
            "telemetry": telemetry(ws)}
