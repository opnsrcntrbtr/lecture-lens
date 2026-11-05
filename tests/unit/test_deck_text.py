import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import deck_text

P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def sp(text, ph=None):
    php = f'<p:nvSpPr><p:nvPr><p:ph type="{ph}"/></p:nvPr></p:nvSpPr>' if ph else ""
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in text.split("|"))
    return f"<p:sp>{php}<p:txBody>{paras}</p:txBody></p:sp>"


def make_deck(path):
    slides = [[sp("Soil 101|‹#›"), sp("Why soil matters", "title"), sp("Roots|Water")],
              [sp("Soil 101"), sp("LAYER 2 · TOPSOIL"), sp("Topsoil holds roots"), sp("Most life is here")],
              [sp("Soil 101"), sp("Clay drains slowly")]]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation {P}><p:sldIdLst>' + "".join(
            f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(3)) + "</p:sldIdLst></p:presentation>")
        z.writestr("ppt/_rels/presentation.xml.rels", f"<Relationships {REL}>" + "".join(
            f'<Relationship Id="rId{i + 1}" Type="x/slide" Target="slides/slide{i + 1}.xml"/>' for i in range(3)) + "</Relationships>")
        for i, shapes in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld {P}><p:cSld><p:spTree>{''.join(shapes)}</p:spTree></p:cSld></p:sld>")
        z.writestr("ppt/slides/_rels/slide1.xml.rels", f'<Relationships {REL}><Relationship Id="rId9" '
                   f'Type="{deck_text.NOTES_REL}" Target="../notesSlides/notesSlide1.xml"/></Relationships>')
        z.writestr("ppt/notesSlides/notesSlide1.xml", f"<p:notes {P}><p:cSld><p:spTree>{sp('[2 min] Ask who gardens.')}</p:spTree></p:cSld></p:notes>")


def test_read_deck_titles_text_notes_and_chrome(tmp_path):
    f = tmp_path / "d.pptx"
    make_deck(f)
    d = deck_text.read_deck(str(f))
    assert [s["title"] for s in d] == ["Why soil matters", "Topsoil holds roots", "Clay drains slowly"]
    assert d[0]["notes"] == "[2 min] Ask who gardens." and d[1]["notes"] == ""
    assert "Soil 101" not in d[1]["text"] and "‹#›" not in d[0]["text"]
