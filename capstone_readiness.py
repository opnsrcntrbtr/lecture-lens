"""CC-3 readiness review: nothing is rated "demonstrated" without a pointer to evidence.

Items combine five launch gates with a ten-item production self-assessment. The learner
rates each item; the copilot only checks the evidence pointer (an exact quote from a
registered artefact at its current hash, or an eval run that exists) and can list candidate
passages to look at. Governance triage turns four yes/no answers into a checklist of
obligations to verify from the dated snapshot; it is not advice. The system-card audit
lists missing sections and writes none.

Files: capstone/readiness.json  [{item_id, gate, rating, evidence, finding_id, t}]
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import capstone as cs

RATINGS = ("demonstrated", "partial", "not_observed")

GATES = ("evaluation_coverage", "latency", "graceful_degradation", "monitoring_baseline", "rollback")

ITEMS = [
    # five launch gates
    {"item_id": "eval_golden_set", "gate": "evaluation_coverage",
     "question": "Is there a golden set that covers each named failure mode, with a pass threshold?",
     "keywords": ["golden", "eval", "test set", "threshold", "failure mode", "recall", "precision"]},
    {"item_id": "judge_agreement", "gate": "evaluation_coverage",
     "question": "If a model judge scores outputs, is its agreement with human labels measured?",
     "keywords": ["judge", "agreement", "human label", "kappa", "calibrat"]},
    {"item_id": "latency_budget", "gate": "latency",
     "question": "Is there a latency budget at a percentile, and a measurement against it?",
     "keywords": ["latency", "p95", "p90", "seconds", "ms", "response time"]},
    {"item_id": "degradation_path", "gate": "graceful_degradation",
     "question": "What does the user get when the model times out, errors or is unsure?",
     "keywords": ["fallback", "hand-off", "handoff", "human", "timeout", "low confidence", "degrad"]},
    {"item_id": "monitoring_signals", "gate": "monitoring_baseline",
     "question": "Which live signals are monitored, with a baseline and an alert level?",
     "keywords": ["monitor", "dashboard", "alert", "baseline", "escalation", "repeat contact"]},
    {"item_id": "rollback_time", "gate": "rollback",
     "question": "Can the change be rolled back, by whom, and how fast?",
     "keywords": ["rollback", "roll back", "kill switch", "feature flag", "revert", "minutes"]},
    # production self-assessment
    {"item_id": "owner_named", "gate": "self_assessment",
     "question": "Is one owner named for model behaviour in production?",
     "keywords": ["owner", "responsible", "accountable", "on-call", "on call"]},
    {"item_id": "incident_runbook", "gate": "self_assessment",
     "question": "Is there an incident runbook with a severity scale?",
     "keywords": ["runbook", "incident", "severity", "sev", "playbook"]},
    {"item_id": "prompt_versioning", "gate": "self_assessment",
     "question": "Are prompts and model versions pinned and recorded with each output?",
     "keywords": ["version", "pinned", "prompt", "model id", "changelog"]},
    {"item_id": "cost_ceiling", "gate": "self_assessment",
     "question": "Is there a cost ceiling per task or user, and what happens when it is hit?",
     "keywords": ["cost", "budget", "cap", "ceiling", "per task", "per user"]},
    {"item_id": "abuse_and_injection", "gate": "self_assessment",
     "question": "Are prompt injection and misuse tested, with the actions the model may take limited?",
     "keywords": ["injection", "jailbreak", "misuse", "abuse", "permission", "allow-list", "allowlist"]},
    {"item_id": "data_handling", "gate": "self_assessment",
     "question": "Is it stated what personal data is processed, kept and for how long?",
     "keywords": ["personal data", "retention", "consent", "pii", "privacy", "delete"]},
    {"item_id": "disclosure", "gate": "self_assessment",
     "question": "Are users told they are dealing with AI, and where?",
     "keywords": ["disclos", "label", "ai-generated", "tell users", "transparen"]},
    {"item_id": "human_oversight", "gate": "self_assessment",
     "question": "Where does a person review or approve before an action takes effect?",
     "keywords": ["approv", "review", "human-in-the-loop", "human in the loop", "oversight", "co-pilot", "copilot"]},
    {"item_id": "feedback_loop", "gate": "self_assessment",
     "question": "How do user reports and failures get back into the golden set?",
     "keywords": ["feedback", "report", "thumbs", "golden set", "label", "retrain"]},
    {"item_id": "stop_criteria", "gate": "self_assessment",
     "question": "What result would make you pause, narrow or stop the launch?",
     "keywords": ["stop", "pause", "narrow", "kill", "roll back if", "threshold"]},
]
ITEM_IDS = {i["item_id"] for i in ITEMS}


class Readiness:
    def __init__(self, ws: cs.Workspace, eval_runs: set[str] | None = None):
        self.ws, self.f = ws, ws.root / "readiness.json"
        self.eval_runs = eval_runs if eval_runs is not None else known_eval_runs(ws)

    def rows(self) -> list[dict]:
        return json.loads(self.f.read_text()) if self.f.exists() else []

    def check_evidence(self, ev: dict | None) -> str | None:
        """Why the pointer is not valid evidence (None: it is)."""
        if not ev:
            return "no evidence pointer"
        if ev.get("eval_run_id"):
            return None if ev["eval_run_id"] in self.eval_runs else "no such eval run"
        aid, quote = ev.get("artefact_id"), (ev.get("quote") or "").strip()
        if not aid or not quote:
            return "need artefact_id and quote, or eval_run_id"
        if aid not in {a["artefact_id"] for a in self.ws.artefacts()}:
            return "artefact not registered"
        text, sha = self.ws.text(aid)
        span = cs.exact_span(quote, text)
        if span is None:
            return "quote is not in the artefact"
        if len(span.split()) < 4:
            return "quote at least four words"
        ev["quote"], ev["artefact_sha"] = span, sha
        return None

    def rate(self, item_id: str, rating: str, evidence: dict | None = None, t: str | None = None) -> dict:
        if item_id not in ITEM_IDS:
            raise ValueError("unknown item")
        if rating not in RATINGS:
            raise ValueError(f"rating must be one of {', '.join(RATINGS)}")
        why = self.check_evidence(evidence) if evidence or rating != "not_observed" else None
        if rating == "demonstrated" and why:
            raise ValueError(f"demonstrated needs evidence: {why}")
        if rating == "partial" and evidence and why:
            raise ValueError(f"evidence: {why}")
        item = next(i for i in ITEMS if i["item_id"] == item_id)
        row = {"item_id": item_id, "gate": item["gate"], "rating": rating,
               "evidence": evidence if evidence and not why else None, "finding_id": None, "t": t or cs.now_iso()}
        rows = [r for r in self.rows() if r["item_id"] != item_id] + [row]
        self.f.write_text(json.dumps(rows, indent=1))
        return row

    def status(self) -> dict:
        """Items with ratings; a demonstrated item whose quote no longer matches its artefact
        (the learner edited it) drops to 'stale'."""
        by = {r["item_id"]: r for r in self.rows()}
        out = []
        for i in ITEMS:
            r = by.get(i["item_id"])
            st = r["rating"] if r else "unrated"
            if r and r.get("evidence") and r["evidence"].get("artefact_id"):
                if self.check_evidence(dict(r["evidence"])):
                    st = "stale"
            out.append({"item_id": i["item_id"], "gate": i["gate"], "question": i["question"], "rating": st,
                        "evidence": (r or {}).get("evidence")})
        gates = {}
        for g in (*GATES, "self_assessment"):
            rs = [o["rating"] for o in out if o["gate"] == g]
            gates[g] = {"items": len(rs), "demonstrated": rs.count("demonstrated"), "partial": rs.count("partial"),
                        "unrated": rs.count("unrated") + rs.count("stale")}
        return {"items": out, "gates": gates,
                "ready": all(gates[g]["demonstrated"] == gates[g]["items"] for g in GATES)}


def known_eval_runs(ws: cs.Workspace) -> set[str]:
    """Eval runs the learner can point to: files under capstone/eval/ (their own product's
    evals, e.g. a judge card) by stem."""
    d = ws.root / "eval"
    return {p.stem for p in d.glob("*.json")} if d.exists() else set()


SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def candidates(ws: cs.Workspace, item_id: str, k: int = 3) -> list[dict]:
    """Passages in the learner's artefacts that mention the item's keywords: places to look,
    not a rating."""
    item = next(i for i in ITEMS if i["item_id"] == item_id)
    hits = []
    for a in ws.artefacts():
        text, _ = ws.text(a["artefact_id"])
        for s in SENT.split(text):
            s = s.strip(" -*#\t")
            if len(s.split()) < 4:
                continue
            low = s.lower()
            score = sum(1 for kw in item["keywords"] if kw in low)
            if score:
                hits.append((score, a["artefact_id"], s[:300]))
    hits.sort(key=lambda h: -h[0])
    return [{"artefact_id": aid, "quote": q} for _, aid, q in hits[:k]]


# ------------------------------------------------------------------ governance triage
TRIAGE_QUESTIONS = {
    "users_in_eu": "Are any users in the EU?",
    "high_risk_like": "Does the use resemble a high-risk category (hiring, credit, education access, essential services)?",
    "india_personal_data": "Does it process personal data of people in India?",
    "chatbot": "Do people talk to it directly (chatbot, voice, generated content shown to them)?",
}
TRIGGERS = [  # (answers that must be yes, regime, obligation substring)
    (("users_in_eu", "chatbot"), "EU AI Act", "Article 50"),
    (("users_in_eu", "high_risk_like"), "EU AI Act", "Annex III"),
    (("india_personal_data",), "India DPDP Rules", ""),
]


def triage(answers: dict, snapshot: dict) -> dict:
    """Obligations to check, each with the snapshot's as-of date and a verify flag. Not advice."""
    yes = {k for k, v in answers.items() if str(v).lower() in ("y", "yes", "true", "1")}
    items = []
    for need, regime, sub in TRIGGERS:
        if set(need) <= yes:
            items += [dict(i, as_of=snapshot.get("as_of"), triggered_by=list(need)) for i in snapshot.get("items", [])
                      if i["regime"] == regime and sub in i["obligation"]]
    return {"as_of": snapshot.get("as_of"), "answers": {k: k in yes for k in TRIAGE_QUESTIONS},
            "to_verify": items,
            "note": "A checklist of things to verify with the sources and your faculty; not legal advice."}


