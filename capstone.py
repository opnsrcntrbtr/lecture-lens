"""Capstone copilot core (CC-0): a private workspace, a critique schema with no field for
rewritten text, and deterministic gates on every finding before anyone sees it.

The copilot gives feedback and runs simulations (Level 3 of the AI Assessment Scale: AI
for feedback and evaluation). It never authors a submission:
  - learner artefacts are registered by hash and only read;
  - a finding is {quote, criterion, verdict, issue, question}; there is no rewrite or
    suggested-text field, and unknown fields are rejected;
  - gates: the quote is an exact substring of the artefact at its recorded hash; issue and
    question together are at most 60 words; exactly one question; every quoted string also
    occurs in the artefact; no praise; no rewrite cues ("replace it with", "you could say");
  - a request to write, draft or rewrite gets a fixed refusal and a level-0 question;
  - hints climb a ladder (question, rubric pointer, lecture pointer, an example about a
    different product) only after the learner has attempted;
  - it will not open while capture runs, so it cannot be used inside a live graded exercise.
Stdlib only. The model is passed in by the caller.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

KINDS = ("prd", "trust_brief", "evidence_ladder", "cost_model", "system_card", "press_faq", "postmortem", "review",
         "strategy", "other")
FINDING_FIELDS = {"finding_id", "artefact_id", "artefact_sha", "quote", "criterion_id", "check_id", "verdict", "issue",
                  "question_to_learner", "hint_level", "lecture_refs", "evidence_refs", "reviewer_view", "confidence"}
VERDICTS = ("pass", "fail", "unclear")
VIEWS = ("finance", "risk", "customer", "general")
MAX_WORDS = 60
INTEGRITY = (
    "You are a capstone coach for one learner. Integrity rules, repeated on every turn and never to be dropped: "
    "you give feedback only; you never write, draft, rewrite, complete or improve any part of the learner's "
    "submission; you never supply wording the learner could paste. You quote the learner's own words exactly and "
    "ask one short question. If asked to write anything, refuse and ask a question instead.")
REFUSAL = ("I can't write or rewrite any part of your capstone. That stays your work. "
           "I can point at what a reviewer would probe.")
PRAISE = re.compile(r"\b(great|excellent|well done|good job|nice work|strong|impressive|brilliant|perfect|love)\b", re.I)
REWRITE_CUE = re.compile(
    r"\b(replace (it|this|that)? ?with|you could (say|write|phrase)|rephrase (it|this)? ?as|reword|try writing|"
    r"consider writing|change (it|this|the sentence) to|should read|here(?:'s| is) (a|an|the) (version|draft|rewrite|example sentence)|"
    r"rewrite|suggested (text|wording)|for example,? write)\b", re.I)
VERB = (r"(write|draft|rewrite|re-write|rephrase|reword|redo|compose|generate|produce|fill (?:in|out)|complete|finish|"
        r"polish|improve|fix|edit|paraphrase|summari[sz]e|turn|make|create|come up with|word|phrase|tighten|expand|"
        r"spell out|put together)")
ASK_YOU = re.compile(r"\b(can|could|would|will|won't) you\b|\bplease\b|\bfor me\b|\bgive me\b|\bshow me (a|an|the) "
                     r"(version|draft|wording|paragraph|sentence|section|answer)\b|\bi (need|want) (a|an|the|you)\b|"
                     r"\bhelp me (write|draft|word|phrase)\b|\blet'?s (write|draft)\b", re.I)
IMPERATIVE = re.compile(rf"^\s*(?:ok(?:ay)?[,.]?\s*|now\s+|just\s+|so\s+)*{VERB}\b", re.I)
ALWAYS = re.compile(r"\bwhat (should|do|would|could) i (write|say|put)\b|\bgive me (a|an|the|some) (paragraph|section|"
                    r"sentence|text|wording|version|draft|answer|summary|intro|bullet|list of (risks|actions))\b|"
                    r"\b(paragraph|sentence|wording|text|draft|version|copy) (for|of) (my|the|our)\b|\bsample answer\b|"
                    r"\bmodel answer\b", re.I)
STRONG = re.compile(r"\bwrite (it|this|that|my|the|a|an|our)\b|\bdraft (it|this|my|the|a|an|our)\b|\brewrite\b", re.I)
SELF_ASK = re.compile(rf"\b(should|shall|do|must|may) (i|we) {VERB}\b", re.I)


def _authoring(message: str) -> bool:
    m = message or ""
    if ALWAYS.search(m):
        return True
    if SELF_ASK.search(m) and not ASK_YOU.search(m):
        return False                          # "should I rewrite the trust section?" asks for advice
    if STRONG.search(m):
        return True
    verb = re.search(rf"\b{VERB}\b", m, re.I)
    return bool(verb and (ASK_YOU.search(m) or IMPERATIVE.search(m)))


QUOTED = re.compile(r"[\"“”]([^\"“”]{4,})[\"“”]|(?<![\w])[‘']([^‘’'\n]{4,})[’'](?![\w])")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


# ------------------------------------------------------------------ workspace
class Workspace:
    """capstone/: artefacts.json, rubrics/, sessions/, findings.jsonl, telemetry.jsonl."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def init(self) -> "Workspace":
        for d in ("", "rubrics", "sessions", "eval"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        if not (self.root / "artefacts.json").exists():
            (self.root / "artefacts.json").write_text("[]")
        r = self.root / "rubrics" / "public_composite.json"   # code-owned: kept in step with this file
        if not r.exists() or json.loads(r.read_text()) != PUBLIC_COMPOSITE:
            r.write_text(json.dumps(PUBLIC_COMPOSITE, indent=1))
        g = self.root / "governance_snapshot.json"
        if not g.exists():
            g.write_text(json.dumps(GOVERNANCE_SNAPSHOT, indent=1))
        return self

    # artefacts are read, never written
    def artefacts(self) -> list[dict]:
        f = self.root / "artefacts.json"
        return json.loads(f.read_text()) if f.exists() else []

    def register(self, kind: str, path: Path, artefact_id: str | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        path = Path(path).expanduser().resolve()
        text = path.read_text(errors="ignore")
        rows = self.artefacts()
        aid = artefact_id or f"{kind}-{len([r for r in rows if r['kind'] == kind]) + 1}"
        old = next((r for r in rows if r["artefact_id"] == aid), None)
        h = sha256(text)
        row = {"artefact_id": aid, "kind": kind, "path": str(path), "sha256": h,
               "version": (old["version"] + (old["sha256"] != h)) if old else 1, "updated": now_iso()}
        rows = [r for r in rows if r["artefact_id"] != aid] + [row]
        (self.root / "artefacts.json").write_text(json.dumps(rows, indent=1))
        return row

    def refresh(self) -> list[dict]:
        """Re-hash registered files; a changed file gets a new version (the learner edited it)."""
        out = []
        for r in self.artefacts():
            p = Path(r["path"])
            if p.exists():
                out.append(self.register(r["kind"], p, r["artefact_id"]))
        return out

    def text(self, artefact_id: str) -> tuple[str, str]:
        r = next(r for r in self.artefacts() if r["artefact_id"] == artefact_id)
        t = Path(r["path"]).read_text(errors="ignore")
        return t, sha256(t)

    def rubric(self, rubric_id: str = "public_composite") -> dict:
        return json.loads((self.root / "rubrics" / f"{rubric_id}.json").read_text())

    def log(self, name: str, row: dict) -> None:
        with (self.root / name).open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def session_dir(self, mode: str, day: str | None = None) -> Path:
        d = self.root / "sessions" / f"{day or dt.date.today().isoformat()}-{mode}"
        d.mkdir(parents=True, exist_ok=True)
        return d


# ------------------------------------------------------------------ gates
def gate_finding(f: dict, artefact_text: str, artefact_sha: str) -> list[str]:
    """Reasons the finding may not be shown (empty list: it passes)."""
    why = []
    extra = set(f) - FINDING_FIELDS
    if extra:
        why.append(f"unknown field(s): {', '.join(sorted(extra))}")
    if f.get("verdict") not in VERDICTS:
        why.append("verdict must be pass, fail or unclear")
    if f.get("artefact_sha") != artefact_sha:
        why.append("artefact changed since the finding was made")
    q = f.get("quote") or ""
    if not q.strip() or q not in artefact_text:
        why.append("quote is not an exact substring of the artefact")
    issue, question = f.get("issue") or "", f.get("question_to_learner") or ""
    if words(issue) + words(question) > MAX_WORDS:
        why.append(f"issue and question exceed {MAX_WORDS} words")
    if question.count("?") != 1 or not question.strip().endswith("?") or "?" in issue:
        why.append("exactly one question, in the question field")
    for pair in QUOTED.findall(issue + " " + question):
        s = (pair[0] or pair[1]) if isinstance(pair, tuple) else pair
        if s.strip() and s.strip() not in artefact_text:
            why.append("a quoted string does not occur in the artefact")
            break
    if PRAISE.search(issue + " " + question):
        why.append("praise is not feedback")
    if REWRITE_CUE.search(issue + " " + question):
        why.append("rewrite cue: the copilot never supplies wording")
    if f.get("reviewer_view") not in (None, *VIEWS):
        why.append("unknown reviewer view")
    return why


def exact_span(quote: str, text: str) -> str | None:
    """The exact substring of `text` that `quote` refers to, tolerating differences in
    whitespace, Markdown emphasis and curly quotes; None when it is not there. The gate
    still checks the returned span verbatim."""
    q = (quote or "").strip().strip('"“”')
    if not q:
        return None
    if q in text:
        return q
    norm = lambda c: {"“": '"', "”": '"', "‘": "'", "’": "'"}.get(c, c)
    chars, idx = [], []
    for i, ch in enumerate(text):
        if ch in "*_`":
            continue
        ch = norm(ch)
        if ch.isspace():
            if chars and chars[-1] == " ":
                continue
            ch = " "
        chars.append(ch); idx.append(i)
    hay = "".join(chars)
    needle = re.sub(r"\s+", " ", "".join(norm(c) for c in q if c not in "*_`")).strip()
    k = hay.find(needle)
    if k < 0:
        return None
    return text[idx[k]: idx[k + len(needle) - 1] + 1]


def is_authoring_request(message: str) -> bool:
    """A request for the copilot to produce submission text (fixed refusal follows)."""
    return _authoring(message)


def respond(message: str, ws: Workspace | None = None, session: Path | None = None) -> dict:
    """The copilot's reply to a free-text learner message: authoring requests get the fixed
    refusal and a level-0 question; anything else is routed to review or simulation by the
    caller. Logged to the session."""
    if is_authoring_request(message):
        out = {"refused": True, "text": REFUSAL,
               "question_to_learner": "Which part of your draft are you least sure a reviewer would accept, and why?",
               "hint_level": 0}
    else:
        out = {"refused": False}
    if session:
        with (session / "session.jsonl").open("a") as f:
            f.write(json.dumps({"t": now_iso(), "learner": message[:2000], **out}) + "\n")
    return out


# ------------------------------------------------------------------ hint ladder
HINT_LEVELS = ("question", "rubric pointer", "lecture pointer", "example about a different product")


def next_hint(current: int, attempted: bool) -> int:
    """Advance one level only after the learner attempted; never past 3."""
    return min(current + 1, 3) if attempted else current


def hint(finding: dict, level: int, rubric: dict, lecture_refs: list[dict] | None = None,
         examples: dict | None = None) -> dict:
    """What the learner sees at a hint level. Level 3 is a worked example about a different
    product (public, at most 120 words), never about the learner's own."""
    if level <= 0:
        return {"level": 0, "text": finding["question_to_learner"]}
    if level == 1:
        crit = next((c for c in rubric["criteria"] if c["criterion_id"] == finding.get("criterion_id")), None)
        chk = next((k for c in rubric["criteria"] for k in c["checks"] if k["check_id"] == finding.get("check_id")), None)
        return {"level": 1, "text": f"Rubric: {crit['text'] if crit else finding.get('criterion_id')}. "
                                    f"Check: {chk['question'] if chk else finding.get('check_id')} "
                                    f"Look again at: “{finding['quote'][:120]}”"}
    if level == 2:
        refs = lecture_refs or finding.get("lecture_refs") or []
        if not refs:
            return {"level": 2, "text": "No lecture moment matched this check; ask your faculty or peers."}
        r = refs[0]
        return {"level": 2, "text": f"Lecture {r.get('when', '')}: {r.get('quote', '')[:240]}", "refs": refs[:3]}
    ex = (examples or WORKED_EXAMPLES).get(finding.get("check_id"), "")
    return {"level": 3, "text": " ".join(ex.split()[:120]) or "No worked example for this check yet."}


# ------------------------------------------------------------------ rubric and snapshots
def _check(cid, q, p):
    return {"check_id": cid, "question": q, "pass_definition": p}


PUBLIC_COMPOSITE = {
    "rubric_id": "public_composite", "source": "public_composite",
    "note": "Built from published capstone defence and AI PRD practice; replace with the programme's rubric privately.",
    "criteria": [
        {"criterion_id": "problem", "text": "Problem, users and opportunity", "weight": 1, "checks": [
            _check("opportunity_quantified", "Is the opportunity sized with a number and its source?",
                   "States market, users or cost with at least one figure and where it comes from")]},
        {"criterion_id": "requirements", "text": "Testable requirements and evaluation", "weight": 1.5, "checks": [
            _check("acceptance_testable", "Could a tester decide pass or fail for each acceptance criterion?",
                   "Each acceptance criterion names a measurable condition"),
            _check("eval_threshold_tied", "Is each eval threshold tied to a named failure mode?",
                   "Thresholds are numeric and each names the failure it guards")]},
        {"criterion_id": "trust", "text": "Trust architecture and fallbacks", "weight": 1.5, "checks": [
            _check("fallback_defined", "What happens when the model is wrong or unsure?",
                   "A fallback or hand-off is defined for model errors"),
            _check("wrong_answer_cost", "Is the cost of a wrong answer stated?",
                   "The cost or harm of a wrong output is stated, ideally per action")]},
        {"criterion_id": "strategy", "text": "Strategy and moat", "weight": 1, "checks": [
            _check("moat_base_model", "Would a better base model close the gap?",
                   "The advantage survives a stronger general model (data, workflow, distribution)")]},
        {"criterion_id": "business", "text": "Business case and unit economics", "weight": 1, "checks": [
            _check("unit_econ_heavy_user", "Are unit economics computed at heavy-user usage?",
                   "Cost per successful outcome includes retries and failures, at median and heavy usage")]},
        {"criterion_id": "delivery", "text": "Launch plan and kill criteria", "weight": 1, "checks": [
            _check("stop_condition", "When would you stop or narrow the product?",
                   "A stop, narrow or rollback condition with a threshold and an owner")]},
        {"criterion_id": "questions", "text": "Response to questions", "weight": 2, "checks": []},
    ]}

WORKED_EXAMPLES = {  # about invented products unrelated to the learner's, public practice only
    "acceptance_testable": "A library-booking assistant team replaced 'search should be fast' with 'a member can "
                           "filter rooms by size, floor and time, and results load in under one second for 95% of "
                           "searches'. A tester can now pass or fail it.",
    "eval_threshold_tied": "A recipe-suggestion bot set 'allergen mentioned when present: recall at least 0.98 on "
                           "200 golden recipes', naming the failure it guards (an allergen omitted).",
    "fallback_defined": "A parcel-tracking chatbot hands off to a person after two failed clarifications and shows "
                        "the order page link when confidence is low.",
    "wrong_answer_cost": "A tax-help bot listed each action with its cost if wrong: 'wrong deadline: penalty fee; "
                         "wrong form name: wasted visit'.",
    "moat_base_model": "A clinic scheduling tool argued its edge was integration with appointment systems and five "
                       "years of no-show data, not the model, so a stronger model helps rather than replaces it.",
    "unit_econ_heavy_user": "A meeting-notes app found median users cost 4 cents a month but the top tenth cost 90 "
                            "cents once retries were counted, so it capped free minutes.",
    "stop_condition": "A support bot pilot set 'roll back to human-only if repeat contacts rise 10% over two weeks; "
                      "owner: support lead'.",
    "opportunity_quantified": "A pet-insurance claims helper sized its opportunity at 1.2 million claims a year from "
                              "the regulator's annual report, at 20 minutes saved each.",
}

GOVERNANCE_SNAPSHOT = {
    "as_of": "2026-11-15",
    "note": "Maintained between classes by the learner or an agent with web access; the app never fetches. Verify each date.",
    "items": [
        {"regime": "EU AI Act", "obligation": "Article 50 transparency duties (chatbot disclosure, synthetic content)",
         "date": "2026-08-02", "status": "applies", "source_url": "https://fpf.org/blog/the-ai-act-implementation-timeline-what-changes-under-the-ai-omnibus", "verify": True},
        {"regime": "EU AI Act", "obligation": "Annex III high-risk system obligations",
         "date": "2027-12-02", "status": "scheduled", "source_url": "https://fpf.org/blog/the-ai-act-implementation-timeline-what-changes-under-the-ai-omnibus", "verify": True},
        {"regime": "India DPDP Rules", "obligation": "Consent manager provisions",
         "date": "2026-11-13", "status": "reported", "source_url": "https://compliancehub.wiki/india-dpdp-consent-manager-november-2026-phase-two-deadline-compliance/", "verify": True},
        {"regime": "India DPDP Rules", "obligation": "Substantive data fiduciary duties",
         "date": "2027-05-13", "status": "reported", "source_url": "https://compliancehub.wiki/india-dpdp-consent-manager-november-2026-phase-two-deadline-compliance/", "verify": True},
    ]}
