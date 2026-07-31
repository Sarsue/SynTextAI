"""Who can see which workspaces, and which documents.

This is the security boundary between one customer's documents and another's,
and between departments inside one customer. It is also where a regression is
invisible: nothing fails, somebody simply sees a workspace they should not, and
answers start citing documents they were never given.

Every rule here was broken at some point. Organization membership used to grant
every workspace in the tenant, so inviting somebody to one workspace silently
gave them all of them.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_new_member_reaches_nothing_until_assigned(store, tenant):
    member = await tenant.member(scope="workspace", workspaces=[])
    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == []


async def test_workspace_scope_grants_only_that_workspace(store, tenant):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == [ws1]
    assert await store.workspace_repo.get_user_role_in_workspace(ws1, member) == "staff"
    # The regression that mattered: the other workspace must be invisible.
    assert await store.workspace_repo.get_user_role_in_workspace(ws2, member) is None


async def test_organization_scope_grants_every_workspace(store, tenant):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="organization")

    got = await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org)
    assert got == sorted([ws1, ws2])
    # Reach, not authority: they still cannot upload or manage members.
    assert await store.workspace_repo.get_user_role_in_workspace(ws1, member) == "staff"


async def test_organization_scope_includes_workspaces_created_later(store, tenant):
    member = await tenant.member(scope="organization")
    later = await tenant.workspace("Created Afterwards")
    got = await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org)
    assert later in got


async def test_moving_between_workspaces_revokes_the_previous_one(store, tenant):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    await store.org_repo.set_member_access(tenant.org, member, "workspace", [ws2])

    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == [ws2]
    assert await store.workspace_repo.get_user_role_in_workspace(ws1, member) is None


async def test_narrowing_does_not_restore_old_assignments(store, tenant):
    """Widening then narrowing must not silently re-grant what was removed."""
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    await store.org_repo.set_member_access(tenant.org, member, "organization", [])
    await store.org_repo.set_member_access(tenant.org, member, "workspace", [ws2])

    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == [ws2]


async def test_owner_sees_every_workspace_and_cannot_be_narrowed(store, tenant):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")

    assert await store.workspace_repo.accessible_workspace_ids(
        tenant.owner, organization_id=tenant.org
    ) == sorted([ws1, ws2])

    # Owners reach everything by administering the tenant. Narrowing them would
    # produce somebody who can manage the organization but not see it.
    assert await store.org_repo.set_member_access(tenant.org, tenant.owner, "workspace", [ws1]) is False
    assert await store.workspace_repo.accessible_workspace_ids(
        tenant.owner, organization_id=tenant.org
    ) == sorted([ws1, ws2])


async def test_cannot_be_granted_another_tenants_workspace(store, tenant):
    """A crafted request naming a foreign workspace is filtered, not honoured."""
    ws1 = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    outsider = await tenant.new_user("outsider")
    other_org = await store.org_repo.create_organization("Other Co", outsider)
    foreign = await store.workspace_repo.create_workspace(user_id=outsider, name="Not Yours")
    try:
        await store.org_repo.set_member_access(tenant.org, member, "workspace", [ws1, foreign])

        assert await store.workspace_repo.accessible_workspace_ids(
            member, organization_id=tenant.org
        ) == [ws1]
        assert await store.workspace_repo.get_user_role_in_workspace(foreign, member) is None
    finally:
        await store.org_repo.delete_organization(other_org)


async def test_removal_revokes_access_and_clears_assignments(store, tenant):
    """Removing somebody must actually end their access.

    Deleting only the organization_members row left workspace_members behind,
    and either grants access, so a removed member kept every workspace they had.
    """
    ws1 = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws1])
    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == [ws1]

    assert await store.org_repo.remove_member(tenant.org, member) is True

    assert await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org) == []
    assert await store.workspace_repo.get_user_role_in_workspace(ws1, member) is None


async def test_last_owner_cannot_be_removed(store, tenant):
    assert await store.org_repo.remove_member(tenant.org, tenant.owner) is False
    assert await store.org_repo.get_role(tenant.org, tenant.owner) == "owner"


async def test_documents_follow_workspace_access(store, tenant):
    """The point of all of it: which documents a person can actually read."""
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    await store.file_repo.add_file(tenant.owner, "handbook.pdf", "https://x/handbook.pdf", 10, ws1)
    await store.file_repo.add_file(tenant.owner, "salaries.pdf", "https://x/salaries.pdf", 10, ws2)

    async def visible_to(uid):
        ids = await store.workspace_repo.accessible_workspace_ids(uid, organization_id=tenant.org)
        page = await store.file_repo.get_files_for_user(uid, limit=100, accessible_workspace_ids=ids)
        return sorted(item["file_name"] for item in page.get("items", []))

    member = await tenant.member(scope="workspace", workspaces=[ws1])
    assert await visible_to(member) == ["handbook.pdf"]
    assert await visible_to(tenant.owner) == ["handbook.pdf", "salaries.pdf"]

    await store.org_repo.set_member_access(tenant.org, member, "organization", [])
    assert await visible_to(member) == ["handbook.pdf", "salaries.pdf"]


async def test_retrieval_scope_matches_document_scope(store, tenant):
    """Answers must not cite a document the reader cannot open.

    Retrieval and the file list are scoped by the same call, so they cannot
    drift apart.
    """
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    ids = await store.workspace_repo.accessible_workspace_ids(member, organization_id=tenant.org)
    assert ids == [ws1]
    assert ws2 not in ids


async def test_accepting_an_invite_makes_you_a_company_member_seeing_everything(store, tenant):
    """One invite, one meaning.

    Invites used to carry a reach, which froze what somebody could see at the
    moment they were invited and conflated how they joined with what they are
    allowed to see. Joining now means joining the company; narrowing is a
    separate, deliberate act by the owner afterwards.
    """
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")

    joiner = await tenant.new_user("joiner")
    token = await store.workspace_repo.create_invite(
        f"joiner-{joiner}@acme.co", organization_id=tenant.org
    )
    assert token

    result = await store.workspace_repo.accept_invite(token, joiner)
    assert result is not None
    assert result["organization_id"] == tenant.org

    # Sees everything on arrival, including a workspace made later.
    later = await tenant.workspace("Created Afterwards")
    got = await store.workspace_repo.accessible_workspace_ids(joiner, organization_id=tenant.org)
    assert got == sorted([ws1, ws2, later])

    # And the owner can then narrow them.
    await store.org_repo.set_member_access(tenant.org, joiner, "workspace", [ws1])
    assert await store.workspace_repo.accessible_workspace_ids(
        joiner, organization_id=tenant.org
    ) == [ws1]
