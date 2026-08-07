"""Workspace API routes for managing user workspaces."""

import asyncio
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Path, Request
from pydantic import BaseModel, EmailStr, Field

from ..repositories.repository_manager import RepositoryManager
from ..core.limits import assert_can_create_workspace
from ..core.permissions import (
    Capability,
    assert_organization_capability,
    assert_workspace_capability,
    is_admin,
)
from ..core.seats import sync_seats_to_stripe
from ..core.utils import delete_workspace_objects
from ..core.auth import authenticate_user, get_store

logger = logging.getLogger(__name__)

workspaces_router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str

class WorkspaceUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="New workspace name")


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    user_id: int
    # What you are here. Defaulting to "owner" made a field that failed to
    # arrive the most privileged one; assume the least instead.
    role: Optional[str] = "staff"
    # Whether you may run this workspace: true for its owner and for an admin
    # of the organization. Declared, because response_model silently drops
    # anything it does not name — the route set this and Pydantic removed it on
    # the way out, so the Team button disappeared for the owner of the company.
    can_manage: bool = False
    # How many documents deleting this would destroy. Declared here or
    # response_model drops it, exactly as it dropped can_manage.
    document_count: int = 0
    created_at: str
    updated_at: str


class InviteRequest(BaseModel):
    email: EmailStr


class MemberResponse(BaseModel):
    user_id: int
    email: str
    role: str
    joined_at: Optional[str]


class PendingInviteResponse(BaseModel):
    id: int
    email: str
    expires_at: str


class InviteInfoResponse(BaseModel):
    # Both None for an organization-wide invite, which names no workspace.
    workspace_id: Optional[int] = None
    workspace_name: Optional[str] = None
    # People join a company, not a folder, so the landing page names this.
    organization_name: Optional[str] = None
    email: str
    status: str


