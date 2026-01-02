"""Async Workspace repository for managing workspace-related database operations."""

from typing import Optional, List, Dict, Any
import logging

from .async_base_repository import AsyncBaseRepository
from ..models import Workspace as WorkspaceORM

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

logger = logging.getLogger(__name__)


class AsyncWorkspaceRepository(AsyncBaseRepository):
    """Async repository for workspace operations."""

    def __init__(self, database_url: str = None):
        super().__init__(database_url)

    async def create_workspace(self, user_id: int, name: str) -> Optional[int]:
        """Create a new workspace for a user."""
        async with self.get_async_session() as session:
            try:
                workspace = WorkspaceORM(user_id=user_id, name=name)
                session.add(workspace)
                await session.flush()
                workspace_id = workspace.id
                await session.commit()
                logger.info(f"Created workspace {name} (ID: {workspace_id}) for user {user_id}")
                return workspace_id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating workspace for user {user_id}: {e}", exc_info=True)
                return None

    async def count_workspaces_for_user(self, user_id: int) -> int:
        """Return the number of workspaces owned by a user."""
        async with self.get_async_session() as session:
            try:
                stmt = select(func.count(WorkspaceORM.id)).where(WorkspaceORM.user_id == user_id)
                result = await session.execute(stmt)
                return int(result.scalar() or 0)
            except Exception as e:
                logger.error(f"Error counting workspaces for user {user_id}: {e}", exc_info=True)
                return 0

    async def list_workspaces_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """List all workspaces for a user."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceORM).where(WorkspaceORM.user_id == user_id).order_by(WorkspaceORM.created_at.asc())
                result = await session.execute(stmt)
                workspaces = result.scalars().all()
                return [
                    {
                        "id": ws.id,
                        "name": ws.name,
                        "user_id": ws.user_id,
                        "created_at": ws.created_at,
                        "updated_at": ws.updated_at,
                    }
                    for ws in workspaces
                ]
            except Exception as e:
                logger.error(f"Error listing workspaces for user {user_id}: {e}", exc_info=True)
                return []

    async def update_workspace(self, workspace_id: int, name: str) -> bool:
        """Update a workspace name."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceORM).where(WorkspaceORM.id == workspace_id)
                result = await session.execute(stmt)
                workspace = result.scalar_one_or_none()
                
                if not workspace:
                    logger.warning(f"Workspace {workspace_id} not found for update")
                    return False
                
                workspace.name = name
                await session.commit()
                logger.info(f"Updated workspace {workspace_id} name to '{name}'")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error updating workspace {workspace_id}: {e}", exc_info=True)
                return False

    async def delete_workspace(self, workspace_id: int) -> bool:
        """Delete a workspace and all its files (cascade)."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceORM).where(WorkspaceORM.id == workspace_id)
                result = await session.execute(stmt)
                workspace = result.scalar_one_or_none()
                
                if not workspace:
                    logger.warning(f"Workspace {workspace_id} not found for deletion")
                    return False
                
                await session.delete(workspace)
                await session.commit()
                logger.info(f"Deleted workspace {workspace_id}")
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting workspace {workspace_id}: {e}", exc_info=True)
                return False
