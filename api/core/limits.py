from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from ..repositories.repository_manager import RepositoryManager

# An organization has a subscription or it has nothing.
#
# There was a free tier here: five documents, 500 MB, one workspace. It belongs
# to a product that no longer exists. Signing up takes a card, the trial was
# dropped rather than built, and a demo is arranged by adding the person to an
# organization that already pays. So nobody can arrive at a free allowance by
# any route the product offers, and the code enforcing one could only fire
# against a company whose payment had lapsed — telling somebody who had been
# paying to "upgrade from free" and quietly permitting a sixth document to be
# the thing that stopped them, rather than the lapse itself.
#
# One question now: is this organization subscribed. Yes means the plan's seats
# apply and nothing else is capped. No means nothing at all.
_NO_SUBSCRIPTION = {
    "error_code": "SUBSCRIPTION_REQUIRED",
    "message": "This organization does not have an active subscription.",
}


async def _get_subscription_status(store: RepositoryManager, user_id: int) -> str:
    """Return the current subscription status string for a user.

    Falls back to 'none' if no subscription is found.
    """
    subscription_data = await store.user_repo.get_subscription(user_id)
    if not subscription_data:
        return "none"

    subscription, _ = subscription_data
    return subscription.get("status") or "none"


def _is_premium_plan(status: str) -> bool:
    """Return True if the status represents an entitled (paid/trial) plan.

    Business rule: only 'active' and 'trialing' grant premium access.
    All other states ('none', 'canceled', 'past_due', 'unpaid', etc.) are treated as restricted.
    """
    normalized = (status or "none").lower()
    return normalized in {"active", "trialing"}


async def resolve_entitlement(store: RepositoryManager, user_id: int) -> Dict[str, Any]:
    """Work out what plan actually applies to this user.

    Entitlement is a property of the *organization*, not the individual. The
    public pricing is "one price per company, add unlimited staff", so an
    invited staff member is covered by whoever owns the workspace they were
    invited into. They never need their own subscription and must never be sent
    through billing onboarding.

    Returns a dict with:
        entitled        True if premium features are available
        source          'own' when the user pays, 'workspace' when inherited,
                        None when not entitled
        organization_id the tenant whose plan governs, i.e. the account that is
                        charged and counted against
        role            the user's role in that organization
        status          the governing subscription status string
        is_org_owner    True if they own at least one workspace, i.e. they are
                        an administrator of an organization and billing is
                        theirs to manage
        is_member_only  True if they belong to workspaces but own none. A pure
                        invitee. They must never be shown billing, and must
                        never be asked to fix somebody else's lapsed plan.
    """
    memberships = await store.org_repo.get_memberships(user_id)

    # Read straight off organization_members. This used to be inferred from
    # "owns zero workspaces", which was a guess that broke as soon as an invitee
    # created a workspace of their own.
    administers = [m for m in memberships if m["role"] in ("owner", "admin")]
    is_org_owner = any(m["role"] == "owner" for m in memberships)
    is_member_only = bool(memberships) and not administers

    # Prefer an organization the user administers, so an owner sees their own
    # plan rather than one inherited from a company they merely belong to.
    ordered = administers + [m for m in memberships if m not in administers]
    for m in ordered:
        org_id = m["organization_id"]
        org_status = await store.org_repo.get_subscription_status(org_id)
        if _is_premium_plan(org_status):
            return {
                "entitled": True,
                "source": "own" if m["role"] == "owner" else "organization",
                "organization_id": org_id,
                "role": m["role"],
                "status": org_status,
                "is_org_owner": is_org_owner,
                "is_member_only": is_member_only,
            }

    primary = ordered[0] if ordered else None
    return {
        "entitled": False,
        "source": None,
        "organization_id": primary["organization_id"] if primary else None,
        "role": primary["role"] if primary else None,
        "status": (
            await store.org_repo.get_subscription_status(primary["organization_id"])
            if primary
            else "none"
        ),
        "is_org_owner": is_org_owner,
        "is_member_only": is_member_only,
    }


