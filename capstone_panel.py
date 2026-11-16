"""CC-5 panel defence and leadership review simulator (text).

Up to three personas, each seeing a different subset of the learner's artefacts and a
question quota tied to rubric criteria. Turn rules are enforced in code, not by the prompt:
one question per turn, at most 40 words; at most one follow-up per answer, and it must
quote at least five consecutive words of that answer; a hard time box. A persona's draft
that concedes, praises or breaks a rule is retried once and then replaced by a template
question, so a session never stacks questions or concedes without new evidence. Personas
never score. After the session the learner scores themselves first; then a separate critic
rates the transcript with binary checks, quoting the learner's answers.

Files: capstone/sessions/<date>-panel-<seed>/{panel.json, turns.jsonl, state.json, score.json}
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re

import capstone as cs

MAX_Q_WORDS = 40
MIN_QUOTE = 5
INTERRUPT_AT = 150
PRESSURES = ("neutral", "sceptical", "interrupting")
TYPES = ("panel", "leadership")


def _c(cid, text, weight, question, passes):
    return {"criterion_id": cid, "text": text, "weight": weight,
            "check": {"check_id": f"panel_{cid}", "question": question, "pass_definition": passes}}


RUBRICS = {
    "panel": [
        _c("problem_stakeholders", "Problem and stakeholders", 1, "Who has the problem, and how big is it?",
           "The answer names the users or stakeholders and sizes the problem with a figure or a source"),
        _c("measurable_goals", "Measurable goals", 1, "How will you know it worked?",
           "The answer gives a metric with a target or threshold"),
        _c("decisions_alternatives", "Key decisions and alternatives rejected", 1,
           "What did you consider and reject, and why?", "The answer names at least one rejected alternative and a reason"),
        _c("technical_feasibility", "Technical approach and feasibility evidence", 1,
           "What evidence shows the approach works?", "The answer cites a test, prototype, measurement or prior result"),
        _c("evaluation_limits", "Evaluation and limitations", 1, "What does your evaluation not cover?",
           "The answer states at least one limitation or gap in the evaluation"),
        _c("business_case", "Business case", 1, "Why is this worth paying for at heavy usage?",
           "The answer gives a cost, price or margin figure, or the assumption behind one"),
        _c("responsible_ai", "Responsible-AI posture", 1, "What happens when the model is wrong, and who is harmed?",
           "The answer names a harm and a safeguard, fallback or human check"),
        _c("delivery", "Delivery", 1, "What ships first, and what would make you stop?",
           "The answer names a first release scope or a stop or rollback condition"),
        _c("response_to_questions", "Response to questions", 3, "", ""),
    ],
    "leadership": [
        _c("product_execution", "Product execution", 1, "What did you ship, by when, and what slipped?",
           "The answer names a deliverable with a date or a slip and its cause"),
        _c("customer_insight", "Customer insight", 1, "What did you learn from users that changed the plan?",
           "The answer names a user finding and the change it caused"),
        _c("product_strategy", "Product strategy", 1, "Why this bet rather than the next best one?",
           "The answer compares with an alternative and gives a reason"),
        _c("influencing_people", "Influencing people", 1, "Who disagreed, and how did you bring them along?",
           "The answer names a disagreement and how it was resolved"),
        _c("not_doing", "What you decided not to do", 1, "What did you decide not to do, and why?",
           "The answer names something left out and the reason"),
        _c("response_to_questions", "Response to questions", 3, "", ""),
    ],
}

PERSONAS = {
    "panel": [
        {"persona_id": "methodologist", "role": "faculty methodologist",
         "kinds": ("prd", "evidence_ladder", "system_card"),
         "criteria": ("measurable_goals", "technical_feasibility", "evaluation_limits")},
        {"persona_id": "product_leader", "role": "industry product leader",
         "kinds": ("strategy", "press_faq", "cost_model", "prd"),
         "criteria": ("problem_stakeholders", "business_case", "decisions_alternatives")},
        {"persona_id": "risk_examiner", "role": "risk and governance examiner",
         "kinds": ("trust_brief", "system_card", "prd"),
         "criteria": ("responsible_ai", "delivery", "evaluation_limits")},
    ],
    "leadership": [
        {"persona_id": "vp_product", "role": "VP of product", "kinds": ("strategy", "prd", "cost_model"),
         "criteria": ("product_execution", "product_strategy", "not_doing")},
        {"persona_id": "customer_lead", "role": "head of customer success", "kinds": ("press_faq", "prd"),
         "criteria": ("customer_insight",)},
        {"persona_id": "peer_director", "role": "engineering director", "kinds": ("prd", "trust_brief", "system_card"),
         "criteria": ("influencing_people", "product_execution")},
    ],
}

CONCEDE = re.compile(r"\byou(?:'re| are) (?:right|correct)\b|\bfair (?:point|enough)\b|\bthat makes sense\b|"
                     r"\bi (?:agree|accept|concede)\b|\bgood (?:answer|point)\b|\bi(?:'m| am) (?:convinced|satisfied)\b|"
                     r"\bthat settles\b|\bno further questions\b|\bpoint taken\b|\bthat answers (?:it|my question)\b", re.I)
DEFENSIVE = re.compile(r"\bobviously\b|\bas i (?:already )?said\b|\bthat'?s not relevant\b|\byou don'?t understand\b|"
                       r"\bthat'?s irrelevant\b|\bi already answered\b", re.I)
EVIDENCE = re.compile(r"\d|\bbecause\b|\bwe (?:tested|measured|found|ran|saw)\b|\bdata\b|\bpilot\b|\binterview", re.I)

STYLE = {
    "neutral": "You are evidence-seeking and polite.",
    "sceptical": "You are sceptical and brisk: you assume a claim is unproven until shown otherwise.",
    "interrupting": "You are sceptical and impatient: you cut long answers short and press on the first claim.",
}

ASK_PROMPT = """You are the {role} on a capstone review panel. {style}
You never concede, never praise, never agree that a point is settled, and never suggest wording.
Without new evidence, you keep pressing.

