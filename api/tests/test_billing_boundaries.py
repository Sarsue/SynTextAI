"""What billing refuses, and whose money it is talking about.

Both rules here were learned the expensive way, against live Stripe, on a real
card. They are cheap to assert and were costly to discover, which is the whole
argument for asserting them.
"""
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _pay(store, organization_id, owner_id, status="active"):
    """Give an organization a subscription, without going near Stripe."""
    await store.user_repo.add_or_update_subscription(
        user_id=owner_id,
        organization_id=organization_id,
        stripe_customer_id=f"cus_test_{organization_id}",
        stripe_subscription_id=f"sub_test_{organization_id}",
        status=status,
    )


# --- deleting an account that still owes, or is owed -------------------------

async def test_owner_cannot_delete_their_account_while_the_company_pays(
    store, tenant, client
):
    """The $99 rule.

    Deleting an account cancels the subscription, deletes the Stripe customer
    and deletes every organization the person solely owns — and refunds
    nothing. An account deleted minutes after paying took the whole month with
    it, silently, with the cancel button unused on the same screen.
    """
    await _pay(store, tenant.org, tenant.owner)

    res = await client.as_(tenant.owner).delete("/api/v1/users")

    assert res.status_code == 409
    assert "cancel it first" in res.json()["detail"].lower()


async def test_owner_can_delete_once_the_subscription_is_cancelled(
    store, tenant, client
):
    """Refusing is a step, not a wall. Cancel, then leave."""
    await _pay(store, tenant.org, tenant.owner, status="canceled")

    res = await client.as_(tenant.owner).delete("/api/v1/users")

    assert res.status_code == 200


async def test_staff_can_always_delete_their_account(store, tenant, client):
    """Somebody else's subscription is not theirs to be blocked by.

    A staff member owns nothing and pays nothing. Blocking them on the
    company's plan would trap an employee inside their employer's billing.
    """
    await _pay(store, tenant.org, tenant.owner)
    member = await tenant.member(scope="organization")

    res = await client.as_(member).delete("/api/v1/users")

    assert res.status_code == 200


# --- whose entitlement is being reported -------------------------------------

async def test_status_for_an_organization_you_do_not_belong_to_is_refused(
    store, tenant, client
):
    """organization_id is a number off the query string, not a permission.

    Unchecked, any signed-in person could read any company's subscription
    status and renewal date by guessing a small integer.
    """
    await _pay(store, tenant.org, tenant.owner)
    outsider = await tenant.new_user("outsider")

    res = await client.as_(outsider).get(
        f"/api/v1/subscriptions/status?organization_id={tenant.org}"
    )

    assert res.status_code == 403


async def test_entitlement_is_the_organizations_not_the_persons(store, tenant, client):
    """An unpaid company is unpaid, whoever is looking at it.

    Entitlement was resolved per user, so somebody who owned a paying company
    was told an unpaid one was entitled too. Its billing page then said "no plan
    yet" in the banner and "your subscription is active" in the panel below,
    with the plan picker hidden because the page believed there was nothing to
    buy.
    """
    await _pay(store, tenant.org, tenant.owner)

    # A second company, unpaid, owned by somebody who is also staff in the paid
    # one. The person is entitled; this company is not.
    other_owner = await tenant.new_user("other-owner")
    unpaid = await store.org_repo.create_organization("Unpaid Co", other_owner)
    await store.org_repo.add_member(tenant.org, other_owner, role="staff")
    await store.org_repo.set_member_access(tenant.org, other_owner, "organization", [])

    try:
        mine = await client.as_(other_owner).get(
            f"/api/v1/subscriptions/status?organization_id={unpaid}"
        )
        assert mine.status_code == 200
        assert mine.json()["entitled"] is False
        assert mine.json()["subscription_status"] == "none"

        # The same person, looking at the company that does pay, is covered by
        # it — that is what makes staff work at all.
        theirs = await client.as_(other_owner).get(
            f"/api/v1/subscriptions/status?organization_id={tenant.org}"
        )
        assert theirs.status_code == 200
        assert theirs.json()["entitled"] is True
        assert theirs.json()["is_member_only"] is True
    finally:
        await store.org_repo.delete_organization(unpaid)