@workspaces_router.get("", response_model=Dict[str, List[WorkspaceResponse]])
async def list_workspaces(
    organization_id: Optional[int] = Query(None, description="Active organization; scopes results to one tenant"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    List all workspaces for the authenticated user.
    
    Returns a list of workspace objects with id, name, user_id, and timestamps.
    """
    try:
        user_id = user_data["user_id"]
        # Driven by what the user may see, scoped to the tenant they are
        # working in, so somebody who belongs to two companies never sees both
        # companies' workspaces side by side with no boundary between them.
        workspaces = await store.workspace_repo.list_accessible_workspaces(
            user_id, organization_id=organization_id
        )

        counts = await store.workspace_repo.document_counts(
            [ws["id"] for ws in workspaces]
        )

        # Convert datetime objects to ISO strings
        items = [
            {
                "id": ws["id"],
                "name": ws["name"],
                "user_id": ws["user_id"],
                # Falling back to "owner" made a missing role the most
                # privileged one. Assume the least instead; the backend refuses
                # regardless, and this only decides what the UI offers.
                "role": ws.get("role", "staff"),
                "can_manage": ws.get("can_manage", False),
                "document_count": counts.get(ws["id"], 0),
                "created_at": ws["created_at"].isoformat() if ws.get("created_at") else None,
                "updated_at": ws["updated_at"].isoformat() if ws.get("updated_at") else None,
            }
            for ws in workspaces
        ]
        
        return {"items": items}
    
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error listing workspaces for user {user_data.get('user_id')}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve workspaces"
        )


@workspaces_router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkspaceResponse)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    organization_id: Optional[int] = Query(None, description="Organization to create it in"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Create a new workspace in the caller's organization.

    Requires permission to create workspaces, which a read-only member does not
    have. This check was missing entirely: only the plan's workspace limit was
    enforced, so any member of a paying organization could create workspaces in
    it — and became owner of what they created.
    """
    try:
        user_id = user_data["user_id"]

        org_id = organization_id
        if org_id is None:
            # Fall back to an organization they administer, so a caller who
            # omits it cannot land in one where they are only a member.
            memberships = await store.org_repo.get_memberships(user_id)
            org_id = next(
                (m["organization_id"] for m in memberships if is_admin(m["role"])),
                memberships[0]["organization_id"] if memberships else None,
            )
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of an organization.",
            )

        await assert_organization_capability(store, user_id, org_id, Capability.CREATE_WORKSPACE)

        # Enforce workspace creation limits against the organization's plan,
        # which is the one being paid for.
        await assert_can_create_workspace(store, user_id, organization_id=org_id)
        
        # Create the workspace
        workspace_id = await store.workspace_repo.create_workspace(
            user_id=user_id,
            name=workspace_data.name
        )
        
        if not workspace_id:
            logger.error(f"Failed to create workspace for user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create workspace"
            )
        
        # Fetch the created workspace to return full details
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
        created_workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
        
        if not created_workspace:
            logger.error(f"Could not retrieve created workspace {workspace_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Workspace created but could not be retrieved"
            )
        
        return {
            "id": created_workspace["id"],
            "name": created_workspace["name"],
            "user_id": created_workspace["user_id"],
            "created_at": created_workspace["created_at"].isoformat() if created_workspace.get("created_at") else None,
            "updated_at": created_workspace["updated_at"].isoformat() if created_workspace.get("updated_at") else None,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating workspace for user {user_data.get('user_id')}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create workspace"
        )


@workspaces_router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: int = Path(..., description="ID of the workspace to update"),
    workspace_data: WorkspaceUpdate = None,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Update a workspace name.
    
    Only the workspace owner can update it.
    """
    try:
        user_id = user_data["user_id"]

        # Membership alone used to pass this, so any invited staff member could
        # rename the owner's workspace. Checked against the capability table
        # rather than an ownership list, which excluded organization admins who
        # own nothing yet administer everything.
        await assert_workspace_capability(store, user_id, workspace_id, Capability.EDIT_WORKSPACE)

        # Update the workspace
        success = await store.workspace_repo.update_workspace(
            workspace_id=workspace_id,
            name=workspace_data.name
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update workspace"
            )
        
        # Read back through what the caller may see, not what they own: an
        # admin owns nothing, so the ownership list came back empty and the
        # lookup below returned None, failing the request with a 500 after the
        # rename had already succeeded.
        workspaces = await store.workspace_repo.list_accessible_workspaces(user_id)
        updated_workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
        if not updated_workspace:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Workspace renamed but could not be read back",
            )

        logger.info(f"Workspace {workspace_id} updated by user {user_id}")

        return {
            "id": updated_workspace["id"],
            "name": updated_workspace["name"],
            "user_id": updated_workspace["user_id"],
            "created_at": updated_workspace["created_at"].isoformat() if updated_workspace.get("created_at") else None,
            "updated_at": updated_workspace["updated_at"].isoformat() if updated_workspace.get("updated_at") else None,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workspace {workspace_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update workspace"
        )


@workspaces_router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: int = Path(..., description="ID of the workspace to delete"),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Delete a workspace and all its files.
    
    Only the workspace owner can delete it.
    Cannot delete the last workspace (user must have at least 1).
    """
    try:
        user_id = user_data["user_id"]

        # Membership alone used to pass this, so an invited staff member could
        # permanently delete the owner's workspace and, by cascade, every
        # document in it.
        await assert_workspace_capability(store, user_id, workspace_id, Capability.DELETE_WORKSPACE)

        # An organization must keep at least one workspace, or everyone in it
        # is left with nowhere to put a document and no way back.
        #
        # This counted workspaces where the caller's role read "owner", which
        # that list reported for admins too. An admin owns none, so the count
        # was made up of the owner's workspaces and the guard let them delete
        # the organization's last one. The organization is what the rule was
        # always about; the caller's own tally never expressed it.
        workspaces = await store.workspace_repo.list_accessible_workspaces(user_id)
        workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
        organization_id = workspace.get("organization_id") if workspace else None
        if organization_id is not None:
            remaining = await store.workspace_repo.count_workspaces_in_organization(
                organization_id
            )
            if remaining <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete the last workspace. Create another one first."
                )

        # Nobody may be left with nothing. Deleting a workspace cascades its
        # workspace_members rows away in silence, and for somebody scoped to
        # workspaces rather than to the organization those rows are the whole of
        # their access. They would sign in to a working account that can see no
        # documents at all, with no error and no way to fix it themselves.
        #
        # Named rather than counted, because "this workspace has members" does
        # not tell the owner the thing they need to act on, and members who keep
        # access elsewhere are not a reason to refuse anything.
        stranded = await store.workspace_repo.members_stranded_by_deleting(workspace_id)
        if stranded:
            names = ", ".join(m["name"] for m in stranded)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Deleting this workspace would leave {names} with no workspace "
                    "at all. Give them access to another workspace, or remove them "
                    "from the company, then delete this one."
                ),
            )

        # Delete the workspace (files will cascade delete)
        success = await store.workspace_repo.delete_workspace(workspace_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete workspace"
            )

        # And its documents. The rows cascaded, but the objects did not: they
        # stayed in the bucket after the customer deleted the workspace, still
        # holding whatever they had uploaded. Deleting a prefix is the whole
        # reason storage mirrors workspaces; this was the one place that knew to
        # do it and did not.
        #
        # After the rows, deliberately. Objects removed first and a failed row
        # delete would leave documents listed in the app whose bytes are gone;
        # this way a failure leaves recoverable orphans instead, and says so.
        removed = await asyncio.to_thread(delete_workspace_objects, workspace_id)
        logger.info(f"Removed {removed} stored object(s) for workspace {workspace_id}")

        logger.info(f"Workspace {workspace_id} deleted by user {user_id}")
        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting workspace {workspace_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete workspace"
        )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

