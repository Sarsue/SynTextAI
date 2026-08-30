"""Chunking a page that has structure, instead of counting to 400.

WHY THIS EXISTS

The general chunker takes 400 tokens with 20% overlap and knows nothing about
what it is cutting. On prose that is fine. On a table it is destructive, and
destructive in exactly the way the vision model was bought to prevent.

Measured 2026-08-13 on a reconstructed Carrier charging chart, 1,725 characters:
the general splitter produced three chunks, of which the second began

    | 80 | 78 | 76 | 74 | 72 | 70 |

with the row's own pressure value left behind in the previous chunk, and
neither the second nor the third carried the header row. All three were
unusable. A chunk of numbers with no row key and no column names cannot answer
anything, no matter how well the page was read.

This also explains a baseline result that otherwise made no sense: benchmark
question 6 cited the correct page and still answered 78 instead of 74. No
amount of extraction quality could have saved that question, because the chunk
retrieved could not have contained an intact row.

WHAT IS ASSERTED

Not "chunk_markdown produces N chunks", which would break on any tuning. The
properties that make a chunk answerable:

  - no row is ever cut in half
  - every piece of a table carries its header and its caption
  - prose is left to the splitter that already handles it well

The prose questions in the HVAC benchmark score 4/4 on citations today, and
nothing here may move them.
"""
import pytest

from api.core.utils import chunk_markdown, chunk_text


def _rows(n: int) -> list:
    """`n` rows of a two-dimensional lookup, seven columns wide."""
    return [
        f"| {134 + i * 7} | {68 + i} | {66 + i} | {64 + i} | {62 + i} | {60 + i} | {58 + i} |"
        for i in range(n)
    ]


CAPTION = "## Required Liquid Line Temperature"
HEADER = "| Liquid Pressure (psig) | 6F | 8F | 10F | 12F | 14F | 16F |"
SEP = "| --- | --- | --- | --- | --- | --- | --- |"


def _page(row_count: int = 40, prose: bool = True) -> str:
    body = "\n".join([CAPTION, "", HEADER, SEP] + _rows(row_count))
    if not prose:
        return body
    return (
        "Page 9\n\nCharge the system using the table below. Measure liquid "
        "pressure at the service port and read across.\n\n"
        + body
        + "\n\nDo not overcharge. Recheck after ten minutes of steady operation.\n"
    )


def _table_chunks(chunks):
    return [c["content"] for c in chunks if "|" in c["content"]]


def test_no_row_is_ever_cut_in_half():
    """The failure that motivated all of this, asserted directly."""
    for content in _table_chunks(chunk_markdown(_page())):
        for line in content.splitlines():
            if line.strip().startswith("|"):
                assert line.count("|") == 8, f"a row was cut: {line!r}"


def test_every_piece_of_a_table_says_what_its_columns_are():
    """A chunk of bare numbers is not retrievable and not answerable."""
    pieces = _table_chunks(chunk_markdown(_page()))
    assert len(pieces) > 1, "the fixture must be big enough to split, or this proves nothing"
    for content in pieces:
        assert HEADER in content, "a table chunk lost its header row"
        assert CAPTION in content, "a table chunk lost its caption"


def test_the_general_chunker_really_does_break_this():
    """The bug, put back.

    If the general splitter ever starts handling tables correctly, the special
    case above is dead code and should be deleted rather than maintained. This
    test is what would tell us.
    """
    broken = 0
    for chunk in chunk_text(_page(), content_type="pdf"):
        content = chunk["content"]
        rows = [l for l in content.splitlines() if l.strip().startswith("|")]
        if not rows:
            continue
        if HEADER not in content or any(l.count("|") != 8 for l in rows):
            broken += 1
    assert broken > 0, "the general chunker no longer breaks tables; delete chunk_markdown"


def test_a_caption_separated_by_a_blank_line_still_comes_with_the_table():
    """Markdown puts a blank line between a heading and a table. Caught in
    review of the first version, which looked for the caption on the line
    immediately above and therefore always found the blank."""
    page = "\n".join([CAPTION, "", HEADER, SEP] + _rows(40))
    for content in _table_chunks(chunk_markdown(page)):
        assert CAPTION in content


def test_a_table_small_enough_to_fit_is_left_whole():
    """Splitting is a cost, not a goal."""
    pieces = _table_chunks(chunk_markdown("\n".join([CAPTION, "", HEADER, SEP] + _rows(3))))
    assert len(pieces) == 1


def test_prose_around_a_table_survives():
    """Questions 17-20 of the HVAC benchmark score 4/4 on citations. The point
    of this change is tables; prose must come out the other side intact."""
    joined = " ".join(c["content"] for c in chunk_markdown(_page()))
    assert "Measure liquid pressure at the service port" in joined
    assert "Recheck after ten minutes of steady operation" in joined


def test_a_page_with_no_table_is_just_prose():
    text = (
        "Page 65\n\nThe indoor and outdoor units communicate over a 24V line. "
        "Cooling begins when Y-C goes high at the outdoor board.\n"
    )
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "24V" in chunks[0]["content"]


def test_a_single_row_wider_than_the_budget_is_kept_whole():
    """Half a row is worse than a long one, so this deliberately over-runs."""
    wide = "| " + " | ".join(f"column value number {i}" for i in range(200)) + " |"
    page = "\n".join([CAPTION, "", HEADER, SEP, wide])
    pieces = _table_chunks(chunk_markdown(page, target_chunk_tokens=50))
    assert any(wide in p for p in pieces), "an over-long row was split instead of kept"


def test_empty_input_is_not_a_crash():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_a_heading_is_not_a_chunk_of_its_own():
    """A label with nothing under it cannot answer anything.

    Measured 2026-08-30 across 147 real pages: 121 of 624 chunks were under 40
    characters and the most common was "**SERVICING**", twenty-three times. It
    is a running page header, and structural extraction correctly marks it as a
    heading, which is what made it flush as its own prose block.
    """
    page = (
        "**SERVICING**\n\n"
        "# **TROUBLESHOOTING**\n\n"
        "The outdoor fan motor is checked with the power off and the leads "
        "disconnected at the control board, using a meter set to ohms.\n"
    )
    chunks = [c["content"] for c in chunk_markdown(page)]

    assert len(chunks) == 1, f"heading was chunked on its own: {chunks}"
    assert "SERVICING" in chunks[0]
    assert "TROUBLESHOOTING" in chunks[0]
    assert "outdoor fan motor" in chunks[0]


def test_a_trailing_heading_attaches_to_what_came_before():
    """Nothing follows it, so it goes backward rather than becoming a chunk."""
    page = (
        "The crankcase heater is de-energized when the compressor is running "
        "and energized when the ambient is below 42 F.\n\n"
        "**NEXT SECTION**\n"
    )
    chunks = [c["content"] for c in chunk_markdown(page)]

    assert len(chunks) == 1, f"trailing heading became its own chunk: {chunks}"
    assert "NEXT SECTION" in chunks[0]


def test_a_table_still_keeps_its_rows_whole():
    """The heading change must not reach the table path."""
    page = (
        "## Required Liquid Line Temperature\n\n"
        "| Pressure | 6F | 8F | 10F |\n"
        "| --- | --- | --- | --- |\n"
        "| 251 | 78 | 76 | 74 |\n"
        "| 259 | 80 | 78 | 76 |\n"
    )
    chunks = [c["content"] for c in chunk_markdown(page)]

    body = "\n".join(chunks)
    assert "| 251 | 78 | 76 | 74 |" in body, "a row was split"
    assert "Required Liquid Line Temperature" in body, "the caption was lost"
