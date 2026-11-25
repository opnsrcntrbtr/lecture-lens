"""Class resources added after a lecture: what each file is, and the forms the build reads
(invented gardening class; files built on the fly)."""
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import resources as R  # noqa: E402

WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(path: Path, paras: list[tuple[str, bool]]):
    num = '<w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>'
    body = "".join(f"<w:p>{num if listed else ''}<w:r><w:t>{t}</w:t></w:r></w:p>" for t, listed in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<w:document {WNS}><w:body>{body}</w:body></w:document>')
    return path


def make_xlsx(path: Path, sheets: dict[str, list[list[str]]]):
    shared, rels, wb, parts = [], [], [], {}
    for i, (name, rows) in enumerate(sheets.items(), 1):
        xml_rows = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, v in enumerate(row):
                if v == "":
                    continue
                shared.append(v)
                cells.append(f'<c r="{chr(65 + c)}{r}" t="s"><v>{len(shared) - 1}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{i}.xml"] = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(xml_rows) + "</sheetData></worksheet>")
        rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>')
        wb.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(wb) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + "</Relationships>")
        z.writestr("xl/sharedStrings.xml",
                   '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        for k, v in parts.items():
            z.writestr(k, v)
    return path


AGENDA = [("Gardening basics, session 3", False), ("Agenda and pre-read", False),
          ("Part 1. Soil and compost (about 40 minutes)", False),
          ("What soil is made of and how compost feeds it.", True),
          ("Part 2. Watering (about 30 minutes)", False), ("Drip lines, mulch and timing.", True)]

SHEET = {
    "Answered": [["No", "Type", "Question", "Asked by", "Email", "Original note", "Final answer"],
                 ["1", "Academic", "Orla asked: how deep should mulch be?", "Orla Venn", "contact-orla-77",
                  "about 5 cm", "About five to eight centimetres, kept off the stems."],
                 ["2", "Programme", "Will the seed list be shared?", "Tamsin Rook", "contact-tamsin-12", "", "Yes, after class."],
                 ["3", "Chat", "Thanks!", "Orla Venn", "", "", "You're welcome."],
                 ["4", "Academic", "Does compost need turning?", "Ivo Brand", "", "", ""],
                 ["5", "Academic", "Should every attendee label seedlings?", "Anonymous Attendee", "", "", "Only the ones you sell."]],
    "Short": [["Question", "Answer"], ["Orla asked: how deep should mulch be?", "Five cm."]],
}


def test_docx_paragraphs_mark_list_items_and_classify_as_agenda(tmp_path):
    p = make_docx(tmp_path / "agenda.docx", AGENDA)
    paras = R.docx_paragraphs(p)
    assert paras[3] == "- What soil is made of and how compost feeds it."
    assert R.classify(p) == "agenda"
    other = make_docx(tmp_path / "essay.docx", [("Why we garden. " * 30, False)] * 3)
    assert R.classify(other) == "reading"


def test_qa_sheet_keeps_questions_and_answers_never_people(tmp_path):
    p = make_xlsx(tmp_path / "qa.xlsx", SHEET)
    assert R.classify(p) == "qa_sheet"
    rows, people, counts = R.qa_from_sheets(R.xlsx_sheets(p))
    assert counts["skipped_type"] == 1 and counts["unanswered"] == 1
    assert [r["no"] for r in rows] == ["1", "2", "5"]
    assert not any("Attendee" in p for p in people)                    # a placeholder is not a person
    assert rows[0]["a"].startswith("About five")               # final answer, and the longer one
    assert "Orla Venn" in people and all(set(r) == {"no", "type", "q", "a"} for r in rows)
    texts, n = R.scrub_people([rows[0]["q"], rows[2]["q"]], people, keep=set())
    assert texts == ["[name] asked: how deep should mulch be?", "Should every attendee label seedlings?"] and n == 1


def test_pdf_pages_become_slides_without_chrome():
    pages = [f"GARDEN COURSE · SESSION 3\nSlide topic {i}\nPoint one about soil {i}\nPoint two\n{i}" for i in range(1, 9)]
    deck = R.deck_from_pages(pages)
    assert len(deck) == 8 and deck[0]["title"] == "Slide topic 1"
    assert "GARDEN COURSE" not in deck[0]["text"] and "Point one about soil 1" in deck[0]["text"]


def lecture(tmp_path):
    f = tmp_path / "20300115-1000_Gardening"
    f.mkdir()
    (f / "manifest.json").write_text(json.dumps({"id": "20300115-1000", "title": "Gardening"}))
    return f


def test_add_writes_each_form_keeps_the_sheet_private_and_is_idempotent(tmp_path):
    folder, live = lecture(tmp_path), tmp_path / "live" / "20300115"
    deck = tmp_path / "Soil deck.pptx"
    deck.write_bytes(b"not read: read_deck is faked")
    fake = lambda path: [{"n": 1, "title": "Soil", "text": "Loam, clay, sand", "notes": "Start with a handful."}]
    readings = []
    imp = lambda folder, path, title: readings.append(title) or {"ok": True, "saved_path": f"resources/{len(readings)}_reading.md"}
    files = [deck, make_docx(tmp_path / "agenda.docx", AGENDA), make_xlsx(tmp_path / "qa.xlsx", SHEET)]
    rep = R.add(folder, files, live_dir=live, read_deck=fake, import_reading=imp,
                guard=lambda lines: lines, now="2030-01-20T10:00:00+00:00")
    assert [a["kind"] for a in rep["added"]] == ["deck", "agenda", "qa_sheet"] and rep["rebuild"]
    assert json.loads((folder / "resources/decks/Soil-deck.deck.json").read_text())[0]["title"] == "Soil"
    assert (live / "agenda.txt").read_text().startswith("Gardening basics")
    assert readings == ["Slides: Soil deck", "agenda"]
    sheet = (folder / "qa_sheet.json").read_text()
    assert "Orla" not in sheet and "contact-" not in sheet and "Tamsin" not in sheet
    assert list(live.glob("qa_sheet_*.xlsx")) and not list((folder / "resources/files").glob("*.xlsx"))
    again = R.add(folder, files, live_dir=live, read_deck=fake)
    assert not again["added"] and [s["reason"] for s in again["skipped"]] == ["already added"] * 3
    assert len(R.load_index(folder)) == 3


def test_an_existing_live_agenda_is_not_replaced(tmp_path):
    folder, live = lecture(tmp_path), tmp_path / "live" / "20300115"
    live.mkdir(parents=True)
    (live / "agenda.txt").write_text("typed before class\n")
    R.add(folder, [make_docx(tmp_path / "agenda.docx", AGENDA)], live_dir=live)
    assert (live / "agenda.txt").read_text() == "typed before class\n"
    assert (folder / "resources/agenda.txt").exists()


def test_build_inputs_combined_deck_and_sheet(tmp_path):
    folder = lecture(tmp_path)
    for name in ("a.pptx", "b.pptx"):
        (tmp_path / name).write_bytes(name.encode())
    slides = {"a": [{"n": 1, "title": "A1", "text": "", "notes": ""}, {"n": 2, "title": "A2", "text": "", "notes": ""}],
              "b": [{"n": 1, "title": "B1", "text": "", "notes": ""}]}
    R.add(folder, [tmp_path / "a.pptx", tmp_path / "b.pptx"], read_deck=lambda p: slides[Path(p).stem])
    deck = R.combined_deck(folder, own=[{"n": 1, "title": "Own", "text": "", "notes": ""}])
    assert [(s["n"], s["title"], s["deck"]) for s in deck] == [(1, "Own", "class deck"), (2, "A1", "a"), (3, "A2", "a"), (4, "B1", "b")]
    idx = R.deck_index_markdown(deck)
    assert "## a" in idx and "- **[deck 3]** A2 (its slide 2)" in idx
    R.add(folder, [make_xlsx(tmp_path / "qa.xlsx", SHEET)], live_dir=tmp_path / "live")
    rows = R.load_sheet(folder)
    assert R.in_sheet("[name] asked: how deep should mulch be?", rows)
    assert not R.in_sheet("Which tomato variety is best?", rows)
    src = R.sheet_source(rows)
    assert "mulch" in src and "seed list" not in src           # programme questions stay in qa.md only
    md = "\n".join(R.sheet_markdown(rows, "Gardening"))
    assert "Q2 · Programme" in md and "Orla" not in md


@pytest.mark.skipif(R._pdftotext() is None, reason="pdftotext not installed")
def test_a_landscape_pdf_is_a_deck(tmp_path):
    def pdf(pages, w, h):
        objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
        kids = []
        for t in pages:
            stream = f"BT /F1 24 Tf 50 300 Td ({t}) Tj ET"
            objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
            kids.append(f"{len(objs)} 0 R")
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
        out, offs = "%PDF-1.4\n", []
        for i, o in enumerate(objs, 1):
            offs.append(len(out))
            out += f"{i} 0 obj\n{o}\nendobj\n"
        x = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offs)
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n"
        return out.encode("latin-1")
    p = tmp_path / "slides.pdf"
    p.write_bytes(pdf(["Watering by hand", "Drip lines save water"], 960, 540))
    assert R.classify(p) == "deck"
    assert [s["title"] for s in R.deck_from_pages(R.pdf_pages(p))] == ["Watering by hand", "Drip lines save water"]


def test_fit_blocks_shares_room_by_weight():
    import lecture_kit as k
    blocks = {"deck": "\n".join(f"[deck {i}] slide text here" for i in range(200)),
              "keyframes": "KEYFRAME TIMES:\n[slide 1 @ 10:00] x",
              "qa": "\n".join(f"Q: question {i} | A: answer" for i in range(200))}
    out = k.fit_blocks(blocks, 3000)
    assert len(out) <= 3000 and "KEYFRAME TIMES" in out and "Q: question 0" in out and "[deck 0]" in out
    assert all(not l.endswith("slide te") for l in out.splitlines())     # cut at a line end
    small = {"deck": "a", "qa": "b"}
    assert k.fit_blocks(small, 3000) == "a\n\nb"
