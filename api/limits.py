from datetime import date
from typing import Dict

from fastapi import HTTPException, status

from .repositories.repository_manager import RepositoryManager

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


async def assert_can_create_doc(store: RepositoryManager, user_id: int, new_doc_size_bytes: int) -> None:
    """Enforce free-plan limits for document creation.

    Raises HTTPException with 402 status code when limits are exceeded.
    """
    status_str = await _get_subscription_status(store, user_id)
    if _is_premium_plan(status_str):
        # Premium/trial users are not restricted by these limits.
        return

    # Enforce document count limit
    doc_count = await store.file_repo.count_files_for_user(user_id)
    if doc_count >= FREE_DOC_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error_code": "DOC_LIMIT_REACHED",
                "message": "Free plan allows up to 5 documents. Delete a document or upgrade to add more.",
            },
        )

    # Enforce total storage limit
    total_bytes = await store.file_repo.total_storage_bytes_for_user(user_id)
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
