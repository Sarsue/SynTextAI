"""Workspace API routes for managing user workspaces."""

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status, Path, Request
from pydantic import BaseModel, Field

from ..repositories.repository_manager import RepositoryManager
from ..limits import assert_can_create_workspace

logger = logging.getLogger(__name__)

workspaces_router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


# Dependency to get the store
def get_store(request: Request) -> RepositoryManager:
    return request.app.state.store


# Helper function to authenticate user and retrieve user ID
async def authenticate_user(request: Request, store: RepositoryManager = Depends(get_store)) -> Dict[str, any]:
    try:
        from ..utils import get_user_id
        
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


class WorkspaceCreate(BaseModel):
    name: str

class WorkspaceUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="New workspace name")


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    user_id: int
    created_at: str
    updated_at: str


@workspaces_router.get("", response_model=Dict[str, List[WorkspaceResponse]])
async def list_workspaces(
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
        
        # Convert datetime objects to ISO strings
        items = [
            {
                "id": ws["id"],
                "name": ws["name"],
                "user_id": ws["user_id"],
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
        
        # Prevent deleting the last workspace
        if len(workspaces) <= 1:
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
