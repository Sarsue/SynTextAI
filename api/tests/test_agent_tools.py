"""The agents' read_page tool, against a real database.

Its document and page numbers come from a model, which is to say from
whatever a document it read told it to ask for. So the page is scoped in the
SQL exactly as a search is, and these check that it is.
"""
import pytest
from sqlalchemy import text

from api.agents.tools import Scope

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _page(store, file_id: int, number: int, content: str) -> None:
    async with store.file_repo.get_async_session() as session:
        await session.execute(
            text("INSERT INTO segments (file_id, page_number, content) VALUES (:f, :p, :c)"),
            {"f": file_id, "p": number, "c": content},
        )
        await session.commit()


async def test_a_page_in_the_readers_workspace_is_read(store, tenant):
    workspace = await tenant.workspace("Docs")
    fid = await store.file_repo.add_file(user_id=tenant.owner, file_name="lease.pdf",
                                         file_url="", workspace_id=workspace)
    await _page(store, fid, 4, "Renewal is one term of five years.")

    page = await Scope(store, tenant.owner, workspace_id=workspace).page(fid, 4)

    assert page["content"] == "Renewal is one term of five years."
    assert page["file_name"] == "lease.pdf" and page["pid"].startswith("p")


async def test_a_page_in_another_tenants_workspace_is_not_found(store, tenant):
    workspace = await tenant.workspace("Private")
    fid = await store.file_repo.add_file(user_id=tenant.owner, file_name="secret.pdf",
                                         file_url="", workspace_id=workspace)
    await _page(store, fid, 1, "Salary bands.")
    outsider = await tenant.new_user("outsider")
    own = await store.workspace_repo.create_workspace(user_id=outsider, name="Mine")

    # Asked from the outsider's own workspace, and from their accessible list.
    assert await Scope(store, outsider, workspace_id=own).page(fid, 1) is None
    accessible = await store.workspace_repo.accessible_workspace_ids(outsider)
    assert await Scope(store, outsider, accessible_ids=accessible).page(fid, 1) is None


async def test_a_question_about_one_file_cannot_read_another(store, tenant):
    workspace = await tenant.workspace("Docs")
    a = await store.file_repo.add_file(user_id=tenant.owner, file_name="a.pdf",
                                       file_url="", workspace_id=workspace)
    b = await store.file_repo.add_file(user_id=tenant.owner, file_name="b.pdf",
                                       file_url="", workspace_id=workspace)
    await _page(store, b, 1, "Not what was asked about.")

    scope = Scope(store, tenant.owner, workspace_id=workspace, file_id=a)
    assert await scope.page(b, 1) is None


async def test_a_database_error_during_search_is_raised_not_returned_as_empty(store, tenant):
    """An empty list means "nothing matched". A failed query must not look like one."""
    from api.repositories.async_file_repository import SearchUnavailable

    workspace = await tenant.workspace("Docs")
    with pytest.raises(SearchUnavailable):
        # An empty vector is rejected when Postgres reads the query, rows or
        # no rows. (A wrong-length one is not: in an empty workspace nothing
        # is ever compared, so nothing fails.)
        await store.file_repo.hybrid_search(
            user_id=tenant.owner, query="anything", query_embedding=[],
            workspace_id=workspace, top_k=5,
        )
