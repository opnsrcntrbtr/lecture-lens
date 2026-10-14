import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import qa_export as qe

EXPORT = "﻿" + """Asha Rao      10:11 AM
Do ferns need shade?

Tutor Tom      10:19 AM
Mostly, yes

Ravi K      10:12 AM
Will the soil test be recorded? Asha asked too.

Tutor Tom      10:14 AM
Yes

Asha Rao      10:11 AM
Do ferns need shade?

Tutor Tom      10:19 AM
Mostly, yes
"""


def test_parse_and_merge_doubled_threads():
    e = qe.parse_export(EXPORT)
    assert len(e) == 6 and e[0]["time"] == "10:11 AM"
    th = qe.export_threads(e, {"Tutor Tom"})
    assert [t["q"] for t in th] == ["Do ferns need shade?", "Will the soil test be recorded? Asha asked too."]
    assert th[0]["answers"] == [{"by": "Tutor Tom", "time": "10:19 AM", "text": "Mostly, yes"}]


def test_recall_and_anonymise():
    th = qe.export_threads(qe.parse_export(EXPORT), {"Tutor Tom"})
    captured = [{"q": "Do ferns need shade?Do ferns need shade?", "answers": [{"text": "Mostly, yes"}]}]
    r = qe.capture_recall(th, captured)
    assert r["question_recall"] == 0.5 and r["reply_recall"] == 0.5 and r["missed_times"] == ["10:12 AM"]
    anon = qe.anonymise(th, {"Tutor Tom"}, me={"Ravi K"})
    assert anon[1]["by"] == "You" and "Asha" not in anon[1]["q"] and anon[0]["answers"][0]["by"] == "Tutor Tom"