async def entitlement_for_organization(
    store: RepositoryManager, user_id: int, organization_id: int
) -> Dict[str, Any]:
    """What this one organization is entitled to, and what this user is in it.

    resolve_entitlement answers a question about a person: is there any company
    of theirs that pays. That is the right question when no organization is
    named, and the wrong one when one is. Somebody who owns a paying company and
    a second unpaid one was told the unpaid one was entitled, because they
    themselves were — so its billing page said "no plan yet" in the banner and
    "your subscription is active" in the panel below.

    Membership is not checked here; the caller has already established the user
    may see this organization.
    """
    status_str = await store.org_repo.get_subscription_status(organization_id)
    role = await store.org_repo.get_role(organization_id, user_id)
    entitled = _is_premium_plan(status_str)

    return {
        "entitled": entitled,
        # Whose money it is: yours when you own this company, the company's when
        # you were invited into it.
        "source": ("own" if role == "owner" else "organization") if entitled else None,
        "organization_id": organization_id,
        "role": role,
        "status": status_str,
        "is_org_owner": role == "owner",
        # A pure invitee here, whatever they may own elsewhere. Billing belongs
        # to this company's owner, and they must never be asked to fix it.
        "is_member_only": role not in ("owner", "admin"),
    }


async def _organization_for_workspace(
    store: RepositoryManager, workspace_id: Optional[int]
) -> Optional[int]:
    """Return the organization whose plan and usage govern this workspace."""
    if workspace_id is None:
        return None
    return await store.org_repo.get_organization_for_workspace(workspace_id)


async def _assert_subscribed(
    store: RepositoryManager, user_id: int, organization_id: Optional[int]
) -> None:
    """Raise 402 unless the governing organization is paying.

    The plan belongs to the organization, because the owner is the account that
    signed up and holds the card. Measuring the caller instead charged a staff
    member for their own non-existent plan and refused them work their company
    had already paid for.
    """
    if organization_id is not None:
        status_str = await store.org_repo.get_subscription_status(organization_id)
    else:
        status_str = await _get_subscription_status(store, user_id)

    if not _is_premium_plan(status_str):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=_NO_SUBSCRIPTION,
        )


async def assert_can_create_doc(
    store: RepositoryManager,
    user_id: int,
    workspace_id: Optional[int] = None,
) -> None:
    """A document may be added to a workspace whose organization is subscribed.

    The governing organization is the workspace's, not the uploader's, so a
    staff member uploading into a company that pays is covered by that company.
    """
    org_id = await _organization_for_workspace(store, workspace_id)
    await _assert_subscribed(store, user_id, org_id)


async def assert_can_create_workspace(
    store: RepositoryManager, user_id: int, organization_id: Optional[int] = None
) -> None:
    """A workspace may be created inside a subscribed organization."""
    await _assert_subscribed(store, user_id, organization_id)


async def assert_can_ask(
    store: RepositoryManager,
    user_id: int,
    workspace_id: Optional[int] = None,
) -> None:
    """A question may be asked of a workspace whose organization is subscribed.

    This was the hole. Uploading a document and creating a workspace both went
    through _assert_subscribed; asking a question did not, so an organization
    that had never paid could still send messages. Every one of them ran the
    whole pipeline — retrieval, the coverage loop, generation — and the model
    call at the end is metered and billed to us. Measured on an unpaid tenant:
    7.3 seconds of work and a generated answer, for free.

    It was easy to miss because the unpaid tenant looks harmless: it cannot
    upload, so it has no documents, so every answer is "I couldn't find relevant
    information in your documents." Nothing leaks. It just costs money, quietly,
    and it means an account that never paid still gets to use the product.

    Same rule as documents: the governing organization is the workspace's, not
    the asker's, so staff are covered by the company that pays for them.
    """
    org_id = await _organization_for_workspace(store, workspace_id)
    await _assert_subscribed(store, user_id, org_id)
