"""Finding a passage: who may, and what comes back.

A new route that reads documents is exactly where the forgotten-WHERE class of
bug has appeared before, so most of this is about refusal. The order matters
too: every check that can refuse runs before the embedding call, so a stranger
or an unpaid organization never costs us money.
"""
import pytest
import pytest_asyncio

from api.routes import search as search_route

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def paying(store, tenant):
    """A tenant that has paid, because searching is gated on it.

    Retrieval costs an embedding call, so an unsubscribed organization is
    refused with 402 before anything is spent. The tests that need to reach
    retrieval have to get past that, and the ones about refusal deliberately do
    not use this.
    """
    from api.core.plans import STARTER

    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id="cus_test_search",
        stripe_subscription_id="sub_test_search",
        status="active",
        seats=STARTER.included_seats,
        plan_key="starter",
    )
    return tenant


@pytest.fixture
def no_paid_calls(monkeypatch):
    """Fails loudly if a refused request still reached the embedder.

    The point of ordering the checks the way the route does is that a request
    that is going to be refused never costs anything, and only a test that
    explodes on the paid call can hold that.
    """
    async def embed(_text):
        raise AssertionError("the embedding service was called on a refused request")

    monkeypatch.setattr(search_route, "get_text_embedding", embed)


async def test_a_stranger_cannot_search_another_tenants_workspace(
    store, tenant, client, no_paid_calls
):
    workspace = await tenant.workspace("Private")
    outsider = await tenant.new_user("outsider")

    response = await client.as_(outsider).get(
        f"/api/v1/search?q=anything&workspace_id={workspace}"
    )

    assert response.status_code == 403


async def test_searching_one_document_is_authorized_by_its_workspace(
    store, tenant, client, no_paid_calls
):
    """Not by who uploaded it, or a member cannot search their owner's files."""
    workspace = await tenant.workspace("Documents")
    file_id = await store.file_repo.add_file(
        user_id=tenant.owner, file_name="private.pdf", file_url="", workspace_id=workspace
    )
    outsider = await tenant.new_user("stranger")

    response = await client.as_(outsider).get(f"/api/v1/search?q=anything&file_id={file_id}")

    assert response.status_code == 403


async def test_a_missing_document_is_not_found(store, tenant, client, no_paid_calls):
    response = await client.as_(tenant.owner).get("/api/v1/search?q=anything&file_id=99999999")

    assert response.status_code == 404


async def test_an_empty_query_is_refused_before_anything_is_spent(
    store, tenant, client, no_paid_calls
):
    workspace = await tenant.workspace("Documents")

    response = await client.as_(tenant.owner).get(
        f"/api/v1/search?q=%20%20&workspace_id={workspace}"
    )

    assert response.status_code == 422


async def test_somebody_with_no_workspaces_gets_nothing_rather_than_an_error(
    store, tenant, client, no_paid_calls
):
    """An empty account is empty, not broken."""
    loner = await tenant.new_user("loner")

    response = await client.as_(loner).get("/api/v1/search?q=anything")

    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_chunks_are_grouped_into_the_pages_a_person_opens(
    store, tenant, client, paying, monkeypatch
):
    """Twenty chunks can be five pages, and a page is what gets opened.

    Ungrouped, a page whose text is split across three chunks appears three
    times with near-identical snippets, which reads as broken.
    """
    workspace = await tenant.workspace("Documents")

    async def fake_embed(_text):
        return [0.01] * 1024

    async def fake_search(**kwargs):
        return [
            {"file_id": 1, "file_name": "policy.pdf", "page_number": 4,
             "content": "first passage on page four", "hybrid_score": 0.9},
            {"file_id": 1, "file_name": "policy.pdf", "page_number": 4,
             "content": "second passage on the same page", "hybrid_score": 0.8},
            {"file_id": 1, "file_name": "policy.pdf", "page_number": 9,
             "content": "a passage on page nine", "hybrid_score": 0.7},
        ]

    monkeypatch.setattr(search_route, "get_text_embedding", fake_embed)
    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)

    response = await client.as_(tenant.owner).get(
        f"/api/v1/search?q=refund&workspace_id={workspace}"
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2, "the two chunks from page four were not grouped"

    first = results[0]
    assert first["page_number"] == 4
    assert first["passages"] == 2
    # The best-scoring chunk's text, because retrieval returned it first.
    assert first["snippet"] == "first passage on page four"
    assert results[1]["page_number"] == 9
    assert results[1]["passages"] == 1


async def test_the_score_never_reaches_the_browser(store, tenant, client, paying, monkeypatch):
    """It is a fusion rank on an arbitrary scale, not a percentage match.

    Shown, somebody reads "0.87" as 87% confident, which it is not, and asks
    why a 0.61 was right.
    """
    workspace = await tenant.workspace("Documents")

    async def fake_embed(_text):
        return [0.01] * 1024

    async def fake_search(**kwargs):
        return [{"file_id": 1, "file_name": "policy.pdf", "page_number": 2,
                 "content": "text", "hybrid_score": 0.87}]

    monkeypatch.setattr(search_route, "get_text_embedding", fake_embed)
    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)

    response = await client.as_(tenant.owner).get(
        f"/api/v1/search?q=refund&workspace_id={workspace}"
    )

    body = response.text
    assert "hybrid_score" not in body
    assert "0.87" not in body


async def test_the_search_is_scoped_to_the_workspace_it_was_given(
    store, tenant, client, paying, monkeypatch
):
    """The argument that keeps one organization's documents out of another's."""
    workspace = await tenant.workspace("Documents")
    seen = {}

    async def fake_embed(_text):
        return [0.01] * 1024

    async def fake_search(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(search_route, "get_text_embedding", fake_embed)
    monkeypatch.setattr(store.file_repo, "hybrid_search", fake_search)

    await client.as_(tenant.owner).get(f"/api/v1/search?q=refund&workspace_id={workspace}")

    assert seen["workspace_id"] == workspace
    assert seen["user_id"] == tenant.owner


async def test_an_organization_that_has_not_paid_cannot_search(
    store, tenant, client, no_paid_calls
):
    """Searching runs an embedding call, so it is metered work like asking.

    Added after removing `assert_can_ask` from the route broke nothing: every
    other test here is refused earlier, for not being a member or not naming a
    document, so the payment check had no test standing behind it. This tenant
    is a legitimate member of its own workspace and simply has not paid.
    """
    workspace = await tenant.workspace("Documents")

    response = await client.as_(tenant.owner).get(
        f"/api/v1/search?q=refund&workspace_id={workspace}"
    )

    assert response.status_code == 402
