"""Registering clients, minting codes, and exchanging them for tokens.

Like the API key repository, this answers "which credential is this, and is it
still alive" and nothing about permission. What the holder may do is decided
from the granting user's current role, every request, in core/auth.

Two rules here are security properties rather than bookkeeping, and both are
easy to lose in a refactor:

  - A code is consumed exactly once, in one statement, and presenting it twice
    withdraws every token it produced. A replayed code means somebody other
    than the client may have held it, and the safe reading of that is that the
    tokens are compromised, not that the second request was a mistake.
  - A refresh rotates. The old refresh token stops working the moment a new one
    is issued, so a stolen one is good for one use before the real client's
    next refresh fails loudly and the theft becomes visible.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import hashlib
import logging
import secrets

from sqlalchemy import select, update

from .async_base_repository import AsyncBaseRepository
from ..core.api_keys import (
    ACCESS_TOKEN_TAG,
    REFRESH_TOKEN_TAG,
    generate_token,
    hash_token,
    matches,
    parse_prefix,
)
from ..models.orm_models import OAuthAuthorizationCode, OAuthClient, OAuthToken

logger = logging.getLogger(__name__)

# Short, because a refresh token exists to make it short. An access token that
# outlives the grant it came from is the gap revocation has to close by hand.
ACCESS_TOKEN_LIFETIME = timedelta(hours=1)

# Long enough to survive a slow human on a consent screen, short enough that an
# intercepted code is worthless by the time anyone reads a log.
CODE_LIFETIME = timedelta(minutes=5)

_LAST_USED_RESOLUTION = timedelta(minutes=1)


class AsyncOAuthRepository(AsyncBaseRepository):
    """Async repository for the OAuth authorization server."""

    def __init__(self, database_url: str = None):
        super().__init__(database_url)

    # -- clients ----------------------------------------------------------

    async def register_client(
        self, client_name: str, redirect_uris: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Register a public client. Grants nothing until somebody approves it.

        Public, with no secret: an MCP client runs on somebody's laptop and
        cannot keep one. PKCE is what proves a token request came from whoever
        started the authorization, and it does that without a shared secret.
        """
        client_id = f"stx_client_{secrets.token_hex(16)}"
        async with self.get_async_session() as session:
            try:
                row = OAuthClient(
                    client_id=client_id,
                    client_secret_hash=None,
                    client_name=client_name,
                    redirect_uris=redirect_uris,
                )
                session.add(row)
                await session.commit()
                return {
                    "client_id": client_id,
                    "client_name": client_name,
                    "redirect_uris": redirect_uris,
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not register OAuth client: {e}", exc_info=True)
                return None

    async def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        async with self.get_async_session() as session:
            try:
                row = (
                    await session.execute(
                        select(OAuthClient).where(OAuthClient.client_id == client_id)
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "client_id": row.client_id,
                    "client_name": row.client_name,
                    "redirect_uris": list(row.redirect_uris or []),
                }
            except Exception as e:
                logger.error(f"Could not read OAuth client: {e}", exc_info=True)
                return None

    # -- codes ------------------------------------------------------------

    async def create_code(
        self,
        client_id: str,
        user_id: int,
        workspace_id: int,
        scopes: List[str],
        code_challenge: str,
        code_challenge_method: str,
        redirect_uri: str,
    ) -> Optional[str]:
        """The code to hand back through the redirect. Returned once, here."""
        code = secrets.token_urlsafe(32)
        async with self.get_async_session() as session:
            try:
                session.add(
                    OAuthAuthorizationCode(
                        code_hash=hash_token(code),
                        client_id=client_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        scopes=scopes,
                        code_challenge=code_challenge,
                        code_challenge_method=code_challenge_method,
                        redirect_uri=redirect_uri,
                        expires_at=datetime.utcnow() + CODE_LIFETIME,
                    )
                )
                await session.commit()
                return code
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not create authorization code: {e}", exc_info=True)
                return None

    async def consume_code(
        self, code: str, client_id: str, redirect_uri: str, code_verifier: str
    ) -> Optional[Dict[str, Any]]:
        """Redeem a code, once. None for every refusal.

        The single-use claim is one UPDATE with `consumed_at IS NULL` in its
        WHERE, so two simultaneous requests cannot both win it. Checking and
        then writing would leave exactly the race this is guarding.

        A code that was already consumed withdraws every token it produced.
        Someone presenting it twice means it may have been held by somebody who
        should not have had it, and refusing the second request while leaving
        the first request's tokens alive protects nothing.
        """
        digest = hash_token(code)
        async with self.get_async_session() as session:
            try:
                row = (
                    await session.execute(
                        select(OAuthAuthorizationCode).where(
                            OAuthAuthorizationCode.code_hash == digest
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None

                if row.consumed_at is not None:
                    logger.warning(
                        "Authorization code replayed; withdrawing its tokens"
                    )
                    await session.execute(
                        update(OAuthToken)
                        .where(
                            OAuthToken.client_id == row.client_id,
                            OAuthToken.user_id == row.user_id,
                            OAuthToken.workspace_id == row.workspace_id,
                            OAuthToken.revoked_at.is_(None),
                        )
                        .values(revoked_at=datetime.utcnow())
                    )
                    await session.commit()
                    return None

                if row.expires_at <= datetime.utcnow():
                    return None
                if row.client_id != client_id:
                    return None
                # The same redirect the code was issued against. A different one
                # is a redirect that was tampered with between the two steps.
                if row.redirect_uri != redirect_uri:
                    return None
                if not _verifier_matches(
                    code_verifier, row.code_challenge, row.code_challenge_method
                ):
                    return None

                claimed = await session.execute(
                    update(OAuthAuthorizationCode)
                    .where(
                        OAuthAuthorizationCode.id == row.id,
                        OAuthAuthorizationCode.consumed_at.is_(None),
                    )
                    .values(consumed_at=datetime.utcnow())
                )
                if claimed.rowcount != 1:
                    # Somebody else claimed it between the read and the write.
                    await session.rollback()
                    return None
                await session.commit()

                return {
                    "user_id": row.user_id,
                    "workspace_id": row.workspace_id,
                    "scopes": list(row.scopes or []),
                    "client_id": row.client_id,
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not consume authorization code: {e}", exc_info=True)
                return None

    # -- tokens -----------------------------------------------------------

    async def issue_tokens(
        self,
        client_id: str,
        user_id: int,
        workspace_id: int,
        scopes: List[str],
        replacing_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """A new access and refresh pair. Both plaintexts exist only here."""
        access, access_prefix, access_hash = generate_token(ACCESS_TOKEN_TAG)
        refresh, refresh_prefix, refresh_hash = generate_token(REFRESH_TOKEN_TAG)
        async with self.get_async_session() as session:
            try:
                if replacing_id is not None:
                    # Rotation. The old pair dies as the new one is written, in
                    # the same transaction, so there is no instant where both
                    # work and none where neither does.
                    await session.execute(
                        update(OAuthToken)
                        .where(OAuthToken.id == replacing_id)
                        .values(revoked_at=datetime.utcnow())
                    )
                row = OAuthToken(
                    client_id=client_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    scopes=scopes,
                    access_prefix=access_prefix,
                    access_hash=access_hash,
                    access_expires_at=datetime.utcnow() + ACCESS_TOKEN_LIFETIME,
                    refresh_prefix=refresh_prefix,
                    refresh_hash=refresh_hash,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return {
                    "id": row.id,
                    "access_token": access,
                    "refresh_token": refresh,
                    "expires_in": int(ACCESS_TOKEN_LIFETIME.total_seconds()),
                    "scopes": scopes,
                }
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not issue OAuth tokens: {e}", exc_info=True)
                return None

    async def resolve_access(self, token: str) -> Optional[Dict[str, Any]]:
        """The live grant this access token names, or None for every refusal."""
        prefix = parse_prefix(token, ACCESS_TOKEN_TAG)
        if not prefix:
            return None
        async with self.get_async_session() as session:
            try:
                row = (
                    await session.execute(
                        select(OAuthToken).where(OAuthToken.access_prefix == prefix)
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                if not matches(token, row.access_hash):
                    return None
                if row.revoked_at is not None:
                    return None
                if row.access_expires_at <= datetime.utcnow():
                    return None
                return {
                    "id": row.id,
                    "user_id": row.user_id,
                    "workspace_id": row.workspace_id,
                    "scopes": list(row.scopes or []),
                    "last_used_at": row.last_used_at,
                }
            except Exception as e:
                logger.error(f"Could not resolve access token: {e}", exc_info=True)
                return None

    async def rotate_refresh(
        self, refresh_token: str, client_id: str
    ) -> Optional[Dict[str, Any]]:
        """Exchange a refresh token for a new pair, retiring the old one."""
        prefix = parse_prefix(refresh_token, REFRESH_TOKEN_TAG)
        if not prefix:
            return None
        async with self.get_async_session() as session:
            try:
                row = (
                    await session.execute(
                        select(OAuthToken).where(OAuthToken.refresh_prefix == prefix)
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                if not matches(refresh_token, row.refresh_hash or ""):
                    return None
                if row.revoked_at is not None:
                    return None
                if row.client_id != client_id:
                    return None
                grant = {
                    "id": row.id,
                    "user_id": row.user_id,
                    "workspace_id": row.workspace_id,
                    "scopes": list(row.scopes or []),
                }
            except Exception as e:
                logger.error(f"Could not read refresh token: {e}", exc_info=True)
                return None

        return await self.issue_tokens(
            client_id=client_id,
            user_id=grant["user_id"],
            workspace_id=grant["workspace_id"],
            scopes=grant["scopes"],
            replacing_id=grant["id"],
        )

    async def touch_last_used(
        self, token_id: int, last_used_at: Optional[datetime] = None
    ) -> None:
        """Record use, at most once a minute. Never raises."""
        now = datetime.utcnow()
        if last_used_at is not None and now - last_used_at < _LAST_USED_RESOLUTION:
            return
        async with self.get_async_session() as session:
            try:
                await session.execute(
                    update(OAuthToken)
                    .where(OAuthToken.id == token_id)
                    .values(last_used_at=now)
                )
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.warning(f"Could not record use of OAuth token {token_id}: {e}")

    async def list_for_workspace(self, workspace_id: int) -> List[Dict[str, Any]]:
        """Live grants on this workspace, newest first.

        Rotation writes a new row and revokes the old one, so unlike API keys
        this list drops revoked rows: a year of refreshes would otherwise bury
        the one connection a person is looking for under its own history.
        """
        async with self.get_async_session() as session:
            try:
                stmt = (
                    select(OAuthToken, OAuthClient.client_name)
                    .join(OAuthClient, OAuthClient.client_id == OAuthToken.client_id)
                    .where(
                        OAuthToken.workspace_id == workspace_id,
                        OAuthToken.revoked_at.is_(None),
                    )
                    .order_by(OAuthToken.created_at.desc())
                )
                rows = (await session.execute(stmt)).all()
                return [
                    {
                        "id": row[0].id,
                        "name": row[1],
                        "scopes": list(row[0].scopes or []),
                        "created_at": row[0].created_at,
                        "last_used_at": row[0].last_used_at,
                    }
                    for row in rows
                ]
            except Exception as e:
                logger.error(f"Could not list OAuth grants: {e}", exc_info=True)
                return []

    async def revoke(self, token_id: int, workspace_id: int) -> bool:
        """Withdraw a grant. Scoped by workspace so an id alone cannot reach one."""
        async with self.get_async_session() as session:
            try:
                row = (
                    await session.execute(
                        select(OAuthToken).where(
                            OAuthToken.id == token_id,
                            OAuthToken.workspace_id == workspace_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return False
                if row.revoked_at is None:
                    row.revoked_at = datetime.utcnow()
                    await session.commit()
                return True
            except Exception as e:
                await session.rollback()
                logger.error(f"Could not revoke OAuth token {token_id}: {e}", exc_info=True)
                return False


def _verifier_matches(verifier: str, challenge: str, method: str) -> bool:
    """PKCE. S256 only.

    `plain` is in the specification and is refused here: it makes the challenge
    equal to the verifier, so an intercepted authorization request carries
    everything needed to redeem the code it produces. Supporting it would mean
    a client could downgrade itself into offering no protection at all.
    """
    if not verifier or not challenge:
        return False
    if (method or "").upper() != "S256":
        return False
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return secrets.compare_digest(expected, challenge)
