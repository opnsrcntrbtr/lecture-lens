"""CC-4 peer review coach, both directions. It never writes a review comment.

Outgoing: you register the review you are writing for a peer. Each comment in it is checked
for three issue types (after the ICLR review-feedback agent): vague (no specific place or
ask), unjustified (a judgement with no reason or evidence), and already answered in the
peer's work (only when the peer agreed and their document is registered). A structural
check follows the Critical Response Process: neutral questions come before opinions.
Every finding uses the CC-0 schema and gates, and its quote comes from your review.

Incoming: you paste feedback you received, with names removed (the name guard runs again).
It is clustered into themes, each linked to quotes from your own artefacts; you classify
each cluster as Tiger, Paper Tiger or Elephant and write the revision plan. Once human
feedback exists, an overlap report lists which of its themes the copilot had also raised.

Peers are peer_A, peer_B, ...; a peer document loads only after you confirm that peer
agreed. Everything stays in capstone/peer/ (private).

Files: capstone/peer/peers.json  [{peer_label, consent, consent_t}]
       capstone/peer/<peer>/doc.md (guarded copy), review.md (guarded copy of yours),
       review_findings.jsonl (CC-0 findings), capstone/peer/feedback.md, feedback_clusters.json
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import capstone as cs
import cite

CLASSES = ("tiger", "paper_tiger", "elephant")
OPINION = re.compile(r"\b(i think|i feel|in my opinion|this is (?:weak|strong|good|bad|unclear|confusing)|should|must|"
                     r"needs? to|is (?:not )?convincing|is (?:too|very)|i (?:don't|do not) (?:like|buy|see))\b", re.I)
SPECIFIC = re.compile(r"\b(section|page|slide|table|figure|para(?:graph)?|line|step|the (?:metric|threshold|persona|"
                      r"cost|fallback|example)|\d+%?|\"[^\"]{4,}\"|'[^']{4,}')", re.I)
REASON = re.compile(r"\b(because|since|so that|as a result|for example|e\.g\.|evidence|data|shows?|given)\b", re.I)


def comments(review: str) -> list[str]:
    """Bullets, numbered points, or paragraphs of the review."""
    out = []
    for block in re.split(r"\n\s*\n", review or ""):
        lines = [l for l in block.splitlines() if l.strip()]
        bullets = [l for l in lines if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", l)]
        if bullets:
            out += [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", l).strip() for l in bullets]
        elif lines and not lines[0].lstrip().startswith("#"):
            out.append(" ".join(l.strip() for l in lines))
    return [c for c in out if len(c.split()) >= 3]


def is_question(c: str) -> bool:
    return c.rstrip().endswith("?") and not OPINION.search(c)


class Peer:
    def __init__(self, ws: cs.Workspace, guard):
        self.ws, self.guard = ws, guard      # guard(list[str]) -> list[str]: the roster name guard
        self.root = ws.root / "peer"
        self.root.mkdir(parents=True, exist_ok=True)

    # peers and consent
    def peers(self) -> list[dict]:
        f = self.root / "peers.json"
        return json.loads(f.read_text()) if f.exists() else []

    def add(self, consent: bool) -> dict:
        rows = self.peers()
        label = f"peer_{chr(ord('A') + len(rows))}"
        row = {"peer_label": label, "consent": bool(consent), "consent_t": cs.now_iso() if consent else None}
        (self.root / "peers.json").write_text(json.dumps(rows + [row], indent=1))
        (self.root / label).mkdir(exist_ok=True)
        return row

    def set_consent(self, label: str, consent: bool) -> dict:
        rows = self.peers()
        r = next(r for r in rows if r["peer_label"] == label)
        r.update({"consent": bool(consent), "consent_t": cs.now_iso() if consent else None})
        (self.root / "peers.json").write_text(json.dumps(rows, indent=1))
        if not consent and (self.root / label / "doc.md").exists():
            (self.root / label / "doc.md").unlink()       # withdrawn consent removes their document
        return r

    def _guarded(self, text: str) -> str:
        return "\n".join(self.guard(text.splitlines()))

    def load_doc(self, label: str, path: Path) -> dict:
        r = next((r for r in self.peers() if r["peer_label"] == label), None)
        if not r:
            raise KeyError("no such peer")
        if not r["consent"]:
            raise PermissionError("confirm that this peer agreed before loading their document")
        text = self._guarded(Path(path).expanduser().read_text(errors="ignore"))
        (self.root / label / "doc.md").write_text(text)
        return {"peer_label": label, "sha256": cs.sha256(text), "words": len(text.split())}

    def set_review(self, label: str, path: Path) -> dict:
        if not any(r["peer_label"] == label for r in self.peers()):
            raise KeyError("no such peer")
        text = self._guarded(Path(path).expanduser().read_text(errors="ignore"))
        (self.root / label / "review.md").write_text(text)
        return {"peer_label": label, "sha256": cs.sha256(text), "comments": len(comments(text))}

    # outgoing review
    def check_review(self, label: str, llm) -> dict:
        d = self.root / label
        review = (d / "review.md").read_text() if (d / "review.md").exists() else ""
        if not review:
            raise FileNotFoundError("register your review first")
        doc = (d / "doc.md").read_text() if (d / "doc.md").exists() else ""
        sha = cs.sha256(review)
        cs_ = comments(review)
        found, gated = [], 0
        first_op = next((i for i, c in enumerate(cs_) if not is_question(c)), None)
        last_q = max((i for i, c in enumerate(cs_) if is_question(c)), default=None)
        if first_op is not None and last_q is not None and first_op < last_q:
            span = cs.exact_span(cs_[first_op][:200], review) or cs_[first_op][:80]
            found.append(self._finding(label, sha, span, "crp_order", "general",
                                       "An opinion comes before your neutral questions.",
                                       "Which questions would you ask before giving this view?"))
        for c in cs_:
            if is_question(c):
                continue
            q = cs.exact_span(c, review) or c
            vague = not SPECIFIC.search(c)
            unjust = OPINION.search(c) is not None and not REASON.search(c)
            if vague or unjust:
                v = self._judge(c, llm)            # the model confirms; rules alone over-flag
                vague, unjust = vague and v.get("vague", vague), unjust and v.get("unjustified", unjust)
            if vague:
                found.append(self._finding(label, sha, q, "vague", "general",
                                           "This comment names no place in the work and no change it asks for.",
                                           "Which section or claim does this refer to, and what would you look for there?"))
            if unjust:
                found.append(self._finding(label, sha, q, "unjustified", "general",
                                           "A judgement is given without the reason or evidence behind it.",
                                           "What in the work led you to this view?"))
            if doc:
                hit = self._answered(c, doc, llm)
                if hit:
                    found.append(self._finding(label, sha, q, "already_answered", "general",
                                               "The peer's document may already address this point.",
                                               "Does the passage you were pointed to answer this, and if not, what is missing?",
                                               evidence=[{"peer_quote": hit}]))
        shown = []
        for f in found:
            why = cs.gate_finding({k: v for k, v in f.items() if k in cs.FINDING_FIELDS}, review, sha)
            if why:
                gated += 1
                continue
            shown.append(f)
        with (d / "review_findings.jsonl").open("w") as fh:
            for f in shown:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        return {"peer_label": label, "comments": len(cs_), "findings": shown, "gated_out": gated,
                "note": "Feedback on your review only; the copilot does not write review comments."}

    def _finding(self, label, sha, quote, check, view, issue, question, evidence=None) -> dict:
        return {"finding_id": uuid.uuid4().hex[:10], "artefact_id": f"{label}-review", "artefact_sha": sha, "quote": quote,
                "criterion_id": "peer_review", "check_id": check, "verdict": "fail", "issue": issue,
                "question_to_learner": question, "hint_level": 0, "lecture_refs": [], "evidence_refs": evidence or [],
                "reviewer_view": view, "confidence": None}

    JUDGE = """A learner wrote this comment in a peer review of a capstone document:
