import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import transcript_ref as tr

MD = """# Desert plants

## Summary
### Flow Summary
Roots and water storage.

## Transcript
[00:00] Speaker 1: Welcome everyone to the session on desert plants and how they store water.
[00:30] Speaker 2: Cacti keep water in thick stems and close their pores during the hot day.
[101:05] Speaker 1: Deep roots reach groundwater that shallow roots never find in sandy soil.

# Questions

## Summary
- Late questions.

## Transcript
[00:00] Speaker 1: Any other questions about seed dormancy after a long drought?
"""


def test_parse_zoom_md_parts_and_summaries():
    parts, summ = tr.parse_zoom_md(MD)
    assert [len(p) for p in parts] == [3, 1]
    assert parts[0][2] == (101 * 60 + 5, "Speaker 1", "Deep roots reach groundwater that shallow roots never find in sandy soil.")
    assert [h for h, _ in summ] == ["Desert plants", "Questions"]
    assert "Roots and water storage." in summ[0][1]


def test_align_offset_places_each_part_on_our_clock():
    parts, _ = tr.parse_zoom_md(MD)
    T = 36000                                   # our clock: part 1 begins at 10:00:00 + 95 s
    ours = [(T + 95 + 1, "welcome everyone to the session on desert plants and how they store water"),
            (T + 95 + 31, "cacti keep water in thick stems and close their pores during the hot day"),
            (T + 95 + 6066, "deep roots reach groundwater that shallow roots never find in sandy soil"),
            (T + 7000, "any other questions about seed dormancy after a long drought")]
    off, votes = tr.align_offset(parts[0], ours)
    assert abs(off - (T + 95)) <= 10 and votes > 0
    ents = tr.zoom_md_entries(MD, ours)
    assert len(ents) == 4 and abs(ents[-1][0] - 7000 - T) <= 10


def test_align_offset_without_overlap():
    parts, _ = tr.parse_zoom_md(MD)
    assert tr.align_offset(parts[1], [(10, "nothing in common here at all")]) == (0, 0)
