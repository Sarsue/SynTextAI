"""A document that has been replaced stops answering questions.

The bug this closes: a workspace holding the 2019 cancellation policy and the
2024 one that replaced it ranked them identically, because `files` carried only
`created_at`, which is when somebody uploaded a file and not when the document
became true. Either could be cited, and nothing in the product could be told
which was current. The customer's own feedback was the only thing that noticed.

Two halves here. The retrieval half proves a replaced document actually leaves
the results, which is the whole value. The authorization half proves the link
cannot be used to reach across a workspace boundary or to hide documents from
people who can see them, because "make this document stop being answerable" is a
destructive act wearing metadata's clothes.
"""
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="session")

DIM = 1024
QUERY_VEC = [0.05] * DIM

POLICY_TEXT = (
    "Cancellation policy. Appointments cancelled with less than 24 hours "
    "notice are charged a cancellation fee. The cancellation fee applies to "
    "every appointment type."
)


async def _add_document(store, tenant, workspace_id, name, content, vector=None):
    """A file with one page and one searchable chunk."""
    from api.models.orm_models import Chunk, Segment

    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name=name, file_url="", workspace_id=workspace_id,
    )
    async with store.file_repo.get_async_session() as session:
        seg = Segment(file_id=file_id, page_number=1, content=content)
        session.add(seg)
        await session.commit()
        session.add(Chunk(
            file_id=file_id, segment_id=seg.id, content=content,
            embedding=vector or QUERY_VEC,
            content_hash="h-" + uuid.uuid4().hex[:8],
        ))
        await session.commit()
    return file_id


@pytest_asyncio.fixture(loop_scope="session")
async def policies(store, tenant):
    """The 2019 policy and the 2024 policy that replaced it.

    Both say the same kind of thing, so neither wins on relevance. Which one
    comes back has to be decided by the supersede link or by nothing.
    """
    workspace_id = await tenant.workspace("Policies")
    old = await _add_document(
        store, tenant, workspace_id,
        f"policy-2019-{uuid.uuid4().hex[:6]}.pdf", POLICY_TEXT + " Revised 2019.",
    )
    new = await _add_document(
        store, tenant, workspace_id,
        f"policy-2024-{uuid.uuid4().hex[:6]}.pdf", POLICY_TEXT + " Revised 2024.",
    )
    return {"workspace_id": workspace_id, "old": old, "new": new}


# --- the value ---------------------------------------------------------------

async def test_both_policies_answer_before_anything_is_marked(store, tenant, policies):
    """The bug, put back. Without this the next test cannot fail."""
    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="what is the cancellation policy",
        query_embedding=QUERY_VEC,
        workspace_id=policies["workspace_id"],
    )
    found = {h["file_id"] for h in hits}
    assert policies["old"] in found, "the 2019 policy was not retrieved to begin with"
    assert policies["new"] in found, "the 2024 policy was not retrieved to begin with"


async def test_a_replaced_document_leaves_the_results(store, tenant, policies):
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=policies["new"], set_superseded_by=True,
    )

    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="what is the cancellation policy",
        query_embedding=QUERY_VEC,
        workspace_id=policies["workspace_id"],
    )
    found = {h["file_id"] for h in hits}
    assert policies["old"] not in found, "the replaced 2019 policy still answers questions"
    assert policies["new"] in found, "the current 2024 policy stopped answering"


async def test_asking_about_the_replaced_document_by_name_still_reads_it(
    store, tenant, policies
):
    """Scoping to one file is an explicit request for that file.

    Refusing to read a document the customer named would be a bug, not a
    feature: "what did the old policy say" is a real question, and the document
    is still in their workspace.
    """
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=policies["new"], set_superseded_by=True,
    )

    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="what is the cancellation policy",
        query_embedding=QUERY_VEC,
        workspace_id=policies["workspace_id"],
        file_id=policies["old"],
    )
    assert {h["file_id"] for h in hits} == {policies["old"]}


async def test_clearing_the_link_brings_the_document_back(store, tenant, policies):
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=policies["new"], set_superseded_by=True,
    )
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=None, set_superseded_by=True,
    )

    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="what is the cancellation policy",
        query_embedding=QUERY_VEC,
        workspace_id=policies["workspace_id"],
    )
    assert policies["old"] in {h["file_id"] for h in hits}


