"""Strategy review (CC-1): one binary checker per failure class, differently-informed
reviewer views, a unit-economics calculator, a solo pre-mortem and internal-FAQ questions.

Every checker returns CC-0 findings and every finding passes the CC-0 gates before it is
kept. Each check runs three times; a verdict that is not unanimous becomes 'unclear'.
Checkers ask for the learner's evidence; they never assert benchmarks or supply wording.
"""
from __future__ import annotations

import json
import re
import uuid

import capstone as cs

# check_id -> (reviewer view, artefact kinds it reads)
CHECKERS = {
    "opportunity_quantified": ("general", ("prd", "strategy", "press_faq")),
    "acceptance_testable": ("general", ("prd",)),
    "eval_threshold_tied": ("risk", ("prd", "evidence_ladder", "trust_brief")),
    "fallback_defined": ("risk", ("trust_brief", "prd", "system_card")),
    "wrong_answer_cost": ("risk", ("trust_brief", "prd")),
    "moat_base_model": ("finance", ("strategy", "prd")),
    "unit_econ_heavy_user": ("finance", ("cost_model", "prd", "strategy")),
    "stop_condition": ("general", ("prd", "strategy", "trust_brief")),
}
RUNS = 3

PROMPT = """REVIEWER VIEW: {view}
CHECK: {question}
PASSES WHEN: {pass_definition}

LEARNER'S ARTEFACTS (read only):
{artefacts}

Judge only this one check, strictly by PASSES WHEN. If anything in the artefacts meets PASSES WHEN, the verdict is
"pass", even if it could be better; quote that passage. Answer "fail" only when nothing meets it; then quote the
passage closest to where it should be. Do not fail the check for other problems, and do not fail it for missing
proof unless PASSES WHEN asks for proof: if the claim is stated, the check passes and your question can probe the proof.
In "issue" and "question", do not put any words in quotation marks. The question field holds exactly one question
mark; the issue field holds none. Reply with JSON only:
{{"verdict": "pass" or "fail",
  "artefact_id": "<id of the artefact you quote>",
  "quote": "<exact words copied from that artefact: the passage that fails the check, or that satisfies it>",
  "issue": "<at most 30 words: what a reviewer would probe; no suggested wording, no praise>",
  "question": "<one question to the learner, ending with ?>"}}"""


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def run_check(ws: cs.Workspace, check_id: str, llm, rubric: dict | None = None, runs: int = RUNS,
              lecture_search=None) -> dict:
    """One checker over the artefacts its view may see: {check_id, verdict, finding | None,
    gate_failures, runs: [verdicts]}. `llm(system, user) -> str`."""
    rubric = rubric or ws.rubric()
    crit, chk = next(((c, k) for c in rubric["criteria"] for k in c["checks"] if k["check_id"] == check_id), (None, None))
    if chk is None:
        raise KeyError(check_id)
    view, kinds = CHECKERS[check_id]
    arts = [a for a in ws.artefacts() if a["kind"] in kinds]
    if not arts:
        return {"check_id": check_id, "verdict": "unclear", "finding": None, "gate_failures": [],
                "runs": [], "note": f"no artefact of kind {' / '.join(kinds)} registered"}
    texts = {a["artefact_id"]: ws.text(a["artefact_id"]) for a in arts}
    block = "\n\n".join(f"=== {aid} ({a['kind']}) ===\n{texts[aid][0][:12000]}" for aid, a in
                        ((a["artefact_id"], a) for a in arts))
    user = PROMPT.format(view=view, question=chk["question"], pass_definition=chk["pass_definition"], artefacts=block)
    verdicts, candidates, failures = [], [], []
    for _ in range(runs):
        d = _parse(llm(cs.INTEGRITY, user))
        if not d or d.get("verdict") not in ("pass", "fail"):
            verdicts.append("invalid")
            continue
        verdicts.append(d["verdict"])
        aid = d.get("artefact_id") if d.get("artefact_id") in texts else next(
            (k for k, (t, _) in texts.items() if (d.get("quote") or "") in t), arts[0]["artefact_id"])
        text, sha = texts[aid]
        span = cs.exact_span(d.get("quote") or "", text)
        if span is None:   # the quote may come from another registered artefact
            aid, span = next(((k, sp) for k, (t, _) in texts.items() if (sp := cs.exact_span(d.get("quote") or "", t))),
                             (aid, None))
            text, sha = texts[aid]
        d["quote"] = span if span is not None else d.get("quote")
        f = {"finding_id": uuid.uuid4().hex[:10], "artefact_id": aid, "artefact_sha": sha,
             "quote": (d.get("quote") or "").strip(), "criterion_id": crit["criterion_id"], "check_id": check_id,
             "verdict": d["verdict"], "issue": (d.get("issue") or "").strip(),
             "question_to_learner": (d.get("question") or "").strip(), "hint_level": 0,
             "lecture_refs": [], "evidence_refs": [], "reviewer_view": view, "confidence": None}
        why = cs.gate_finding(f, text, sha)
        (failures.append({"run": len(verdicts), "why": why}) if why else candidates.append(f))
    valid = [v for v in verdicts if v != "invalid"]
    verdict = valid[0] if len(valid) == runs and len(set(valid)) == 1 else "unclear"
    finding = next((c for c in candidates if c["verdict"] == verdict), None) or (candidates[0] if candidates else None)
    if finding:
        finding = {**finding, "verdict": verdict, "confidence": round(valid.count(finding["verdict"]) / runs, 2)}
        if lecture_search and verdict != "pass":
            try:
                finding["lecture_refs"] = lecture_search(f"{chk['question']} {chk['pass_definition']}")[:3]
            except Exception:
                pass
    return {"check_id": check_id, "verdict": verdict, "finding": finding, "gate_failures": failures, "runs": verdicts}


