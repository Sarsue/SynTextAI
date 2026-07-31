"""What each role may do.

One table, consulted everywhere. Permission decisions used to be role strings
compared inline at nineteen separate call sites — `if role != "owner"`, `if role
not in ("owner", "admin")` — so adding a role meant finding all of them, and
missing one meant either a silent denial or a silent grant.

Adding a role is now a row in ROLE_CAPABILITIES. Adding a capability is a member
of Capability plus the roles that hold it. Neither requires touching a route.

The rule the product actually sells:

    owner    everything, including billing
    admin    can manage: upload and delete documents, manage workspaces and
             the people in them
    staff    can read: ask questions and read answers, change nothing

Read access is a separate question with a separate answer — see
accessible_workspace_ids, which decides *which workspaces* you may see. This
module decides *what you may do* in one you can already see. Both must pass.
"""
from enum import Enum
from typing import Dict, FrozenSet, Optional

from fastapi import HTTPException, status

from ..repositories.repository_manager import RepositoryManager


class Capability(str, Enum):
    # Reading
    READ = "read"

    # Documents
    UPLOAD_DOCUMENT = "upload_document"
    DELETE_DOCUMENT = "delete_document"

    # Workspaces
    CREATE_WORKSPACE = "create_workspace"
    EDIT_WORKSPACE = "edit_workspace"
    DELETE_WORKSPACE = "delete_workspace"

    # People
    INVITE_MEMBER = "invite_member"
    REMOVE_MEMBER = "remove_member"
    CHANGE_MEMBER_ACCESS = "change_member_access"

    # The organization itself
    RENAME_ORGANIZATION = "rename_organization"
    MANAGE_BILLING = "manage_billing"


_MUTATE_DOCUMENTS = {Capability.UPLOAD_DOCUMENT, Capability.DELETE_DOCUMENT}
_MUTATE_WORKSPACES = {
    Capability.CREATE_WORKSPACE,
    Capability.EDIT_WORKSPACE,
    Capability.DELETE_WORKSPACE,
}
_MUTATE_PEOPLE = {
    Capability.INVITE_MEMBER,
    Capability.REMOVE_MEMBER,
    Capability.CHANGE_MEMBER_ACCESS,
}

ROLE_CAPABILITIES: Dict[str, FrozenSet[Capability]] = {
    "owner": frozenset(Capability),
    # Everything except billing: an administrator runs the workspace, the owner
    # pays for it. Somebody has to be unable to cancel the subscription.
    "admin": frozenset(Capability) - {Capability.MANAGE_BILLING},
    # Read and ask, change nothing. Somebody who should be able to add
    # documents is an admin: managing and contributing are the same permission
    # here, because a knowledge base nobody may add to is not one worth having
    # a separate role for.
    #
    # 'member' is accepted as a synonym only so a row written before the
    # vocabulary was unified still resolves; the migration rewrites them.
    "staff": frozenset({Capability.READ}),
    "member": frozenset({Capability.READ}),
}

# Roles that administer the tenant. Kept here so "is this person an admin" has
# one definition rather than an inline tuple in each caller.
ADMIN_ROLES = frozenset({"owner", "admin"})


def capabilities_for(role: Optional[str]) -> FrozenSet[Capability]:
    """What a role may do. An unknown or absent role may do nothing."""
    return ROLE_CAPABILITIES.get((role or "").lower(), frozenset())


def can(role: Optional[str], capability: Capability) -> bool:
    return capability in capabilities_for(role)


def is_admin(role: Optional[str]) -> bool:
    return (role or "").lower() in ADMIN_ROLES


# Written for the person being refused, not for the developer. "Only the
# workspace owner can upload documents" tells somebody what to do next; "403
# Forbidden" does not.
_REFUSALS: Dict[Capability, str] = {
    Capability.READ: "You do not have access to this workspace.",
    Capability.UPLOAD_DOCUMENT: "Only an owner or admin can upload documents.",
    Capability.DELETE_DOCUMENT: "Only an owner or admin can delete documents.",
    Capability.CREATE_WORKSPACE: "Only an owner or admin can create workspaces.",
    Capability.EDIT_WORKSPACE: "Only an owner or admin can rename a workspace.",
    Capability.DELETE_WORKSPACE: "Only an owner or admin can delete a workspace.",
    Capability.INVITE_MEMBER: "Only an owner or admin can invite people.",
    Capability.REMOVE_MEMBER: "Only an owner or admin can remove people.",
    Capability.CHANGE_MEMBER_ACCESS: "Only an owner or admin can change who sees what.",
    Capability.RENAME_ORGANIZATION: "Only an owner or admin can rename the organization.",
    Capability.MANAGE_BILLING: "Only the owner can manage billing.",
}


def refusal(capability: Capability) -> str:
    return _REFUSALS.get(capability, "You do not have permission to do that.")


async def assert_workspace_capability(
    store: RepositoryManager,
    user_id: int,
    workspace_id: int,
    capability: Capability,
) -> str:
    """Raise 403 unless the user holds `capability` in this workspace.

    Returns the role, so a caller that also wants to branch on it does not have
    to look it up twice.
    """
    role = await store.workspace_repo.get_user_role_in_workspace(workspace_id, user_id)
    if not can(role, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=refusal(capability if role else Capability.READ),
        )
    return role


async def assert_organization_capability(
    store: RepositoryManager,
    user_id: int,
    organization_id: int,
    capability: Capability,
) -> str:
    """Raise 403 unless the user holds `capability` in this organization."""
    role = await store.org_repo.get_role(organization_id, user_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization.",
        )
    if not can(role, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=refusal(capability),
        )
    return role
