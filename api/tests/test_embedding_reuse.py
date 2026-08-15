"""Not paying twice to embed the same text.

Embedding is the only part of ingestion billed per call, and it is a pure
function of its input, so the same text always produces the same vector. A
customer re-uploading a corrected policy, or the same standard terms appearing
across twenty contracts, used to be charged for every copy.

Against a real Postgres: the reuse is a query joining chunks to files to
workspaces, and whether it stays inside one organization is the part worth
proving.

These tests used to pin `CONTEXTUALIZE_CHUNKS` off, because contextual
retrieval and embedding reuse are in direct tension: reuse works because
embedding is a pure function of its input, and a per-chunk context sentence
makes that input document-specific, so identical boilerplate across twenty
contracts could no longer share a vector. Turning the flag on in .env.dev made
this file fail and report a deliberate behaviour change as a regression. The
contextualiser was removed on 2026-08-15 after it was measured and did not
help, so there is nothing left to pin.
"""
import uuid

import pytest
import pytest_asyncio

from api.processors import base_processor
from api.processors.text_processor import TextProcessor

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024


@pytest.fixture
def counting_embedder(monkeypatch):
    """Counts what is sent to the paid API, and never repeats a vector.

    Every call returns a different vector, on purpose. A stub that returned the
    same numbers every time would make "the reused vector is the same vector"
    true whether or not anything was reused, which is a test that cannot fail.
    """
    state = {"texts": [], "calls": 0}

    async def embed(texts, batch_size=50):
        state["texts"].extend(texts)
        out = []
        for _ in texts:
            state["calls"] += 1
            out.append([state["calls"] * 0.001] * DIM)
        return out

    monkeypatch.setattr(base_processor, "get_text_embeddings_in_batches", embed)
    return state


def pages(count: int, body: str):
    return [
        {"page_num": n, "text": f"Page {n}. " + body * 20}
        for n in range(1, count + 1)
    ]


@pytest_asyncio.fixture(loop_scope="session")
async def two_files(store, tenant):
    """Two files in one organization, plus one in a different organization."""
    workspace_id = await tenant.workspace("Reuse")

    async def make(name: str, ws: int) -> dict:
        # add_file, because that is what the upload route uses and it is the
        # one that actually attaches the workspace the reuse lookup joins on.
        file_id = await store.file_repo.add_file(
            user_id=tenant.owner,
            file_name=f"{name}-{uuid.uuid4().hex[:8]}.txt",
            file_url="",
            workspace_id=ws,
        )
        assert file_id
        return {"id": file_id, "filename": f"{name}-{file_id}.txt"}

    return {
        "first": await make("first", workspace_id),
        "second": await make("second", workspace_id),
        "workspace": workspace_id,
    }


async def test_the_same_text_is_embedded_once_per_organization(
    store, tenant, two_files, counting_embedder
):
    """The saving: upload a document, upload it again, pay once."""
    processor = TextProcessor(store)
    body = "The refund window is thirty days from delivery. "

    first = await processor.embed_and_store_pages(
        pages(3, body), file_id=two_files["first"]["id"], user_id=tenant.owner,
        filename=two_files["first"]["filename"], file_type="text",
    )
    paid_first = len(counting_embedder["texts"])
    assert paid_first > 0
    assert first["reused_embeddings"] == 0

    second = await processor.embed_and_store_pages(
        pages(3, body), file_id=two_files["second"]["id"], user_id=tenant.owner,
        filename=two_files["second"]["filename"], file_type="text",
    )

    assert len(counting_embedder["texts"]) == paid_first, (
        "the second copy was embedded again instead of reusing the vectors"
    )
    # Every chunk of the second document, not "as many as were embedded the
    # first time": those are different units. The first ingest pays once per
    # *unique* text, while this counts chunk positions, and a document whose
    # pages repeat has more positions than unique texts.
    assert second["reused_embeddings"] == second["stored_chunks"]
    assert second["stored_chunks"] == first["stored_chunks"], (
        "reuse must not cost chunks: the second document has to be as complete "
        "as the first"
    )