def review(ws: cs.Workspace, llm, checks: list[str] | None = None, lecture_search=None, session=None) -> dict:
    """All checkers; findings to findings.jsonl and the session; gate pass rate to telemetry."""
    results = [run_check(ws, c, llm, lecture_search=lecture_search) for c in (checks or list(CHECKERS))]
    shown = [r["finding"] for r in results if r["finding"]]
    attempted = sum(len([v for v in r["runs"] if v != "invalid"]) for r in results)
    gated_out = sum(len(r["gate_failures"]) for r in results)
    for f in shown:
        ws.log("findings.jsonl", f)
    rate = round(1 - gated_out / attempted, 3) if attempted else None
    ws.log("telemetry.jsonl", {"t": cs.now_iso(), "kind": "review", "checks": len(results), "findings": len(shown),
                               "gate_pass_rate": rate, "invalid_runs": sum(r["runs"].count("invalid") for r in results)})
    if session:
        with (session / "session.jsonl").open("a") as fh:
            fh.write(json.dumps({"t": cs.now_iso(), "review": [{k: r[k] for k in ("check_id", "verdict", "runs")}
                                                                for r in results]}) + "\n")
    return {"results": results, "gate_pass_rate": rate}


# ------------------------------------------------------------------ unit economics (deterministic)
def unit_econ(x: dict) -> dict:
    """Cost per successful outcome with retries and failures, at median and heavy (p90) usage,
    and gross margin. Inputs are the learner's: cost_per_call, calls_per_task, retry_rate,
    failure_rate, price_per_task (or price_per_user_month), tasks_per_user_month_median,
    tasks_per_user_month_p90; optional learner_* numbers are checked against ours."""
    c, n = float(x["cost_per_call"]), float(x["calls_per_task"])
    retry, fail = float(x.get("retry_rate", 0)), float(x.get("failure_rate", 0))
    if not 0 <= fail < 1:
        raise ValueError("failure_rate must be in [0, 1)")
    per_attempt = c * n * (1 + retry)
    per_success = per_attempt / (1 - fail)
    out = {"cost_per_attempt": round(per_attempt, 6), "cost_per_successful_outcome": round(per_success, 6)}
    for k in ("median", "p90"):
        t = x.get(f"tasks_per_user_month_{k}")
        if t is not None:
            out[f"cost_per_user_month_{k}"] = round(per_success * float(t), 4)
    price_task = x.get("price_per_task")
    if price_task:
        out["gross_margin_per_task"] = round((float(price_task) - per_success) / float(price_task), 4)
    price_user = x.get("price_per_user_month")
    if price_user:
        for k in ("median", "p90"):
            if f"cost_per_user_month_{k}" in out:
                out[f"gross_margin_user_{k}"] = round((float(price_user) - out[f"cost_per_user_month_{k}"]) / float(price_user), 4)
    checks = []
    for k, v in x.items():
        if k.startswith("learner_") and k[8:] in out:
            ok = abs(float(v) - out[k[8:]]) <= max(0.01 * abs(out[k[8:]]), 1e-6)
            checks.append({"field": k[8:], "learner": float(v), "computed": out[k[8:]], "ok": ok})
    out["checks"] = checks
    if "gross_margin_user_p90" in out and out["gross_margin_user_p90"] < 0:
        out["warning"] = "heavy users cost more than they pay"
    return out


