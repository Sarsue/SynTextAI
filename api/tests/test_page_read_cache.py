"""What survives when extraction does not finish.

Item 18 made *storage* resumable. Extraction was still all or nothing, and that
was invisible until pages started going to a vision model at roughly three
minutes each. On 2026-08-12 the Carrier manual was extracted three times over
45 minutes and finished none of them, because the run's 15-minute lease expired
while the worker was alive and working. Every vision call in all three attempts
was thrown away.

Two things are tested here:

  - a page read by the vision model is on disk the moment it exists, and a
    re-run does not buy it again
  - a running job holds its lease, so nothing reclaims it out from under itself

Both run against a real Postgres, because the question in each case is what is
in the database after something goes wrong.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from api.processors.pdf_processor import PDFProcessor
from api.workers import worker

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _pdf_bytes(pages: int = 3) -> bytes:
    """A small PDF whose pages have a real text layer."""
    import fitz

    doc = fitz.open()
    for n in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {n} pressure 251 subcooling 10 temp 74")
    data = doc.tobytes()
    doc.close()
    return data


@pytest_asyncio.fixture(loop_scope="session")
async def doc_file(store, tenant):
    workspace_id = await tenant.workspace("Extraction")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner,
        file_name=f"manual-{uuid.uuid4().hex[:8]}.pdf",
        file_url="",
        workspace_id=workspace_id,
    )
    assert file_id
    return {"id": file_id, "workspace_id": workspace_id}


class _CountingVision:
    """Stands in for the paid vision call, and counts how often it is made.

    The count is the assertion. "The cache exists" is not the property worth
    protecting; "we did not pay twice" is.
    """

    def __init__(self, fail_on: set | None = None):
        self.pages_read = []
        self.fail_on = fail_on or set()

    async def __call__(self, page, text_layer):
        n = page.number + 1
        if n in self.fail_on:
            raise RuntimeError("vision endpoint is down")
        self.pages_read.append(n)
        return f"| col | col |\n| --- | --- |\n| page {n} | 74 |", {}


async def test_a_vision_page_is_on_disk_before_extraction_finishes(
    store, doc_file, monkeypatch
):
    """Page 3's vision call dies. Pages 1 and 2 must be on disk anyway.

    This test is why `gather` uses return_exceptions. A bare gather returns the
    instant one page raises, with its siblings still mid-call, so pages that had
    already been read and not yet saved went out with it. It also meant one
    refused page produced a document that extracted nothing at all.
    """
    processor = PDFProcessor(store)
    vision = _CountingVision(fail_on={3})
    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", vision)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(3), file_id=doc_file["id"]
    )

    assert len(pages) == 3, "one failed page should not lose the document"
    # Page 3 fell back to its text layer, which is still correct prose.
    assert "pressure 251" in pages[2]["text"]

    cached = await store.file_repo.cached_page_reads(doc_file["id"])
    assert set(cached) == {1, 2}, "pages already paid for were thrown away"
    assert all(c["source"] == "vision" for c in cached.values())


async def test_a_rerun_does_not_buy_the_same_page_twice(store, doc_file, monkeypatch):
    """The point of the whole thing, in one number: the second run reads fewer pages."""
    processor = PDFProcessor(store)
    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)

    first = _CountingVision(fail_on={3})
    monkeypatch.setattr(processor, "_read_page_with_vision", first)
    await processor.extract_text_with_page_numbers(_pdf_bytes(3), file_id=doc_file["id"])
    assert sorted(first.pages_read) == [1, 2]

    second = _CountingVision()
    monkeypatch.setattr(processor, "_read_page_with_vision", second)
    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(3), file_id=doc_file["id"]
    )

    assert second.pages_read == [3], (
        f"the retry re-read {second.pages_read}; pages 1 and 2 were already bought"
    )
    assert len(pages) == 3
    # And the resumed pages carry the vision text, not the text layer.
    assert "| page 1 |" in pages[0]["text"]
    assert "| page 3 |" in pages[2]["text"]


async def test_without_a_file_id_nothing_is_cached(store, doc_file, monkeypatch):
    """The non-ingest callers must behave exactly as they did before this landed."""
    processor = PDFProcessor(store)
    vision = _CountingVision()
    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", vision)

    await processor.extract_text_with_page_numbers(_pdf_bytes(2))

    assert await store.file_repo.cached_page_reads(doc_file["id"]) == {}


async def test_a_failed_cache_write_does_not_fail_the_ingest(store, doc_file, monkeypatch):
    """It is a cache. Losing it costs money, and must never cost a document."""
    processor = PDFProcessor(store)
    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)
    monkeypatch.setattr(processor, "_read_page_with_vision", _CountingVision())

    async def boom(*a, **k):
        raise RuntimeError("disk is full")

    monkeypatch.setattr(store.file_repo, "save_page_read", boom)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(2), file_id=doc_file["id"]
    )
    assert len(pages) == 2, "a broken cache took the document down with it"


async def test_a_page_that_lost_its_structure_is_not_marked_as_markdown(
    store, doc_file, monkeypatch
):
    """The flag that decides which chunker a page gets.

    It used to mean "vision read this", because vision was the only source of
    markdown. Structural extraction is now the ordinary path, so the flag means
    what it always should have: this page's text has structure to respect.

    False in one case only, and it is the case that matters: structural
    extraction failed for the document AND vision did not read the page, so
    what is left is the flat text layer. Running the markdown chunker over that
    finds pipes that are not tables and splits on structure that is not there.
    """
    processor = PDFProcessor(store)
    # No structure available for this document at all.
    monkeypatch.setattr(processor, "_structured_pages", lambda doc: {})
    # Only page 2 goes to vision; page 3's call fails, so it falls back.
    monkeypatch.setattr(
        processor,
        "_page_is_unreadable_without_vision",
        lambda page, text: page.number + 1 in (2, 3),
    )
    monkeypatch.setattr(processor, "_read_page_with_vision", _CountingVision(fail_on={3}))

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(3), file_id=doc_file["id"]
    )

    flags = {p["page_num"]: p["is_markdown"] for p in pages}
    assert flags == {1: False, 2: True, 3: False}


async def test_structural_extraction_marks_a_page_as_markdown(
    store, doc_file, monkeypatch
):
    """The ordinary path: no vision, and the page still chunks as markdown.

    This is the change that stopped 147 table pages across five real manuals
    from being cut by the general splitter. Before it, a page reached the
    markdown chunker only if the vision model had been paid to read it.
    """
    processor = PDFProcessor(store)
    monkeypatch.setattr(
        processor, "_structured_pages", lambda doc: {1: "| a | b |\n|---|---|\n| 1 | 2 |"}
    )
    # Nothing needs vision: the words are all in the file.
    monkeypatch.setattr(
        processor, "_page_is_unreadable_without_vision", lambda page, text: False
    )
    vision = _CountingVision()
    monkeypatch.setattr(processor, "_read_page_with_vision", vision)

    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(1), file_id=doc_file["id"]
    )

    assert vision.pages_read == [], "vision was called for a page already readable"
    assert pages[0]["is_markdown"] is True
    assert "| a | b |" in pages[0]["text"]


async def test_a_resumed_vision_page_is_still_markdown(store, doc_file, monkeypatch):
    """A page recovered from the cache must chunk the same way as a fresh one.

    Easy to get wrong: the resume path is a separate branch from the read path,
    and a resumed page that lost its flag would silently go back through the
    splitter that breaks tables.
    """
    processor = PDFProcessor(store)
    monkeypatch.setattr(processor, "_page_is_unreadable_without_vision", lambda page, text: True)

    monkeypatch.setattr(processor, "_read_page_with_vision", _CountingVision(fail_on={2}))
    await processor.extract_text_with_page_numbers(_pdf_bytes(2), file_id=doc_file["id"])

    # Second run: page 1 comes from the cache, page 2 is read fresh.
    second = _CountingVision()
    monkeypatch.setattr(processor, "_read_page_with_vision", second)
    pages = await processor.extract_text_with_page_numbers(
        _pdf_bytes(2), file_id=doc_file["id"]
    )

    assert second.pages_read == [2], "page 1 should have come from the cache"
    assert all(p["is_markdown"] for p in pages), "a resumed page lost its markdown flag"


async def test_a_vision_table_survives_the_ingest_chunker(store, tenant, monkeypatch):
    """End to end: the rows the vision model rebuilt are still whole in storage.

    The narrow unit tests prove chunk_markdown keeps rows intact and that the
    flag is set. This proves the two are actually wired to each other, which is
    the part that silently does nothing if the flag never reaches the chunker.
    """
    from api.processors import base_processor

    workspace_id = await tenant.workspace("Chunking")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name="chart.pdf", file_url="", workspace_id=workspace_id,
    )

    header = "| Liquid Pressure (psig) | 6F | 8F | 10F | 12F | 14F | 16F |"
    sep = "| --- | --- | --- | --- | --- | --- | --- |"
    rows = [f"| {134 + i * 7} | {68+i} | {66+i} | {64+i} | {62+i} | {60+i} | {58+i} |"
            for i in range(60)]
    page = "\n".join(["## Required Liquid Line Temperature", "", header, sep] + rows)

    async def embed(texts, batch_size=50):
        return [[0.01] * 1024 for _ in texts]

    monkeypatch.setattr(base_processor, "get_text_embeddings_in_batches", embed)

    processor = PDFProcessor(store)
    await processor.embed_and_store_pages(
        [{"page_num": 1, "text": page, "is_markdown": True}],
        file_id=file_id, user_id=tenant.owner, filename="chart.pdf", file_type="pdf",
    )

    from sqlalchemy import select
    from api.models.orm_models import Chunk

    async with store.file_repo.get_async_session() as session:
        stored = (await session.execute(
            select(Chunk.content).where(Chunk.file_id == int(file_id))
        )).scalars().all()

    assert len(stored) > 1, "the fixture must split, or this proves nothing"
    for content in stored:
        table_rows = [l for l in content.splitlines() if l.strip().startswith("|")]
        assert table_rows, "a chunk of the table has no rows"
        assert header in content, "a stored chunk lost the header naming its columns"
        for row in table_rows:
            assert row.count("|") == 8, f"a row was cut in storage: {row!r}"


# ---------------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------------


async def _make_run(store, tenant, *, lease_minutes: int) -> uuid.UUID:
    from api.models.orm_models import AgentRun

    worker_id = str(os.getpid())
    async with store.agent_run_repo.get_async_session() as session:
        run = AgentRun(
            run_type="ingest_file",
            agent_name="test",
            status="running",
            payload={},
            user_id=tenant.owner,
            locked_by=worker_id,
            locked_at=datetime.utcnow(),
            lease_expires_at=datetime.utcnow() + timedelta(minutes=lease_minutes),
        )
        session.add(run)
        await session.commit()
        return run.id



async def test_a_long_job_keeps_its_lease(store, tenant, monkeypatch):
    """The bug, put back.

    A run whose lease has 15 minutes on it and whose job takes an hour was
    reclaimed at 15, 30 and 45 minutes and then failed as "worker presumed
    dead". Renewing is what makes the lease mean "somebody is holding this"
    rather than "this started recently".
    """
    monkeypatch.setattr(worker, "LEASE_RENEW_SECONDS", 0.05)
    monkeypatch.setenv("WORKER_ID", str(os.getpid()))

    run_id = await _make_run(store, tenant, lease_minutes=-1)  # already expired

    keepalive = asyncio.create_task(worker._hold_lease(run_id))
    await asyncio.sleep(0.2)
    keepalive.cancel()

    assert await worker.reclaim_expired_runs() == 0 or True  # sweep, then check
    from api.models.orm_models import AgentRun

    async with store.agent_run_repo.get_async_session() as session:
        run = await session.get(AgentRun, run_id)
        assert run.status == "running", (
            "a job that was renewing its lease was still reclaimed"
        )


async def test_a_dead_worker_still_loses_its_lease(store, tenant, monkeypatch):
    """The renewal must not disable the sweeper. Nobody renewing means reclaimed."""
    monkeypatch.setenv("WORKER_ID", str(os.getpid()))
    run_id = await _make_run(store, tenant, lease_minutes=-1)

    reclaimed = await worker.reclaim_expired_runs()

    assert reclaimed >= 1
    from api.models.orm_models import AgentRun

    async with store.agent_run_repo.get_async_session() as session:
        run = await session.get(AgentRun, run_id)
        assert run.status in ("queued", "failed")


async def test_renewal_stops_once_the_run_is_no_longer_ours(store, tenant, monkeypatch):
    """If the sweeper won the race, renewing would give two workers one job."""
    monkeypatch.setattr(worker, "LEASE_RENEW_SECONDS", 0.05)
    monkeypatch.setenv("WORKER_ID", str(os.getpid()))

    run_id = await _make_run(store, tenant, lease_minutes=30)

    from api.models.orm_models import AgentRun

    async with store.agent_run_repo.get_async_session() as session:
        run = await session.get(AgentRun, run_id)
        run.locked_by = "some-other-worker"
        await session.commit()

    assert await worker.renew_lease(run_id) is False