WHAT YOU HAVE READ (the learner's own documents, read only):
{context}

CRITERION YOU ARE PROBING: {criterion}
SAMPLE ANGLE: {angle}

Ask exactly one question of at most 40 words, grounded in something the learner wrote.
Reply with JSON only: {{"question": "<one question ending with ?>"}}"""

FOLLOW_PROMPT = """You are the {role} on a capstone review panel. {style}
You never concede, never praise, never agree that a point is settled, and never suggest wording.
Without new evidence, you keep pressing.

YOUR QUESTION: {question}
THE LEARNER'S ANSWER: {answer}

Ask one follow-up question of at most 40 words, with exactly one question mark. It must quote at
least five consecutive words from the learner's answer (count them), copied exactly, inside double
quotes. Example shape: You said "<five or more words of theirs>". <one question>?
Reply with JSON only: {{"quote": "<five or more consecutive words copied from the answer>",
"question": "<one question containing that quote, ending with ?>"}}"""

CRITIC_PROMPT = """You rate one criterion of a capstone panel transcript. Judge only the learner's answers.
CRITERION: {criterion}
PASSES WHEN: {passes}

TRANSCRIPT (questions and the learner's answers):
{qa}

Reply with JSON only: {{"verdict": "pass" or "fail",
"answer_quote": "<exact words copied from one of the learner's answers that decide the verdict>"}}"""


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def wc(s: str) -> int:
    return len((s or "").split())


def _json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def question_problems(q: str) -> list[str]:
    why = []
    q = (q or "").strip()
    if not q:
        return ["empty"]
    if q.count("?") != 1 or not q.endswith("?"):
        why.append("exactly one question mark, at the end")
    if wc(q) > MAX_Q_WORDS:
        why.append(f"over {MAX_Q_WORDS} words")
    if CONCEDE.search(q):
        why.append("concession")
    if cs.PRAISE.search(q):
        why.append("praise")
    if cs.REWRITE_CUE.search(q):
        why.append("suggests wording")
    return why


def followup_problems(q: str, quote: str, answer: str) -> list[str]:
    why = question_problems(q)
    span = cs.exact_span(quote or "", answer or "")
    if span is None or wc(span) < MIN_QUOTE:
        why.append(f"quote of at least {MIN_QUOTE} consecutive words from the answer")
    elif cs.exact_span(" ".join(span.split()[:MIN_QUOTE]), q) is None:
        why.append("the question must contain the quote")
    return why


TRAIL = {"a", "an", "the", "for", "of", "to", "and", "or", "in", "on", "with", "is", "it", "that", "this", "but"}


def template_followup(answer: str) -> tuple[str, str]:
    """Deterministic follow-up on the answer's longest clause (five to ten words, cut at a
    comma or semicolon where possible), then an evidence question. Always within the rules."""
    text = re.sub(r"[?\"“”]", "", answer or "")
    clauses = [c.strip(" ,;:.") for c in re.split(r"[,;:.!]\s+|\s+-\s+", text)]
    clauses = [c for c in clauses if wc(c) >= MIN_QUOTE] or [text.strip(" ,;:.")]
    base = max(clauses, key=wc)
    ws_ = base.split()[:10]
    while len(ws_) > MIN_QUOTE and ws_[-1].lower() in TRAIL:
        ws_.pop()
    quote = " ".join(ws_).rstrip(",;:.")
    return f'You said "{quote}". What evidence would change your mind on that?', quote


def is_cold(ws: cs.Workspace) -> bool:
    """Every third panel session is a cold mock: no hints, no criterion shown with questions."""
    n = len(list((ws.root / "sessions").glob("*-panel-*")))
    return (n + 1) % 3 == 0


class Panel:
    def __init__(self, ws: cs.Workspace, seed: int, day: str | None = None):
        self.ws, self.seed = ws, seed
        self.dir = ws.root / "sessions" / f"{day or dt.date.today().isoformat()}-panel-{seed}"

    # files
    def cfg(self) -> dict:
        return json.loads((self.dir / "panel.json").read_text())

    def state(self) -> dict:
        return json.loads((self.dir / "state.json").read_text())

    def _save(self, st: dict) -> None:
        (self.dir / "state.json").write_text(json.dumps(st))

    def turns(self) -> list[dict]:
        p = self.dir / "turns.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def _turn(self, row: dict) -> dict:
        with (self.dir / "turns.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def start(self, session_type: str = "panel", pressure: str = "neutral", minutes: int | None = None,
              now: dt.datetime | None = None) -> dict:
        if session_type not in TYPES or pressure not in PRESSURES:
            raise ValueError("bad session type or pressure")
        cold = is_cold(self.ws)
        self.dir.mkdir(parents=True, exist_ok=True)
        rng = random.Random(self.seed)
        personas = []
        for p in PERSONAS[session_type]:
            arts = [a for a in self.ws.artefacts() if a["kind"] in p["kinds"]]
            personas.append({"persona_id": p["persona_id"], "role": p["role"],
                             "private_context": [a["artefact_id"] for a in arts],
                             "quota": {c: 1 for c in p["criteria"]}})
        queue = [(p["persona_id"], c) for p in personas for c in p["quota"]]
        rng.shuffle(queue)
        by_p = {}
        for pid, c in queue:   # round-robin by persona so no one asks twice in a row
            by_p.setdefault(pid, []).append(c)
        order, pids = [], sorted(by_p, key=lambda k: queue.index((k, by_p[k][0])))
        while any(by_p.values()):
            for pid in pids:
                if by_p[pid]:
                    order.append([pid, by_p[pid].pop(0)])
        tb = minutes or {"neutral": 25, "sceptical": 20, "interrupting": 20}[pressure]
        cfg = {"session_type": session_type, "personas": personas, "time_box_min": tb, "pressure": pressure,
               "cold": cold, "seed": self.seed}
        (self.dir / "panel.json").write_text(json.dumps(cfg, indent=1))
        self._save({"started": (now or now_utc()).isoformat(timespec="seconds"), "queue": order,
                    "awaiting_answer": False, "followed_up": [], "rejected": [], "done": False})
        return self.view()

    def _context(self, persona: dict) -> str:
        parts = []
        for aid in persona["private_context"]:
            try:
                parts.append(f"=== {aid} ===\n{self.ws.text(aid)[0][:5000]}")
            except StopIteration:
                continue
        return "\n\n".join(parts) or "(no documents registered for this reviewer)"

    def _criterion(self, cid: str) -> dict:
        return next(c for c in RUBRICS[self.cfg()["session_type"]] if c["criterion_id"] == cid)

    def time_left(self, now: dt.datetime | None = None) -> float:
        st, cfg = self.state(), self.cfg()
        el = ((now or now_utc()) - dt.datetime.fromisoformat(st["started"])).total_seconds() / 60
        return round(cfg["time_box_min"] - el, 1)

    def next_question(self, llm, now: dt.datetime | None = None) -> dict:
        st, cfg = self.state(), self.cfg()
        if st["done"]:
            return self.view()
        if st["awaiting_answer"]:
            return {**self.view(), "error": "answer the current question first"}
        if self.time_left(now) <= 0:
            st["done"] = True
            self._save(st)
            return {**self.view(), "note": "time is up"}
        turns = self.turns()
        last_q = next((t for t in reversed(turns) if t["speaker"] != "learner"), None)
        last_a = turns[-1] if turns and turns[-1]["speaker"] == "learner" else None
        persona_by = {p["persona_id"]: p for p in cfg["personas"]}
        style = STYLE[cfg["pressure"]]
        if last_q and last_a and not last_q["is_followup"] and last_q["turn"] not in st["followed_up"]:
            p = persona_by[last_q["speaker"]]
            answer = last_a["text"]
            if cfg["pressure"] == "interrupting" and wc(answer) > INTERRUPT_AT:
                answer = " ".join(answer.split()[:INTERRUPT_AT])
            q, quote, src, tries = None, None, "model", []
            for _ in range(2):
                d = _json(llm(cs.INTEGRITY, FOLLOW_PROMPT.format(role=p["role"], style=style,
                                                                 question=last_q["text"], answer=answer))) or {}
                why = followup_problems(d.get("question", ""), d.get("quote", ""), answer)
                tries.append(why)
                if not why:
                    q, quote = d["question"].strip(), cs.exact_span(d["quote"], answer)
                    break
            if q is None:
                q, quote = template_followup(answer)
                src = "template"
            st["followed_up"].append(last_q["turn"])
            st["rejected"] += [w for w in tries if w]
            st["awaiting_answer"] = True
            self._save(st)
            self._turn({"turn": len(turns), "t": (now or now_utc()).isoformat(timespec="seconds"),
                        "speaker": p["persona_id"], "text": q, "is_followup": True, "quoted_span": quote,
                        "criterion_id": last_q["criterion_id"], "source": src, "rejected": [w for w in tries if w]})
            return self.view()
        if not st["queue"]:
            st["done"] = True
            self._save(st)
            return {**self.view(), "note": "all questions asked"}
        pid, cid = st["queue"].pop(0)
        p, crit = persona_by[pid], self._criterion(cid)
        q, src, tries = None, "model", []
        for _ in range(2):
            d = _json(llm(cs.INTEGRITY, ASK_PROMPT.format(role=p["role"], style=style, context=self._context(p),
                                                          criterion=crit["text"], angle=crit["check"]["question"]))) or {}
            why = question_problems(d.get("question", ""))
            tries.append(why)
            if not why:
                q = d["question"].strip()
                break
        if q is None:
            q, src = crit["check"]["question"], "template"
        st["rejected"] += [w for w in tries if w]
        st["awaiting_answer"] = True
        self._save(st)
        self._turn({"turn": len(turns), "t": (now or now_utc()).isoformat(timespec="seconds"), "speaker": pid,
                    "text": q, "is_followup": False, "quoted_span": None, "criterion_id": cid, "source": src,
                    "rejected": [w for w in tries if w]})
        return self.view()

    def answer(self, text: str, now: dt.datetime | None = None) -> dict:
        st = self.state()
        if not st["awaiting_answer"]:
            return {**self.view(), "error": "no question is waiting"}
        st["awaiting_answer"] = False
        self._save(st)
        interrupted = self.cfg()["pressure"] == "interrupting" and wc(text) > INTERRUPT_AT
        self._turn({"turn": len(self.turns()), "t": (now or now_utc()).isoformat(timespec="seconds"),
                    "speaker": "learner", "text": text, "is_followup": False, "quoted_span": None,
                    "interrupted": interrupted})
        out = self.view()
        if interrupted:
            out["note"] = f"Interrupted after {INTERRUPT_AT} words: the panel presses on your first point."
        return out

    def view(self) -> dict:
        """What the learner sees: the transcript so far; criteria are hidden on a cold mock."""
        cfg, st = self.cfg(), self.state()
        persona = {p["persona_id"]: p["role"] for p in cfg["personas"]}
        turns = []
        for t in self.turns():
            row = {"turn": t["turn"], "speaker": t["speaker"], "role": persona.get(t["speaker"], "you"),
                   "text": t["text"], "is_followup": t["is_followup"]}
            if not cfg["cold"] and t.get("criterion_id"):
                row["criterion"] = self._criterion(t["criterion_id"])["text"]
            turns.append(row)
        return {"seed": self.seed, "session_type": cfg["session_type"], "pressure": cfg["pressure"],
                "cold": cfg["cold"], "time_box_min": cfg["time_box_min"], "time_left_min": self.time_left(),
                "awaiting_answer": st["awaiting_answer"], "done": st["done"], "turns": turns,
                "self_scored": "self" in st, "criteria": [c["criterion_id"] for c in RUBRICS[cfg["session_type"]]]}

    def finish(self) -> dict:
        st = self.state()
        st["done"], st["awaiting_answer"] = True, False
        self._save(st)
        return self.view()

    def self_score(self, scores: dict) -> dict:
        crit = [c["criterion_id"] for c in RUBRICS[self.cfg()["session_type"]]]
        bad = {k: v for k, v in scores.items() if k not in crit or str(v) not in ("1", "2", "3", "4")}
        if bad or not scores:
            raise ValueError("score each criterion 1 (weak) to 4 (strong)")
        st = self.state()
        st["self"] = {k: int(v) for k, v in scores.items()}
        self._save(st)
        return st["self"]

    def _qa(self, cid: str | None) -> tuple[str, list[str]]:
        turns, lines, answers = self.turns(), [], []
        for i, t in enumerate(turns):
            if t["speaker"] == "learner" or (cid and t.get("criterion_id") != cid):
                continue
            a = turns[i + 1]["text"] if i + 1 < len(turns) and turns[i + 1]["speaker"] == "learner" else ""
            lines.append(f"Q: {t['text']}\nA: {a or '(no answer)'}")
            if a:
                answers.append(a)
        return "\n\n".join(lines), answers

    def critique(self, llm, runs: int = 3) -> list[dict]:
        """Binary checks per criterion, after the self-score; each verdict quotes the learner."""
        st = self.state()
        if "self" not in st:
            raise RuntimeError("score yourself first (your scores are captured before the critic's)")
        out = []
        for c in RUBRICS[self.cfg()["session_type"]]:
            cid = c["criterion_id"]
            if cid == "response_to_questions":
                out.append({"criterion_id": cid, "checks": response_checks(self._qa(None)[1], self.turns()),
                            "self_score": st["self"].get(cid)})
                continue
            qa, answers = self._qa(cid)
            if not answers:
                out.append({"criterion_id": cid, "checks": [{"check_id": c["check"]["check_id"], "verdict": "not_asked",
                                                             "answer_quote": None}], "self_score": st["self"].get(cid)})
                continue
            verdicts, quote = [], None
            for _ in range(runs):
                d = _json(llm("You are a strict, fair examiner. Reply with JSON only.",
                              CRITIC_PROMPT.format(criterion=c["text"], passes=c["check"]["pass_definition"], qa=qa))) or {}
                v = d.get("verdict")
                span = next((sp for a in answers if (sp := cs.exact_span(d.get("answer_quote") or "", a))), None)
                verdicts.append(v if v in ("pass", "fail") and span else "invalid")
                quote = quote or span
            verdict = verdicts[0] if len(set(verdicts)) == 1 and verdicts[0] != "invalid" else "unclear"
            out.append({"criterion_id": cid, "checks": [{"check_id": c["check"]["check_id"], "verdict": verdict,
                                                         "answer_quote": quote, "runs": verdicts}],
                        "self_score": st["self"].get(cid)})
        (self.dir / "score.json").write_text(json.dumps(out, indent=1))
        return out

    def metrics(self) -> dict:
        """Conduct measures (CC-6): computed from the record, not judged."""
        turns = self.turns()
        qs = [t for t in turns if t["speaker"] != "learner"]
        fus = [t for t in qs if t["is_followup"]]
        cfg = self.cfg()
        quota = sum(sum(p["quota"].values()) for p in cfg["personas"])
        asked = len({(t["speaker"], t["criterion_id"]) for t in qs if not t["is_followup"]})
        grounded = 0
        for t in fus:
            i = turns.index(t)
            prev = next((x for x in reversed(turns[:i]) if x["speaker"] == "learner"), None)
            if prev and t.get("quoted_span") and cs.exact_span(t["quoted_span"], prev["text"]) and \
                    wc(t["quoted_span"]) >= MIN_QUOTE:
                grounded += 1
        rej = [w for t in qs for r in t.get("rejected", []) for w in r]
        return {"questions": len(qs), "followups": len(fus),
                "stacked_question_rate": round(sum(1 for t in qs if t["text"].count("?") > 1) / len(qs), 3) if qs else None,
                "followup_grounded_share": round(grounded / len(fus), 3) if fus else None,
                "rubric_coverage": round(asked / quota, 3) if quota else None,
                "concessions_blocked": sum(1 for w in rej if w == "concession"),
                "template_fallbacks": sum(1 for t in qs if t.get("source") == "template"),
                "cold": cfg["cold"]}


def response_checks(answers: list[str], turns: list[dict]) -> list[dict]:
    """Deterministic checks for 'response to questions' (weighted most heavily)."""
    qs = [t for t in turns if t["speaker"] != "learner"]
    answered = [a for a in answers if wc(a) >= 5]
    defensive = next((m.group(0) for a in answers if (m := DEFENSIVE.search(a))), None)
    with_ev = [a for a in answers if EVIDENCE.search(a)]
    return [
        {"check_id": "answered_every_question", "verdict": "pass" if qs and len(answered) >= len(qs) else "fail",
         "answer_quote": None, "detail": f"{len(answered)} of {len(qs)} questions answered in five words or more"},
        {"check_id": "not_defensive", "verdict": "fail" if defensive else "pass", "answer_quote": defensive},
        {"check_id": "evidence_in_answers", "verdict": "pass" if answers and len(with_ev) / len(answers) >= 0.5 else "fail",
         "answer_quote": None, "detail": f"{len(with_ev)} of {len(answers)} answers give a number, test or reason"},
    ]
