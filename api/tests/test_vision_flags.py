"""A page the vision model read but could not have verified says so downstream.

WHY IT MATTERS

`_read_page_with_vision` makes a deliberate, costly choice. On a page whose text
layer is a credible record, a transcription that introduces numbers absent from
the page is rejected outright, because a confident wrong torque value is a
safety claim rather than a typo. On a figure-dominant page the text layer is
incomplete rather than merely disordered, so it cannot arbitrate, and the read
is kept and flagged instead. Enforcing the strict rule there cost four benchmark
questions on 2026-08-14.

"Flagged" was doing no work. The flags went into `page_reads`, an extraction
cache that nothing queries while answering, and the in-run collection was
assigned twice and read nowhere. So a page kept precisely because it could not
be verified looked exactly like one that had been.

WHAT IS ASSERTED

The journey, in the two steps it actually takes:

  - extraction puts the flags on the page
  - storage puts them on the segment, which is what `hybrid_search` returns
    alongside every result and what a citation is built from

Displaying them to a reader is a separate decision and is not made here.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text as sql_text

from api.processors.pdf_processor import PDFProcessor

pytestmark = pytest.mark.asyncio(loop_scope="session")

UNVERIFIED = {
    "vision_unverified_page": "figure page; text layer cannot verify",
    "vision_unverified_numbers": ["21", "410"],
}


def _pdf_bytes(pages: int = 2) -> bytes:
    import fitz

    doc = fitz.open()
    for n in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {n} nomenclature chart")
    data = doc.tobytes()
    doc.close()
    return data


@pytest_asyncio.fixture(loop_scope="session")
async def doc_file(store, tenant):
    workspace_id = await tenant.workspace("VisionFlags")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner,
        file_name=f"diagram-{uuid.uuid4().hex[:8]}.pdf",
        file_url="",
        workspace_id=workspace_id,
    )
    return {"id": file_id, "owner": tenant.owner}


async def test_extraction_puts_the_flags_on_the_page(store, doc_file, monkeypatch):
    """Page 1 could not be verified, page 2 was read cleanly."""
    processor = PDFProcessor(store)

    async def fake_vision(page, text_layer, png: bytes = b""):
        n = page.number + 1
        return f"| col |\n| --- |\n| {n} |", (UNVERIFIED if n == 1 else {})

    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", fake_vision)
    # Never touch object storage from a test. Without this the suite writes a
    # figure into the real bucket on every run.
    async def _no_upload(png, file_id, workspace_id, page_num):
        return None
    monkeypatch.setattr(processor, "_store_figure", _no_upload)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(2), file_id=doc_file["id"]
    )

    assert {k: v for k, v in pages[0]["flags"].items() if k != "figure_url"} == UNVERIFIED
    assert "flags" not in pages[1], "a clean page should carry no flags at all"


async def test_a_resumed_page_is_no_more_trusted_than_a_fresh_one(
    store, doc_file, monkeypatch
):
    """The flags come back out of the cache with the text they belong to. A
    second attempt at a document must not quietly launder an unverified page."""
    processor = PDFProcessor(store)

    async def fake_vision(page, text_layer, png: bytes = b""):
        n = page.number + 1
        return f"| col |\n| --- |\n| {n} |", (UNVERIFIED if n == 1 else {})

    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", fake_vision)
    # Never touch object storage from a test. Without this the suite writes a
    # figure into the real bucket on every run.
    async def _no_upload(png, file_id, workspace_id, page_num):
        return None
    monkeypatch.setattr(processor, "_store_figure", _no_upload)
    await processor.extract_text_with_page_numbers(_pdf_bytes(2), file_id=doc_file["id"])

    async def must_not_run(page, text_layer):
        raise AssertionError("a cached page was read again")

    monkeypatch.setattr(processor, "_read_page_with_vision", must_not_run)
    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(2), file_id=doc_file["id"]
    )

    assert {k: v for k, v in pages[0]["flags"].items() if k != "figure_url"} == UNVERIFIED


async def test_storage_puts_the_flags_where_a_citation_can_reach_them(store, doc_file):
    """hybrid_search already returns segments.meta_data with every row."""
    ok = await store.file_repo.update_file_with_chunks(
        user_id=doc_file["owner"],
        filename="diagram.pdf",
        file_type="pdf",
        file_id=doc_file["id"],
        mark_processed=True,
        extracted_data=[
            {
                "text": "Cabinet width 21 inches, refrigerant R-410A.",
                "page_text": "Cabinet width 21 inches, refrigerant R-410A.",
                "page_num": 1,
                "flags": UNVERIFIED,
                "embedding": [0.1] * 1024,
                "content_hash": uuid.uuid4().hex,
            },
        ],
    )
    assert ok

    async with store.file_repo.get_async_session() as session:
        meta = (await session.execute(
            sql_text(
                "SELECT meta_data FROM segments WHERE file_id = :f AND page_number = 1"
            ),
            {"f": doc_file["id"]},
        )).scalar()

    assert meta and meta.get("flags") == UNVERIFIED


# --- the flag reaching the reader -------------------------------------------

def _result(segment_id: int, *, unverified: bool, numbers=None):
    meta = {}
    if unverified:
        meta = {
            "vision_unverified_page": "figure page; text layer cannot verify",
            "vision_unverified_numbers": numbers or ["134", "2.1"],
        }
    return {
        "content": f"Body of segment {segment_id}.",
        "file_name": "service-manual.pdf",
        "file_url": "https://storage.example/service-manual.pdf",
        "page_number": 22,
        "meta_data": meta,
    }


def test_the_model_is_told_a_segment_came_from_a_figure():
    """The flag was read into a variable and dropped.

    A page kept PRECISELY because the text layer could not arbitrate produced a
    citation identical to a verified one. The model is the only thing that knows
    which figure it is about to quote, so it has to be told.
    """
    from api.services.syntext_agent import SyntextAgent

    context, _ = SyntextAgent()._format_context_and_sources(
        [_result(1, unverified=True, numbers=["134", "2.1"])]
    )
    assert "READ FROM A FIGURE" in context
    assert "134" in context, "the values in question are not named"
    assert "say plainly" in context


def test_a_verified_segment_carries_no_caution():
    """Cautioning everything is the same as cautioning nothing."""
    from api.services.syntext_agent import SyntextAgent

    context, targets = SyntextAgent()._format_context_and_sources(
        [_result(1, unverified=False)]
    )
    assert "READ FROM A FIGURE" not in context
    assert "unverified" not in targets[1][0]


def test_the_citation_itself_says_so():
    """The reader may skip the sentence and click the link."""
    from api.services.syntext_agent import SyntextAgent

    _, targets = SyntextAgent()._format_context_and_sources(
        [_result(1, unverified=True)]
    )
    label, target = targets[1]
    assert "read from a figure, unverified" in label
    # The link still has to work: a caution is not a reason to break the citation.
    assert target.endswith("#page=22")


def test_only_the_flagged_segment_is_marked():
    """One unverified page in a set of three must not taint the other two."""
    from api.services.syntext_agent import SyntextAgent

    _, targets = SyntextAgent()._format_context_and_sources([
        _result(1, unverified=False),
        _result(2, unverified=True),
        _result(3, unverified=False),
    ])
    assert "unverified" not in targets[1][0]
    assert "unverified" in targets[2][0]
    assert "unverified" not in targets[3][0]


def test_the_prompt_tells_the_model_what_to_do_with_it():
    """The header is evidence; the instruction is what makes it an answer."""
    import inspect
    from api.services import syntext_agent

    src = inspect.getsource(syntext_agent.SyntextAgent.query_pipeline)
    assert "READ FROM A FIGURE" in src
    assert "safety claim" in src


async def test_a_figure_page_keeps_its_picture(store, doc_file, monkeypatch):
    """The words read off a diagram are not the diagram.

    A wiring diagram means what it means by where its lines go, and the vision
    model's paragraph about it cannot carry that. The page is already rendered
    to PNG to be sent to the model, so keeping those bytes costs nothing and
    gives a citation something to show rather than only describe.
    """
    processor = PDFProcessor(store)

    async def fake_vision(page, text_layer, png: bytes = b""):
        assert png, "the caller should hand the already-rendered page over"
        return "## Nomenclature\n\nModel AB-21", {}

    stored = {}

    async def fake_store(png, file_id, workspace_id, page_num):
        stored["png_bytes"] = len(png)
        stored["page"] = page_num
        return f"https://example.invalid/{file_id}-figure-p{page_num}.png"

    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", fake_vision)
    monkeypatch.setattr(processor, "_store_figure", fake_store)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(1), file_id=doc_file["id"]
    )

    assert stored["page"] == 1
    assert stored["png_bytes"] > 0, "an empty render would store nothing useful"
    assert pages[0]["flags"]["figure_url"].endswith("figure-p1.png")


async def test_a_figure_that_cannot_be_stored_does_not_lose_the_page(
    store, doc_file, monkeypatch
):
    """Best effort. The transcription is still worth having on its own."""
    processor = PDFProcessor(store)

    async def fake_vision(page, text_layer, png: bytes = b""):
        return "## Nomenclature\n\nModel AB-21", {}

    async def failing_store(png, file_id, workspace_id, page_num):
        return None

    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", fake_vision)
    monkeypatch.setattr(processor, "_store_figure", failing_store)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(1), file_id=doc_file["id"]
    )

    assert "Nomenclature" in pages[0]["text"]
    assert "figure_url" not in (pages[0].get("flags") or {})