async def test_a_reused_vector_is_the_same_vector(store, tenant, two_files, counting_embedder):
    """A cache that returns a different vector is worse than no cache."""
    processor = TextProcessor(store)
    body = "Torque the retaining bolts to forty newton metres. "

    for key in ("first", "second"):
        await processor.embed_and_store_pages(
            pages(2, body), file_id=two_files[key]["id"], user_id=tenant.owner,
            filename=two_files[key]["filename"], file_type="text",
        )

    from sqlalchemy import text as sql

    async with store.file_repo.get_async_session() as session:
        rows = (await session.execute(sql(
            """
            SELECT content_hash, count(DISTINCT embedding::text) AS distinct_vectors
            FROM chunks
            WHERE file_id IN (:a, :b) AND content_hash IS NOT NULL
            GROUP BY content_hash
            """
        ), {"a": two_files["first"]["id"], "b": two_files["second"]["id"]})).all()

    assert rows, "no hashes were stored, so nothing could ever be reused"
    for content_hash, distinct_vectors in rows:
        assert distinct_vectors == 1, (
            f"hash {content_hash[:12]} has {distinct_vectors} different vectors"
        )


async def test_another_organization_gets_no_reuse(store, tenant, two_files, counting_embedder):
    """Scoped to one organization, deliberately.

    Reusing across tenants would leak nothing, since the vector is derived from
    text the caller already holds. It is excluded because a cache that crosses
    tenants is something somebody has to keep proving safe, and the saving is
    almost entirely a customer re-uploading their own document.
    """
    processor = TextProcessor(store)
    body = "Standard terms and conditions apply to all orders. "

    await processor.embed_and_store_pages(
        pages(2, body), file_id=two_files["first"]["id"], user_id=tenant.owner,
        filename=two_files["first"]["filename"], file_type="text",
    )
    paid_first = len(counting_embedder["texts"])

    # A second organization, with its own owner and workspace.
    other_owner = await tenant.new_user("outsider")
    other_org = await store.org_repo.create_organization("Other Co", other_owner)
    other_ws = await store.workspace_repo.create_workspace(
        user_id=other_owner, name="Theirs"
    )
    other_file_id = await store.file_repo.add_file(
        user_id=other_owner,
        file_name=f"theirs-{uuid.uuid4().hex[:8]}.txt",
        file_url="",
        workspace_id=other_ws,
    )
    other_file = {"id": other_file_id, "filename": f"theirs-{other_file_id}.txt"}

    try:
        result = await processor.embed_and_store_pages(
            pages(2, body), file_id=other_file["id"], user_id=other_owner,
            filename=other_file["filename"], file_type="text",
        )

        assert result["reused_embeddings"] == 0, (
            "one organization reused another's vectors"
        )
        assert len(counting_embedder["texts"]) > paid_first, (
            "the other organization's text was not embedded at all"
        )
    finally:
        await store.org_repo.delete_organization(other_org)
        await store.user_repo.delete_user_account(other_owner)


async def test_a_broken_lookup_does_not_fail_the_upload(
    store, tenant, two_files, counting_embedder, monkeypatch
):
    """An optimisation must never be able to lose a document.

    If the reuse query fails, everything is embedded the way it was before this
    existed, which is a bigger bill and a working upload.
    """
    async def broken(*args, **kwargs):
        raise RuntimeError("the lookup blew up")

    monkeypatch.setattr(store.file_repo, "embeddings_for_hashes", broken)
    processor = TextProcessor(store)

    result = await processor.embed_and_store_pages(
        pages(2, "Content that still has to land. "),
        file_id=two_files["first"]["id"], user_id=tenant.owner,
        filename=two_files["first"]["filename"], file_type="text",
    )

    assert result["stored_chunks"] > 0
    assert result["reused_embeddings"] == 0
