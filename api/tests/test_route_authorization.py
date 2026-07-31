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

async def test_there_is_no_per_workspace_invite(store, tenant, client):
    """One kind of invite only.

    A per-workspace invite froze what somebody could see at the moment they
    were invited. Joining and being granted access are separate acts now.
    """
    ws = await tenant.workspace("Finance")
    res = await client.as_(tenant.owner).post(
        f"/api/v1/workspaces/{ws}/invites", json={"email": "colleague@acme.co"}
    )
    assert res.status_code == 405 or res.status_code == 404


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


async def test_history_list_excludes_workspaces_the_caller_lost(store, tenant, client):
    ws1 = await tenant.workspace("Finance")
    ws2 = await tenant.workspace("Dentist")
    member = await tenant.member(scope="organization")

    keep = await store.chat_repo.add_chat_history("kept", member, ws1)
    gone = await store.chat_repo.add_chat_history("gone", member, ws2)

    res = await client.as_(member).get(f"/api/v1/histories?organization_id={tenant.org}")
    assert res.status_code == 200
    assert sorted(h["id"] for h in res.json()) == sorted([keep, gone])

    await store.org_repo.set_member_access(tenant.org, member, "workspace", [ws1])

    res = await client.as_(member).get(f"/api/v1/histories?organization_id={tenant.org}")
    assert res.status_code == 200
    assert [h["id"] for h in res.json()] == [keep]


async def test_member_can_send_a_message_in_a_workspace_they_can_see(store, tenant, client):
    """An organization-wide member owns no workspace and is assigned to none.

    The guard called list_workspaces_for_user, which answers a different
    question, so it returned nothing and every message was refused with
    'Workspace not found'.
    """
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="organization")
    history = await store.chat_repo.add_chat_history("hello", member, ws)

    res = await client.as_(member).post(
        f"/api/v1/messages?message=hi&history_id={history}&language=english&workspace_id={ws}"
    )
    assert res.status_code != 404, res.text


async def test_context_reports_capabilities_not_role_guesses(store, tenant, client):
    """The UI hides exactly what the backend would refuse.

    Both used to derive the answer separately from a role string, so they could
    disagree — a button shown that then 403s, or hidden when it would have
    worked.
    """
    member = await tenant.member(scope="organization")

    res = await client.as_(member).get(f"/api/v1/organizations/{tenant.org}/context")
    assert res.status_code == 200
    body = res.json()
    assert body["can_manage_billing"] is False
    assert body["can_manage_members"] is False
    assert body["can_manage_documents"] is False
    assert body["capabilities"] == ["read"]

    res = await client.as_(tenant.owner).get(f"/api/v1/organizations/{tenant.org}/context")
    body = res.json()
    assert body["can_manage_billing"] is True
    assert "manage_billing" in body["capabilities"]


async def test_owner_can_change_role_and_access_in_one_request(store, tenant, client):
    """One decision, one call.

    Sending them separately leaves a moment where role and access disagree,
    and makes the UI do two round trips for what the owner sees as one change.
    """
    ws1 = await tenant.workspace("Finance")
    await tenant.workspace("Dentist")
    member = await tenant.member(scope="workspace", workspaces=[ws1])

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/organizations/{tenant.org}/members/{member}/access",
        json={"scope": "organization", "workspace_ids": [], "role": "admin"},
    )
    assert res.status_code == 200, res.text
    assert await store.org_repo.get_role(tenant.org, member) == "admin"


async def test_role_alone_can_be_changed(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/organizations/{tenant.org}/members/{member}/access",
        json={"role": "admin"},
    )
    assert res.status_code == 200, res.text
    assert await store.org_repo.get_role(tenant.org, member) == "admin"
    # Promotion carries reach: an admin administers the whole tenant.
    assert await store.workspace_repo.accessible_workspace_ids(
        member, organization_id=tenant.org
    ) == [ws]


async def test_nobody_can_change_their_own_role(store, tenant, client):
    """Otherwise an admin promotes themselves by degrees."""
    ws = await tenant.workspace("Finance")
    admin = await tenant.member(scope="organization")
    await store.org_repo.set_member_role(tenant.org, admin, "admin")

    res = await client.as_(admin).patch(
        f"/api/v1/organizations/{tenant.org}/members/{admin}/access",
        json={"role": "admin"},
    )
    assert res.status_code == 400


async def test_ownership_cannot_be_granted_through_this_path(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="workspace", workspaces=[ws])

    res = await client.as_(tenant.owner).patch(
        f"/api/v1/organizations/{tenant.org}/members/{member}/access",
        json={"role": "owner"},
    )
    assert res.status_code == 400
    assert await store.org_repo.get_role(tenant.org, member) == "staff"


