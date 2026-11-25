"""`lecture capstone ...`: the capstone copilot from the command line and for the app (JSON).

  init | status | register --kind K --path P | refresh
  review [--check ID ...] | findings | hint --finding ID --attempt TEXT | ask TEXT
  econ --file inputs.json | premortem add|propose|classify ... | faq
  sim start --seed N [--full [--practice]] | sim next | sim decide ... | sim update --audience A --text T |
  sim reply --inject I | sim rate --ratings obj=P,... | sim hotwash [--postmortem ID]
  postmortem --artefact ID | sample
  readiness list | rate --item I --rating R [--artefact A --quote Q | --eval-run E] | candidates --item I
            | triage --answers k=y,... | card --artefact A
  panel start [--type panel|leadership] [--pressure P] [--minutes N] [--seed N] | next | answer --text T
        | finish | self --scores c=3,... | critique | show | metrics
  label --finding ID --valid y|n --specific y|n | progress
  peer add [--consent] | consent --peer P --yes|--no | list | doc --peer P --path F | review --peer P --path F
       | check --peer P | feedback --text T | classify --cluster C --class K --plan T | clusters | overlap
Everything except init, status and register refuses while capture runs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capstone as cs           # noqa: E402
import capstone_review as cr    # noqa: E402
import capstone_sim as sim      # noqa: E402
import capstone_eval as ce      # noqa: E402
import capstone_panel as cp     # noqa: E402
import capstone_readiness as rd  # noqa: E402
import capstone_peer as cpeer   # noqa: E402

ROOT = Path(os.path.expanduser(os.environ.get("STUDY_CAPSTONE_DIR", str(HERE / "capstone"))))
SAMPLES = HERE / "capstone_samples"


def capture_running() -> bool:
    if os.environ.get("STUDY_CAPSTONE_IGNORE_CAPTURE") == "1":   # tests only
        return False
    try:
        r = subprocess.run([str(HERE / "sp-mode"), "--json"], capture_output=True, text=True, timeout=15)
        return bool(json.loads(r.stdout or "{}").get("running"))
    except Exception:
        return False


def llm_fn():
    import study
    return lambda system, user: study.llm(system, user, max_tokens=600, temperature=0.5, slot="interactive")


def lecture_search():
    idx = Path(os.path.expanduser(os.environ.get("STUDY_LECTURE_DIR", str(HERE / "lectures")))) / "index.sqlite"
    if not idx.exists():
        return None
    import evidence
    con = evidence.connect(idx)

    def search(q):
        out = []
        for h in evidence.search(con, q, k=3, sources=("transcript",)):
            out.append({"when": (h.get("t_abs") or "")[:16].replace("T", " "), "lecture": h["lecture_id"],
                        "line_ids": h["line_ids"][:2], "quote": h["text"][:240]})
        return out
    return search


def latest_findings(ws: cs.Workspace) -> list[dict]:
    p = ws.root / "findings.jsonl"
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
    by = {}
    for r in rows:
        by[r["check_id"]] = r
    arts = {a["artefact_id"]: a for a in ws.artefacts()}
    card = checker_card(ws)
    out = []
    for r in by.values():
        a = arts.get(r["artefact_id"])
        r = dict(r)
        r["stale"] = bool(a and a["sha256"] != r["artefact_sha"])
        r["exploratory"] = card.get(r["check_id"], "exploratory") != "ok"
        out.append(r)
    return out


def name_guard():
    """The roster name guard used for lectures, applied to peer documents and pasted feedback."""
    try:
        import datetime as _dt
        import lecture_kit as lk
        return lk._day_guard(_dt.date.today().strftime("%Y%m%d"))
    except Exception:
        return lambda lines: lines


def bench_report() -> dict | None:
    """The newest M-11 report (tools/capstone_bench.py), if any."""
    reports = sorted((HERE / "eval" / "results").glob("*-capstone-m11.json"))
    return json.loads(reports[-1].read_text()) if reports else None


def checker_card(ws: cs.Workspace) -> dict:
    """check_id -> 'ok' | 'exploratory'. A check is 'ok' only with the M-11 bench thresholds met
    and enough of the learner's own labels (CC-6); every check starts exploratory."""
    return {r["criterion"]: r["status"] for r in ce.checker_card(ws, bench_report(), list(cr.CHECKERS))}