# ------------------------------------------------------------------ system card audit
CARD_SECTIONS = {
    "intended_use": r"intended use|purpose|what it is for",
    "out_of_scope": r"out[- ]of[- ]scope|not intended|should not be used|misuse",
    "results_by_slice": r"results? by|slice|segment|subgroup|per (language|region|cohort)",
    "safeguards": r"safeguard|guardrail|mitigation|filter",
    "limitations": r"limitation|known (issue|failure)|weakness",
    "fallback": r"fallback|hand(?:s|ed|ing)?[- ]?off|roll(?:s|ed)? ?back|kill switch",
    "human_oversight": r"human oversight|human[- ]in[- ]the[- ]loop|approv|review by",
    "data_handling": r"data handling|retention|personal data|privacy|consent",
}


def audit_card(text: str) -> dict:
    """Which sections the learner's system card has (by heading or a sentence that names it);
    lists the missing ones and writes nothing."""
    heads = "\n".join(l for l in text.splitlines() if l.lstrip().startswith("#") or l.strip().endswith(":"))
    present, weak = [], []
    for sec, pat in CARD_SECTIONS.items():
        if re.search(pat, heads, re.I):
            present.append(sec)
        elif re.search(pat, text, re.I):
            weak.append(sec)
    missing = [s for s in CARD_SECTIONS if s not in present and s not in weak]
    return {"present": present, "mentioned_only": weak, "missing": missing,
            "complete": not missing and not weak}
