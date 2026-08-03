"""An invite decides what somebody will be, and how far they reach.

The pair that matters most here is an admin confined to some workspaces. It was
impossible to express, then briefly half-true: the read path hid the workspaces
they had not been given while the write path still let them upload into those
same workspaces by naming an id. Seeing and doing were answered by different
rules and only one had been tightened.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _accept(store, tenant, email, **invite):
    """Invite an address, then accept it as a new user. Returns the user id."""
    token = await store.workspace_repo.create_invite(
        email, organization_id=tenant.org, **invite
    )
    assert token, "invite was not created"
    uid = await tenant.new_user(email.split("@")[0])
    # The address on the invite is what the accept path matches on.
    async with store.user_repo.get_async_session() as session:
        from sqlalchemy import text as sql
        await session.execute(
            sql("UPDATE users SET email = :e WHERE id = :i"), {"e": email, "i": uid}
        )
        await session.commit()
    joined = await store.workspace_repo.accept_pending_invites_for_email(uid, email)
    assert tenant.org in joined, "invite was not accepted"
    return uid


async def test_invited_admin_is_admin(store, tenant):
    ws = await tenant.workspace("Finance")
    uid = await _accept(
        store, tenant, "new-admin@example.com",
        role="admin", scope="workspace", workspace_ids=[ws],
    )

    assert await store.org_repo.get_role(tenant.org, uid) == "admin"


async def test_admin_confined_to_workspaces_sees_only_those(store, tenant):
    """Scope applies to admins. Admin says what you may do, not how far you see."""
    finance = await tenant.workspace("Finance")
    payroll = await tenant.workspace("Payroll")
    uid = await _accept(
        store, tenant, "confined-admin@example.com",
        role="admin", scope="workspace", workspace_ids=[finance],
    )

    reachable = await store.workspace_repo.accessible_workspace_ids(
        uid, organization_id=tenant.org
    )
    assert finance in reachable
    assert payroll not in reachable


async def test_admin_confined_to_workspaces_cannot_act_in_the_others(store, tenant):
    """The hole this closes: hidden in the list, writable by id.

    get_user_role_in_workspace returned 'owner' for any admin regardless of
    scope, so a confined admin could upload into a workspace they could not see
    by passing its id, and held owner capabilities while doing it.
    """
    finance = await tenant.workspace("Finance")
    payroll = await tenant.workspace("Payroll")
    uid = await _accept(
        store, tenant, "confined-admin-2@example.com",
        role="admin", scope="workspace", workspace_ids=[finance],
    )

    assert await store.workspace_repo.get_user_role_in_workspace(finance, uid) == "admin"
    assert await store.workspace_repo.get_user_role_in_workspace(payroll, uid) is None


async def test_organization_wide_admin_reaches_everything(store, tenant):
    finance = await tenant.workspace("Finance")
    payroll = await tenant.workspace("Payroll")
    uid = await _accept(
        store, tenant, "wide-admin@example.com", role="admin", scope="organization",
    )

    reachable = await store.workspace_repo.accessible_workspace_ids(
        uid, organization_id=tenant.org
    )
    assert {finance, payroll} <= set(reachable)
    assert await store.workspace_repo.get_user_role_in_workspace(payroll, uid) == "admin"


async def test_organization_wide_staff_reads_everything_and_manages_nothing(store, tenant):
    finance = await tenant.workspace("Finance")
    uid = await _accept(
        store, tenant, "wide-staff@example.com", role="staff", scope="organization",
    )

    assert finance in await store.workspace_repo.accessible_workspace_ids(
        uid, organization_id=tenant.org
    )
    # Staff is the role, and staff holds no management capability.
    assert await store.workspace_repo.get_user_role_in_workspace(finance, uid) == "staff"


async def test_a_workspace_scope_with_no_workspaces_means_the_whole_company(store, tenant):
    """Refusing to grant nothing.

    An empty set would let somebody in who could see no workspace at all, which
    reads as a broken invite rather than a deliberate one.
    """
    await tenant.workspace("Finance")
    uid = await _accept(
        store, tenant, "empty-scope@example.com",
        role="staff", scope="workspace", workspace_ids=[],
    )

    members = await store.org_repo.list_members(tenant.org)
    theirs = next(m for m in members if m["user_id"] == uid)
    assert theirs["scope"] == "organization"
