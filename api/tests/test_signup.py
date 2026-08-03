"""Signing up produces exactly one company, however many times it is asked.

One email owning two organizations is not a cosmetic problem. The subscription
attaches to one of them, so the customer pays for a company they are not
standing in, is refused entry to the one they are, and is told their plan needs
attention while their card has already been charged. It happened in production
on the first live signup.

The cause was two POST /users?intent=signup arriving together: the auth listener
sent one when Firebase reported the account, and the sign-up screen sent another
when the popup closed. Both asked whether this person already owned an
organization, both asked before either had inserted its membership row, and both
created one.

The duplicate request is gone, but that is not what these tests hold up. A
double-click, a retry, or a second tab reproduces it just as well, and
check-then-insert cannot be made atomic across two requests from the
application. The guarantee lives in a partial unique index on
organization_members, so the second insert loses and its caller resolves to the
organization that already exists.
"""
import asyncio

import pytest
import pytest_asyncio

from api.routes.users import _start_organization

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _owned(store, user_id):
    return [
        m["organization_id"]
        for m in await store.org_repo.get_memberships(user_id)
        if m["role"] == "owner"
    ]


@pytest_asyncio.fixture(loop_scope="session")
async def signup_user(store):
    """A brand new person, removed afterwards along with anything they own."""
    email = "signup-probe@syntextai.test"
    user_id = await store.user_repo.add_user(email, "signup probe")
    yield user_id, email
    for org in set(await _owned(store, user_id)):
        await store.org_repo.delete_organization(org)
    await store.user_repo.delete_user_account(user_id)


async def test_signing_up_twice_at_once_creates_one_organization(store, signup_user):
    """The production bug, reproduced directly."""
    user_id, email = signup_user

    results = await asyncio.gather(
        _start_organization(store, user_id, email),
        _start_organization(store, user_id, email),
        _start_organization(store, user_id, email),
    )

    owned = await _owned(store, user_id)
    assert len(owned) == 1, f"expected one owned organization, got {owned}"
    # Every caller must be told about the same company, not just left with one
    # row in the table. A caller handed None would report signup as failed.
    assert set(results) == {owned[0]}


async def test_signing_up_again_later_returns_the_same_organization(store, signup_user):
    """Idempotent when the requests are sequential, which is the ordinary case."""
    user_id, email = signup_user

    first = await _start_organization(store, user_id, email)
    second = await _start_organization(store, user_id, email)

    assert first == second
    assert await _owned(store, user_id) == [first]


async def test_owning_one_company_does_not_prevent_joining_others(store, signup_user, tenant):
    """The rule is one *owned* organization, not one membership.

    Somebody can own their own company and be staff in a customer's. Constraining
    that would break the case the tenant model exists for.
    """
    user_id, email = signup_user
    own = await _start_organization(store, user_id, email)

    await store.org_repo.add_member(tenant.org, user_id, role="staff")

    memberships = {m["organization_id"]: m["role"] for m in await store.org_repo.get_memberships(user_id)}
    assert memberships[own] == "owner"
    assert memberships[tenant.org] == "staff"
