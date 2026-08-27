"""An invite can be taken back before it is accepted.

WHY THIS EXISTS

There was no way to withdraw one. An address typed wrong, an offer to somebody
who has since left, an invite sent with more access than intended: all of them
left a live token in somebody's inbox for seven days, and the only remedy was
to wait. Re-inviting the same address did expire the old one, but that only
helps when you still want the person to join.

WHAT IS ASSERTED HERE

That the token actually dies, not merely that the row changed. And that the
reach of the cancel matches the reach of the listing exactly, because the hole
that shape of bug leaves is an invite an owner can see on their screen and
cannot cancel from it.
"""
import pytest
from sqlalchemy import text as sql

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _invite(store, tenant, email, **kwargs):
    """Create an invite and return (token, id)."""
    token = await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, **kwargs
    )
    assert token, "invite was not created"
    listed = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    invite_id = next(i["id"] for i in listed if i["email"] == email)
    return token, invite_id


async def test_cancelling_kills_the_token(store, tenant):
    """The row's status is bookkeeping. The token is the thing in the wild."""
    token, invite_id = await _invite(store, tenant, "leaver@example.com")

    assert (await store.workspace_repo.get_invite_by_token(token))["status"] == "pending"

    revoked = await store.workspace_repo.revoke_invite(invite_id, tenant.org)
    assert revoked == {"id": invite_id, "email": "leaver@example.com"}

    after = await store.workspace_repo.get_invite_by_token(token)
    assert after["status"] == "revoked", (
        "the token still reads as pending, so the link in their inbox still works"
    )


async def test_a_cancelled_invite_leaves_the_pending_list(store, tenant):
    _, invite_id = await _invite(store, tenant, "typo@example.com")

    await store.workspace_repo.revoke_invite(invite_id, tenant.org)

    listed = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    assert [i for i in listed if i["email"] == "typo@example.com"] == []


async def test_another_tenant_cannot_cancel_it(store, tenant):
    """Invite ids are sequential integers, so this is guessable by anyone."""
    token, invite_id = await _invite(store, tenant, "theirs@example.com")

    stranger = await tenant.new_user("stranger")
    other_org = await store.org_repo.create_organization("Other Co", stranger)

    assert await store.workspace_repo.revoke_invite(invite_id, other_org) is None
    assert (await store.workspace_repo.get_invite_by_token(token))["status"] == "pending", (
        "another company cancelled an invite that was not theirs"
    )


async def test_cancelling_twice_is_not_an_error_the_second_time(store, tenant):
    """The owner clicked twice, or two admins did. The second one gets None,
    which the route answers as 404, rather than a 500."""
    _, invite_id = await _invite(store, tenant, "double@example.com")

    assert await store.workspace_repo.revoke_invite(invite_id, tenant.org) is not None
    assert await store.workspace_repo.revoke_invite(invite_id, tenant.org) is None


async def test_an_invite_reaching_the_company_through_a_workspace_can_be_cancelled(
    store, tenant
):
    """The hole a simpler implementation leaves.

    list_pending_organization_invites finds invites two ways: by their own
    organization_id, or by the organization of the workspace they name. An
    invite created before organization_id was populated has it null and is
    listed only by the second route. Scoping the cancel on the column alone
    would put such an invite on the owner's screen with a button that always
    fails.
    """
    ws = await tenant.workspace("Finance")
    token = await store.workspace_repo.create_invite("legacy@example.com", workspace_id=ws)
    assert token

    # Make it look like a row written before organization_id was filled in.
    async with store.workspace_repo.get_async_session() as session:
        await session.execute(
            sql("UPDATE workspace_invites SET organization_id = NULL WHERE token = :t"),
            {"t": token},
        )
        await session.commit()

    listed = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    match = [i for i in listed if i["email"] == "legacy@example.com"]
    assert match, "precondition failed: the listing no longer finds it either"

    assert await store.workspace_repo.revoke_invite(match[0]["id"], tenant.org) is not None
    assert (await store.workspace_repo.get_invite_by_token(token))["status"] == "revoked"
