"""What the API refuses.

The previous suite proves the access rules are right in the data layer. These
prove the routes actually apply them, which is a different question: a correct
rule that no endpoint consults protects nothing. Uploading was once
unauthorized entirely — any authenticated user could pass any workspace_id and
upload into a workspace they were not a member of, because only file ownership
was ever checked.

Authentication itself is stubbed. These are about what happens once the caller
is known.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --- reading a workspace's documents ----------------------------------------

async def test_listing_files_in_an_unreachable_workspace_is_refused(store, tenant, client):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    ok = await client.as_(member).get(f"/api/v1/files?workspace_id={ws1}")
    assert ok.status_code == 200

    denied = await client.as_(member).get(f"/api/v1/files?workspace_id={ws2}")
    assert denied.status_code == 403


async def test_outsider_cannot_read_another_tenants_workspace(store, tenant, client):
    ws = await tenant.workspace("Finance")
    outsider = await tenant.new_user("outsider")

    res = await client.as_(outsider).get(f"/api/v1/files?workspace_id={ws}")
    assert res.status_code == 403


# --- uploading ---------------------------------------------------------------

async def test_staff_cannot_upload(store, tenant, client):
    """Owners manage documents; staff ask questions. Enforced server side."""
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(member).post(
        f"/api/v1/files?workspace_id={ws}",
        files={"files": ("x.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert res.status_code == 403


# --- opening a document ------------------------------------------------------

async def test_access_url_refused_for_a_workspace_the_caller_cannot_read(store, tenant, client):
    """A signed URL is authorized by the document's workspace, not its uploader."""
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    file_id = await store.file_repo.add_file(
        tenant.owner, "salaries.pdf", "https://x/salaries.pdf", 10, ws2
    )
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    res = await client.as_(member).get(f"/api/v1/files/{file_id}/access-url")
    assert res.status_code == 403


# --- inviting ----------------------------------------------------------------

async def test_only_owner_can_invite_to_a_workspace(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(member).post(
        f"/api/v1/workspaces/{ws}/invites", json={"email": "colleague@acme.co"}
    )
    assert res.status_code == 403


async def test_only_admin_can_invite_to_the_organization(store, tenant, client):
    member = await tenant.member(scope="organization")

    res = await client.as_(member).post(
        f"/api/v1/organizations/{tenant.org}/invites", json={"email": "colleague@acme.co"}
    )
    assert res.status_code == 403


# --- changing who sees what --------------------------------------------------

async def test_member_cannot_change_anyone_s_access(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member("member-a", scope="workspace", workspaces=[ws])
    other = await tenant.member("member-b", scope="workspace", workspaces=[ws])

    res = await client.as_(member).patch(
        f"/api/v1/organizations/{tenant.org}/members/{other}/access",
        json={"scope": "organization", "workspace_ids": []},
    )
    assert res.status_code == 403


async def test_owner_can_change_access(store, tenant, client):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/organizations/{tenant.org}/members/{member}/access",
        json={"scope": "workspace", "workspace_ids": [ws2]},
    )
    assert res.status_code == 200
    assert await store.workspace_repo.accessible_workspace_ids(
        member, organization_id=tenant.org
    ) == [ws2]


async def test_workspace_scope_with_no_workspaces_is_rejected(store, tenant, client):
    """Refused rather than silently leaving somebody able to see nothing."""
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/organizations/{tenant.org}/members/{member}/access",
        json={"scope": "workspace", "workspace_ids": []},
    )
    assert res.status_code == 400


# --- removing ----------------------------------------------------------------

async def test_member_cannot_remove_anyone(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member("member-a", scope="workspace", workspaces=[ws])
    other = await tenant.member("member-b", scope="workspace", workspaces=[ws])

    res = await client.as_(member).delete(
        f"/api/v1/organizations/{tenant.org}/members/{other}"
    )
    assert res.status_code == 403


async def test_owner_cannot_remove_themselves(store, tenant, client):
    """Would leave a tenant nobody can administer."""
    res = await client.as_(tenant.owner).delete(
        f"/api/v1/organizations/{tenant.org}/members/{tenant.owner}"
    )
    assert res.status_code == 400
    assert await store.org_repo.get_role(tenant.org, tenant.owner) == "owner"


async def test_owner_can_remove_a_member_and_access_ends(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(tenant.owner).delete(
        f"/api/v1/organizations/{tenant.org}/members/{member}"
    )
    assert res.status_code == 200
    assert await store.workspace_repo.accessible_workspace_ids(
        member, organization_id=tenant.org
    ) == []


# --- reading the organization ------------------------------------------------

async def test_non_member_cannot_read_the_member_list(store, tenant, client):
    outsider = await tenant.new_user("outsider")
    res = await client.as_(outsider).get(f"/api/v1/organizations/{tenant.org}/members")
    assert res.status_code == 403


async def test_non_member_cannot_read_organization_context(store, tenant, client):
    outsider = await tenant.new_user("outsider")
    res = await client.as_(outsider).get(f"/api/v1/organizations/{tenant.org}/context")
    assert res.status_code == 403


# --- conversations -----------------------------------------------------------

async def test_cannot_start_a_conversation_in_an_unreachable_workspace(store, tenant, client):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    res = await client.as_(member).post(
        f"/api/v1/histories?title=hello&workspace_id={ws2}"
    )
    assert res.status_code == 403
