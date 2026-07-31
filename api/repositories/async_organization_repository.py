"""Organization (tenant) queries.

The organization is the billing entity and the security boundary. Membership is
recorded in organization_members rather than inferred from how many workspaces
somebody happens to own, which is what the earlier "owns zero workspaces means
they are an invitee" proxy did.
"""
from typing import Any, Dict, List, Optional
import logging

from sqlalchemy import select, func

from .async_base_repository import AsyncBaseRepository
from ..models.orm_models import (
    Organization,
    OrganizationMember,
    Subscription,
    Workspace as WorkspaceORM,
)

logger = logging.getLogger(__name__)

# Ranked so the strongest role wins when a user somehow holds several.
_ROLE_RANK = {"owner": 3, "admin": 2, "member": 1}


class AsyncOrganizationRepository(AsyncBaseRepository):
    """Async repository for organization and membership operations."""

    async def create_organization(self, name: str, owner_user_id: int) -> Optional[int]:
        """Create an organization and record its owner. Returns the new id."""
        async with self.get_async_session() as session:
            try:
                org = Organization(name=name)
                session.add(org)
                await session.flush()
                session.add(
                    OrganizationMember(
                        organization_id=org.id,
                        user_id=owner_user_id,
                        role="owner",
                    )
                )
                await session.commit()
                logger.info(f"Created organization {org.id} ('{name}') owned by user {owner_user_id}")
                return org.id
            except Exception as e:
                await session.rollback()
                logger.error(f"Error creating organization '{name}': {e}", exc_info=True)
                return None

    async def rename_organization(self, organization_id: int, name: str) -> bool:
        """Rename an organization. Authorization is the caller's responsibility."""
        async with self.get_async_session() as session:
            try:
                org = await session.get(Organization, organization_id)
                if not org:
                    return False
                org.name = name
                await session.commit()
                logger.info(f"Renamed organization {organization_id} to '{name}'")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error renaming organization {organization_id}: {e}", exc_info=True)
                return False

    async def delete_organization(self, organization_id: int) -> bool:
        """Delete an organization and everything cascading from it."""
        async with self.get_async_session() as session:
            try:
                org = await session.get(Organization, organization_id)
                if not org:
                    return False
                await session.delete(org)
                await session.commit()
                logger.info(f"Deleted organization {organization_id}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error deleting organization {organization_id}: {e}", exc_info=True)
                return False

    async def get_memberships(self, user_id: int) -> List[Dict[str, Any]]:
        """Every organization this user belongs to, with their role in each."""
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(Organization, OrganizationMember.role)
                    .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
                    .where(OrganizationMember.user_id == user_id)
                    .order_by(Organization.id)
                )
                rows = (await session.execute(stmt)).all()
                return [
                    {"organization_id": org.id, "name": org.name, "role": role}
                    for org, role in rows
                ]
            except Exception as e:
                logger.error(f"Error listing memberships for user {user_id}: {e}", exc_info=True)
                return []

    async def get_role(self, organization_id: int, user_id: int) -> Optional[str]:
        """The user's role in an organization, or None if they are not a member."""
        async with self.get_async_session() as session:
            try:
                stmt = select(OrganizationMember.role).where(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.user_id == user_id,
                )
                return (await session.execute(stmt)).scalar_one_or_none()
            except Exception as e:
                logger.error(
                    f"Error getting role for user {user_id} in org {organization_id}: {e}",
                    exc_info=True,
                )
                return None

    async def add_member(self, organization_id: int, user_id: int, role: str = "member") -> bool:
        """Add a member, leaving an existing membership untouched."""
        async with self.get_async_session() as session:
            try:
                existing = (
                    await session.execute(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == organization_id,
                            OrganizationMember.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    return True
                session.add(
                    OrganizationMember(
                        organization_id=organization_id,
                        user_id=user_id,
                        role=role,
                    )
                )
                await session.commit()
                logger.info(f"Added user {user_id} to org {organization_id} as {role}")
                return True
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Error adding user {user_id} to org {organization_id}: {e}", exc_info=True
                )
                return False

    async def remove_member(self, organization_id: int, user_id: int) -> bool:
        """Remove somebody from an organization entirely. Refuses the last owner.

        Also clears their workspace assignments inside this organization.
        Deleting only the organization_members row left the workspace_members
        rows behind, and access is granted by either, so a "removed" member
        kept every workspace they had been assigned to — and got them back
        silently if they were ever re-added.
        """
        async with self.get_async_session() as session:
            try:
                member = (
                    await session.execute(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == organization_id,
                            OrganizationMember.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if not member:
                    return False

                if member.role == "owner":
                    owners = (
                        await session.execute(
                            select(func.count(OrganizationMember.id)).where(
                                OrganizationMember.organization_id == organization_id,
                                OrganizationMember.role == "owner",
                            )
                        )
                    ).scalar() or 0
                    if owners <= 1:
                        logger.warning(
                            f"Refusing to remove the last owner of org {organization_id}"
                        )
                        return False

                from ..models.orm_models import WorkspaceMember

                assignments = (await session.execute(
                    select(WorkspaceMember)
                    .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMember.workspace_id)
                    .where(
                        WorkspaceMember.user_id == user_id,
                        WorkspaceORM.organization_id == organization_id,
                    )
                )).scalars().all()
                for assignment in assignments:
                    await session.delete(assignment)

                await session.delete(member)
                await session.commit()
                logger.info(
                    f"Removed user {user_id} from org {organization_id} "
                    f"and {len(assignments)} workspace assignment(s)"
                )
                return True
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Error removing user {user_id} from org {organization_id}: {e}", exc_info=True
                )
                return False

    async def list_members(self, organization_id: int) -> List[Dict[str, Any]]:
        """Members of an organization with their roles, strongest role first."""
        from ..models.orm_models import User

        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(
                        User.id,
                        User.email,
                        OrganizationMember.role,
                        OrganizationMember.joined_at,
                        OrganizationMember.scope,
                    )
                    .join(OrganizationMember, OrganizationMember.user_id == User.id)
                    .where(OrganizationMember.organization_id == organization_id)
                )
                rows = (await session.execute(stmt)).all()

                # Which workspaces each member is explicitly assigned to. Needed
                # to render, and to edit, the reach of somebody already in the
                # organization: without it the UI can only set access at invite
                # time and never change it afterwards.
                from ..models.orm_models import WorkspaceMember
                assignment_rows = (await session.execute(
                    select(WorkspaceMember.user_id, WorkspaceMember.workspace_id)
                    .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMember.workspace_id)
                    .where(WorkspaceORM.organization_id == organization_id)
                )).all()
                assigned: Dict[int, List[int]] = {}
                for uid, wid in assignment_rows:
                    assigned.setdefault(uid, []).append(wid)

                members = [
                    {
                        "user_id": uid,
                        "email": email,
                        "role": role,
                        "joined_at": joined,
                        # Owners and admins administer the tenant, so their
                        # reach is the whole organization regardless of what
                        # the column says.
                        "scope": "organization" if role in ("owner", "admin") else scope,
                        "workspace_ids": sorted(assigned.get(uid, [])),
                        "can_edit_access": role not in ("owner", "admin"),
                    }
                    for uid, email, role, joined, scope in rows
                ]
                members.sort(key=lambda m: (-_ROLE_RANK.get(m["role"], 0), m["email"] or ""))
                return members
            except Exception as e:
                logger.error(f"Error listing members of org {organization_id}: {e}", exc_info=True)
                return []

    async def set_member_access(
        self,
        organization_id: int,
        user_id: int,
        scope: str,
        workspace_ids: Optional[List[int]] = None,
    ) -> bool:
        """Change how far an existing member's access reaches.

        scope='organization' gives them every workspace in the tenant, now and
        later. scope='workspace' limits them to workspace_ids, replacing
        whatever they had.

        Refuses to touch an owner or admin: their reach comes from
        administering the tenant, and narrowing it here would produce a member
        who can manage the organization but not see it.
        """
        from ..models.orm_models import WorkspaceMember

        if scope not in ("organization", "workspace"):
            logger.error(f"Invalid scope {scope!r}")
            return False

        async with self.get_async_session() as session:
            try:
                member = (await session.execute(
                    select(OrganizationMember).where(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.user_id == user_id,
                    )
                )).scalar_one_or_none()
                if not member:
                    return False
                if member.role in ("owner", "admin"):
                    logger.warning(
                        f"Refusing to scope user {user_id}, who is {member.role} of org {organization_id}"
                    )
                    return False

                member.scope = scope

                # Only workspaces in this organization may be assigned, so a
                # crafted request cannot grant access to another tenant's.
                allowed = set((await session.execute(
                    select(WorkspaceORM.id).where(WorkspaceORM.organization_id == organization_id)
                )).scalars().all())
                requested = {int(w) for w in (workspace_ids or [])} & allowed

                existing = (await session.execute(
                    select(WorkspaceMember)
                    .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMember.workspace_id)
                    .where(
                        WorkspaceMember.user_id == user_id,
                        WorkspaceORM.organization_id == organization_id,
                    )
                )).scalars().all()
                have = {row.workspace_id: row for row in existing}

                if scope == "organization":
                    # Reach no longer comes from assignments. Clear them so
                    # narrowing later starts from a blank slate rather than
                    # silently restoring an old set.
                    for row in existing:
                        await session.delete(row)
                else:
                    for wid in have.keys() - requested:
                        await session.delete(have[wid])
                    for wid in requested - have.keys():
                        session.add(WorkspaceMember(
                            workspace_id=wid, user_id=user_id, role="staff"
                        ))

                await session.commit()
                logger.info(
                    f"Set user {user_id} in org {organization_id} to {scope} "
                    f"({sorted(requested) if scope == 'workspace' else 'all workspaces'})"
                )
                return True
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Error setting access for user {user_id} in org {organization_id}: {e}",
                    exc_info=True,
                )
                return False

    async def count_members(self, organization_id: int) -> int:
        """Seats consumed by this organization."""
        async with self.get_async_session() as session:
            try:
                stmt = select(func.count(OrganizationMember.id)).where(
                    OrganizationMember.organization_id == organization_id
                )
                return int((await session.execute(stmt)).scalar() or 0)
            except Exception as e:
                logger.error(f"Error counting members of org {organization_id}: {e}", exc_info=True)
                return 0

    async def accessible_workspace_ids(self, user_id: int) -> List[int]:
        """Every workspace the user may read, via organization membership.

        Access follows the tenant: belonging to an organization grants access to
        that organization's workspaces.
        """
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(WorkspaceORM.id)
                    .join(
                        OrganizationMember,
                        OrganizationMember.organization_id == WorkspaceORM.organization_id,
                    )
                    .where(OrganizationMember.user_id == user_id)
                )
                return sorted({r for r in (await session.execute(stmt)).scalars().all()})
            except Exception as e:
                logger.error(
                    f"Error listing accessible workspaces for user {user_id}: {e}", exc_info=True
                )
                return []

    async def get_organization_for_workspace(self, workspace_id: int) -> Optional[int]:
        """The organization that owns a workspace."""
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceORM.organization_id).where(WorkspaceORM.id == workspace_id)
                return (await session.execute(stmt)).scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error resolving org for workspace {workspace_id}: {e}", exc_info=True)
                return None

    async def get_subscription_status(self, organization_id: int) -> str:
        """Subscription status for an organization, 'none' when there is none."""
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(Subscription.status)
                    .where(Subscription.organization_id == organization_id)
                    .order_by(Subscription.updated_at.desc())
                    .limit(1)
                )
                return (await session.execute(stmt)).scalar_one_or_none() or "none"
            except Exception as e:
                logger.error(
                    f"Error getting subscription status for org {organization_id}: {e}", exc_info=True
                )
                return "none"

    async def get_subscription_row(self, organization_id: int) -> Optional[Dict[str, Any]]:
        """The organization's current subscription, as a plain dict.

        Seat accounting needs the Stripe subscription id and the plan together;
        fetching them through two separate status/limit calls meant two queries
        and no way to tell "no subscription" from "subscription with no plan".
        """
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(Subscription)
                    .where(Subscription.organization_id == organization_id)
                    .order_by(Subscription.updated_at.desc())
                    .limit(1)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if not row:
                    return None
                return {
                    "id": row.id,
                    "status": row.status,
                    "plan_key": row.plan_key,
                    "seats": row.seats,
                    "stripe_customer_id": row.stripe_customer_id,
                    "stripe_subscription_id": row.stripe_subscription_id,
                    "current_period_end": row.current_period_end,
                }
            except Exception as e:
                logger.error(
                    f"Error getting subscription row for org {organization_id}: {e}", exc_info=True
                )
                return None

    async def get_seat_limit(self, organization_id: int) -> Optional[int]:
        """Seat allowance for an organization. None means unlimited."""
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(Subscription.seats)
                    .where(Subscription.organization_id == organization_id)
                    .order_by(Subscription.updated_at.desc())
                    .limit(1)
                )
                return (await session.execute(stmt)).scalar_one_or_none()
            except Exception as e:
                logger.error(f"Error getting seat limit for org {organization_id}: {e}", exc_info=True)
                return None
