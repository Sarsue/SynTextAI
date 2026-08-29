"""API keys: issuing them, resolving one on a request, and revoking it.

This repository answers "which credential is this, and is it still alive". It
answers nothing about permission. What the holder may do is decided from the
creator's *current* role, every request, in the authorization layer — see
core/auth.Principal and core/permissions.

Nothing here ever returns a stored secret, because none is stored. The plaintext
token exists once, inside create_api_key's return value, and is unreachable
afterwards by design.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging

from sqlalchemy import select, update

from .async_base_repository import AsyncBaseRepository
from ..core.api_keys import generate_token, matches, parse_prefix
from ..models.orm_models import WorkspaceApiKey

logger = logging.getLogger(__name__)

# last_used_at is written on use, and "use" is every request an integration
# makes. Writing a row per request turns a read-only search into a write, so it
# is written at most this often. The column exists to tell an owner whether
# anything still depends on a key before they revoke it, and a minute's
# resolution answers that question exactly as well as a millisecond's.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


class AsyncApiKeyRepository(AsyncBaseRepository):
    """Async repository for workspace API keys."""

    def __init__(self, database_url: str = None):
        super().__init__(database_url)

    async def create_api_key(
        self,
        workspace_id: int,
        created_by_user_id: int,
        name: str,
        scopes: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Issue a key. The returned dict carries `token`, once and only here."""
        full, prefix, token_hash = generate_token()
        async with self.get_async_session() as session:
            try:
                row = WorkspaceApiKey(
                    workspace_id=workspace_id,
                    created_by_user_id=created_by_user_id,
                    name=name,
                    prefix=prefix,
                    token_hash=token_hash,
                    scopes=scopes or ["knowledge:read"],
                    expires_at=expires_at,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return {
                    "id": row.id,
                    "name": row.name,
                    "prefix": row.prefix,
                    "scopes": row.scopes,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    # The one moment this value is readable. It is not stored
                    # and cannot be recovered from the row.
                    "token": full,
                }
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Error creating API key for workspace {workspace_id}: {e}",
                    exc_info=True,
                )
                return None

    async def resolve(self, full_token: str) -> Optional[Dict[str, Any]]:
        """The live key this token names, or None.

        None covers every refusal: unparseable, unknown, wrong secret, revoked,
        expired. The caller turns all of them into one 401, because telling a
        stranger which of those it was is telling them something.
        """
        prefix = parse_prefix(full_token)
        if not prefix:
            return None

        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceApiKey).where(WorkspaceApiKey.prefix == prefix)
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is None:
                    return None
                # Constant time, and only after the row is in hand: comparing
                # before the lookup would tell a caller whether a prefix exists.
                if not matches(full_token, row.token_hash):
                    return None
                if row.revoked_at is not None:
                    return None
                if row.expires_at is not None and row.expires_at <= datetime.utcnow():
                    return None
                return {
                    "id": row.id,
                    "workspace_id": row.workspace_id,
                    "created_by_user_id": row.created_by_user_id,
                    "scopes": list(row.scopes or []),
                    "last_used_at": row.last_used_at,
                }
            except Exception as e:
                logger.error(f"Error resolving API key: {e}", exc_info=True)
                return None

    async def touch_last_used(
        self, key_id: int, last_used_at: Optional[datetime] = None
    ) -> None:
        """Record use, at most once per _LAST_USED_RESOLUTION.

        Never raises. A failure to record use must not refuse a request that
        authenticated correctly: this column is for a human reading Settings,
        not for the decision that just succeeded.
        """
        now = datetime.utcnow()
        if last_used_at is not None and now - last_used_at < _LAST_USED_RESOLUTION:
            return
        async with self.get_async_session() as session:
            try:
                await session.execute(
                    update(WorkspaceApiKey)
                    .where(WorkspaceApiKey.id == key_id)
                    .values(last_used_at=now)
                )
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.warning(f"Could not record use of API key {key_id}: {e}")

    async def list_for_workspace(self, workspace_id: int) -> List[Dict[str, Any]]:
        """Every key on this workspace, revoked ones included.

        Revoked rows stay in the list. "This key was revoked on the 3rd" is the
        answer somebody is looking for when an integration stopped working, and
        a row that vanishes cannot give it.
        """
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(WorkspaceApiKey)
                    .where(WorkspaceApiKey.workspace_id == workspace_id)
                    .order_by(WorkspaceApiKey.created_at.desc())
                )
                rows = (await session.execute(stmt)).scalars().all()
                return [
                    {
                        "id": r.id,
                        "name": r.name,
                        "prefix": r.prefix,
                        "scopes": list(r.scopes or []),
                        "created_by_user_id": r.created_by_user_id,
                        "created_at": r.created_at,
                        "last_used_at": r.last_used_at,
                        "revoked_at": r.revoked_at,
                        "expires_at": r.expires_at,
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.error(
                    f"Error listing API keys for workspace {workspace_id}: {e}",
                    exc_info=True,
                )
                return []

    async def revoke(self, key_id: int, workspace_id: int) -> bool:
        """Withdraw a key. Scoped by workspace so an id alone cannot reach one.

        Already-revoked returns True: the caller asked for this key to be dead
        and it is. Reporting a failure would send somebody looking for a problem
        that does not exist.
        """
        async with self.get_async_session() as session:
            try:
                stmt = select(WorkspaceApiKey).where(
                    WorkspaceApiKey.id == key_id,
                    WorkspaceApiKey.workspace_id == workspace_id,
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row is None:
                    return False
                if row.revoked_at is None:
                    row.revoked_at = datetime.utcnow()
                    await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Error revoking API key {key_id}: {e}", exc_info=True)
                return False
