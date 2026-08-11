"""What survives when a long document does not finish.

A 500-page PDF used to be all or nothing. Every chunk was held in memory and
written in one transaction at the end, so a crash on page 490 threw away the
other 489 and the retry began again at page 1. Nothing in the document was
searchable until all of it was.

These run against a real Postgres, because what is being tested is what is
still on disk after something goes wrong, which a mock cannot show.

The embedding service is faked. It is an HTTP call to a paid API, and this
suite is about which rows exist, not about vector quality.
"""
import uuid

import pytest
import pytest_asyncio

from api.processors import base_processor
from api.processors.text_processor import TextProcessor

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024


@pytest.fixture(autouse=True)
def small_batches(monkeypatch):
    """Three pages per batch, so a five-page document has a middle to fail in.

    The real value is 50, which would need a 100-page fixture to exercise the
    same boundary.
    """
    monkeypatch.setattr(base_processor, "BATCH_SIZE", 3)


@pytest.fixture
def fake_embeddings(monkeypatch):
    """A stand-in for the paid embedding call, with a switch to make it fail."""
    state = {"fail_after_calls": None, "calls": 0}

    async def embed(texts, batch_size=50):
        state["calls"] += 1
        if state["fail_after_calls"] is not None and state["calls"] > state["fail_after_calls"]:
            raise RuntimeError("embedding service is down")
        return [[0.01] * DIM for _ in texts]

    monkeypatch.setattr(base_processor, "get_text_embeddings_in_batches", embed)
    return state


def pages(count: int, tag: str):
    """`count` pages with enough text that the chunker keeps them.

    Every sentence carries its own page and index, so no two chunks anywhere
    hold identical text. That matters since embedding reuse landed: repetitive
    filler made the later pages of a document reuse the earlier ones' vectors,
    so the embedder was never called again and a test that simulates the
    embedder failing had nothing to fail.
    """
    return [
        {
            "page_num": n,
            "text": f"Page {n}. " + " ".join(
                f"Line {i} of page {n} concerns {tag} and value {n * 100 + i}."
                for i in range(1, 22)
            ),
        }
        for n in range(1, count + 1)
    ]


@pytest_asyncio.fixture(loop_scope="session")
async def doc(store, tenant):
    """A file row in a real workspace, cleaned up with its tenant."""
    workspace_id = await tenant.workspace("Ingestion")
    # add_file, because that is what the upload route uses. create_file used to
    # drop workspace_id silently, which produced a file belonging to no
    # workspace: fine for these assertions, and nothing like a real row.
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner,
        file_name=f"long-{uuid.uuid4().hex[:8]}.txt",
        file_url="",
        workspace_id=workspace_id,
    )
    assert file_id
    return {"id": file_id, "filename": f"long-{file_id}.txt", "workspace_id": workspace_id}


async def _chunk_count(store, file_id: int) -> int:
    from sqlalchemy import func, select
    from api.models.orm_models import Chunk

    async with store.file_repo.get_async_session() as session:
        return (await session.execute(
            select(func.count(Chunk.id)).where(Chunk.file_id == int(file_id))
        )).scalar() or 0


async def test_a_crash_partway_keeps_the_pages_it_reached(store, tenant, doc, fake_embeddings):
    """The whole point of item 18.

    Five pages, three per batch, and the embedding service dies on the second
    batch. The first batch must still be on disk. Before this change the answer
    was zero rows, because nothing was written until the very end.
    """
    processor = TextProcessor(store)
    fake_embeddings["fail_after_calls"] = 1

    with pytest.raises(ValueError):
        await processor.embed_and_store_pages(
            pages(5, "first attempt"),
            file_id=doc["id"],
            user_id=tenant.owner,
            filename=doc["filename"],
            file_type="text",
        )

    assert await _chunk_count(store, doc["id"]) > 0, "the completed batch was lost"
    assert await store.file_repo.stored_page_numbers(doc["id"]) == {1, 2, 3}


