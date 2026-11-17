"""Incident simulation (CC-2): a seeded scenario bound to a gap in the learner's own
artefacts, timed injects, the learner as incident commander, a hot-wash with the learner's
self-rating captured before the critic's, and a postmortem critique in CC-0 findings.

The scenario is deterministic for a seed (templates, not model text), so the same seed
reproduces the same drill. Hidden inject fields stay hidden until the hot-wash. The model,
when given, only voices one role's reaction per turn; it never decides or scores.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

import capstone as cs

FALLBACK = re.compile(r"fallback|roll ?back|hand[- ]?off|hands? (it|the conversation|them) (off|over|to)|kill switch|"
                      r"revert|human review|escalat|route[sd]? (all )?(messages |it )?to (people|staff|the desk|a (person|human))|"
                      r"pauses? (the bot|suggestions|the product)|nothing is ordered", re.I)

FAMILIES = {
    "hallucinated_policy": {"vectors": ["retrieval miss", "outdated policy document", "confident guess"],
                            "harm": "an invented refund or eligibility rule told to customers"},
    "prompt_injection_excessive_agency": {"vectors": ["instructions hidden in a customer message",
                                                       "instructions in an uploaded file", "tool output carrying commands"],
                                          "harm": "the agent takes an action it was never meant to take"},
    "personal_data_leak": {"vectors": ["one customer's details shown to another", "personal data written to logs",
                                       "a summary that repeats a phone number"],
                           "harm": "personal data exposed to the wrong person"},
    "regression_after_update": {"vectors": ["model version change", "prompt change", "retrieval index rebuild"],
                                "harm": "quality drops on a segment that used to work"},
    "cost_blowup": {"vectors": ["retry loop", "long-context requests", "a viral spike in heavy users"],
                    "harm": "cost per outcome several times the budget"},
}
AUTONOMY = ("suggest", "act with approval", "act autonomously")
SURFACES = ("chat", "email", "in-app action", "voice")
ROLES = ("operations lead", "support lead", "legal", "angry customer", "executive")
OBJECTIVES = ("severity_call", "time_to_declare", "mitigation_before_root_cause", "fallback_in_artefacts",
              "comms_cadence_honesty", "regulatory_clock", "golden_set_update", "action_item_quality")
RATINGS = ("P", "S", "M", "U")   # performed without challenges, with some, with major, unable
GAP_PATTERNS = [
    (re.compile(r"[^.\n]*\b(autonomous(ly)?|automatically|without (human )?approval)\b[^.\n]*", re.I), "autonomy without a stated fallback"),
    (re.compile(r"[^.\n]*\b(refund|policy|eligib)\w*[^.\n]*", re.I), "policy answers"),
    (re.compile(r"[^.\n]*\b(personal|phone|address|email|account)\b[^.\n]*", re.I), "personal data handling"),
]

INJECTS = {
    "hallucinated_policy": [
        ("support lead", "Three customers this morning quote a 60-day refund window your policy does not have; one posts a screenshot.",
         "severity_call", "a severity with reason; who is affected", ["contain by restricting policy answers", "keep answering but add a disclaimer and monitor"]),
        ("legal", "A consumer forum thread says your bot 'promised' refunds. Legal asks whether you must honour them.",
         "comms_cadence_honesty", "an honest customer message; a decision owner", ["honour affected cases", "correct publicly and review case by case"]),
        ("executive", "The executive asks if you can keep the bot live for the sale weekend.",
         "mitigation_before_root_cause", "a fallback or narrowed scope now, root cause later", ["narrow to order status only", "pause the bot and route to people"]),
        ("operations lead", "Logs show the wrong window was quoted to about 1,400 customers since Tuesday; the retrieval index still serves last year's policy.",
         "golden_set_update", "a golden-set case that checks policy answers against the current document", ["rebuild the index, then re-enable after the cases pass", "keep answers restricted until the golden set covers policy"]),
        ("executive", "The executive wants a written status for all staff in ten minutes and asks whether any regulator must be told.",
         "regulatory_clock", "whether a reporting clock applies, from the snapshot and marked verify, and who confirms", ["no notification duty: say why and who confirms", "ask legal to confirm consumer-protection duties by a stated time"]),
    ],
    "prompt_injection_excessive_agency": [
        ("operations lead", "Logs show the agent issued two account changes after a message containing 'ignore previous instructions'.",
         "severity_call", "a severity; actions taken; blast radius", ["disable the action tool", "require approval on all write actions"]),
        ("angry customer", "A customer says their delivery address was changed without asking and demands to know who did it.",
         "comms_cadence_honesty", "an apology without blame; what is fixed now", ["call affected customers", "message with a ticket and timeline"]),
        ("executive", "The board meets tomorrow and wants to know if any data left the company.",
         "regulatory_clock", "what is known, what is not, when the next update comes", ["report as a possible breach", "investigate first with a stated deadline"]),
        ("operations lead", "The same phrasing works on the staging build; no golden-set case sends instructions inside customer text.",
         "golden_set_update", "injection cases added to the golden set; write actions gated", ["add injection cases and block release on them", "require approval on write actions until the cases pass"]),
        ("support lead", "Forty customers have written in; the support desk needs a script and the time of the next update.",
         "comms_cadence_honesty", "a script that says what is known, what is not, and when the next update comes", ["one script for all affected customers", "personal calls to the two whose accounts changed, script for the rest"]),
    ],
    "personal_data_leak": [
        ("support lead", "A customer reports seeing another person's phone number in a chat summary.",
         "severity_call", "a severity tied to personal data; containment", ["turn off summaries", "mask numbers in outputs"]),
        ("legal", "Legal asks when the regulator must hear about this and who decides.",
         "regulatory_clock", "the clock from the governance snapshot, marked verify", ["notify within the stated window", "assess scope first, notify if confirmed"]),
        ("operations lead", "The same pattern appears in logs from last week.",
         "golden_set_update", "a golden-set case that would have caught it", ["add PII cases to the golden set", "add an output filter test"]),
        ("angry customer", "The person whose number was shown posts publicly that you 'sold' their data.",
         "comms_cadence_honesty", "a public reply that is honest about what happened, without blame or legal hedging", ["reply publicly and contact them directly", "contact them directly and post a general notice"]),
        ("executive", "The executive asks whether summaries can come back on tomorrow.",
         "mitigation_before_root_cause", "a re-enable condition tied to the new golden-set cases", ["re-enable once the masking test passes", "keep off until the root cause is confirmed"]),
    ],
    "regression_after_update": [
        ("operations lead", "After last night's model update, Hindi-English messages fail twice as often.",
         "severity_call", "a severity by segment; the update as a suspect", ["roll back the model", "route that segment to people"]),
        ("support lead", "Repeat contacts are up 18% since the update.",
         "fallback_in_artefacts", "the rollback path in the learner's own artefacts", ["roll back", "hold and add monitoring"]),
        ("executive", "The executive asks how this got past testing.",
         "golden_set_update", "why the golden set missed it; what changes", ["add segment cases", "make segment pass rates a release gate"]),
        ("angry customer", "A customer who writes in Hindi and English says the bot 'stopped understanding' them and threatens to leave.",
         "comms_cadence_honesty", "an apology that names the problem and when it will be fixed", ["route them to a person now", "reply with the rollback time"]),
        ("legal", "Legal asks whether the quality drop counts as a reportable incident anywhere you operate.",
         "regulatory_clock", "whether a reporting clock applies, from the snapshot and marked verify", ["no duty: say why and who confirms", "check the high-risk classification before deciding"]),
    ],
    "cost_blowup": [
        ("operations lead", "Overnight spend is six times the daily budget; a retry loop is suspected.",
         "severity_call", "a severity with a number; who owns spend", ["cap retries now", "throttle heavy users"]),
        ("executive", "Finance asks whether the product is still viable at this usage.",
         "comms_cadence_honesty", "the unit economics at heavy usage, honestly", ["raise prices for heavy use", "limit free usage"]),
        ("support lead", "Throttled users complain that answers now take a minute.",
         "mitigation_before_root_cause", "a temporary experience trade-off, stated", ["show a queue message", "fall back to a cheaper model"]),
        ("operations lead", "The retry loop came from a timeout change in yesterday's deploy; no eval checks cost per outcome.",
         "golden_set_update", "a cost-per-outcome check added to the release gate", ["add a cost budget test to the golden set", "alert on cost per outcome in monitoring"]),
        ("legal", "Legal asks whether throttling paying customers triggers any notice duty.",
         "regulatory_clock", "whether a clock applies (contract or regulator), stated with who confirms", ["no regulator clock: say why and who confirms", "notify customers under the contract terms by a stated time"]),
    ],
}


def _gap(ws: cs.Workspace | None) -> dict:
    """A gap in the learner's artefacts to bind the scenario to: the first sentence that gives
    the model autonomy, policy or personal data, with no fallback stated near it."""
    for a in (ws.artefacts() if ws else []):
        text, _ = ws.text(a["artefact_id"])
        for pat, label in GAP_PATTERNS:
            for m in pat.finditer(text):
                s = m.group(0).strip()
                window = text[max(0, m.start() - 300): m.end() + 300].lower()
                if s and not FALLBACK.search(window):
                    return {"artefact_id": a["artefact_id"], "quote": s[:300], "label": label}
    return {"artefact_id": None, "quote": None, "label": "no artefact gap found; generic scenario"}


def make_scenario(seed: int, ws: cs.Workspace | None = None, micro: bool = True) -> dict:
    rng = random.Random(seed)
    gap = _gap(ws)
    fam = rng.choice(sorted(FAMILIES))
    if gap["label"] == "personal data handling":
        fam = "personal_data_leak"
    elif gap["label"] == "policy answers":
        fam = "hallucinated_policy"
    injects = INJECTS[fam][: 3 if micro else 5]
    offsets = MICRO_OFFSETS if micro else FULL_OFFSETS
    sc = {"scenario_id": f"sim-{seed}", "seed": seed, "format": "micro" if micro else "full",
          "time_box_min": 20 if micro else 60,
          "failure_class": fam, "vector_family": rng.choice(FAMILIES[fam]["vectors"]),
          "autonomy_level": rng.choice(AUTONOMY), "surface": rng.choice(SURFACES),
          "harm": FAMILIES[fam]["harm"], "bound_gap": {"artefact_id": gap["artefact_id"], "quote": gap["quote"]},
          "injects": [{"inject_id": f"i{i + 1}", "t_offset_min": offsets[i], "role": r, "text": t,
                       "tests_objective": o, "expected_evidence": e, "permissible_branches": b}
                      for i, (r, t, o, e, b) in enumerate(injects)]}
    return sc


MICRO_OFFSETS, FULL_OFFSETS = [0, 7, 14], [0, 10, 20, 35, 50]
CADENCE_MIN = {"SEV1": 15, "SEV2": 15, "SEV3": 30, "SEV4": 30}     # status updates at least this often
NEXT_UPDATE = re.compile(r"next update|update (you|again)|\bby \d|within \d+\s*(min|hour|h\b)|at \d{1,2}[:.]\d\d|in \d+\s*(min|hour)", re.I)
OVERCLAIM = re.compile(r"fully (resolved|fixed)|no (customers?|one|users?) (were|was) affected|nothing to worry|100% (safe|secure)", re.I)
CLOCK_WORDS = re.compile(r"\b(\d+)\s*(hours?|hrs?|h|days?)\b|without (undue )?delay", re.I)


def lint_inject(i: dict) -> list[str]:
    why = []
    for k in ("tests_objective", "expected_evidence", "permissible_branches", "role", "text"):
        if not i.get(k):
            why.append(f"missing {k}")
    if len(i.get("permissible_branches") or []) < 2:
        why.append("needs at least two permissible branches")
    if i.get("tests_objective") not in OBJECTIVES:
        why.append("unknown objective")
    return why


def public_view(sc: dict, revealed: int) -> dict:
    """What the learner may see before the hot-wash: no hidden fields. The time of the next
    inject is shown, never its text."""
    nxt = sc["injects"][revealed]["t_offset_min"] if revealed < len(sc["injects"]) else None
    return {"scenario_id": sc["scenario_id"], "format": sc["format"], "surface": sc["surface"],
            "autonomy_level": sc["autonomy_level"], "time_box_min": sc.get("time_box_min", 20),
            "n_injects": len(sc["injects"]), "next_at_min": nxt,
            "brief": f"You are incident commander. Your product runs on {sc['surface']} at autonomy "
                     f"'{sc['autonomy_level']}'. Something is going wrong.",
            "injects": [{k: i[k] for k in ("inject_id", "t_offset_min", "role", "text")} for i in sc["injects"][:revealed]]}


# ------------------------------------------------------------------ session files
class Drill:
    def __init__(self, ws: cs.Workspace, seed: int, day: str | None = None):
        self.ws, self.seed = ws, seed
        self.dir = ws.session_dir(f"sim-{seed}", day)

    def start(self, now: dt.datetime | None = None, micro: bool = True, real_time: bool | None = None) -> dict:
        """A full session runs on the real clock by default (injects unlock at their minute);
        a micro drill unlocks on request, as before."""
        sc = make_scenario(self.seed, self.ws, micro)
        (self.dir / "scenario.json").write_text(json.dumps(sc, indent=1))
        st = {"started": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "revealed": 1,
              "hotwash": False, "real_time": (not micro) if real_time is None else real_time}
        (self.dir / "state.json").write_text(json.dumps(st))
        return self.view(now)

    def view(self, now: dt.datetime | None = None) -> dict:
        st = self.state()
        v = public_view(self.scenario(), st["revealed"])
        v["elapsed_min"] = round(((now or dt.datetime.now(dt.timezone.utc)) - dt.datetime.fromisoformat(st["started"])).total_seconds() / 60, 1)
        v["real_time"] = st.get("real_time", False)
        return v

    def update(self, audience: str, text: str, now: dt.datetime | None = None) -> dict:
        """A status update outside an inject decision (customers, internal or regulator)."""
        if audience not in ("customer", "internal", "regulator"):
            raise ValueError("audience must be customer, internal or regulator")
        row = {"t": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "audience": audience, "text": text}
        with (self.dir / "updates.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def updates(self) -> list[dict]:
        p = self.dir / "updates.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def scenario(self) -> dict:
        return json.loads((self.dir / "scenario.json").read_text())

    def state(self) -> dict:
        return json.loads((self.dir / "state.json").read_text())

    def _save(self, st):
        (self.dir / "state.json").write_text(json.dumps(st))

    def next(self, now: dt.datetime | None = None, practice: bool | None = None) -> dict:
        """Reveal the next inject when its time has come (or at once in practice mode; a
        real-time session is never in practice mode)."""
        sc, st = self.scenario(), self.state()
        if practice is None:
            practice = not st.get("real_time", False)
        if st["revealed"] < len(sc["injects"]):
            nxt = sc["injects"][st["revealed"]]
            elapsed = ((now or dt.datetime.now(dt.timezone.utc)) - dt.datetime.fromisoformat(st["started"])).total_seconds() / 60
            if practice or elapsed >= nxt["t_offset_min"]:
                st["revealed"] += 1
                self._save(st)
        return self.view(now)

    def decide(self, inject_id: str, severity: str, containment: str, comms: dict, golden_set_would_catch: str,
               rationale: str = "", now: dt.datetime | None = None) -> dict:
        row = {"t": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "inject_id": inject_id,
               "severity": severity, "containment": containment,
               "comms": {k: comms.get(k, "") for k in ("customer", "internal", "regulator")},
               "golden_set_would_catch": golden_set_would_catch, "rationale": rationale}
        with (self.dir / "decisions.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def decisions(self) -> list[dict]:
        p = self.dir / "decisions.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def role_reply(self, inject_id: str, llm) -> str:
        """One role's in-character reaction to the learner's last decision (the model voices,
        it never scores or advises)."""
        sc = self.scenario()
        inj = next(i for i in sc["injects"] if i["inject_id"] == inject_id)
        last = next((d for d in reversed(self.decisions()) if d["inject_id"] == inject_id), None)
        if not last:
            return ""
        prompt = (f"You are the {inj['role']} in an incident drill. The situation: {inj['text']}\n"
                  f"The incident commander decided: severity {last['severity']}; containment: {last['containment']}; "
                  f"customer message: {last['comms'].get('customer', '')}.\n"
                  "React in character in at most 50 words: one concern or follow-up question. "
                  "Do not advise, do not score, do not write anything for them.")
        return (llm(cs.INTEGRITY, prompt) or "").strip()[:500]

    def self_rate(self, ratings: dict) -> dict:
        """The learner's own ratings, captured before the critic's are shown."""
        bad = {k: v for k, v in ratings.items() if k not in OBJECTIVES or v not in RATINGS}
        if bad:
            raise ValueError(f"ratings must be {', '.join(RATINGS)} for objectives {', '.join(OBJECTIVES)}")
        st = self.state()
        st["self"] = ratings
        self._save(st)
        return ratings

    def hotwash(self, now: dt.datetime | None = None, postmortem: str | None = None) -> dict:
        """Critic ratings (deterministic where the rule allows) beside the self-ratings;
        refuses until the learner has rated. Reveals the hidden inject fields. With a
        registered postmortem, action-item quality is rated from it."""
        st, sc = self.state(), self.scenario()
        if "self" not in st:
            raise RuntimeError("rate yourself first (self-rating is captured before the critic's)")
        end = now or dt.datetime.now(dt.timezone.utc)
        aar = critic(sc, self.decisions(), dt.datetime.fromisoformat(st["started"]), self.ws, updates=self.updates(),
                     ended=end, postmortem=postmortem)
        for row in aar:
            row["self_rating"] = st["self"].get(row["objective_id"])
        st["hotwash"] = True
        self._save(st)
        (self.dir / "aar.json").write_text(json.dumps(aar, indent=1))
        snap = cs.governance(self.ws.root)
        clocks = [{**{k: c[k] for k in ("regime", "obligation", "window", "status", "source_url", "verify")},
                   "as_of": snap.get("as_of")} for c in snap.get("clocks", []) if sc["failure_class"] in c.get("families", [])]
        return {"aar": aar, "scenario": sc, "clocks": clocks, "updates": self.updates()}


def critic(sc: dict, decisions: list[dict], started: dt.datetime, ws: cs.Workspace | None,
           updates: list[dict] | None = None, ended: dt.datetime | None = None, postmortem: str | None = None) -> list[dict]:
    """P/S/M/U per objective from the decisions and the clock. Rules are stated in each
    observation; anything not decidable from the record is marked U with the reason. A full
    session adds the comms cadence (gaps between status updates), the reporting clock named
    in the regulator message, a written golden-set case, and action items from the
    postmortem."""
    if sc.get("format") == "full":
        return critic_full(sc, decisions, started, ws, updates or [], ended or dt.datetime.now(dt.timezone.utc), postmortem)
    out = []
    first = decisions[0] if decisions else None
    by = {d["inject_id"]: d for d in decisions}

    def row(obj, rating, obs, gap):
        out.append({"objective_id": obj, "critic_rating": rating, "observation_quote": obs, "gap_class": gap})

    sev = [d for d in decisions if re.match(r"(?i)sev\s*[0-4]|p[0-4]|critical|high|medium|low", d.get("severity") or "")]
    row("severity_call", "P" if len(sev) == len(sc["injects"]) else "S" if sev else "U",
        f"{len(sev)} of {len(sc['injects'])} injects got a severity", "process")
    if first:
        mins = (dt.datetime.fromisoformat(first["t"]) - started).total_seconds() / 60
        row("time_to_declare", "P" if mins <= 5 else "S" if mins <= 10 else "M", f"first declaration after {mins:.1f} min",
            "detection")
    else:
        row("time_to_declare", "U", "no declaration", "detection")
    contained = first and len((first.get("containment") or "").split()) >= 3
    row("mitigation_before_root_cause", "P" if contained else "U",
        f"first containment: {(first or {}).get('containment', '')[:120]}", "process")
    fb = False
    if ws:
        for a in ws.artefacts():
            if FALLBACK.search(ws.text(a["artefact_id"])[0]):
                fb = True
    row("fallback_in_artefacts", "P" if fb else "M", "a fallback or rollback is written in your artefacts" if fb
        else "no fallback or rollback found in your artefacts", "playbook")
    comms = [d for d in decisions if len((d["comms"].get("customer") or "").split()) >= 5]
    row("comms_cadence_honesty", "P" if len(comms) == len(sc["injects"]) else "S" if comms else "U",
        f"customer messages on {len(comms)} of {len(sc['injects'])} injects", "communication")
    needs_reg = sc["failure_class"] in ("personal_data_leak", "prompt_injection_excessive_agency")
    reg = any((d["comms"].get("regulator") or "").strip() for d in decisions)
    row("regulatory_clock", ("P" if reg else "M") if needs_reg else "P",
        "regulator message present" if reg else ("no regulator message for a possible data incident" if needs_reg
                                                 else "no regulatory clock in this scenario"), "communication")
    gs = [d for d in decisions if (d.get("golden_set_would_catch") or "").lower() in ("yes", "no")]
    reasoned = all(d["golden_set_would_catch"].lower() == "yes" or (d.get("rationale") or "").strip() for d in gs)
    row("golden_set_update", "P" if gs and len(gs) == len(sc["injects"]) and reasoned else "S" if gs else "U",
        f"golden-set answer on {len(gs)} of {len(sc['injects'])} injects" + ("" if reasoned else "; a 'no' without a reason"),
        "tool")
    row("action_item_quality", "U", "rated from your postmortem (lecture capstone postmortem)", "process")
    return out


def critic_full(sc, decisions, started, ws, updates, ended, postmortem):
    out = critic(dict(sc, format="micro"), decisions, started, ws)       # the shared rules first
    by_obj = {r["objective_id"]: r for r in out}
    T = lambda s: dt.datetime.fromisoformat(s)
    mins = lambda t: (t - started).total_seconds() / 60

    def put(obj, rating, obs, gap):
        by_obj[obj].update({"critic_rating": rating, "observation_quote": obs, "gap_class": gap})

    # comms cadence: every customer or internal message, in decisions or as updates
    msgs = [(T(d["t"]), a, d["comms"].get(a) or "") for d in decisions for a in ("customer", "internal")
            if len((d["comms"].get(a) or "").split()) >= 5]
    msgs += [(T(u["t"]), u["audience"], u["text"]) for u in updates if u["audience"] in ("customer", "internal")
             and len(u["text"].split()) >= 5]
    msgs.sort()
    sev = next((re.match(r"(?i)sev\s*([1-4])", d.get("severity") or "") for d in decisions
                if re.match(r"(?i)sev\s*[1-4]", d.get("severity") or "")), None)
    target = CADENCE_MIN.get(f"SEV{sev[1]}" if sev else "SEV3", 30)
    if msgs:
        first_t = T(decisions[0]["t"]) if decisions else msgs[0][0]
        end_t = min(ended, started + dt.timedelta(minutes=sc.get("time_box_min", 60)))
        times = sorted({first_t, end_t, *[m[0] for m in msgs if first_t <= m[0] <= end_t]})
        gap = max((b - a).total_seconds() / 60 for a, b in zip(times, times[1:])) if len(times) > 1 else 0
        cust = [m for m in msgs if m[1] == "customer"]
        with_next = sum(1 for m in cust if NEXT_UPDATE.search(m[2]))
        over = [m for m in cust if OVERCLAIM.search(m[2])]
        rating = "P" if gap <= target and cust and with_next == len(cust) and not over else \
                 "S" if gap <= 2 * target and not over else "M"
        put("comms_cadence_honesty", rating,
            f"longest gap between updates {gap:.0f} min (target {target} at {('SEV' + sev[1]) if sev else 'SEV3'}); "
            f"{with_next} of {len(cust)} customer messages give the next update time" + ("; a message over-claims" if over else ""),
            "communication")
    else:
        put("comms_cadence_honesty", "U", "no status update of five words or more", "communication")

    # reporting clock: the regulator message names a window from the snapshot, or says why none applies
    snap = cs.governance(ws.root) if ws else cs.GOVERNANCE_SNAPSHOT
    clocks = [c for c in snap.get("clocks", []) if sc["failure_class"] in c.get("families", [])]
    reg = " ".join([d["comms"].get("regulator") or "" for d in decisions] +
                   [u["text"] for u in updates if u["audience"] == "regulator"]).strip()
    data = sc["failure_class"] in ("personal_data_leak", "prompt_injection_excessive_agency")
    named = [m.group(0) for m in CLOCK_WORDS.finditer(reg)]
    known = {str(c["hours"]) for c in clocks} | {str(c["hours"] // 24) for c in clocks if c["hours"] >= 24}
    matches = [n for n in named if (re.match(r"\d+", n) and re.match(r"\d+", n).group(0) in known) or "delay" in n.lower()]
    as_of = f" (snapshot as of {snap.get('as_of')}, verify)"
    if not reg:
        put("regulatory_clock", "M" if data else "S", "no regulator message" + (" for a possible data incident" if data else
            "; say whether any clock applies and who confirms") + as_of, "communication")
    elif data:
        put("regulatory_clock", "P" if matches else "S",
            (f"regulator message names {', '.join(matches)}" if matches else "regulator message names no reporting window") + as_of,
            "communication")
    else:
        put("regulatory_clock", "P" if len(reg.split()) >= 5 else "S",
            "you stated the reporting position" + as_of, "communication")

    # golden set: on the inject that tests it, a written case with an expected behaviour
    gi = [i["inject_id"] for i in sc["injects"] if i["tests_objective"] == "golden_set_update"]
    gd = [d for d in decisions if d["inject_id"] in gi]
    case = [d for d in gd if len((d.get("rationale") or "").split()) >= 8 and
            re.search(r"\b(should|must|expect|refuse|route|escalat|block|flag|mask|fail)", d.get("rationale") or "", re.I)]
    if gi:
        put("golden_set_update", "P" if case else "S" if gd else "U",
            "a golden-set case with an expected behaviour is written" if case else
            ("answered without a case that states the expected behaviour" if gd else "no answer on the golden-set inject"), "tool")

    # action items from the learner's postmortem, when one is registered
    if postmortem and ws:
        try:
            fs = postmortem_findings(ws, postmortem)
        except (KeyError, FileNotFoundError, StopIteration):
            fs = None
        if fs is not None:
            checks = {f["check_id"] for f in fs}
            put("action_item_quality", "M" if "vague_actions" in checks or len(fs) >= 2 else "S" if fs else "P",
                "postmortem checks: " + (", ".join(sorted(checks)) if checks else "none failed"), "process")
    return [by_obj[o] for o in OBJECTIVES if o in by_obj]


# ------------------------------------------------------------------ postmortem critique
WEAK_MARKERS = {
    "missing_numbers": "Does the postmortem state duration, users affected or cost in numbers?",
    "shallow_root_cause": "Does the root cause explain why the failure was possible, not only what failed?",
    "vague_actions": "Does every action item have one owner and a date?",
    "blame": "Does the postmortem describe systems and decisions rather than people at fault?",
}


def postmortem_findings(ws: cs.Workspace, artefact_id: str) -> list[dict]:
    """Deterministic checks for published markers of weak postmortems, as gated CC-0 findings
    quoting the learner's own lines."""
    text, sha = ws.text(artefact_id)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []

    def add(check, quote, issue, question):
        f = {"finding_id": f"pm-{check}", "artefact_id": artefact_id, "artefact_sha": sha, "quote": quote,
             "criterion_id": "postmortem", "check_id": check, "verdict": "fail", "issue": issue,
             "question_to_learner": question, "hint_level": 0, "lecture_refs": [], "evidence_refs": [],
             "reviewer_view": "general", "confidence": 1.0}
        if not cs.gate_finding(f, text, sha):
            out.append(f)

    if not re.search(r"\d", text) and lines:
        add("missing_numbers", lines[0][:200], "No duration, user count or cost appears in numbers.",
            WEAK_MARKERS["missing_numbers"])
    rc = next((l for l in lines if re.search(r"root cause", l, re.I)), None)
    if rc and not re.search(r"\bbecause\b|\bwhy\b|\bdue to\b|\bled to\b", text[text.find(rc):text.find(rc) + 600], re.I):
        add("shallow_root_cause", rc[:200], "The root cause names what failed but not why it could fail.",
            WEAK_MARKERS["shallow_root_cause"])
    acts = [l for l in lines if re.match(r"[-*\d.)\s]*(action|todo|follow[- ]?up)", l, re.I) or re.match(r"[-*]\s*\[ ?\]", l)]
    for a in acts:
        if not re.search(r"owner|@|\b\d{4}-\d{2}-\d{2}\b|\bby (mon|tue|wed|thu|fri|sat|sun|\d)", a, re.I):
            add("vague_actions", a[:200], "This action has no single owner or date.", WEAK_MARKERS["vague_actions"])
            break
    bl = next((l for l in lines if re.search(r"\b(fault|blame|careless|should have known|human error)\b", l, re.I)), None)
    if bl:
        add("blame", bl[:200], "This line frames the cause as a person's failing.", WEAK_MARKERS["blame"])
    return out
