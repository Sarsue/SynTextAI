"""Say what deleting an account destroys, before it is deleted.

WHY THIS EXISTS

The owner holds the card and the Stripe customer, and deleting their account
cancels both. An organization left behind therefore has no payer, and an
unsubscribed organization cannot be used at all, so every organization they own
goes with them, other members and their documents included.

That is defensible and it is not something to discover afterwards. Handing
ownership to somebody staying was the alternative and is worse: it gives a
person a company nobody can pay for, and billing access they never asked for.

WHAT IS ASSERTED HERE

That the numbers put in front of somebody are the real ones. A warning that
undercounts is worse than none, because it is believed.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_it_counts_the_people_who_lose_access(store, tenant):
    await tenant.member("colleague-one")
    await tenant.member("colleague-two")

    impact = await store.org_repo.deletion_impact(tenant.owner)

    assert len(impact["organizations"]) == 1
    assert impact["organizations"][0]["organization_id"] == tenant.org
    assert impact["organizations"][0]["other_members"] == 2
    assert impact["loses_access"] == 2, "both colleagues lose the company"


async def test_it_counts_documents_across_every_workspace(store, tenant):
    a = await tenant.workspace("First")
    b = await tenant.workspace("Second")
    for ws in (a, b):
        await store.file_repo.add_file(
            user_id=tenant.owner, file_name=f"doc-{ws}.pdf", file_url="", workspace_id=ws
        )

    impact = await store.org_repo.deletion_impact(tenant.owner)

    assert impact["documents_deleted"] == 2, (
        "a count that stops at the first workspace understates what is destroyed"
    )
    assert impact["organizations"][0]["workspaces"] == 2


async def test_a_company_you_only_belong_to_is_not_yours_to_destroy(store, tenant):
    """Being a member is not owning. Their company must not appear in your
    warning, and deleting your account must not touch it."""
    member = await tenant.member("staff")

    impact = await store.org_repo.deletion_impact(member)

    assert impact["organizations"] == [], (
        "a staff member's deletion destroys no organization"
    )
    assert impact["loses_access"] == 0


async def test_an_owner_alone_destroys_only_their_own(store, tenant):
    impact = await store.org_repo.deletion_impact(tenant.owner)

    assert impact["loses_access"] == 0, "nobody else is in it"
    assert len(impact["organizations"]) == 1


async def test_it_changes_nothing(store, tenant):
    """Read-only. It is called to render a dialog somebody may well cancel."""
    await tenant.member("colleague")
    before = {m["user_id"] for m in await store.org_repo.list_members(tenant.org)}

    await store.org_repo.deletion_impact(tenant.owner)

    after = {m["user_id"] for m in await store.org_repo.list_members(tenant.org)}
    assert before == after
    assert await store.org_repo.get_role(tenant.org, tenant.owner) == "owner"
