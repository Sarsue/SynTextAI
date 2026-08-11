"""A company's own numbers, and nobody else's.

Every figure on this dashboard reaches its organization by joining through the
workspace, because documents, conversations and ratings carry no organization
of their own. That join is the tenancy boundary, so most of what follows is one
question asked twice: does another company's activity ever appear here.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_an_outsider_cannot_read_a_companys_usage(store, tenant, client):
    outsider = await tenant.new_user("outsider")

    response = await client.as_(outsider).get(f"/api/v1/organizations/{tenant.org}/usage")

    assert response.status_code == 403


async def test_staff_cannot_read_it_either(store, tenant, client):
    """Not because the numbers are dangerous, but because they are about people.

    "Who asked the most" and "which answers somebody called wrong" is a
    different product when a colleague can read it.
    """
    staff = await tenant.member("helper")

    response = await client.as_(staff).get(f"/api/v1/organizations/{tenant.org}/usage")

    assert response.status_code == 403


async def test_the_owner_sees_their_own_numbers(store, tenant, client):
    response = await client.as_(tenant.owner).get(
        f"/api/v1/organizations/{tenant.org}/usage"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 30
    assert body["questions_asked"] == 0
    assert body["documents"]["total"] == 0
    assert body["feedback"] == {"helpful": 0, "unhelpful": 0, "reasons": []}


async def test_another_companys_documents_are_never_counted(store, tenant, client):
    """The join, tested rather than trusted."""
    mine = await tenant.workspace("Mine")
    await store.file_repo.add_file(
        user_id=tenant.owner, file_name="mine.pdf", file_url="", workspace_id=mine
    )

    # A second company, with a document of its own.
    other_owner = await tenant.new_user("other-owner")
    other_org = await store.org_repo.create_organization("Other Co", other_owner)
    other_ws = await store.workspace_repo.create_workspace(
        user_id=other_owner, name="Theirs"
    )
    await store.file_repo.add_file(
        user_id=other_owner, file_name="theirs.pdf", file_url="", workspace_id=other_ws
    )

    try:
        response = await client.as_(tenant.owner).get(
            f"/api/v1/organizations/{tenant.org}/usage"
        )

        assert response.status_code == 200
        assert response.json()["documents"]["total"] == 1, (
            "another company's document was counted in this one's total"
        )
    finally:
        await store.org_repo.delete_organization(other_org)
        await store.user_repo.delete_user_account(other_owner)


async def test_documents_never_retrieved_stays_empty_until_there_is_evidence(
    store, tenant, client
):
    """Otherwise every document in the account is named on day one.

    The file ids behind this metric are only recorded from 2026-08-11 onward,
    so an account whose runs all predate that has no evidence either way.
    Listing every document as unused in that situation would be alarming and
    false, so it says nothing at all instead.
    """
    workspace = await tenant.workspace("Docs")
    await store.file_repo.add_file(
        user_id=tenant.owner, file_name="never-asked-about.pdf", file_url="",
        workspace_id=workspace,
    )
    # Marked processed, so only the missing evidence keeps it off the list.
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name="second.pdf", file_url="", workspace_id=workspace
    )
    await store.file_repo.update_file_status(file_id, "processed")

    response = await client.as_(tenant.owner).get(
        f"/api/v1/organizations/{tenant.org}/usage"
    )

    assert response.json()["documents"]["never_retrieved"] == []


async def test_a_failed_document_is_surfaced_by_name(store, tenant, client):
    """The most likely reason an answer is missing something.

    A failed upload sits in the file list looking like any other row unless
    somebody scrolls to it, so the dashboard counts it separately.
    """
    workspace = await tenant.workspace("Docs")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name="broken.pdf", file_url="", workspace_id=workspace
    )
    await store.file_repo.update_file_status(file_id, "failed")

    response = await client.as_(tenant.owner).get(
        f"/api/v1/organizations/{tenant.org}/usage"
    )

    assert response.json()["documents"]["failed"] == 1


async def test_a_run_that_cited_nothing_is_not_treated_as_evidence(store, tenant, client):
    """The false alarm this metric can produce, and did.

    A run records `cited_file_ids` as an empty list when it retrieved nothing
    useful, or when it predates the ids being carried through at all. Counting
    that as evidence switches the metric on with nothing behind it, and every
    document in the account is then listed as never used. Found by opening the
    panel and seeing all ten documents named, one of which had just been cited.
    """
    workspace = await tenant.workspace("Docs")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name="quiet.pdf", file_url="", workspace_id=workspace
    )
    await store.file_repo.update_file_status(file_id, "processed")

    # A run that reached no document, exactly as one looks in the wild.
    await store.agent_run_repo.enqueue_run(
        run_type="answer_query",
        agent_name="QueryAgent",
        agent_version=None,
        payload={"message": "something nothing answered"},
        user_id=tenant.owner,
        workspace_id=workspace,
    )
    from sqlalchemy import text as sql

    async with store.agent_run_repo.get_async_session() as session:
        await session.execute(sql(
            """
            UPDATE agent_runs
            SET result = '{"cited_file_ids": []}'::jsonb
            WHERE workspace_id = :ws
            """
        ), {"ws": workspace})
        await session.commit()

    response = await client.as_(tenant.owner).get(
        f"/api/v1/organizations/{tenant.org}/usage"
    )

    assert response.json()["documents"]["never_retrieved"] == [], (
        "an empty citation list was treated as proof the document is unused"
    )
