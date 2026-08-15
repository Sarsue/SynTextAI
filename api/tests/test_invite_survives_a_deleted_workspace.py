"""Nobody is orphaned by a workspace that went away while they were invited.

WHAT WAS MEASURED, 2026-08-15

An invite naming one workspace, that workspace deleted, then accepted:

    members_stranded_by_deleting -> []      the delete was not blocked
    invite row after delete       -> still there, still pending, still naming it
    accept                        -> []
    role in the company           -> None
    workspaces reachable          -> []

The person joined nothing at all. Not the workspace, not the company. They
signed up expecting to join a colleague and arrived as an account belonging to
no tenant, which is the flow that asks them to start a subscription.

TWO REASONS IT GOT THAT FAR

The deletion guard reads `workspace_members`, which only exists once somebody
has accepted, so a pending invite was invisible to it.

And the workspace an invite names is held two ways. A legacy invite sets
`workspace_id`, a foreign key, which cascades. Every invite the app creates now
uses `workspace_ids`, a JSON array with no foreign key, so nothing cleaned it up
and it kept naming a workspace that had gone. Accepting inserted a
workspace_members row against a missing id, the foreign key refused it, and the
whole acceptance rolled back inside a bare `except` that returns [].

The rollback also undid `status = 'accepted'`, so the invite stayed pending and
failed identically forever.

WHAT IS ASSERTED HERE

Both halves, because either alone leaves a hole. The owner is told before they
cause it, and if it happens anyway, by a race or another code path, the invite
still seats them in the company so somebody can fix it with a tick.
"""
import uuid

import pytest
from sqlalchemy import text as sql

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


async def _user_with_email(store, tenant, email: str) -> int:
    uid = await tenant.new_user("invitee")
    async with store.user_repo.get_async_session() as session:
        await session.execute(
            sql("UPDATE users SET email = :e WHERE id = :i"), {"e": email, "i": uid}
        )
        await session.commit()
    return uid


async def test_deleting_names_the_invite_it_would_break(store, tenant):
    """The owner hears about it at the moment they would cause it."""
    doomed = await tenant.workspace("Doomed")
    email = _email("pending")
    await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="workspace", workspace_ids=[doomed]
    )

    at_risk = await store.workspace_repo.invites_stranded_by_deleting(doomed)
    assert [i["email"] for i in at_risk] == [email]


async def test_an_invite_with_somewhere_else_to_go_is_not_flagged(store, tenant):
    """Finance survives Payroll, and is nobody's problem."""
    payroll = await tenant.workspace("Payroll")
    finance = await tenant.workspace("Finance")
    email = _email("twoplaces")
    await store.workspace_repo.create_invite(
        email,
        organization_id=tenant.org,
        scope="workspace",
        workspace_ids=[payroll, finance],
    )

    assert await store.workspace_repo.invites_stranded_by_deleting(payroll) == []


async def test_accepting_after_the_only_workspace_went_still_joins_the_company(
    store, tenant
):
    """The measured failure, asserted as the behaviour it should have had."""
    doomed = await tenant.workspace("Doomed")
    await tenant.workspace("Survivor")
    email = _email("late")
    await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="workspace", workspace_ids=[doomed]
    )
    assert await store.workspace_repo.delete_workspace(doomed)

    uid = await _user_with_email(store, tenant, email)
    joined = await store.workspace_repo.accept_pending_invites_for_email(uid, email)

    assert tenant.org in joined, "they joined no company at all"
    assert await store.org_repo.get_role(tenant.org, uid) == "staff"


async def test_a_deleted_workspace_does_not_become_a_promotion(store, tenant):
    """The tempting shortcut, refused.

    An empty grant already means "organization-wide" for an invite that named
    nothing. Reusing that here would turn losing a workspace into gaining every
    workspace, so somebody offered one would arrive able to read them all.
    """
    doomed = await tenant.workspace("Doomed")
    survivor = await tenant.workspace("Survivor")
    email = _email("noescalation")
    await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, scope="workspace", workspace_ids=[doomed]
    )
    assert await store.workspace_repo.delete_workspace(doomed)

    uid = await _user_with_email(store, tenant, email)
    joined = await store.workspace_repo.accept_pending_invites_for_email(uid, email)

    # Asserted first, or the rest passes for the wrong reason: before this was
    # fixed nobody joined at all, so "they cannot reach Survivor" was true and
    # meaningless.
    assert tenant.org in joined, "they joined no company at all"

    reachable = await store.workspace_repo.accessible_workspace_ids(uid)
    assert survivor not in reachable, "a deleted workspace granted every workspace"
    assert reachable == [], "they should arrive with no workspace, for an owner to set"


async def test_the_workspaces_that_survive_are_still_granted(store, tenant):
    """Losing one named workspace must not cost the others."""
    doomed = await tenant.workspace("Doomed")
    finance = await tenant.workspace("Finance")
    email = _email("partial")
    await store.workspace_repo.create_invite(
        email,
        organization_id=tenant.org,
        scope="workspace",
        workspace_ids=[doomed, finance],
    )
    assert await store.workspace_repo.delete_workspace(doomed)

    uid = await _user_with_email(store, tenant, email)
    joined = await store.workspace_repo.accept_pending_invites_for_email(uid, email)

    assert tenant.org in joined
    assert await store.workspace_repo.accessible_workspace_ids(uid) == [finance]
