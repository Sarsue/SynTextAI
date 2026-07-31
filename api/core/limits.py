from typing import Any, Dict, Optional

from fastapi import HTTPException, status

from ..repositories.repository_manager import RepositoryManager

FREE_DOC_LIMIT = 5
FREE_STORAGE_LIMIT_BYTES = 500 * 1024 * 1024  # 500 MB
FREE_WORKSPACE_LIMIT = 1


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
        # An exempt organization is entitled without a subscription. This is
        # what carries demo access: prospects are invited into Osas Inc rather
        # than given a trial of their own.
        if await store.org_repo.is_billing_exempt(org_id):
            return {
                "entitled": True,
                "source": "exempt",
                "organization_id": org_id,
                "role": m["role"],
                "status": "exempt",
                "is_org_owner": is_org_owner,
                "is_member_only": is_member_only,
            }
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


async def _organization_for_workspace(
    store: RepositoryManager, workspace_id: Optional[int]
) -> Optional[int]:
    """Return the organization whose plan and usage govern this workspace."""
    if workspace_id is None:
        return None
    return await store.org_repo.get_organization_for_workspace(workspace_id)


async def assert_can_create_doc(
    store: RepositoryManager,
    user_id: int,
    new_doc_size_bytes: int,
    workspace_id: Optional[int] = None,
) -> None:
    """Enforce free-plan limits for document creation.

    Limits belong to the organization, so when uploading into a workspace the
    governing plan is the workspace's organization, not the uploader's.
    Without this, a staff member in a paid workspace would be measured against
    their own non-existent free plan and blocked after five documents.

    Raises HTTPException with 402 status code when limits are exceeded.
    """
    org_id = await _organization_for_workspace(store, workspace_id)

    if org_id is not None:
        status_str = await store.org_repo.get_subscription_status(org_id)
    else:
        status_str = await _get_subscription_status(store, user_id)
    if _is_premium_plan(status_str):
        # Premium/trial organizations are not restricted by these limits.
        return

    # Free plan. Count against the workspace when there is one, so two staff
    # sharing a free workspace share its allowance rather than getting one each.
    if workspace_id is not None:
        doc_count = await store.file_repo.count_files_for_workspace(workspace_id)
        total_bytes = await store.file_repo.total_storage_bytes_for_workspace(workspace_id)
    else:
        doc_count = await store.file_repo.count_files_for_user(user_id)
        total_bytes = await store.file_repo.total_storage_bytes_for_user(user_id)

    if doc_count >= FREE_DOC_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error_code": "DOC_LIMIT_REACHED",
                "message": "Free plan allows up to 5 documents. Delete a document or upgrade to add more.",
            },
        )

    if total_bytes + max(new_doc_size_bytes, 0) > FREE_STORAGE_LIMIT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error_code": "STORAGE_LIMIT_EXCEEDED",
                "message": "Free plan includes up to 500 MB storage. Delete files or upgrade to continue.",
            },
        )


async def assert_can_create_workspace(store: RepositoryManager, user_id: int) -> None:
    """Enforce free-plan limits for workspace creation.

    Free users may create only a single workspace. Trialing/active subscriptions
    are allowed multiple workspaces for now.
    """
    status_str = await _get_subscription_status(store, user_id)
    if _is_premium_plan(status_str):
        return

    count = await store.workspace_repo.count_workspaces_for_user(user_id)
    if count >= FREE_WORKSPACE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error_code": "WORKSPACE_LIMIT_REACHED",
                "message": "Free plan allows 1 workspace. Delete an existing workspace or upgrade to create more.",
            },
        )
