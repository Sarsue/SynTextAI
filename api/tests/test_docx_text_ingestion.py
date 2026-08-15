"""What a Word or text document loses on the way in, and no longer does.

TWO FAILURES, BOTH INVISIBLE TO THE BENCHMARK

The citation benchmark corpus is PDFs. Everything asserted here happens only to
DOCX and TXT files, which is why both of these survived being measured.

1. PUNCTUATION WAS DELETED BEFORE EMBEDDING

`clean_text` had a branch that stripped every character except letters, digits,
commas, dots, spaces and newlines. It was chosen by `detect_content_type` for
any text holding a comma and more than three lines, meaning ordinary business
prose, and it could never fire on a PDF because a PDF page carries a "Page N"
marker and matched an earlier branch. So it damaged exactly the formats a
dental practice or an accountant uploads.

The damage is not cosmetic. That text is what gets embedded, what the keyword
index is built from, and what the model is handed to answer from:

    billing@acme.com  ->  billingacme.com
    555-0134          ->  5550134
    1.5%              ->  1.5
    $1,200/month      ->  1,200month

It also disarmed the literal-token arm of the search, whose entire job is to let
a rare token decide the ranking. 5550134 is not what anybody types.

2. A WORD TABLE WAS NOT A TABLE

`_chunk_table` cuts on row boundaries and repeats the header above every piece,
because a chunk of bare numbers with no column names cannot answer anything.
Only PDF pages read by the vision model were ever marked as markdown, so a
Word table went through the general 400-token splitter instead: cut mid-row,
header left behind in the first piece. That is the same failure, one format
over, that benchmark question 6 was traced to.

WHAT IS ASSERTED

The properties, not the implementation. No test here counts chunks.
"""
import pytest

from api.core.utils import chunk_markdown, chunk_text, clean_text, detect_content_type
from api.processors.docx_processor import _markdown_table
from api.processors.text_processor import TextProcessor


# A section as DocxProcessor emits one: the "Section N" label, then prose with
# the punctuation a real document has in it.
SECTION = """Section 3: Refund Policy
Customers may request a refund within 30 days, provided the item is unused.
Fees are non-refundable (see Schedule A).
Contact billing@acme.com or call 555-0134.
Late payments accrue 1.5% interest per month; balances above $1,200/month are escalated.
"""


class _Cell:
    def __init__(self, text):
        self.text = text


class _Row:
    def __init__(self, cells):
        self.cells = [_Cell(c) for c in cells]


class _Table:
    """Enough of a python-docx table for _markdown_table, which only reads
    `.rows[].cells[].text`."""

    def __init__(self, rows):
        self.rows = [_Row(r) for r in rows]


def _price_table(row_count: int) -> _Table:
    header = ["Plan", "Users", "Monthly", "Annual"]
    body = [
        [f"Tier {i}", str(5 * i), f"${49 * i}", f"${470 * i}"]
        for i in range(1, row_count + 1)
    ]
    return _Table([header] + body)


def _table_chunks(chunks):
    return [c["content"] for c in chunks if "|" in c["content"]]


# ---------------------------------------------------------------- punctuation


@pytest.mark.parametrize(
    "fragment",
    ["billing@acme.com", "555-0134", "1.5%", "$1,200/month", "(see Schedule A)"],
)
def test_punctuation_in_a_word_section_reaches_the_chunk(fragment):
    """Each of these was destroyed, and each is something a customer searches for."""
    joined = " ".join(c["content"] for c in chunk_text(SECTION))
    assert fragment in joined


def test_prose_with_commas_is_not_mistaken_for_a_spreadsheet():
    """The misclassification that caused it. Four lines and a comma is a memo."""
    assert detect_content_type(SECTION) == "text"


def test_nothing_claims_to_clean_a_csv_any_more():
    """If a CSV parser is ever added it gets its own path, not a regex that
    deletes punctuation from every other format on the way past."""
    assert clean_text(SECTION, "csv_like") == SECTION.strip()


# --------------------------------------------------------------- word tables


def test_a_word_table_is_a_table():
    lines = _markdown_table(_price_table(3))
    assert lines[0] == "| Plan | Users | Monthly | Annual |"
    assert set(lines[1].replace("|", "").strip()) <= set("-: "), "no separator row"
    assert all(line.startswith("|") and line.endswith("|") for line in lines)


def test_no_row_of_a_word_table_is_ever_cut_in_half():
    """The failure this exists for, asserted on the format that had it."""
    page = "Table 1\n" + "\n".join(_markdown_table(_price_table(60)))
    pieces = _table_chunks(chunk_markdown(page))
    assert len(pieces) > 1, "the fixture must be big enough to split, or this proves nothing"
    for content in pieces:
        for line in content.splitlines():
            if line.strip().startswith("|"):
                assert line.count("|") == 5, f"a row was cut: {line!r}"


def test_every_piece_of_a_word_table_says_what_its_columns_are():
    page = "Table 1\n" + "\n".join(_markdown_table(_price_table(60)))
    for content in _table_chunks(chunk_markdown(page)):
        assert "| Plan | Users | Monthly | Annual |" in content, "lost its header"
        assert "Table 1" in content, "lost its caption"


def test_a_cell_holding_a_pipe_does_not_invent_a_column():
    lines = _markdown_table(_Table([["Plan", "Notes"], ["Starter", "a | b"]]))
    assert lines[-1] == r"| Starter | a \| b |"


def test_a_cell_holding_a_line_break_does_not_invent_a_row():
    lines = _markdown_table(_Table([["Plan", "Notes"], ["Starter", "first\nsecond"]]))
    assert len(lines) == 3
    assert lines[-1] == "| Starter | first second |"


def test_a_merged_cell_does_not_leave_a_ragged_table():
    """python-docx repeats a merged cell across the row, so widths can differ.
    A markdown table with rows of different widths is not one."""
    lines = _markdown_table(_Table([["Plan", "Users", "Price"], ["Enterprise"]]))
    assert all(line.count("|") == 4 for line in lines)


def test_an_empty_table_is_not_a_crash():
    assert _markdown_table(_Table([])) == []
    assert _markdown_table(_Table([["", ""], ["", ""]])) == []


# ------------------------------------------------------- markdown uploads


@pytest.mark.parametrize(
    "filename,expected",
    [("handbook.md", True), ("handbook.markdown", True), ("notes.txt", False), ("", False)],
)
def test_only_a_markdown_upload_is_chunked_as_markdown(filename, expected):
    """Taken from the extension the uploader gave us, not guessed from the text."""
    processor = TextProcessor(store=None)
    sections = processor.extract_text_with_sections(
        b"# Pricing\n\n| Plan | Price |\n| --- | --- |\n| Starter | $49 |\n", filename
    )
    assert sections, "the fixture must produce a section"
    assert all(s["is_markdown"] is expected for s in sections)