COMMENT: {c}
Is it vague (names no specific place in the work and no specific change)? Is it unjustified
(a judgement with no reason or evidence given)? Reply with JSON only: {{"vague": true or false, "unjustified": true or false}}"""

    def _judge(self, c: str, llm) -> dict:
        m = re.search(r"\{.*\}", llm(self.JUDGE.format(c=c)) or "", re.S)
        try:
            d = json.loads(m.group(0)) if m else {}
        except ValueError:
            d = {}
        return {k: bool(v) for k, v in d.items() if k in ("vague", "unjustified")}

    ANSWERED = """REVIEW COMMENT: {c}
PASSAGE FROM THE REVIEWED DOCUMENT: {p}
Does the passage already answer or address the comment? Reply with JSON only: {{"answered": true or false}}"""

    def _answered(self, c: str, doc: str, llm) -> str | None:
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-*•#])", doc) if len(p.split()) >= 6]
        if not paras:
            return None
        sc = cite.lexical_support(c, paras)
        i = max(range(len(paras)), key=lambda k: sc[k])
        if sc[i] < 0.2:   # the model confirms; this only finds the candidate passage
            return None
        m = re.search(r"\{.*\}", llm(self.ANSWERED.format(c=c, p=paras[i][:800])) or "", re.S)
        try:
            ok = bool(json.loads(m.group(0)).get("answered")) if m else False
        except (ValueError, AttributeError):
            ok = False
        return paras[i][:300] if ok else None

    # incoming feedback
    CLUSTER = """FEEDBACK ITEMS (numbered):
{items}

