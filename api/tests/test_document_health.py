"""Documents that cannot answer questions, saying so.

Two faults that look identical from outside the product and are not the same
thing at all. Both leave a document that lists, opens, and says Ready, and never
comes back in an answer.

DEAD: `chunks.content` is NULL. That column only exists from 2026-08-06, so
anything ingested before it has vectors made from text stored nowhere. Both
keyword arms rank `chunks.tsv`, generated from that same column, so those chunks
cannot be matched by words at all, and they cannot be re-embedded because the
text is gone. Only a re-upload fixes it, and only the customer can do that.

STALE: the vectors came from a different embedding model. Same dimensions, so
nothing errors anywhere. On one benchmark corpus this stopped 11 of 17 questions
retrieving their source. Repairable from our side.

Neither was visible to anybody. `reembed_chunks.py --check` could count them and
had never been run in production.
"""
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024
VEC = [0.05] * DIM


async def _file_with_chunk(store, tenant, workspace_id, *, content, model):
    from api.models.orm_models import Chunk, Segment

    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name=f"doc-{uuid.uuid4().hex[:6]}.pdf",
        file_url="", workspace_id=workspace_id,
    )
    async with store.file_repo.get_async_session() as session:
        seg = Segment(file_id=file_id, page_number=1, content="page text")
        session.add(seg)
        await session.commit()
        session.add(Chunk(
            file_id=file_id, segment_id=seg.id, content=content,
            embedding=VEC, content_hash="h-" + uuid.uuid4().hex[:8],
            embedding_model=model,
        ))
        await session.commit()
    return file_id


async def test_a_document_whose_text_was_never_stored_is_reported_dead(store, tenant):
    from api.repositories.async_file_repository import CURRENT_EMBEDDING_MODEL

    ws = await tenant.workspace("Health")
    dead = await _file_with_chunk(store, tenant, ws, content=None, model=None)
    fine = await _file_with_chunk(store, tenant, ws, content="text",
                                  model=CURRENT_EMBEDDING_MODEL)

    health = await store.file_repo.degraded_files(ws)
    assert health.get(dead) == "dead"
    assert fine not in health, "a healthy document was flagged"


async def test_a_document_indexed_by_another_model_is_reported_stale(store, tenant):
    ws = await tenant.workspace("Health stale")
    stale = await _file_with_chunk(store, tenant, ws, content="text",
                                   model="voyage-3.5-lite")

    health = await store.file_repo.degraded_files(ws)
    assert health.get(stale) == "stale"


async def test_an_unmeasured_vector_is_not_called_stale(store, tenant):
    """NULL is "not measured", not "wrong".

    The column is new, so almost every existing row is NULL. Counting those as
    stale flagged 2,528 of 2,650 chunks on the local database: a warning on
    nearly every document somebody owns, which is no signal at all. Some of
    those vectors are perfectly current, and deciding which needs the cosine
    sample in reembed_chunks.py.
    """
    ws = await tenant.workspace("Health unknown")
    unknown = await _file_with_chunk(store, tenant, ws, content="text", model=None)

    health = await store.file_repo.degraded_files(ws)
    assert unknown not in health, "an unmeasured document was reported as broken"


async def test_dead_wins_over_stale(store, tenant):
    """A file with both faults reports the one the customer has to act on."""
    from api.models.orm_models import Chunk, Segment

    ws = await tenant.workspace("Health both")
    file_id = await _file_with_chunk(store, tenant, ws, content=None, model=None)
    async with store.file_repo.get_async_session() as session:
        seg = (await session.execute(
            __import__("sqlalchemy").select(Segment).where(Segment.file_id == file_id)
        )).scalars().first()
        session.add(Chunk(
            file_id=file_id, segment_id=seg.id, content="text",
            embedding=VEC, content_hash="h-" + uuid.uuid4().hex[:8],
            embedding_model="voyage-3.5-lite",
        ))
        await session.commit()

    health = await store.file_repo.degraded_files(ws)
    assert health.get(file_id) == "dead"


async def test_the_file_list_tells_the_customer(store, tenant, client):
    """The list is the only place anybody would ever find out."""
    ws = await tenant.workspace("Health list")
    dead = await _file_with_chunk(store, tenant, ws, content=None, model=None)

    res = await client.as_(tenant.owner).get(f"/api/v1/files?workspace_id={ws}")
    assert res.status_code == 200, res.text
    by_id = {f["id"]: f for f in res.json()["items"]}
    assert by_id[dead]["health"] == "dead"


async def test_ingestion_records_the_model_it_used(store, tenant):
    """Otherwise the next model change is invisible all over again."""
    from api.repositories.async_file_repository import CURRENT_EMBEDDING_MODEL
    assert CURRENT_EMBEDDING_MODEL, "no embedding model name to record"

    ws = await tenant.workspace("Health record")
    fine = await _file_with_chunk(store, tenant, ws, content="text",
                                  model=CURRENT_EMBEDDING_MODEL)
    assert await store.file_repo.degraded_files(ws) == {}
