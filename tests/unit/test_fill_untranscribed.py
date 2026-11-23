"""Audio recorded but never transcribed is filled at build time (lecture_kit.fill_untranscribed). Invented fixtures."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lecture_kit as lk  # noqa: E402

UTC = dt.timezone.utc
T0 = dt.datetime(2030, 1, 5, 10, 0, tzinfo=UTC)


class Con:
    def __init__(self, rows): self.rows = rows
    def execute(self, sql, params): return lk._Rows(self.rows)


def test_only_uncovered_chunks_are_transcribed(monkeypatch):
    chunks = [((T0 + dt.timedelta(seconds=30 * i)).isoformat(), f"/d/System Audio (output)_{i}.mp4") for i in range(6)]
    audio = [{"ts": T0 + dt.timedelta(seconds=5), "text": "hello", "device": "System Audio (output)"},
             {"ts": T0 + dt.timedelta(seconds=31), "text": "again", "device": "System Audio (output)"}]
    seen = []

    def fake(batch, keep_from, prompt, threads):
        seen.append([n for _, n in batch])
        return [{"t": (t + dt.timedelta(seconds=2)).isoformat(), "text": f"filled {n}"} for t, n in batch]

    out, n = lk.fill_untranscribed(Con(chunks), T0, T0 + dt.timedelta(minutes=5), audio, transcribe=fake)
    # chunks 0 and 1 are covered by the two lines (within 35 s); 2..5 are filled
    assert n == 3
    assert seen and all("_0." not in x for x in seen[0])
    assert [a["text"] for a in out][:2] == ["hello", "again"] and out == sorted(out, key=lambda a: a["ts"])
    assert all(a["device"] == "whisper (filled)" for a in out[2:])


def test_nothing_missing_or_turned_off(monkeypatch):
    audio = [{"ts": T0, "text": "x", "device": "d"}]
    assert lk.fill_untranscribed(Con([(T0.isoformat(), "/d/System Audio (output)_0.mp4")]), T0, T0, audio,
                                 transcribe=lambda *a: 1 / 0) == (audio, 0)
    monkeypatch.setenv("STUDY_FILL_UNTRANSCRIBED", "0")
    assert lk.fill_untranscribed(Con([]), T0, T0, audio) == (audio, 0)
