"""NG-3 notes v2 and the M-6 checklist tools (invented class, fake model)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import notes_v2 as nv  # noqa: E402

TEXTS = ["Welcome everyone, can you hear me", "Today we cover soil and watering",
         "Mulch keeps water in the soil and cuts watering by half", "Compost feeds soil life",
         "A worked example: a 10 square metre bed needs 40 litres a week",
         "Drip lines put water at the roots", "Water early in the morning, not at noon"] * 3


def lines():
    out = []
    for i, t in enumerate(TEXTS):
        s = 600 + i * 40
        out.append({"line_id": f"L/t/{i}", "t": f"10:{s // 60:02d}:{s % 60:02d}", "text": t})
    out.append({"line_id": "L/s/1", "t": "10:10:30", "text": "Soil, Compost and Mulch Basics slide"})
    return out


def fake(prompt):
    if "TITLE CANDIDATES" in prompt:
        return json.dumps({"title_choice": 2, "sections": [{"heading": "Soil", "from": 1, "to": 9},
                                                           {"heading": "Tiny", "from": 10, "to": 11},
                                                           {"heading": "Watering", "from": 13, "to": 30}]})
    if "Copy out, word for word" in prompt:
        n = [l.split(" ")[0][1:] for l in prompt.splitlines() if l.startswith("L")]
        first = int(n[0])
        return json.dumps({"extracts": [
            {"type": "number", "quote": "cuts watering by half", "lines": [first + 2]},
            {"type": "definition", "quote": "mulch makes soil warmer", "lines": [first + 2]},       # not verbatim: dropped
            {"type": "example", "quote": "a 10 square metre bed needs 40 litres a week", "lines": [first + 3]}]})  # off by one: kept
    if "Write study notes for this section" in prompt:
        return "- Mulch cuts watering by half [10:11:20]\n- A 10 square metre bed needs 40 litres a week\nnot a bullet"
    if "Pick the 5 extracts" in prompt:
        return json.dumps({"tldr": [{"id": "e1", "text": "Mulch halves watering."}, {"id": "e99", "text": "x"}]})
    return "{}"


def test_build_outline_title_extracts_and_timecodes():
    res = nv.build(lines(), ["Gardening session 3", "Soil and water"], "Part 1 Soil", fake)
    ol = res["outline"]
    assert ol["title"] == "Soil and water" and ol["title_source"] == "model_choice" and not ol["title_review"]
    secs = ol["sections"]
    assert secs[0]["from"] == 1 and secs[-1]["to"] == len(TEXTS)
    assert all(b["from"] == a["to"] + 1 for a, b in zip(secs, secs[1:]))           # contiguous
    assert all(s["to"] - s["from"] + 1 >= nv.MIN_SECTION or s is secs[0] for s in secs)
    assert all(e["quote"] in " ".join(TEXTS) for e in res["extracts"])
    assert res["audit"]["dropped_extracts"] >= len(secs)                             # the reworded quote each time
    md = res["notes"]
    assert md.startswith("# Soil and water") and "## TL;DR" in md and "e99" not in md
    for s in secs:
        a, b = nv._secs(s["t0"]), nv._secs(s["t1"])
        for line in s["bullets_md"].splitlines():
            codes = nv.TC.findall(line)
            assert codes and all(a <= nv._secs(c) <= b for c in codes)              # every bullet in range
    assert res["audit"]["timecodes_repaired"] >= 1


def test_title_must_come_from_the_candidates():
    bad = lambda p: json.dumps({"title_choice": 9, "sections": []}) if "TITLE CANDIDATES" in p else fake(p)
    res = nv.build(lines(), ["Gardening session 3"], "", bad)
    assert res["outline"]["title"] == "Gardening session 3" and res["outline"]["title_review"]
    assert len(res["outline"]["sections"]) == 1


def test_audit_flags_numbers_not_in_sources():
    rows, _ = nv.numbered(lines())
    secs = [{"from": 1, "to": len(rows), "bullets": ["- Mulch saves 75 percent of water [10:11:20]"]}]
    au = nv.audit("# T\n## Heading With Caps\n- Mulch saves 75 percent of water [10:11:20]\n", [], secs, rows, 0, 0,
                  "Soil, Compost and Mulch Basics slide")
    assert au["numbers_not_in_sources"] == ["75"] and "Heading With" not in " ".join(au["terms_not_in_sources"])


def test_facts_draft_spreads_and_verifies():
    import facts_draft
    many = [{"line_id": f"L/t/{i}", "t": f"{10 + i // 60:02d}:{i % 60:02d}:00", "text": f"point {i} mulch keeps water in"}
            for i in range(90)]

    def drafter(p):
        return json.dumps({"facts": [{"fact": "Mulch keeps water in.", "quote": "mulch keeps water in", "lines": [1]},
                                     {"fact": "Made up.", "quote": "never said this", "lines": [2]}]})
    facts = facts_draft.draft(many, drafter, n=5)
    assert len(facts) == 5 and len({f["t"][:2] + f["t"][3:4] for f in facts}) >= 3     # several windows
    assert [f["fact_id"] for f in facts] == ["f01", "f02", "f03", "f04", "f05"]


def test_bench_sentences_and_recall():
    import notes_bench
    ss = notes_bench.sentences("# T\n_[10:00:00–10:10:00]_\n- Mulch keeps water in the soil today [10:01:00]. Short.\n")
    assert ss == ["Mulch keeps water in the soil today."]
    r, got = notes_bench.recall("notes", [{"fact": "a"}, {"fact": "b"}],
                                lambda p: json.dumps({"present": "FACT: a" in p}))
    assert r == 0.5 and got == [True, False]
