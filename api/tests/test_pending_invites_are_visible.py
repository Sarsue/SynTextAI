"""Somebody invited to the company can be seen before they arrive.

WHY THIS EXISTS

There was one listing of pending invites and it filtered on `workspace_id`. An
invite to the organization names no workspace, so that column is null on every
one of them, and the query returned nothing. The invite was created, stored,
emailed and set to expire in seven days, and appeared on no screen anywhere.

The owner's only record that they had invited somebody was remembering it. That
is the shape of the report this came from: a workspace was deleted, a person
could not be found afterwards, and it was not obvious whether they had ever
been added, had been removed, or had simply never accepted.

WHAT IS ASSERTED

That both kinds of invite are answerable by the question an owner actually
asks, which is "who is coming", not "who is coming to this particular
workspace". And that the listing stops describing somebody once they are no
longer pending, because a list of people who already arrived is worse than no
list at all.
"""
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


async def test_an_invite_to_the_company_is_listed(store, tenant):
    """The case that was invisible: no workspace named, so nothing found it."""
    email = _email("orgwide")
    token = await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="organization"
    )
    assert token

    pending = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    assert email in [p["email"] for p in pending]


async def test_an_invite_naming_a_workspace_is_listed_too(store, tenant):
    """One question, so one answer. An owner asking who is coming does not
    care which of the two shapes the invite happened to take."""
    ws = await tenant.workspace("Finance")
    email = _email("workspace")
    token = await store.workspace_repo.create_invite(
        email, workspace_id=ws, scope="workspace", workspace_ids=[ws]
    )
    assert token

    pending = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    assert email in [p["email"] for p in pending]


async def test_another_company_cannot_see_who_is_coming_here(store, tenant):
    """The listing is scoped to one tenant, like everything else."""
    email = _email("ours")
    await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="organization"
    )

    other_org = tenant.org + 999_999
    pending = await store.workspace_repo.list_pending_organization_invites(other_org)
    assert email not in [p["email"] for p in pending]


async def test_somebody_who_has_arrived_is_no_longer_pending(store, tenant):
    """A list that keeps naming people who already accepted is a list nobody
    can act on."""
    email = _email("arrived")
    await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="organization"
    )

    uid = await tenant.new_user("arrived")
    async with store.user_repo.get_async_session() as session:
        from sqlalchemy import text as sql
        await session.execute(
            sql("UPDATE users SET email = :e WHERE id = :i"), {"e": email, "i": uid}
        )
        await session.commit()
    joined = await store.workspace_repo.accept_pending_invites_for_email(uid, email)
    assert tenant.org in joined

    pending = await store.workspace_repo.list_pending_organization_invites(tenant.org)
    assert email not in [p["email"] for p in pending]