def hints_state(ws: cs.Workspace) -> dict:
    p = ws.root / "hints.json"
    return json.loads(p.read_text()) if p.exists() else {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lecture capstone")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init"); sub.add_parser("status"); sub.add_parser("refresh"); sub.add_parser("findings")
    sub.add_parser("faq"); sub.add_parser("sample")
    p = sub.add_parser("register"); p.add_argument("--kind", required=True); p.add_argument("--path", required=True)
    p.add_argument("--id")
    p = sub.add_parser("review"); p.add_argument("--check", action="append")
    p = sub.add_parser("hint"); p.add_argument("--finding", required=True); p.add_argument("--attempt", default="")
    p = sub.add_parser("ask"); p.add_argument("message")
    p = sub.add_parser("econ"); p.add_argument("--file"); p.add_argument("--inputs")
    p = sub.add_parser("premortem"); p.add_argument("action", choices=["list", "add", "propose", "classify"])
    p.add_argument("--text"); p.add_argument("--id"); p.add_argument("--class", dest="klass")
    p.add_argument("--mitigation"); p.add_argument("--owner")
    p = sub.add_parser("postmortem"); p.add_argument("--artefact", required=True)
    p = sub.add_parser("sim"); p.add_argument("action", choices=["start", "next", "decide", "update", "reply", "rate", "hotwash", "show"])
    p.add_argument("--seed", type=int); p.add_argument("--full", action="store_true"); p.add_argument("--inject")
    p.add_argument("--practice", action="store_true", help="full session: unlock injects on request, not on the clock")
    p.add_argument("--audience", default="internal"); p.add_argument("--text", default="")
    p.add_argument("--postmortem", help="registered postmortem artefact to rate action items from")
    p.add_argument("--severity", default=""); p.add_argument("--containment", default="")
    p.add_argument("--customer", default=""); p.add_argument("--internal", default=""); p.add_argument("--regulator", default="")
    p.add_argument("--golden", default=""); p.add_argument("--rationale", default=""); p.add_argument("--ratings", default="")
    p = sub.add_parser("readiness"); p.add_argument("action", choices=["list", "rate", "candidates", "triage", "card"])
    p.add_argument("--item"); p.add_argument("--rating"); p.add_argument("--artefact"); p.add_argument("--quote")
    p.add_argument("--eval-run", dest="eval_run"); p.add_argument("--answers", default="")
    p = sub.add_parser("voice"); p.add_argument("action", choices=["voices", "say", "answer", "log", "summary"])
    p.add_argument("--audio", default=""); p.add_argument("--latency-ms", dest="latency_ms", type=int, default=0)
    p = sub.add_parser("panel"); p.add_argument("action", choices=["start", "next", "answer", "finish", "self", "critique",
                                                                    "show", "metrics"])
    p.add_argument("--type", default="panel", choices=cp.TYPES); p.add_argument("--pressure", default="neutral",
                                                                                 choices=cp.PRESSURES)
    p.add_argument("--minutes", type=int); p.add_argument("--seed", type=int); p.add_argument("--text", default="")
    p.add_argument("--scores", default="")
    p = sub.add_parser("label"); p.add_argument("--finding", required=True); p.add_argument("--valid", required=True)
    p.add_argument("--specific", required=True)
    sub.add_parser("progress")
    p = sub.add_parser("peer"); p.add_argument("action", choices=["add", "consent", "list", "doc", "review", "check",
                                                                  "feedback", "classify", "clusters", "overlap"])
    p.add_argument("--peer"); p.add_argument("--path"); p.add_argument("--text"); p.add_argument("--file")
    p.add_argument("--consent", action="store_true"); p.add_argument("--yes", action="store_true")
    p.add_argument("--no", action="store_true"); p.add_argument("--cluster"); p.add_argument("--class", dest="klass")
    p.add_argument("--plan")
    a = ap.parse_args(argv)

    def out(obj):
        print(json.dumps(obj, ensure_ascii=False))

    ws = cs.Workspace(ROOT).init()
    if a.cmd == "init":
        return out({"ok": True, "workspace": str(ROOT)})
    locked = capture_running()
    if a.cmd == "status":
        return out({"ok": True, "locked": locked, "artefacts": ws.artefacts(), "findings": latest_findings(ws),
                    "rubric": ws.rubric()["rubric_id"], "workspace": str(ROOT)})
    if a.cmd == "register":
        return out({"ok": True, "artefact": ws.register(a.kind, Path(a.path), a.id)})
    if locked:
        return out({"ok": False, "error": "capture is running: the capstone copilot does not open during a class"})
    session = ws.session_dir(a.cmd)
    if a.cmd == "refresh":
        return out({"ok": True, "artefacts": ws.refresh()})
    if a.cmd == "sample":
        dest = ROOT / "sample"
        dest.mkdir(exist_ok=True)
        reg = []
        for f, kind in (("prd.md", "prd"), ("strategy.md", "strategy")):
            shutil.copy(SAMPLES / "A" / f, dest / f)
            reg.append(ws.register(kind, dest / f, f"sample-{kind}"))
        return out({"ok": True, "registered": reg, "note": "an invented product with planted flaws, for practice"})
    if a.cmd == "review":
        r = cr.review(ws, llm_fn(), a.check, lecture_search(), session)
        return out({"ok": True, "gate_pass_rate": r["gate_pass_rate"],
                    "results": [{k: x[k] for k in ("check_id", "verdict", "runs", "finding")} | {"note": x.get("note")}
                                for x in r["results"]]})
    if a.cmd == "findings":
        return out({"ok": True, "findings": latest_findings(ws), "hints": hints_state(ws)})
    if a.cmd == "hint":
        f = next((x for x in latest_findings(ws) if x["finding_id"] == a.finding), None)
        if not f:
            return out({"ok": False, "error": "no such finding"})
        hs = hints_state(ws)
        cur = hs.get(a.finding, {}).get("level", 0)
        attempted = len(a.attempt.strip()) >= 15
        lvl = cs.next_hint(cur, attempted)
        hs[a.finding] = {"level": lvl, "attempts": hs.get(a.finding, {}).get("attempts", 0) + attempted}
        (ws.root / "hints.json").write_text(json.dumps(hs, indent=1))
        ws.log("telemetry.jsonl", {"t": cs.now_iso(), "kind": "hint", "finding": a.finding, "attempted_first": attempted,
                                   "level": lvl})
        with (session / "session.jsonl").open("a") as fh:
            fh.write(json.dumps({"t": cs.now_iso(), "finding": a.finding, "attempt": a.attempt[:2000], "level": lvl}) + "\n")
        h = cs.hint(f, lvl, ws.rubric())
        return out({"ok": True, **h, "needs_attempt": not attempted and lvl == cur and cur < 3})
    if a.cmd == "ask":
        r = cs.respond(a.message, ws, session)
        if not r["refused"]:
            r["text"] = "I review your registered artefacts and run drills; try Run review or Start drill."
        return out({"ok": True, **r})
    if a.cmd == "econ":
        x = json.loads(Path(a.file).read_text()) if a.file else json.loads(a.inputs or "{}")
        try:
            r = cr.unit_econ(x)
        except (KeyError, ValueError) as e:
            return out({"ok": False, "error": f"missing or bad input: {e}"})
        (ROOT / "unit_econ.json").write_text(json.dumps({"inputs": x, "computed": r}, indent=1))
        return out({"ok": True, **r})
    if a.cmd == "premortem":
        f = ROOT / "premortem.json"
        rows = json.loads(f.read_text()) if f.exists() else []
        if a.action == "add":
            if not (a.text or "").strip():
                return out({"ok": False, "error": "--text is your risk, in your words"})
            rows.append({"risk_id": f"lr-{len(rows) + 1}", "author": "learner", "text": a.text, "class": a.klass,
                         "mitigation": a.mitigation, "owner": a.owner})
        elif a.action == "propose":
            mine = [r for r in rows if r["author"] == "learner"]
            if not mine:
                return out({"ok": False, "error": "list your own Tigers, Paper Tigers and Elephants first"})
            have = {r["risk_id"] for r in rows}
            rows += [r for r in cr.premortem_propose(mine) if r["risk_id"] not in have]
        elif a.action == "classify":
            r = next((r for r in rows if r["risk_id"] == a.id), None)
            if not r or (a.klass and a.klass not in cr.PREMORTEM_CLASSES):
                return out({"ok": False, "error": f"need --id and --class in {cr.PREMORTEM_CLASSES}"})
            r.update({k: v for k, v in (("class", a.klass), ("mitigation", a.mitigation), ("owner", a.owner)) if v})
        f.write_text(json.dumps(rows, indent=1))
        return out({"ok": True, "premortem": rows})
    if a.cmd == "faq":
        return out({"ok": True, "questions": cr.faq_questions(ws, llm_fn())})
    if a.cmd == "postmortem":
        return out({"ok": True, "findings": sim.postmortem_findings(ws, a.artefact)})
    if a.cmd == "readiness":
        r = rd.Readiness(ws)
        try:
            if a.action == "rate":
                ev = ({"eval_run_id": a.eval_run} if a.eval_run else
                      {"artefact_id": a.artefact, "quote": a.quote} if a.artefact else None)
                r.rate(a.item, a.rating, ev)
            elif a.action == "candidates":
                return out({"ok": True, "item": a.item, "candidates": rd.candidates(ws, a.item)})
            elif a.action == "triage":
                answers = dict(x.split("=", 1) for x in a.answers.split(",") if "=" in x)
                return out({"ok": True, "questions": rd.TRIAGE_QUESTIONS,
                            **rd.triage(answers, json.loads((ROOT / "governance_snapshot.json").read_text()))})
            elif a.action == "card":
                if not a.artefact:
                    return out({"ok": False, "error": "--artefact is your system card's id"})
                return out({"ok": True, "artefact": a.artefact, **rd.audit_card(ws.text(a.artefact)[0])})
        except (ValueError, StopIteration) as e:
            return out({"ok": False, "error": str(e) or "unknown artefact"})
        return out({"ok": True, **r.status()})
    if a.cmd == "panel":
        cur = ROOT / "current_panel.json"
        if a.action == "start":
            seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(2), "big")
            pn = cp.Panel(ws, seed)
            try:
                v = pn.start(a.type, a.pressure, a.minutes)
            except ValueError as e:
                return out({"ok": False, "error": str(e)})
            cur.write_text(json.dumps({"seed": seed, "dir": str(pn.dir)}))
            return out({"ok": True, **pn.next_question(llm_fn())})
        if not cur.exists():
            return out({"ok": False, "error": "no panel: panel start first"})
        c = json.loads(cur.read_text())
        pn = cp.Panel(ws, c["seed"])
        pn.dir = Path(c["dir"])
        try:
            if a.action == "answer":
                if len(a.text.strip()) < 2:
                    return out({"ok": False, "error": "type your answer"})
                pn.answer(a.text)
                return out({"ok": True, **pn.next_question(llm_fn())})
            if a.action == "next":
                return out({"ok": True, **pn.next_question(llm_fn())})
            if a.action == "finish":
                return out({"ok": True, **pn.finish()})
            if a.action == "self":
                pn.self_score(dict(x.split("=", 1) for x in a.scores.split(",") if "=" in x))
                return out({"ok": True, **pn.view()})
            if a.action == "critique":
                return out({"ok": True, "score": pn.critique(llm_fn()), "metrics": pn.metrics(), **pn.view()})
            if a.action == "metrics":
                return out({"ok": True, **pn.metrics()})
        except (ValueError, RuntimeError) as e:
            return out({"ok": False, "error": str(e)})
        score = pn.dir / "score.json"
        return out({"ok": True, **pn.view(), "score": json.loads(score.read_text()) if score.exists() else None})
    if a.cmd == "voice":   # CC-7: the panel out loud (built-in voices, local whisper)
        import capstone_voice as cv
        if a.action == "voices":
            return out({"ok": True, "voices": cv.installed_voices()})
        cur = ROOT / "current_panel.json"
        if not cur.exists():
            return out({"ok": False, "error": "no panel: panel start first"})
        c = json.loads(cur.read_text())
        pn = cp.Panel(ws, c["seed"])
        pn.dir = Path(c["dir"])
        vp = cv.VoicePanel(pn)
        if a.action == "say":
            return out({"ok": True, **vp.say_current()})
        if a.action == "answer":
            if not a.audio or not Path(a.audio).is_file():
                return out({"ok": False, "error": "no recording"})
            r = vp.answer_audio(Path(a.audio), llm_fn())
            return out({"ok": not r.get("error"), **r})
        if a.action == "log":
            return out({"ok": True, **vp.log_latency(a.latency_ms)})
        return out({"ok": True, **vp.summary()})
    if a.cmd == "label":
        yn = lambda v: str(v).lower() in ("y", "yes", "true", "1")
        try:
            return out({"ok": True, "label": ce.label_finding(ws, a.finding, yn(a.valid), yn(a.specific))})
        except KeyError as e:
            return out({"ok": False, "error": str(e)})
    if a.cmd == "peer":
        pr = cpeer.Peer(ws, name_guard())
        judge = lambda p: llm_fn()("You check peer-review comments. JSON only.", p)
        try:
            if a.action == "add":
                return out({"ok": True, "peer": pr.add(a.consent)})
            if a.action == "consent":
                return out({"ok": True, "peer": pr.set_consent(a.peer, a.yes and not a.no)})
            if a.action == "doc":
                return out({"ok": True, **pr.load_doc(a.peer, Path(a.path))})
            if a.action == "review":
                return out({"ok": True, **pr.set_review(a.peer, Path(a.path))})
            if a.action == "check":
                return out({"ok": True, **pr.check_review(a.peer, judge)})
            if a.action == "feedback":
                text = Path(a.file).expanduser().read_text() if a.file else (a.text or "")
                return out({"ok": True, **pr.feedback(text, judge)})
            if a.action == "classify":
                return out({"ok": True, "cluster": pr.classify(a.cluster, a.klass, a.plan)})
            if a.action == "clusters":
                return out({"ok": True, "clusters": pr.clusters()})
            if a.action == "overlap":
                return out({"ok": True, **pr.overlap()})
        except (KeyError, StopIteration):
            return out({"ok": False, "error": "no such peer or cluster"})
        except (PermissionError, ValueError, FileNotFoundError) as e:
            return out({"ok": False, "error": str(e)})
        return out({"ok": True, "peers": pr.peers()})
    if a.cmd == "progress":
        return out({"ok": True, **ce.progress(ws, bench_report(), list(cr.CHECKERS))})
    if a.cmd == "sim":
        cur = ROOT / "current_drill.json"
        if a.action == "start":
            seed = a.seed if a.seed is not None else int.from_bytes(os.urandom(2), "big")
            d = sim.Drill(ws, seed)
            view = d.start(micro=not a.full, real_time=(not a.practice) if a.full else None)
            cur.write_text(json.dumps({"seed": seed, "dir": str(d.dir)}))
            return out({"ok": True, "seed": seed, **view})
        if not cur.exists():
            return out({"ok": False, "error": "no drill: sim start first"})
        c = json.loads(cur.read_text())
        d = sim.Drill(ws, c["seed"])
        d.dir = Path(c["dir"])
        st = d.state()
        if a.action == "show":
            return out({"ok": True, "seed": c["seed"], **d.view(), "updates": d.updates(),
                        "decisions": d.decisions(), "rated": "self" in st, "hotwash": st.get("hotwash", False)})
        if a.action == "next":
            return out({"ok": True, "seed": c["seed"], **d.next()})
        if a.action == "update":
            try:
                return out({"ok": True, "update": d.update(a.audience, a.text)})
            except ValueError as e:
                return out({"ok": False, "error": str(e)})
        if a.action == "decide":
            row = d.decide(a.inject, a.severity, a.containment,
                           {"customer": a.customer, "internal": a.internal, "regulator": a.regulator}, a.golden, a.rationale)
            return out({"ok": True, "decision": row})
        if a.action == "reply":
            return out({"ok": True, "role_reply": d.role_reply(a.inject, llm_fn())})
        if a.action == "rate":
            try:
                ratings = dict(x.split("=", 1) for x in a.ratings.split(",") if "=" in x)
                return out({"ok": True, "self": d.self_rate(ratings)})
            except ValueError as e:
                return out({"ok": False, "error": str(e)})
        if a.action == "hotwash":
            try:
                return out({"ok": True, **d.hotwash(postmortem=a.postmortem)})
            except RuntimeError as e:
                return out({"ok": False, "error": str(e)})


if __name__ == "__main__":
    main()
