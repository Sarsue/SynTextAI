"""Async Workspace repository for managing workspace-related database operations."""

from typing import Optional, List, Dict, Any
import logging
import uuid
from datetime import datetime, timedelta

from .async_base_repository import AsyncBaseRepository
from ..models import Workspace as WorkspaceORM
from ..models.orm_models import WorkspaceMember, WorkspaceInvite, User as UserORM

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_

logger = logging.getLogger(__name__)


class AsyncWorkspaceRepository(AsyncBaseRepository):
    """Async repository for workspace operations."""

    def __init__(self, database_url: str = None):
        super().__init__(database_url)

    async def create_workspace(
        self, user_id: int, name: str, organization_id: Optional[int] = None
    ) -> Optional[int]:
        """Create a new workspace inside an organization.

        Every workspace belongs to a tenant. When no organization is given, the
        creator's own is used, and one is created if they somehow have none, so
        a workspace can never exist outside an organization.
        """
        if organization_id is None:
            organization_id = await self._default_organization_for(user_id)
            if organization_id is None:
                logger.error(f"Cannot create workspace: no organization for user {user_id}")
                return None

        async with self.get_async_session() as session:
            try:
                workspace = WorkspaceORM(
                    user_id=user_id, name=name, organization_id=organization_id
                )
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

    async def _default_organization_for(self, user_id: int) -> Optional[int]:
        """The organization this user administers, creating one if they have none.

        Signup creates an organization, so this is a safety net for accounts that
        predate that, not the normal path.
        """
        from ..models.orm_models import Organization, OrganizationMember, User as UserORM

        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(OrganizationMember.organization_id)
                    .where(
                        OrganizationMember.user_id == user_id,
                        OrganizationMember.role.in_(("owner", "admin")),
                    )
                    .order_by(OrganizationMember.organization_id)
                    .limit(1)
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    return existing

                email = (
                    await session.execute(select(UserORM.email).where(UserORM.id == user_id))
                ).scalar_one_or_none()
                label = (email or "").split("@")[0] or "My"
                org = Organization(name=f"{label}'s Organization")
                session.add(org)
                await session.flush()
                session.add(
                    OrganizationMember(organization_id=org.id, user_id=user_id, role="owner")
                )
                await session.commit()
                logger.info(f"Created default organization {org.id} for user {user_id}")
                return org.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error resolving organization for user {user_id}: {e}", exc_info=True)
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
        """List all workspaces a user owns or is a member of."""
        async with self.get_async_session() as session:
            try:
                # Owned workspaces
                owned_stmt = select(WorkspaceORM).where(WorkspaceORM.user_id == user_id)
                # Member workspaces
                member_stmt = (
                    select(WorkspaceORM, WorkspaceMember.role)
                    .join(WorkspaceMember, WorkspaceMember.workspace_id == WorkspaceORM.id)
                    .where(WorkspaceMember.user_id == user_id)
                    .where(WorkspaceORM.user_id != user_id)  # exclude owned (already in first query)
                )

                owned = (await session.execute(owned_stmt)).scalars().all()
                members = (await session.execute(member_stmt)).all()

                results = []
                for ws in owned:
                    results.append({
                        "id": ws.id,
                        "name": ws.name,
                        "user_id": ws.user_id,
                        "role": "owner",
                        "created_at": ws.created_at,
                        "updated_at": ws.updated_at,
                    })
                for ws, role in members:
                    results.append({
                        "id": ws.id,
                        "name": ws.name,
                        "user_id": ws.user_id,
                        "role": role,
                        "created_at": ws.created_at,
                        "updated_at": ws.updated_at,
                    })
                results.sort(key=lambda x: x["created_at"] or datetime.min)
                return results
            except Exception as e:
                logger.error(f"Error listing workspaces for user {user_id}: {e}", exc_info=True)
                return []

    async def accessible_workspace_ids(
        self, user_id: int, organization_id: Optional[int] = None
    ) -> List[int]:
        """Workspace ids this user may read, restricted to one organization.

        Document visibility is a property of the workspace, not of whoever
        uploaded the file. Callers use this to scope queries by workspace rather
        than by files.user_id, which previously meant an invited staff member
        saw none of the workspace's documents at all.

        Pass organization_id to keep results inside the tenant the user is
        currently working in. Without it, somebody who belongs to two companies
        gets both companies' workspaces in one flat list: not a data leak, since
        membership is still required, but the boundary between two clients'
        knowledge bases stops being visible, which is exactly what a dental or
        legal customer is trusting us with.
        """
        async with self.get_async_session() as session:
            try:
                from ..models.orm_models import OrganizationMember

                # Access follows the tenant: belonging to an organization grants
                # access to that organization's workspaces.
                via_org = (
                    select(WorkspaceORM.id)
                    .join(
                        OrganizationMember,
                        OrganizationMember.organization_id == WorkspaceORM.organization_id,
                    )
                    .where(OrganizationMember.user_id == user_id)
                )
                if organization_id is not None:
                    via_org = via_org.where(WorkspaceORM.organization_id == organization_id)
                # Legacy workspace_members rows are still honoured so nothing is
                # lost for anyone not yet migrated onto an organization.
                legacy = select(WorkspaceMember.workspace_id).where(
                    WorkspaceMember.user_id == user_id
                )
                if organization_id is not None:
                    legacy = legacy.join(
                        WorkspaceORM, WorkspaceORM.id == WorkspaceMember.workspace_id
                    ).where(WorkspaceORM.organization_id == organization_id)
                org_ids = [r for r in (await session.execute(via_org)).scalars().all()]
                legacy_ids = [r for r in (await session.execute(legacy)).scalars().all()]
                return sorted(set(org_ids) | set(legacy_ids))
            except Exception as e:
                logger.error(f"Error listing accessible workspaces for user {user_id}: {e}", exc_info=True)
                return []

    async def get_user_role_in_workspace(self, workspace_id: int, user_id: int) -> Optional[str]:
        """Return the user's role in a workspace ('owner', 'staff') or None if no access."""
        async with self.get_async_session() as session:
            try:
                ws_stmt = select(WorkspaceORM).where(
                    WorkspaceORM.id == workspace_id,
                    WorkspaceORM.user_id == user_id
                )
                if (await session.execute(ws_stmt)).scalar_one_or_none():
                    return "owner"

                mem_stmt = select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id
                )
                member = (await session.execute(mem_stmt)).scalar_one_or_none()
                if member:
                    return member.role

                # Fall back to organization membership, which is the real
                # source of truth now that a workspace belongs to a tenant.
                from ..models.orm_models import OrganizationMember

                org_role = (await session.execute(
                    select(OrganizationMember.role)
                    .join(
                        WorkspaceORM,
                        WorkspaceORM.organization_id == OrganizationMember.organization_id,
                    )
                    .where(
                        WorkspaceORM.id == workspace_id,
                        OrganizationMember.user_id == user_id,
                    )
                )).scalar_one_or_none()
                if org_role in ("owner", "admin"):
                    return "owner"
                return "staff" if org_role else None
            except Exception as e:
                logger.error(f"Error getting role for user {user_id} in workspace {workspace_id}: {e}", exc_info=True)
                return None

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

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    async def list_members(self, workspace_id: int) -> List[Dict[str, Any]]:
        """Return all members of a workspace including the owner."""
        async with self.get_async_session() as session:
            try:
                ws = (await session.execute(
                    select(WorkspaceORM).where(WorkspaceORM.id == workspace_id)
                )).scalar_one_or_none()
                if not ws:
                    return []

                owner = (await session.execute(
                    select(UserORM).where(UserORM.id == ws.user_id)
                )).scalar_one_or_none()

                results = []
                if owner:
                    results.append({
                        "user_id": owner.id,
                        "email": owner.email,
                        "role": "owner",
                        "joined_at": ws.created_at,
                    })

                members_stmt = (
                    select(WorkspaceMember, UserORM)
                    .join(UserORM, UserORM.id == WorkspaceMember.user_id)
                    .where(WorkspaceMember.workspace_id == workspace_id)
                )
                for member, user in (await session.execute(members_stmt)).all():
                    results.append({
                        "user_id": user.id,
                        "email": user.email,
                        "role": member.role,
                        "joined_at": member.joined_at,
                    })
                return results
            except Exception as e:
                logger.error(f"Error listing members for workspace {workspace_id}: {e}", exc_info=True)
                return []

    async def add_member(self, workspace_id: int, user_id: int, role: str = "staff") -> bool:
        """Add a user to a workspace."""
        async with self.get_async_session() as session:
            try:
                member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
                session.add(member)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error adding member {user_id} to workspace {workspace_id}: {e}", exc_info=True)
                return False

    async def remove_member(self, workspace_id: int, user_id: int) -> bool:
        """Remove a member from a workspace."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
                member = (await session.execute(stmt)).scalar_one_or_none()
                if not member:
                    return False
                await session.delete(member)
                await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error removing member {user_id} from workspace {workspace_id}: {e}", exc_info=True)
                return False

    # ------------------------------------------------------------------
    # Invites
    # ------------------------------------------------------------------

    async def create_invite(self, workspace_id: int, email: str) -> Optional[str]:
        """Create an invite token for an email. Returns the token string."""
        async with self.get_async_session() as session:
            try:
                # Expire any existing pending invite for this email+workspace
                existing_stmt = select(WorkspaceInvite).where(
                    WorkspaceInvite.workspace_id == workspace_id,
                    WorkspaceInvite.email == email,
                    WorkspaceInvite.status == "pending",
                )
                existing = (await session.execute(existing_stmt)).scalar_one_or_none()
                if existing:
                    existing.status = "expired"

                token = str(uuid.uuid4())
                invite = WorkspaceInvite(
                    workspace_id=workspace_id,
                    email=email,
                    token=token,
                    status="pending",
                    expires_at=datetime.utcnow() + timedelta(days=7),
                )
                session.add(invite)
                await session.commit()
                return token
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating invite for {email} in workspace {workspace_id}: {e}", exc_info=True)
                return None

    async def get_invite_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Look up an invite by token. Returns None if not found or expired."""
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(WorkspaceInvite, WorkspaceORM)
                    .join(WorkspaceORM, WorkspaceORM.id == WorkspaceInvite.workspace_id)
                    .where(WorkspaceInvite.token == token)
                )
                row = (await session.execute(stmt)).one_or_none()
                if not row:
                    return None
                invite, workspace = row
                return {
                    "id": invite.id,
                    "workspace_id": invite.workspace_id,
                    "workspace_name": workspace.name,
                    "email": invite.email,
                    "token": invite.token,
                    "status": invite.status,
                    "expires_at": invite.expires_at,
                }
            except Exception as e:
                logger.error(f"Error fetching invite by token: {e}", exc_info=True)
                return None

    async def accept_invite(self, token: str, user_id: int) -> Optional[int]:
        """Accept an invite. Returns workspace_id on success, None on failure."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceInvite).where(WorkspaceInvite.token == token)
                invite = (await session.execute(stmt)).scalar_one_or_none()

                if not invite:
                    return None
                if invite.status != "pending":
                    return None
                if invite.expires_at < datetime.utcnow():
                    invite.status = "expired"
                    await session.commit()
                    return None

                invite.status = "accepted"

                # Idempotent: only add if not already a member
                existing = (await session.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.workspace_id == invite.workspace_id,
                        WorkspaceMember.user_id == user_id,
                    )
                )).scalar_one_or_none()

                if not existing:
                    session.add(WorkspaceMember(
                        workspace_id=invite.workspace_id,
                        user_id=user_id,
                        role="staff",
                    ))

                # Joining a workspace means joining its organization. This is the
                # record that makes them a member of the tenant, rather than it
                # being inferred later from how many workspaces they own.
                from ..models.orm_models import OrganizationMember
                org_id = (await session.execute(
                    select(WorkspaceORM.organization_id).where(WorkspaceORM.id == invite.workspace_id)
                )).scalar_one_or_none()

                if org_id:
                    already = (await session.execute(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == org_id,
                            OrganizationMember.user_id == user_id,
                        )
                    )).scalar_one_or_none()
                    if not already:
                        session.add(OrganizationMember(
                            organization_id=org_id,
                            user_id=user_id,
                            role="member",
                        ))
                        logger.info(f"User {user_id} joined organization {org_id} via invite")

                await session.commit()
                return invite.workspace_id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error accepting invite {token}: {e}", exc_info=True)
                return None

    async def has_pending_invite_for_email(self, email: str) -> bool:
        """True if this address has an unexpired invite waiting.

        Used at signup to tell "joining a company" apart from "starting one".
        Reading it from the invite table rather than trusting a client-supplied
        flag means it cannot be spoofed to dodge creating an organization.
        """
        if not email:
            return False
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceInvite.id).where(
                    func.lower(WorkspaceInvite.email) == email.strip().lower(),
                    WorkspaceInvite.status == "pending",
                    WorkspaceInvite.expires_at > datetime.utcnow(),
                ).limit(1)
                return (await session.execute(stmt)).scalar_one_or_none() is not None
            except Exception as e:
                logger.error(f"Error checking pending invites for {email}: {e}", exc_info=True)
                return False

    async def list_pending_invites(self, workspace_id: int) -> List[Dict[str, Any]]:
        """Return pending invites for a workspace."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceInvite).where(
                    WorkspaceInvite.workspace_id == workspace_id,
                    WorkspaceInvite.status == "pending",
                    WorkspaceInvite.expires_at > datetime.utcnow(),
                )
                invites = (await session.execute(stmt)).scalars().all()
                return [
                    {
                        "id": inv.id,
                        "email": inv.email,
                        "expires_at": inv.expires_at,
                    }
                    for inv in invites
                ]
            except Exception as e:
                logger.error(f"Error listing invites for workspace {workspace_id}: {e}", exc_info=True)
                return []