async def test_the_retry_starts_where_it_stopped(store, tenant, doc, fake_embeddings):
    """A resumed run must not redo, and must not duplicate, the stored pages."""
    processor = TextProcessor(store)
    page_data = pages(5, "content")

    fake_embeddings["fail_after_calls"] = 1
    with pytest.raises(ValueError):
        await processor.embed_and_store_pages(
            page_data, file_id=doc["id"], user_id=tenant.owner,
            filename=doc["filename"], file_type="text",
        )
    after_crash = await _chunk_count(store, doc["id"])

    # The retry, with the service back.
    fake_embeddings["fail_after_calls"] = None
    result = await processor.embed_and_store_pages(
        page_data, file_id=doc["id"], user_id=tenant.owner,
        filename=doc["filename"], file_type="text",
    )

    assert result["skipped_pages"] == 3, "it started again from page 1"
    assert result["stored_pages"] == 2, "it did not finish the remaining pages"
    assert await store.file_repo.stored_page_numbers(doc["id"]) == {1, 2, 3, 4, 5}

    # One segment per page, not two: a resumed page must not be written twice.
    from sqlalchemy import func, select
    from api.models.orm_models import Segment

    async with store.file_repo.get_async_session() as session:
        segments = (await session.execute(
            select(func.count(Segment.id)).where(Segment.file_id == int(doc["id"]))
        )).scalar()
    assert segments == 5, f"expected one segment per page, found {segments}"
    assert await _chunk_count(store, doc["id"]) > after_crash


async def test_a_document_is_searchable_before_it_is_finished(store, tenant, doc, fake_embeddings):
    """The second half of item 18, and it costs nothing extra.

    Retrieval scopes by workspace and never looks at processing status, so a
    chunk can be found the moment its transaction commits. What must not happen
    is the file calling itself processed while pages are still missing.
    """
    processor = TextProcessor(store)
    fake_embeddings["fail_after_calls"] = 1

    with pytest.raises(ValueError):
        await processor.embed_and_store_pages(
            pages(5, "searchable"), file_id=doc["id"], user_id=tenant.owner,
            filename=doc["filename"], file_type="text",
        )

    assert await _chunk_count(store, doc["id"]) > 0

    record = await store.file_repo.get_file_by_id(int(doc["id"]))
    assert record["processing_status"] != "processed", (
        "a half-stored document called itself ready, so the file list would "
        "show it finished while most of it is missing"
    )


async def test_a_document_that_chunks_to_nothing_fails_loudly(
    store, tenant, doc, fake_embeddings, monkeypatch
):
    """The guard against a document that looks ready and answers nothing.

    Kept, and re-pointed, because the incremental path changed how emptiness is
    counted: it is now "nothing stored and nothing skipped" rather than "no
    chunks came back from the loop".

    The failure is forced where it actually happens. There is real text here,
    and the chunker drops it, which is the shape that shipped silently twice.
    Extracting no text at all is a different branch, and the orchestration
    marks that one failed in `handle_processing_error`.
    """
    monkeypatch.setattr(base_processor, "chunk_text", lambda text: [])
    processor = TextProcessor(store)

    result = await processor.process(
        file_data=b"Real text that a broken chunker throws away. " * 20,
        file_id=doc["id"],
        user_id=tenant.owner,
        filename=doc["filename"],
    )

    assert result["success"] is False
    assert await _chunk_count(store, doc["id"]) == 0
    record = await store.file_repo.get_file_by_id(int(doc["id"]))
    assert record["processing_status"] == "failed", (
        "a document with nothing retrievable behind it was left looking ready"
    )


async def test_storing_a_batch_does_not_declare_the_file_finished(store, tenant, doc, fake_embeddings):
    """`mark_processed=False` is what keeps a half-done file out of the ready list."""
    ok = await store.file_repo.update_file_with_chunks(
        user_id=tenant.owner,
        filename=doc["filename"],
        file_type="text",
        extracted_data=[{
            "text": "a chunk of text",
            "page_text": "a page of text",
            "page_num": 1,
            "embedding": [0.01] * DIM,
        }],
        file_id=int(doc["id"]),
        mark_processed=False,
    )

    assert ok is True
    record = await store.file_repo.get_file_by_id(int(doc["id"]))
    assert record["processing_status"] != "processed"