async def test_an_owner_s_role_cannot_be_changed(store, tenant, client):
    """Including by an admin, which would otherwise strip the billing owner."""
    admin = await tenant.member(scope="organization")
    await store.org_repo.set_member_role(tenant.org, admin, "admin")

    res = await client.as_(admin).patch(
        f"/api/v1/organizations/{tenant.org}/members/{tenant.owner}/access",
        json={"role": "staff"},
    )
    assert res.status_code == 400
    assert await store.org_repo.get_role(tenant.org, tenant.owner) == "owner"


async def test_a_member_cannot_create_a_workspace(store, tenant, client):
    """The hole this class of bug leaves.

    Only the plan's workspace limit was enforced, never the role, so any member
    of a paying organization could create workspaces in it — and became owner
    of what they created, which is a privilege escalation dressed as a feature.
    """
    await tenant.workspace("Finance")
    member = await tenant.member(scope="organization")

    res = await client.as_(member).post(
        f"/api/v1/workspaces?organization_id={tenant.org}", json={"name": "Mine"}
    )
    assert res.status_code == 403, res.text


async def test_a_member_cannot_rename_or_delete_a_workspace(store, tenant, client):
    ws = await tenant.workspace("Finance")
    member = await tenant.member(scope="organization")

    renamed = await client.as_(member).patch(
        f"/api/v1/workspaces/{ws}", json={"name": "Renamed"}
    )
    assert renamed.status_code == 403

    deleted = await client.as_(member).delete(f"/api/v1/workspaces/{ws}")
    assert deleted.status_code == 403


async def test_an_admin_can_rename_a_workspace_they_do_not_own(store, tenant, client):
    """Admins own nothing yet administer everything.

    The check read an ownership list, which is empty for them, so an admin was
    refused on a workspace they are meant to manage.
    """
    ws = await tenant.workspace("Finance")
    admin = await tenant.member(scope="organization")
    await store.org_repo.set_member_role(tenant.org, admin, "admin")

    res = await client.as_(admin).patch(
        f"/api/v1/workspaces/{ws}", json={"name": "Renamed by admin"}
    )
    assert res.status_code == 200, res.text


async def test_signing_in_never_creates_an_organization(store, tenant, client):
    """Signing in enters what you belong to. It does not make you an owner.

    An organization used to be created for anyone without a pending invite, so
    signing in was enough to become an owner of something.
    """
    import uuid
    from api.routes import users as users_route

    email = f"signin-{uuid.uuid4().hex[:8]}@acme.co"

    async def fake_token():
        return {"email": email, "name": email, "uid": "gc-test"}

    from api.app import app
    app.dependency_overrides[users_route.get_firebase_user_info_from_token] = fake_token
    try:
        res = await client.as_(tenant.owner).post("/api/v1/users?intent=signin")
        assert res.status_code in (200, 201), res.text
        assert res.json().get("organization_id") is None

        uid = await store.user_repo.get_user_id_from_email(email)
        assert await store.org_repo.get_memberships(uid) == []
    finally:
        app.dependency_overrides.pop(users_route.get_firebase_user_info_from_token, None)
        uid = await store.user_repo.get_user_id_from_email(email)
        if uid:
            await store.user_repo.delete_user_account(uid)


async def test_an_existing_member_can_sign_up_and_own_a_company(store, tenant, client):
    """The case that had no path at all.

    Somebody invited into a company could never start one of their own: the
    endpoint returned early on 'user already exists', so every way in led back
    to the organization that had invited them.
    """
    from api.routes import users as users_route
    from api.app import app

    member = await tenant.member(scope="organization")

    # The real address on the row, not a guess: signing up is keyed to the
    # email, so a mismatch silently creates a second account instead of
    # exercising the case under test.
    from sqlalchemy import select
    from api.models.orm_models import User
    async with store.user_repo.get_async_session() as session:
        email = (await session.execute(
            select(User.email).where(User.id == member)
        )).scalar_one()

    async def fake_token():
        return {"email": email, "name": email, "uid": f"gc-{member}"}

    app.dependency_overrides[users_route.get_firebase_user_info_from_token] = fake_token
    try:
        res = await client.as_(member).post("/api/v1/users?intent=signup")
        assert res.status_code == 200, res.text
        own_org = res.json().get("organization_id")
        assert own_org is not None

        roles = {
            m["organization_id"]: m["role"]
            for m in await store.org_repo.get_memberships(member)
        }
        # Still staff where they were invited, owner of what they just started.
        assert roles[tenant.org] == "staff"
        assert roles[own_org] == "owner"
        await store.org_repo.delete_organization(own_org)
    finally:
        app.dependency_overrides.pop(users_route.get_firebase_user_info_from_token, None)
