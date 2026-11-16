"""CC-4 peer review coach (invented reviews and a fake guard and model)."""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import capstone as cs          # noqa: E402
import capstone_peer as cp     # noqa: E402

SAMPLES = Path(__file__).resolve().parents[2] / "capstone_samples"
guard = lambda lines: [re.sub(r"\bKeelam\b", "a peer", l) for l in lines]
REVIEW = """Questions first:
- How did you pick the 300 households for the pilot?

Comments:
- This is weak.
- The threshold in the Evaluation section should be higher because missed staples are the failure users report most.
- I think the plan needs more detail.
- What happens when the card is declined?
"""
PEER_DOC = """# Pantry Pal
## Evaluation
The release threshold is reorder precision at least 0.85 on 200 past orders, recall at least 0.75.

## Launch plan
Pilot with 300 households chosen at random from weekly online shoppers in one city.
"""


def ws_with(tmp_path):
    for f in ("prd.md", "strategy.md"):
        shutil.copy(SAMPLES / "A" / f, tmp_path / f)
    ws = cs.Workspace(tmp_path / "cap").init()
    ws.register("prd", tmp_path / "prd.md", "prd")
    return ws


def judge(p):
    if "Does the passage already answer" in p:
        return json.dumps({"answered": "households" in p and "pick" in p})
    if "Is it vague" in p:
        c = p.split("COMMENT:")[1].split("\n")[0]
        return json.dumps({"vague": "weak" in c or "more detail" in c, "unjustified": "weak" in c or "more detail" in c})
    if "FEEDBACK ITEMS" in p:
        return json.dumps({"themes": [{"name": "Evaluation", "items": [1, 2]}, {"name": "Pilot", "items": [3]}]})
    return "{}"


def test_peer_documents_need_consent_and_are_guarded(tmp_path):
    pr = cp.Peer(ws_with(tmp_path), guard)
    a = pr.add(consent=False)
    assert a["peer_label"] == "peer_A"
    doc = tmp_path / "peer.md"
    doc.write_text(PEER_DOC + "\nWritten by Keelam.\n")
    with pytest.raises(PermissionError):
        pr.load_doc("peer_A", doc)
    pr.set_consent("peer_A", True)
    pr.load_doc("peer_A", doc)
    stored = (pr.root / "peer_A" / "doc.md").read_text()
    assert "Keelam" not in stored and "a peer" in stored
    pr.set_consent("peer_A", False)
    assert not (pr.root / "peer_A" / "doc.md").exists()


def test_review_findings_are_gated_and_quote_the_review(tmp_path):
    pr = cp.Peer(ws_with(tmp_path), guard)
    pr.add(consent=True)
    rv = tmp_path / "review.md"
    rv.write_text(REVIEW)
    pr.set_review("peer_A", rv)
    d = tmp_path / "peer.md"
    d.write_text(PEER_DOC)
    pr.load_doc("peer_A", d)
    r = pr.check_review("peer_A", judge)
    review = (pr.root / "peer_A" / "review.md").read_text()
    checks = [(f["check_id"], f["quote"][:20]) for f in r["findings"]]
    assert ("vague", "This is weak.") in checks and ("unjustified", "This is weak.") in checks
    assert ("vague", "I think the plan nee") in checks
    assert not any(q.startswith("The threshold") and c in ("vague", "unjustified") for c, q in checks)
    assert any(c == "crp_order" for c, _ in checks)          # an opinion before the last neutral question
    assert all(f["quote"] in review and f["question_to_learner"].endswith("?") for f in r["findings"])
    assert all(cs.gate_finding({k: v for k, v in f.items() if k in cs.FINDING_FIELDS}, review, f["artefact_sha"]) == []
               for f in r["findings"])
    assert "write review comments" in r["note"]


def test_already_answered_needs_the_peer_document(tmp_path):
    pr = cp.Peer(ws_with(tmp_path), guard)
    pr.add(consent=True)
    rv = tmp_path / "review.md"
    rv.write_text("- The pilot does not say how the 300 households were picked, so I cannot judge the sample.\n")
    pr.set_review("peer_A", rv)
    assert not [f for f in pr.check_review("peer_A", judge)["findings"] if f["check_id"] == "already_answered"]
    d = tmp_path / "peer.md"
    d.write_text(PEER_DOC)
    pr.load_doc("peer_A", d)
    hits = [f for f in pr.check_review("peer_A", judge)["findings"] if f["check_id"] == "already_answered"]
    assert hits and "households" in hits[0]["evidence_refs"][0]["peer_quote"]


def test_feedback_clusters_link_to_artefacts_and_class_plan_are_learner_only(tmp_path):
    pr = cp.Peer(ws_with(tmp_path), guard)
    fb = ("- Keelam says the precision threshold of 0.85 seems arbitrary.\n- The golden set of 200 past orders is small.\n"
          "- The pilot city may not represent other users.\n")
    out = pr.feedback(fb, judge)
    assert "Keelam" not in (pr.root / "feedback.md").read_text()
    c1 = out["clusters"][0]
    assert c1["name"] == "Evaluation" and len(c1["items"]) == 2 and c1["class"] is None and c1["plan"] is None
    assert c1["artefact_quotes"] and c1["artefact_quotes"][0]["artefact_id"] == "prd"
    with pytest.raises(ValueError):
        pr.classify("fc1", "lion", None)
    r = pr.classify("fc1", "tiger", "Run the golden set again at 400 orders.")
    assert r["class"] == "tiger" and pr.feedback(fb, judge)["clusters"][0]["class"] == "tiger"   # kept on re-run


def test_overlap_report(tmp_path):
    ws = ws_with(tmp_path)
    pr = cp.Peer(ws, guard)
    pr.feedback("- The precision threshold for the golden set is not tied to a failure.\n- Pricing is unclear at heavy use.\n", judge)
    ws.log("findings.jsonl", {"finding_id": "f1", "check_id": "eval_threshold_tied", "issue": "The threshold is not tied to a named failure mode.",
                              "question_to_learner": "Which failure does the precision threshold guard?"})
    rep = pr.overlap()
    assert rep["themes"][0]["copilot_raised"] and rep["share_raised"] is not None