@workspaces_router.get("/{workspace_id}/members")
async def list_members(
    workspace_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """List members and pending invites. Requires permission to manage people."""
    user_id = user_data["user_id"]
    await assert_workspace_capability(store, user_id, workspace_id, Capability.INVITE_MEMBER)

    members = await store.workspace_repo.list_members(workspace_id)
    invites = await store.workspace_repo.list_pending_invites(workspace_id)

    return {
        "members": [
            {**m, "joined_at": m["joined_at"].isoformat() if m.get("joined_at") else None}
            for m in members
        ],
        "pending_invites": [
            {**i, "expires_at": i["expires_at"].isoformat() if i.get("expires_at") else None}
            for i in invites
        ],
    }


@workspaces_router.delete("/{workspace_id}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    workspace_id: int = Path(...),
    target_user_id: int = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Remove a staff member from one workspace."""
    user_id = user_data["user_id"]
    await assert_workspace_capability(store, user_id, workspace_id, Capability.REMOVE_MEMBER)
    if target_user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot remove themselves")

    await store.workspace_repo.remove_member(workspace_id, target_user_id)

    # Seat removal must be immediate, not deferred to renewal: a customer who
    # removes someone should stop paying for them straight away.
    organization_id = await store.org_repo.get_organization_for_workspace(workspace_id)
    if organization_id is not None:
        await sync_seats_to_stripe(store, organization_id, reason="member removed")
    return None


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

# Deliberately no per-workspace invite.
#
# There is one kind of invite: it makes somebody a member of the organization,
# able to see every workspace. What they see after that is set per member, by
# the owner, and can be changed at any time. Two invite types froze the answer
# at the moment of invitation and conflated how somebody joined with what they
# are allowed to see. See POST /api/v1/organizations/{id}/invites.


@workspaces_router.get("/invites/{token}", response_model=InviteInfoResponse)
async def get_invite_info(
    token: str = Path(...),
    store: RepositoryManager = Depends(get_store),
):
    """Public endpoint — validate a token and return workspace info before login."""
    invite = await store.workspace_repo.get_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found or expired")
    if invite["status"] != "pending":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=f"Invite is {invite['status']}")
    from datetime import datetime
    if invite["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite has expired")

    return {
        "workspace_id": invite["workspace_id"],
        "workspace_name": invite["workspace_name"],
        "organization_name": invite.get("organization_name"),
        "email": invite["email"],
        "status": invite["status"],
    }


@workspaces_router.post("/invites/{token}/accept")
async def accept_invite(
    token: str = Path(...),
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Authenticated user accepts an invite and joins the workspace.

    The invite token is an unguessable UUID4, but that alone doesn't guarantee
    the person accepting is the person the invite was actually sent to, a
    forwarded email, a pasted link, or a leaked URL would otherwise let anyone
    who has the link join as staff. Check the invite's target email against
    the authenticated user's verified email before accepting.
    """
    user_id = user_data["user_id"]
    invite = await store.workspace_repo.get_invite_by_token(token)
    if not invite or invite.get("status") != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite is invalid, expired, or already used")

    account_email = (user_data["user_info"].get("email") or "").strip().lower()
    invite_email = (invite.get("email") or "").strip().lower()
    if account_email != invite_email:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This invite was sent to {invite['email']}. Sign in with that email to accept it."
        )

    result = await store.workspace_repo.accept_invite(token, user_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite is invalid, expired, or already used")

    workspace_id = result.get("workspace_id")
    # Return the organization too, so the client can enter it directly instead
    # of bouncing through the chooser to work out where it just landed.
    organization_id = result.get("organization_id")
    if organization_id is None and workspace_id is not None:
        organization_id = await store.org_repo.get_organization_for_workspace(workspace_id)

    # Headcount just changed, so the Stripe quantity has to follow it. Seats
    # beyond the plan's included allowance are charged, which is the whole
    # point of seat pricing. This never raises: the person has already joined,
    # and undoing that because Stripe was briefly unreachable would be worse
    # than a quantity that the next sync corrects.
    if organization_id is not None:
        await sync_seats_to_stripe(store, organization_id, reason="invite accepted")

    return {
        "workspace_id": workspace_id,
        "organization_id": organization_id,
        "message": (
            "You have joined the workspace" if workspace_id is not None
            else "You have joined the organization"
        ),
    }