Group the items into themes (a theme can hold one item). Give each theme a short neutral
name. Reply with JSON only: {{"themes": [{{"name": "...", "items": [1, 3]}}]}}"""

    def feedback(self, text: str, llm) -> dict:
        text = self._guarded(text)
        (self.root / "feedback.md").write_text(text)
        items = comments(text) or [l.strip() for l in text.splitlines() if len(l.split()) >= 4]
        if not items:
            raise ValueError("no feedback items found")
        m = re.search(r"\{.*\}", llm(self.CLUSTER.format(items="\n".join(f"{i}. {t}" for i, t in enumerate(items, 1)))) or "",
                      re.S)
        try:
            themes = json.loads(m.group(0)).get("themes", []) if m else []
        except (ValueError, AttributeError):
            themes = []
        used, clusters = set(), []
        for t in themes if isinstance(themes, list) else []:
            idx = [int(i) for i in t.get("items", []) if str(i).isdigit() and 1 <= int(i) <= len(items) and int(i) not in used]
            if not idx:
                continue
            used.update(idx)
            clusters.append({"name": (t.get("name") or "")[:80], "items": [items[i - 1] for i in idx]})
        clusters += [{"name": "other", "items": [items[i - 1]]} for i in range(1, len(items) + 1) if i not in used]
        arts = [(a["artefact_id"], self.ws.text(a["artefact_id"])[0]) for a in self.ws.artefacts()]
        old = {c["cluster_id"]: c for c in self.clusters()}
        out = []
        for k, c in enumerate(clusters, 1):
            quotes = []
            for aid, t in arts:
                sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if len(s.split()) >= 5]
                if not sents:
                    continue
                sc = cite.lexical_support(" ".join(c["items"]), sents)
                j = max(range(len(sents)), key=lambda x: sc[x])
                if sc[j] >= 0.15:
                    quotes.append({"artefact_id": aid, "quote": sents[j][:300], "score": sc[j]})
            quotes = sorted(quotes, key=lambda q: -q["score"])[:2]
            cid = f"fc{k}"
            prev = old.get(cid, {})
            out.append({"cluster_id": cid, "name": c["name"], "items": c["items"],
                        "artefact_quotes": [{k2: q[k2] for k2 in ("artefact_id", "quote")} for q in quotes],
                        "class": prev.get("class"), "plan": prev.get("plan")})
        (self.root / "feedback_clusters.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
        return {"clusters": out}

    def clusters(self) -> list[dict]:
        f = self.root / "feedback_clusters.json"
        return json.loads(f.read_text()) if f.exists() else []

    def classify(self, cid: str, klass: str | None, plan: str | None) -> dict:
        rows = self.clusters()
        r = next(r for r in rows if r["cluster_id"] == cid)
        if klass is not None:
            if klass not in CLASSES:
                raise ValueError(f"class must be one of {', '.join(CLASSES)}")
            r["class"] = klass
        if plan is not None:
            r["plan"] = plan          # learner-written only
        (self.root / "feedback_clusters.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        return r

    def overlap(self) -> dict:
        """Human-raised themes the copilot had also raised (lexical match against findings)."""
        f = self.ws.root / "findings.jsonl"
        finds = [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
        rows = []
        for c in self.clusters():
            text = c["name"] + " " + " ".join(c["items"])
            best, score = None, 0.0
            for x in finds:
                s = cite.lexical_support(x["issue"] + " " + x["question_to_learner"] + " " + x["check_id"].replace("_", " "),
                                         [text])[0]
                if s > score:
                    best, score = x, s
            rows.append({"cluster_id": c["cluster_id"], "name": c["name"], "copilot_raised": score >= 0.35,
                         "check_id": best["check_id"] if best and score >= 0.35 else None, "score": round(score, 2)})
        n = len(rows)
        return {"themes": rows, "share_raised": round(sum(r["copilot_raised"] for r in rows) / n, 2) if n else None,
                "note": "Lexical match: a guide to where the copilot and people agree, not a validity measure."}
