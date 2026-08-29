"""What a credential a program holds may reach, and when it stops working.

The whole safety argument for API keys is one sentence: the key carries no
permission of its own, only a pointer to the person who created it and a ceiling
that can narrow what that person currently has. Everything else here is that
sentence, driven against real rows.

test_api_key_ceiling.py holds the same invariants with no database, which is
where to look first when one of these fails: if both fail it is the ceiling, and
if only these fail it is the wiring around it.
"""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from api.routes import search as search_route

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def paying(store, tenant):
    """Searching is gated on a subscription, keys included."""
    from api.core.plans import STARTER

    await store.user_repo.add_or_update_subscription(
        user_id=tenant.owner,
        organization_id=tenant.org,
        stripe_customer_id="cus_test_keys",
        stripe_subscription_id="sub_test_keys",
        status="active",
        seats=STARTER.included_seats,
        plan_key="starter",
    )
    return tenant


@pytest.fixture
def embedder(monkeypatch):
    """A retrieval call that costs nothing, for the paths meant to reach it."""

    async def embed(_text):
        return [0.0] * 1024

    monkeypatch.setattr(search_route, "get_text_embedding", embed)


@pytest.fixture
def no_paid_calls(monkeypatch):
    """Fails loudly if a refused request still reached the embedder."""

    async def embed(_text):
        raise AssertionError("the embedding service was called on a refused request")

    monkeypatch.setattr(search_route, "get_text_embedding", embed)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def issue(store, workspace_id: int, user_id: int, **kwargs) -> dict:
    return await store.api_key_repo.create_api_key(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=kwargs.pop("name", "Test integration"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# What a key can reach
# ---------------------------------------------------------------------------

async def test_a_key_searches_the_workspace_it_was_issued_for(
    store, paying, client, embedder
):
    workspace = await paying.workspace("Handbook")
    key = await issue(store, workspace, paying.owner)

    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=policy&workspace_id={workspace}", headers=auth(key["token"])
    )

    assert response.status_code == 200


async def test_a_key_cannot_reach_another_workspace_its_creator_owns(
    store, paying, client, no_paid_calls
):
    """The owner can see both. The credential was cut for one."""
    issued_for = await paying.workspace("Handbook")
    other = await paying.workspace("Payroll")
    key = await issue(store, issued_for, paying.owner)

    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=salaries&workspace_id={other}", headers=auth(key["token"])
    )

    # 404 and not 403, the rule everywhere here for a resource named by an id.
    assert response.status_code == 404


async def test_removing_the_creator_from_the_workspace_kills_the_key(
    store, paying, client, embedder
):
    """Nobody revoked anything. Removing the person was enough."""
    workspace = await paying.workspace("Contracts")
    member = await paying.member("analyst", scope="workspace", workspaces=[workspace])
    key = await issue(store, workspace, member)

    # It works while they are in the workspace, which is the half of this test
    # that has to reach retrieval. Hence `embedder` rather than `no_paid_calls`:
    # the refusal being proved here is the second request, not the first.
    before = await client.as_(member).get(
        f"/api/v1/search?q=x&workspace_id={workspace}", headers=auth(key["token"])
    )
    assert before.status_code != 404

    await store.org_repo.set_member_access(paying.org, member, "workspace", [])

    after = await client.as_(member).get(
        f"/api/v1/search?q=x&workspace_id={workspace}", headers=auth(key["token"])
    )
    assert after.status_code == 404


# ---------------------------------------------------------------------------
# When a key stops working
# ---------------------------------------------------------------------------

async def test_a_revoked_key_is_refused(store, paying, client, no_paid_calls):
    workspace = await paying.workspace("Revoked")
    key = await issue(store, workspace, paying.owner)

    assert await store.api_key_repo.revoke(key["id"], workspace)

    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=x&workspace_id={workspace}", headers=auth(key["token"])
    )
    assert response.status_code == 401


async def test_an_expired_key_is_refused(store, paying, client, no_paid_calls):
    workspace = await paying.workspace("Expired")
    key = await issue(
        store,
        workspace,
        paying.owner,
        expires_at=datetime.utcnow() - timedelta(seconds=1),
    )

    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=x&workspace_id={workspace}", headers=auth(key["token"])
    )
    assert response.status_code == 401


async def test_a_real_prefix_with_a_wrong_secret_is_refused(
    store, paying, client, no_paid_calls
):
    """The prefix is public. Knowing it must buy nothing."""
    workspace = await paying.workspace("Guessing")
    key = await issue(store, workspace, paying.owner)
    prefix = key["prefix"]
    forged = f"stx_live_{prefix}_{'0' * 64}"

    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=x&workspace_id={workspace}", headers=auth(forged)
    )
    assert response.status_code == 401


async def test_an_unknown_key_is_refused(store, paying, client, no_paid_calls):
    workspace = await paying.workspace("Unknown")
    response = await client.as_(paying.owner).get(
        f"/api/v1/search?q=x&workspace_id={workspace}",
        headers=auth(f"stx_live_{'a' * 12}_{'b' * 64}"),
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Managing keys, which only a person may do
# ---------------------------------------------------------------------------

async def test_the_token_is_shown_once_and_never_listed(store, tenant, client):
    workspace = await tenant.workspace("Once")

    created = await client.as_(tenant.owner).post(
        f"/api/v1/workspaces/{workspace}/api-keys", json={"name": "Claude desktop"}
    )
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("stx_live_")

    listed = await client.as_(tenant.owner).get(f"/api/v1/workspaces/{workspace}/api-keys")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert "token" not in rows[0]
    # The prefix is there, because it is how a person tells two rows apart.
    assert rows[0]["prefix"] == token[len("stx_live_"):].split("_")[0]


async def test_staff_cannot_issue_a_key(store, tenant, client):
    """Reading the knowledge base and handing out access to it are different."""
    workspace = await tenant.workspace("Staff")
    member = await tenant.member("reader", scope="workspace", workspaces=[workspace])

    response = await client.as_(member).post(
        f"/api/v1/workspaces/{workspace}/api-keys", json={"name": "Sneaky"}
    )
    assert response.status_code == 403


async def test_a_revoked_key_stays_in_the_list(store, tenant, client):
    """'Revoked on the 3rd' is the answer somebody is looking for."""
    workspace = await tenant.workspace("History")
    created = await client.as_(tenant.owner).post(
        f"/api/v1/workspaces/{workspace}/api-keys", json={"name": "Old script"}
    )
    key_id = created.json()["id"]

    deleted = await client.as_(tenant.owner).delete(
        f"/api/v1/workspaces/{workspace}/api-keys/{key_id}"
    )
    assert deleted.status_code == 204

    rows = (await client.as_(tenant.owner).get(
        f"/api/v1/workspaces/{workspace}/api-keys"
    )).json()
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is not None


async def test_a_key_from_another_workspace_cannot_be_revoked_through_this_one(
    store, tenant, client
):
    mine = await tenant.workspace("Mine")
    theirs = await tenant.workspace("Theirs")
    key = await issue(store, theirs, tenant.owner)

    response = await client.as_(tenant.owner).delete(
        f"/api/v1/workspaces/{mine}/api-keys/{key['id']}"
    )
    assert response.status_code == 404


async def test_an_expiry_in_the_past_is_refused(store, tenant, client):
    workspace = await tenant.workspace("Backdated")
    response = await client.as_(tenant.owner).post(
        f"/api/v1/workspaces/{workspace}/api-keys",
        json={"name": "Stale", "expires_at": "2020-01-01T00:00:00"},
    )
    assert response.status_code == 422
