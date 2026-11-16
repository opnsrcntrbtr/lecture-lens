"""EP-4 names backfill: dry run, decisions, apply with backups, idempotence, refusal while
capturing, and an audit that holds counts only. All names are invented."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as lk  # noqa: E402

NAME = "Quessandra"


def setup(tmp_path, monkeypatch):
    lec = tmp_path / "lectures"
    f = lec / "20300101-1000_Garden"; f.mkdir(parents=True)
    (f / "notes.md").write_text(f"# Garden\n- {NAME} asked about mulch.\n- The PRD lists a timestamp.\n")
    (f / "cards.tsv").write_text(f"Why mulch?\tIt holds water (as Quesandra said).\tGarden\n")
    res = f / "resources"; res.mkdir()
    (res / "platform_transcript.txt").write_text(f"{NAME}: raw import stays as it is\n")
    live = lec / "live" / "20300101"; live.mkdir(parents=True)
    (live / "whisper_segments.jsonl").write_text(json.dumps({"t": "x", "text": f"Thanks, {NAME}."}) + "\n")
    (live / "top_questions.md").write_text(f"1. Ombrix's question about beds?\n")
    vault = tmp_path / "vault" / "Lectures"; vault.mkdir(parents=True)
    (vault / "20300101-1000 Garden.md").write_text(f"Notes. {NAME} joined late.\n")
    priv = lec / ".private"; priv.mkdir()
    (priv / "attendees.txt").write_text(f"{NAME} Lorimar\n")
    monkeypatch.setattr(lk, "LECTURES", lec)
    monkeypatch.setattr(lk, "PRIVATE", priv)
    monkeypatch.setattr(lk, "VAULT", tmp_path / "vault")
    monkeypatch.setattr(lk, "VAULT_SUB", "Lectures")
    monkeypatch.setattr(lk, "_ROSTER", None)
    monkeypatch.setattr(lk, "_capture_running", lambda: False)
    monkeypatch.setenv("LIVE_QA_STAFF", "Dr Sam Sample")
    return lec, f, live, vault, priv


def run(action, **kw):
    lk.cmd_names(argparse.Namespace(action=action, json=True, remove=kw.get("remove"), keep=kw.get("keep")))


def test_scan_counts_without_names_and_skips_raw(tmp_path, monkeypatch, capsys):
    lec, f, live, vault, priv = setup(tmp_path, monkeypatch)
    run("scan")
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["files_with_names"] == 3 and out["groups_with_names"] == 2
    audit = (lec / "names_audit.json").read_text()
    assert NAME not in audit and "Quesandra" not in audit and "platform_transcript" not in audit
    assert "whisper_segments" not in audit
    cand = json.loads((priv / "names_candidates.json").read_text())
    assert {c["token"] for c in cand if c["decision"] == "removed"} >= {NAME, "Quesandra"}
    assert NAME in (f / "notes.md").read_text()          # dry run changes nothing


def test_apply_rewrites_with_backup_and_is_idempotent(tmp_path, monkeypatch, capsys):
    lec, f, live, vault, priv = setup(tmp_path, monkeypatch)
    run("decide", remove=["Ombrix"], keep=["Klarna"])
    capsys.readouterr()
    run("apply")
    out = json.loads(capsys.readouterr().out)
    assert out["files_rewritten"] == 4 and out["files_with_names"] == 0
    for p in (f / "notes.md", f / "cards.tsv", vault / "20300101-1000 Garden.md", live / "top_questions.md"):
        t = p.read_text()
        assert NAME not in t and "Quesandra" not in t and "Ombrix" not in t and "an attendee" in t
    assert "PRD" in (f / "notes.md").read_text() and "timestamp" in (f / "notes.md").read_text()
    assert NAME in (f / "resources" / "platform_transcript.txt").read_text()     # raw import untouched
    assert NAME in (live / "whisper_segments.jsonl").read_text()
    backups = list(Path(out["backup"]).rglob("notes.md"))
    assert backups and NAME in backups[0].read_text()
    run("apply")
    again = json.loads(capsys.readouterr().out)
    assert again["files_rewritten"] == 0 and again["files_with_names"] == 0


def test_apply_refuses_while_capturing(tmp_path, monkeypatch, capsys):
    lec, f, *_ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(lk, "_capture_running", lambda: True)
    run("apply")
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and "capture" in out["error"] and NAME in (f / "notes.md").read_text()
