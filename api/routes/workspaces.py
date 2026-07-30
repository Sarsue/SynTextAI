"""Workspace API routes for managing user workspaces."""

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Path, Request
from pydantic import BaseModel, EmailStr, Field

from ..repositories.repository_manager import RepositoryManager
from ..core.limits import assert_can_create_workspace
from ..services.email_service import send_workspace_invite

logger = logging.getLogger(__name__)

workspaces_router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


# Dependency to get the store
def get_store(request: Request) -> RepositoryManager:
    return request.app.state.store


# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request, store: RepositoryManager = Depends(get_store)) -> Dict[str, any]:
    try:
        from ..core.utils import get_user_id
        
        token = request.headers.get('Authorization')
        if not token:
            logger.error("Missing Authorization token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        success, user_info = get_user_id(token)
        if not success or not user_info:
            logger.error("Failed to authenticate user with token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

        user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
        if not user_id:
            logger.error(f"No user ID found for email: {user_info['email']}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        logger.info(f"Authenticated user_id: {user_id}")
        return {"user_id": user_id, "user_info": user_info}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during user authentication")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Authentication failed")


class WorkspaceCreate(BaseModel):
    name: str

class WorkspaceUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="New workspace name")


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    user_id: int
    role: Optional[str] = "owner"
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
    workspace_id: int
    workspace_name: str
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
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)

        # Keep the list inside the tenant the user is working in, so somebody
        # who belongs to two companies never sees both companies' workspaces
        # side by side with no boundary between them.
        if organization_id is not None:
            allowed = set(
                await store.workspace_repo.accessible_workspace_ids(
                    user_id, organization_id=organization_id
                )
            )
            workspaces = [ws for ws in workspaces if ws["id"] in allowed]

        # Convert datetime objects to ISO strings
        items = [
            {
                "id": ws["id"],
                "name": ws["name"],
                "user_id": ws["user_id"],
                "role": ws.get("role", "owner"),
                "created_at": ws["created_at"].isoformat() if ws.get("created_at") else None,
                "updated_at": ws["updated_at"].isoformat() if ws.get("updated_at") else None,
            }
            for ws in workspaces
        ]
        
        return {"items": items}
    
    except Exception as e:
        logger.error(f"Error listing workspaces for user {user_data.get('user_id')}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve workspaces"
        )


@workspaces_router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkspaceResponse)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """
    Create a new workspace for the authenticated user.
    
    Free plan users are limited to 1 workspace.
    Trial/premium users can create multiple workspaces.
    
    Returns the created workspace object.
    """
    try:
        user_id = user_data["user_id"]
        
        # Enforce workspace creation limits based on subscription plan
        await assert_can_create_workspace(store, user_id)
        
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
        
        # Verify workspace exists and belongs to user
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
        workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)

        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found or you don't have permission to update it"
            )

        # list_workspaces_for_user returns workspaces this user is a *member* of
        # as well as ones they own, so membership alone was passing this check
        # and any invited staff member could rename the owner's workspace.
        if workspace.get("role") != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the workspace owner can rename it."
            )

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
        
        # Fetch updated workspace
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
        updated_workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
        
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
        
        # Get all user workspaces
        workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
        
        # Check if workspace exists and belongs to user
        workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
        
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found or you don't have permission to delete it"
            )

        # list_workspaces_for_user includes workspaces this user merely belongs
        # to, so membership alone was passing this check and an invited staff
        # member could permanently delete the owner's workspace and, by cascade,
        # every document in it.
        if workspace.get("role") != "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the workspace owner can delete it."
            )

        # Prevent deleting your last *owned* workspace. Counting every workspace
        # here meant one owned plus one joined looked like two, so an owner could
        # delete the only workspace they actually have.
        owned_count = sum(1 for ws in workspaces if ws.get("role") == "owner")
        if owned_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete your last workspace. Users must have at least one workspace."
            )

        # Delete the workspace (files will cascade delete)
        success = await store.workspace_repo.delete_workspace(workspace_id)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete workspace"
            )
        
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
    """List members and pending invites. Owner only."""
    user_id = user_data["user_id"]
    role = await store.workspace_repo.get_user_role_in_workspace(workspace_id, user_id)
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can view members")

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
    """Remove a staff member. Owner only."""
    user_id = user_data["user_id"]
    role = await store.workspace_repo.get_user_role_in_workspace(workspace_id, user_id)
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can remove members")
    if target_user_id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner cannot remove themselves")

    await store.workspace_repo.remove_member(workspace_id, target_user_id)
    return None


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

@workspaces_router.post("/{workspace_id}/invites", status_code=status.HTTP_201_CREATED)
async def send_invite(
    workspace_id: int = Path(...),
    body: InviteRequest = ...,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
):
    """Send an invite email. Owner only."""
    user_id = user_data["user_id"]
    role = await store.workspace_repo.get_user_role_in_workspace(workspace_id, user_id)
    if role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the workspace owner can invite members")

    workspaces = await store.workspace_repo.list_workspaces_for_user(user_id)
    workspace = next((ws for ws in workspaces if ws["id"] == workspace_id), None)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    token = await store.workspace_repo.create_invite(workspace_id, body.email)
    if not token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create invite")

    inviter_name = user_data["user_info"].get("name") or user_data["user_info"].get("email", "Your team")
    send_workspace_invite(
        to_email=body.email,
        workspace_name=workspace["name"],
        token=token,
        inviter_name=inviter_name,
    )

    return {"message": f"Invite sent to {body.email}"}


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

    workspace_id = await store.workspace_repo.accept_invite(token, user_id)
    if not workspace_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite is invalid, expired, or already used")

    return {"workspace_id": workspace_id, "message": "You have joined the workspace"}