async def test_deleting_the_replacement_brings_the_older_one_back(
    store, tenant, policies
):
    """ON DELETE SET NULL, tested rather than assumed.

    CASCADE here would delete the older document too, and no FK action would
    leave it hidden forever with nothing pointing at it.
    """
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=policies["new"], set_superseded_by=True,
    )
    await store.file_repo.delete_file_entry(policies["new"])

    still_there = await store.file_repo.get_file_by_id(policies["old"])
    assert still_there is not None, "deleting the replacement deleted the original"
    assert still_there["superseded_by_id"] is None

    hits = await store.file_repo.hybrid_search(
        user_id=tenant.owner,
        query="what is the cancellation policy",
        query_embedding=QUERY_VEC,
        workspace_id=policies["workspace_id"],
    )
    assert policies["old"] in {h["file_id"] for h in hits}


# --- the route ---------------------------------------------------------------

async def test_owner_can_mark_a_document_replaced(store, tenant, policies, client):
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": policies["new"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["superseded_by_id"] == policies["new"]


async def test_a_field_the_route_does_not_know_is_not_an_update(
    store, tenant, policies, client
):
    """An `effective_date` column shipped here first and was removed before
    release: nothing set it, nothing showed it, and retrieval did not read it.

    A body carrying only unknown fields must be refused rather than silently
    accepted, or a caller written against a field that no longer exists gets a
    200 back and believes it worked."""
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"effective_date": "2024-03-01"},
    )
    assert res.status_code == 400


async def test_an_empty_body_is_refused(store, tenant, policies, client):
    """Not the same as clearing both fields, and must not be read as one."""
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency", json={},
    )
    assert res.status_code == 400


async def test_a_document_cannot_replace_itself(store, tenant, policies, client):
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": policies["old"]},
    )
    assert res.status_code == 400


async def test_a_cycle_is_refused(store, tenant, policies, client):
    """A replaced by B and B replaced by A hides both, permanently and silently."""
    first = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": policies["new"]},
    )
    assert first.status_code == 200

    second = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['new']}/currency",
        json={"superseded_by_id": policies["old"]},
    )
    assert second.status_code == 400, "a supersede cycle was accepted"


async def test_staff_cannot_mark_a_document_replaced(store, tenant, policies, client):
    """Owners manage documents; staff ask questions.

    Hiding a document from every answer the company gets is a document
    management act, so it is held to the same capability as deleting one.
    """
    member = await tenant.member(scope="workspace", workspaces=[policies["workspace_id"]])
    res = await client.as_(member).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": policies["new"]},
    )
    assert res.status_code == 403


async def test_a_document_cannot_be_replaced_by_one_in_another_workspace(
    store, tenant, policies, client
):
    """The link would hide a document from people who can see it on the say-so
    of people who cannot."""
    other_ws = await tenant.workspace("Elsewhere")
    elsewhere = await _add_document(
        store, tenant, other_ws, f"other-{uuid.uuid4().hex[:6]}.pdf", POLICY_TEXT,
    )

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": elsewhere},
    )
    assert res.status_code == 404


async def test_an_outsider_cannot_mark_another_tenants_document(
    store, tenant, policies, client
):
    outsider = await tenant.new_user("outsider")
    res = await client.as_(outsider).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": policies["new"]},
    )
    assert res.status_code == 403


async def test_naming_a_file_that_does_not_exist_is_404_not_403(
    store, tenant, policies, client
):
    """A refusal must not tell a stranger which ids exist."""
    res = await client.as_(tenant.owner).patch(
        f"/api/v1/files/{policies['old']}/currency",
        json={"superseded_by_id": 99_999_999},
    )
    assert res.status_code == 404


async def test_the_file_list_says_which_documents_are_replaced(
    store, tenant, policies, client
):
    """Caught by driving the app, not by the tests above.

    Every test here passed while `/api/v1/files` still returned the old six
    fields, because the route rebuilds its own response dict rather than
    serialising what the repository returns. Retrieval skipped the document and
    the list had no way to say so, which is exactly the silent state this
    feature exists to remove.
    """
    await store.file_repo.set_document_currency(
        policies["old"], superseded_by_id=policies["new"], set_superseded_by=True,
    )

    res = await client.as_(tenant.owner).get(
        f"/api/v1/files?workspace_id={policies['workspace_id']}"
    )
    assert res.status_code == 200, res.text
    by_id = {f["id"]: f for f in res.json()["items"]}

    assert by_id[policies["old"]]["superseded_by_id"] == policies["new"]
    assert by_id[policies["new"]]["superseded_by_id"] is None
