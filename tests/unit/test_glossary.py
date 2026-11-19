"""CT-3 glossary: terms from slides and evidence, names kept out, allow-list for the name
guard, suggestions for review only. Invented data."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import glossary as gl     # noqa: E402
import name_guard         # noqa: E402

DICT = {"the", "golden", "set", "case", "study", "trust", "bands", "garden", "planner", "watering", "soil", "beds"}
in_dict = lambda w: w in DICT


def lectures(tmp_path):
    live = tmp_path / "live" / "20300105"
    live.mkdir(parents=True)
    slides = [{"kind": "slide", "title": "Garden Planner · Golden Set", "text": "RAG over the PlantDB catalogue\nKlarna case study\nPlantDB again"},
              {"kind": "slide", "title": "Klarna and Zillow", "text": "LLM judges, CSAT and RAG on PlantDB"},
              {"kind": "gallery", "title": "Zorblat waves", "text": "Zorblat Zorblat"},
              {"kind": "slide", "title": "Questions from Quillon", "text": "Zillow forecasting"}]
    (live / "slides.jsonl").write_text("".join(json.dumps(s) + "\n" for s in slides))
    (live / "evidence.json").write_text(json.dumps([{"id": "S1", "title": "Waymo safety report", "fact": "Waymo LLM study"},
                                                    {"id": "S2", "title": "Klarna chatbot", "fact": "Klarna rehired staff; Zillow Offers closed"}]))
    return tmp_path


def test_build_finds_terms_keeps_names_out_and_keeps_manual_entries(tmp_path):
    root = lectures(tmp_path)
    is_name = lambda w: w.lower() in {"quillon"}               # a roster name seen on a slide
    g = gl.build(root, is_name, in_dict)
    terms = {e["term"]: e for e in gl.active(g)}
    assert {"RAG", "LLM", "CSAT", "PlantDB", "Klarna", "Zillow"} <= set(terms)
    every = {e["term"] for e in g["terms"]}
    assert "Quillon" not in every and "Zorblat" not in every   # roster name; gallery frames are not slides
    assert "Waymo" in terms and terms["Waymo"]["source"] == "evidence"
    assert "plant db" in terms["PlantDB"]["aliases"] and "r a g" in terms["RAG"]["aliases"]
    g = gl.remove(gl.add(g, "Garden Planner"), "CSAT")
    g2 = gl.build(root, is_name, in_dict, old=g)
    t2 = {e["term"]: e for e in g2["terms"]}
    assert t2["Garden Planner"]["source"] == "manual" and t2["CSAT"]["status"] == "removed"
    assert "CSAT" not in {e["term"] for e in gl.active(g2)}


def test_glossary_words_are_never_removed_by_the_name_guard(tmp_path):
    g = gl.build(lectures(tmp_path), lambda w: False, in_dict)
    allow = gl.allow_words(g)
    assert {"klarna", "zillow", "plantdb", "waymo"} <= allow and "rag" not in allow   # acronyms need no shield
    lines = ["Klarna rehired people after the chatbot.", "Thanks Zillow, as Waymo showed."]
    out, rep = name_guard.guard(lines, panel_authors={"Zillow"}, dictionary=set(DICT), allow=allow, staff=set())
    assert out == lines


def test_suggestions_are_listed_not_applied():
    g = {"terms": [{"term": "Zillow", "status": "active"}, {"term": "LlamaIndex", "status": "active"}, {"term": "RAG", "status": "active"}]}
    lines = [{"line_id": "k/t/1", "text": "The Zilow case shows forecasting limits."},
             {"line_id": "k/t/2", "text": "Use Lamaindex for retrieval; Zillow again; rag is fine."}]
    lines.append({"line_id": "k/t/3", "text": "Zillows and services matter."})
    s = gl.suggestions(lines, g, name_guard.similarity, in_dictionary=lambda w: w in {"services", "matter"})
    heard = {r["heard"]: r for r in s}
    assert heard["Zilow"]["term"] == "Zillow" and heard["Zilow"]["line_ids"] == ["k/t/1"]
    assert heard["Lamaindex"]["term"] == "LlamaIndex"
    assert "Zillow" not in heard and "rag" not in heard and "Zillows" not in heard and "services" not in heard
    assert lines[0]["text"] == "The Zilow case shows forecasting limits."


def test_slide_only_words_wait_for_review_and_ocr_junk_is_dropped(tmp_path):
    live = tmp_path / "live" / "20300105"
    live.mkdir(parents=True)
    slides = [{"kind": "slide", "title": "Guest talk by Vantaro on GenAI", "text": "EXAMPLE NOTICE\nmapWhere agendaUNDERSTANDDECIDEThe"},
              {"kind": "slide", "title": "Vantaro on pricing", "text": "EXAMPLE of GenAI pricing, GenAI again, MatrixHow"}]
    (live / "slides.jsonl").write_text("".join(json.dumps(s) + "\n" for s in slides))
    g = gl.build(tmp_path, lambda w: False, lambda w: w in {"example", "notice", "of", "pricing", "guest", "talk", "by", "on"})
    by = {e["term"]: e for e in g["terms"]}
    assert by["Vantaro"]["status"] == "review" and "vantaro" not in gl.allow_words(g)
    assert "EXAMPLE" not in by and "NOTICE" not in by and "mapWhere" not in by
    assert not any(t.startswith("agenda") for t in by) and by["GenAI"]["status"] == "active" and "MatrixHow" not in by
    g = gl.add(g, "Vantaro")
    assert "vantaro" in gl.allow_words(g)
