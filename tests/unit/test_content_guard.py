"""The content guard must catch what it claims to, with a made-up blocked list."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import content_guard as g  # noqa: E402


def make_terms(tmp_path, words, allow=()):
    norm = [" ".join(g.WORD.findall(w.lower())) for w in words]
    norm += [n.replace(" ", "") for n in norm]
    lengths = sorted({len(n) for n in norm if " " not in n and len(n) >= 5})
    h = tmp_path / "h.sha256"
    h.write_text("# embedded-lengths: " + ",".join(map(str, lengths)) + "\n"
                 + "\n".join(hashlib.sha256(n.encode()).hexdigest() for n in set(norm)) + "\n")
    a = tmp_path / "allow.txt"
    a.write_text("\n".join(allow))
    return g.Terms(hash_file=h, allow_file=a, local=tmp_path / "missing.txt")


def test_word_and_phrase_are_caught_in_any_case_and_punctuation(tmp_path):
    t = make_terms(tmp_path, ["Zorbania", "Acme Polytechnic of Zorbania"])
    assert t.find("Lecture at ACME-Polytechnic   of zorbania today") == ["acme polytechnic of zorbania", "zorbania"]
    assert t.find("nothing to see here") == []


def test_word_glued_inside_another_is_caught(tmp_path):
    t = make_terms(tmp_path, ["zorbania"])
    assert t.find("see learn.zorbaniaportal.example/player") == ["zorbaniaportal"]


def test_allowlisted_lookalike_passes(tmp_path):
    t = make_terms(tmp_path, ["zorban"], allow=["zorbanic"])
    assert t.find("a zorbanic acid test") == []
    assert t.find("the zorbanite test") == ["zorbanite"]


def test_short_words_only_match_whole(tmp_path):
    t = make_terms(tmp_path, ["zqx"])
    assert t.find("ZQX campus") == ["zqx"]
    assert t.find("azqxb") == []


def test_forbidden_paths():
    assert g.check_path("lectures/2026/notes.md")
    assert g.check_path("deck/week1.pptx")
    assert g.check_path(".env") and not g.check_path(".env.example")
    assert g.check_path("app/screenshot.png") and not g.check_path("website/static/img/logo.png")
    assert not g.check_path("lecture_kit.py")


def test_secrets_and_personal_data(tmp_path):
    t = make_terms(tmp_path, ["zorbania"])
    kinds = lambda s: [m.split(":")[0].split(" (")[0] for _, _, m in g.check_text(s, t, "f")]
    assert kinds("OMLX_API_" + "KEY=abcd1234abcd1234abcd1234abcd") == ["possible secret"]
    assert kinds("OMLX_API_KEY=<your key>") == []
    assert kinds("token ghp_" + "a" * 36) == ["possible secret"]
    assert kinds("see /Us" + "ers/jane/project/x") == ["personal data"]
    assert kinds("see ~/project and /Users/you/project") == []
    assert kinds("mail jane@" + "corp.io") == ["personal data"]
    assert kinds("mail jane@example.com or 1+bot@users.noreply.github.com") == []
    assert kinds("Signed-off-by: a-bot <support@" + "github.com>") == []


def test_the_repository_itself_is_clean():
    # Strings above are split so this file passes the guard it tests.
    """The public tree must pass its own guard (uses the committed hash list)."""
    if not g.HASH_FILE.exists():
        return
    assert g.scan_all(g.Terms()) == []