# ------------------------------------------------------------------ pre-mortem
INCIDENT_CATEGORIES = {
    "hallucinated_policy": ("policy", "hallucinat", "invent", "made up"),
    "prompt_injection": ("injection", "jailbreak", "malicious input"),
    "excessive_agency": ("agency", "autonomous", "permission", "unauthori"),
    "personal_data_leak": ("personal data", "pii", "privacy", "leak", "dpdp"),
    "regression_after_update": ("regression", "model update", "new version", "drift"),
    "cost_blowup": ("cost", "retry", "token", "budget"),
    "over_reliance": ("over-reliance", "overreliance", "automation bias", "trust too"),
    "bias_unfair_outcome": ("bias", "fair", "disparit"),
}


def premortem_propose(learner_risks: list[dict]) -> list[dict]:
    """Candidate risks from incident categories the learner's own list does not touch, as
    questions; classification and mitigation stay the learner's to write."""
    text = " ".join((r.get("text") or "") for r in learner_risks).lower()
    out = []
    for cat, keys in INCIDENT_CATEGORIES.items():
        if not any(k in text for k in keys):
            out.append({"risk_id": f"cp-{cat}", "author": "copilot", "category": cat,
                        "text": f"What happens to your product if a {cat.replace('_', ' ')} incident occurs?",
                        "class": None, "mitigation": None, "owner": None})
    return out


PREMORTEM_CLASSES = ("tiger", "paper_tiger", "elephant")


# ------------------------------------------------------------------ internal FAQ (questions only)
FAQ_PROMPT = """LEARNER'S ARTEFACTS (read only):
{artefacts}

List {n} hard questions an internal reviewer (finance, risk or a customer) would ask about this product.
Questions only: no answers, no suggestions. Reply with JSON only: {{"questions": ["...?", "...?"]}}"""


def faq_questions(ws: cs.Workspace, llm, n: int = 6) -> list[str]:
    arts = ws.artefacts()
    block = "\n\n".join(f"=== {a['artefact_id']} ({a['kind']}) ===\n{ws.text(a['artefact_id'])[0][:8000]}" for a in arts)
    d = _parse(llm(cs.INTEGRITY, FAQ_PROMPT.format(artefacts=block, n=n))) or {}
    out = []
    for q in d.get("questions") or []:
        q = str(q).strip()
        if q.endswith("?") and q.count("?") == 1 and cs.words(q) <= 40 and not cs.REWRITE_CUE.search(q) \
                and not cs.PRAISE.search(q):
            out.append(q)
    return out[:n]
