"""Async Workspace repository for managing workspace-related database operations."""

from typing import Optional, List, Dict, Any
import logging
import uuid
from datetime import datetime, timedelta

from .async_base_repository import AsyncBaseRepository
from ..models import Workspace as WorkspaceORM
from ..models.orm_models import WorkspaceMember, WorkspaceInvite, Organization, User as UserORM

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

    async def list_accessible_workspaces(
        self, user_id: int, organization_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Every workspace this user may open, with their role in each.

        Built from accessible_workspace_ids, which is the single answer to
        "what may this person see". list_workspaces_for_user is a different
        question — what they own or were explicitly assigned — and using it to
        build the picker meant a member with organization-wide reach saw
        nothing at all: they hold no assignment rows by design, so the list
        started empty and filtering it could not add anything back.
        """
        ids = await self.accessible_workspace_ids(user_id, organization_id=organization_id)
        if not ids:
            return []

        async with self.get_async_session() as session:
            try:
                from ..models.orm_models import OrganizationMember

                rows = (await session.execute(
                    select(WorkspaceORM).where(WorkspaceORM.id.in_(ids))
                )).scalars().all()

                # Roles are resolved inside this one session.
                #
                # Calling get_user_role_in_workspace per workspace opened a new
                # session for each while this one was still held, which
                # exhausted a pool of two on the second workspace and produced
                # QueuePool timeouts under nothing more than normal use.
                admin_orgs = set((await session.execute(
                    select(OrganizationMember.organization_id).where(
                        OrganizationMember.user_id == user_id,
                        OrganizationMember.role.in_(("owner", "admin")),
                    )
                )).scalars().all())

                result = []
                for ws in rows:
                    if ws.user_id == user_id or ws.organization_id in admin_orgs:
                        role = "owner"
                    else:
                        role = "staff"
                    result.append({
                        "id": ws.id,
                        "name": ws.name,
                        "user_id": ws.user_id,
                        "role": role,
                        "created_at": ws.created_at,
                        "updated_at": ws.updated_at,
                    })
                result.sort(key=lambda x: x["created_at"] or datetime.min)
                return result
            except Exception as e:
                logger.error(
                    f"Error listing accessible workspaces for user {user_id}: {e}", exc_info=True
                )
                return []

    async def list_workspaces_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """List all workspaces a user owns or is a member of.

        Note this is NOT the same as what they may see: somebody with
        organization-wide reach owns nothing and is assigned to nothing, yet
        may open every workspace in the tenant. Use list_accessible_workspaces
        to answer that.
        """
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
        """Workspace ids this user may read.

        Two different reaches, decided by the role held in the organization:

        - owner/admin administer the tenant, and members invited to the
          organization itself, see every workspace in it including ones added
          later.
        - members invited to a workspace see only the workspaces they created
          or were explicitly added to.

        This used to grant every workspace in the organization to anyone who
        belonged to it, which made workspace_members able to widen access but
        never to narrow it. Inviting somebody to one workspace silently gave
        them all of them: with a Finance and a Dentist workspace in one tenant,
        a member invited to Finance could read Dentist's documents and cite
        them in answers.

        No backfill was needed to tighten this, because owners keep their reach
        through their organization role rather than through a workspace_members
        row — several of which do not exist for workspaces created by the owner.

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

                # Organizations this user reads in full: either they administer
                # the tenant, or they were invited to the organization rather
                # than to one workspace inside it.
                orgwide_stmt = select(OrganizationMember.organization_id).where(
                    OrganizationMember.user_id == user_id,
                    or_(
                        OrganizationMember.role.in_(("owner", "admin")),
                        OrganizationMember.scope == "organization",
                    ),
                )
                orgwide = set((await session.execute(orgwide_stmt)).scalars().all())

                ids: set = set()

                if orgwide:
                    scoped = orgwide & {organization_id} if organization_id is not None else orgwide
                    if scoped:
                        via_org = select(WorkspaceORM.id).where(
                            WorkspaceORM.organization_id.in_(scoped)
                        )
                        ids |= set((await session.execute(via_org)).scalars().all())

                # Workspaces they created. Kept separate from the role check so
                # a plain member who makes their own workspace does not lose it.
                owned = select(WorkspaceORM.id).where(WorkspaceORM.user_id == user_id)
                if organization_id is not None:
                    owned = owned.where(WorkspaceORM.organization_id == organization_id)
                ids |= set((await session.execute(owned)).scalars().all())

                # Workspaces they were explicitly added to.
                #
                # Joined to organization_members as well, so an assignment can
                # never outlive membership of the tenant. Removal clears these
                # rows too, but relying on that alone means any row left behind
                # by a partial failure silently grants access.
                explicit = (
                    select(WorkspaceMember.workspace_id)
                    .join(WorkspaceORM, WorkspaceORM.id == WorkspaceMember.workspace_id)
                    .join(
                        OrganizationMember,
                        OrganizationMember.organization_id == WorkspaceORM.organization_id,
                    )
                    .where(
                        WorkspaceMember.user_id == user_id,
                        OrganizationMember.user_id == user_id,
                    )
                )
                if organization_id is not None:
                    explicit = explicit.where(WorkspaceORM.organization_id == organization_id)
                ids |= set((await session.execute(explicit)).scalars().all())

                return sorted(ids)
            except Exception as e:
                logger.error(f"Error listing accessible workspaces for user {user_id}: {e}", exc_info=True)
                # Fail closed: an error here must not widen access.
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

                # Reach depends on how they were invited. This used to return
                # "staff" for any workspace in the organization regardless,
                # which is what let someone invited to a single workspace read
                # all of them.
                from ..models.orm_models import OrganizationMember

                membership = (await session.execute(
                    select(OrganizationMember.role, OrganizationMember.scope)
                    .join(
                        WorkspaceORM,
                        WorkspaceORM.organization_id == OrganizationMember.organization_id,
                    )
                    .where(
                        WorkspaceORM.id == workspace_id,
                        OrganizationMember.user_id == user_id,
                    )
                )).first()
                if not membership:
                    return None
                org_role, org_scope = membership
                if org_role in ("owner", "admin"):
                    return "owner"
                # Invited to the organization: reads every workspace in it, but
                # as staff, so uploading and member management stay with owners.
                if org_scope == "organization":
                    return "staff"
                return None
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

    async def create_invite(
        self,
        email: str,
        workspace_id: Optional[int] = None,
        organization_id: Optional[int] = None,
    ) -> Optional[str]:
        """Create an invite token for an email. Returns the token string.

        Pass workspace_id to invite somebody into one workspace, or
        organization_id alone to give them the whole tenant. The two reaches
        are what organization_members.scope records when the invite is
        accepted.
        """
        if workspace_id is None and organization_id is None:
            logger.error("create_invite needs a workspace or an organization")
            return None

        async with self.get_async_session() as session:
            try:
                if organization_id is None:
                    organization_id = (await session.execute(
                        select(WorkspaceORM.organization_id).where(WorkspaceORM.id == workspace_id)
                    )).scalar_one_or_none()

                # Expire any pending invite for this email in the same tenant,
                # whatever its reach: re-inviting somebody should replace the
                # earlier offer rather than leave two live tokens with
                # different access.
                existing_stmt = select(WorkspaceInvite).where(
                    WorkspaceInvite.email == email,
                    WorkspaceInvite.status == "pending",
                    WorkspaceInvite.organization_id == organization_id,
                )
                for existing in (await session.execute(existing_stmt)).scalars().all():
                    existing.status = "expired"

                token = str(uuid.uuid4())
                invite = WorkspaceInvite(
                    workspace_id=workspace_id,
                    organization_id=organization_id,
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
                logger.error(
                    f"Error creating invite for {email} "
                    f"(workspace={workspace_id}, organization={organization_id}): {e}",
                    exc_info=True,
                )
                return None

    async def get_invite_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Look up an invite by token. Returns None if not found or expired."""
        async with self.get_async_session() as session:
            try:
                # Outer join on the workspace: an organization-wide invite names
                # no workspace, and an inner join dropped the row entirely, so
                # the invite came back as None and read as invalid or expired.
                stmt = (
                    select(WorkspaceInvite, WorkspaceORM, Organization.name)
                    .outerjoin(WorkspaceORM, WorkspaceORM.id == WorkspaceInvite.workspace_id)
                    .outerjoin(
                        Organization,
                        Organization.id == func.coalesce(
                            WorkspaceInvite.organization_id, WorkspaceORM.organization_id
                        ),
                    )
                    .where(WorkspaceInvite.token == token)
                )
                row = (await session.execute(stmt)).one_or_none()
                if not row:
                    return None
                invite, workspace, organization_name = row
                return {
                    "id": invite.id,
                    "workspace_id": invite.workspace_id,
                    "organization_id": invite.organization_id,
                    # None for an organization-wide invite; the landing page
                    # shows the organization name in that case.
                    "workspace_name": workspace.name if workspace else None,
                    # People are joining a company, not a folder, so the invite
                    # should say the organization's name.
                    "organization_name": organization_name,
                    "email": invite.email,
                    "token": invite.token,
                    "status": invite.status,
                    "expires_at": invite.expires_at,
                }
            except Exception as e:
                logger.error(f"Error fetching invite by token: {e}", exc_info=True)
                return None

    async def accept_pending_invites_for_email(self, user_id: int, email: str) -> List[int]:
        """Accept every unexpired invite waiting for this address.

        Called at signup so that being invited and signing up are one act. The
        invite link is how somebody discovers they were invited, not a step the
        join depends on: whether they click it, or simply sign up with the
        address that was invited, they land in the same place. Returns the
        organizations joined.
        """
        if not email:
            return []
        joined: List[int] = []
        async with self.get_async_session() as session:
            try:
                from ..models.orm_models import OrganizationMember

                # Outer join: an organization-wide invite has no workspace, and
                # an inner join dropped it, so signing up with an invited
                # address silently joined nothing.
                stmt = (
                    select(
                        WorkspaceInvite,
                        func.coalesce(
                            WorkspaceInvite.organization_id, WorkspaceORM.organization_id
                        ),
                    )
                    .outerjoin(WorkspaceORM, WorkspaceORM.id == WorkspaceInvite.workspace_id)
                    .where(
                        func.lower(WorkspaceInvite.email) == email.strip().lower(),
                        WorkspaceInvite.status == "pending",
                        WorkspaceInvite.expires_at > datetime.utcnow(),
                    )
                )
                for invite, org_id in (await session.execute(stmt)).all():
                    invite.status = "accepted"
                    # Joining always means joining the company, with every
                    # workspace visible. What they see after that is the owner's
                    # to set, so an invite no longer carries a reach of its own.
                    scope = "organization"

                    if invite.workspace_id is not None:
                        existing_ws = (await session.execute(
                            select(WorkspaceMember).where(
                                WorkspaceMember.workspace_id == invite.workspace_id,
                                WorkspaceMember.user_id == user_id,
                            )
                        )).scalar_one_or_none()
                        if not existing_ws:
                            session.add(WorkspaceMember(
                                workspace_id=invite.workspace_id, user_id=user_id, role="staff"
                            ))

                    if org_id:
                        existing_org = (await session.execute(
                            select(OrganizationMember).where(
                                OrganizationMember.organization_id == org_id,
                                OrganizationMember.user_id == user_id,
                            )
                        )).scalar_one_or_none()
                        if existing_org:
                            # Two invites can be waiting at once. Widen, never
                            # narrow: the broader offer wins.
                            if scope == "organization" and existing_org.scope != "organization":
                                existing_org.scope = "organization"
                        else:
                            session.add(OrganizationMember(
                                organization_id=org_id, user_id=user_id,
                                role="staff", scope=scope,
                            ))
                        if org_id not in joined:
                            joined.append(org_id)

                await session.commit()
                if joined:
                    logger.info(f"User {user_id} joined organizations {joined} via invites at signup")
                return joined
            except Exception as e:
                await session.rollback()
                logger.error(f"Error accepting invites for {email}: {e}", exc_info=True)
                return []

    async def accept_invite(self, token: str, user_id: int) -> Optional[Dict[str, Any]]:
        """Accept an invite.

        Returns {"workspace_id", "organization_id"} on success, None on
        failure. workspace_id is None for an organization-wide invite.
        """
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

                # A workspace invite also adds the row that limits them to it.
                # An organization invite adds none, because their reach comes
                # from the membership scope instead and must extend to
                # workspaces created later.
                if invite.workspace_id is not None:
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

                # Either way they join the organization. This is the record that
                # makes them a member of the tenant, rather than it being
                # inferred later from how many workspaces they own, and the
                # record seat billing counts.
                from ..models.orm_models import OrganizationMember
                org_id = invite.organization_id
                if org_id is None and invite.workspace_id is not None:
                    org_id = (await session.execute(
                        select(WorkspaceORM.organization_id).where(WorkspaceORM.id == invite.workspace_id)
                    )).scalar_one_or_none()

                # One kind of invite: it makes you a member of the
                # organization, seeing every workspace. Narrowing is a separate,
                # deliberate act by the owner.
                scope = "organization"

                if org_id:
                    already = (await session.execute(
                        select(OrganizationMember).where(
                            OrganizationMember.organization_id == org_id,
                            OrganizationMember.user_id == user_id,
                        )
                    )).scalar_one_or_none()
                    if already:
                        # Somebody already in the tenant who is then invited to
                        # the whole organization should gain that reach. Never
                        # the reverse: a later workspace invite must not shrink
                        # access they already hold.
                        if scope == "organization" and already.scope != "organization":
                            already.scope = "organization"
                            logger.info(f"User {user_id} widened to organization scope in org {org_id}")
                    else:
                        session.add(OrganizationMember(
                            organization_id=org_id,
                            user_id=user_id,
                            role="staff",
                            scope=scope,
                        ))
                        logger.info(f"User {user_id} joined organization {org_id} with {scope} scope")

                await session.commit()
                # A dict, not the workspace id: an organization-wide invite has
                # no workspace, and returning None for it made a successful
                # accept indistinguishable from a failure. The invite was
                # already marked accepted by then, so the caller reported an
                # error for a join that had actually happened and could not be
                # retried.
                return {"workspace_id": invite.workspace_id, "organization_id": org_id}
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
