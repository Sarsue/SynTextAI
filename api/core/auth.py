"""Who is calling, decided in one place.

WHY THIS EXISTS

Seven route modules each defined their own authenticate_user, in five different
variants. Three were identical, two read the header off the Request object
instead of taking it as a parameter, one dropped the logging, and one validated
the "Bearer " prefix itself and called decode_firebase_token directly rather
than going through get_user_id.

None of the differences were deliberate. They are what happens when a function
is copied into a new file and then edited in place, and they matter more here
than anywhere else in the codebase: this is the function that decides whose data
a request may touch. Five variants means a fix applied to one is missing from
four, and nothing says so.

files.py also returned a different shape, {"user_id", "user_gc_id"}, where every
other module returned {"user_id", "user_info"}. No route ever read user_gc_id
off it; users.py builds its own from user_info when it needs one. So the shape
below is what all 41 call sites already use.

WHAT IS DELIBERATE HERE

The header is a parameter rather than something read off Request, because that
is what lets FastAPI document it and lets a test override this dependency
cleanly.

A missing or unusable token is 401. A token that verifies against Firebase but
belongs to no row here is 404, not 401: the caller proved who they are, and
there is simply no account. Collapsing those two would tell a stranger with a
valid Google account that their email is unknown to us, and would tell a real
user whose row is missing that their credentials are wrong. Both are worse.

Token verification itself lives in core/utils.get_user_id and is not repeated.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from ..core.api_keys import looks_like_access_token, looks_like_api_key
from ..core.permissions import Capability, capabilities_for_scopes
from ..core.utils import get_user_id
from ..repositories.repository_manager import RepositoryManager

logger = logging.getLogger(__name__)


def get_store(request: Request) -> RepositoryManager:
    """The repository manager the app started with."""
    return request.app.state.store


async def authenticate_user(
    authorization: str = Header(None),
    store: RepositoryManager = Depends(get_store),
) -> Dict[str, Any]:
    """Resolve the caller to a user row, or refuse.

    Returns {"user_id": int, "user_info": dict}. user_id is this system's id;
    user_info carries what the token asserted, including the Firebase uid under
    "user_id", which is why the two are kept apart rather than flattened.
    """
    if not authorization:
        logger.info("Request without an Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    success, user_info = get_user_id(authorization)
    if not success or not user_info:
        logger.info("Token failed verification")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    email = user_info.get("email")
    if not email:
        # A verified token with no email cannot be matched to a row, and
        # every account here is keyed by email.
        logger.warning("Verified token carried no email")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    user_id = await store.user_repo.get_user_id_from_email(email)
    if not user_id:
        # Deliberately not logged with the email attached: this runs on every
        # request and the address is customer data.
        logger.info("Verified token belongs to no account here")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return {"user_id": user_id, "user_info": user_info}


# ---------------------------------------------------------------------------
# The second door: a credential a program can hold.
#
# authenticate_user above is unchanged and stays the only door for the site
# itself. Everything below is additive, and no existing call site moves.
#
# The shape here is deliberately not "API key authentication". It is a Principal
# produced by one of several credential resolvers, because MCP and OAuth are
# coming and they are further ways to establish identity, not further
# authorization systems. A new credential type becomes a resolver in _RESOLVERS
# and changes nothing else.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Principal:
    """Who is calling, and the most they are allowed to be.

    A ceiling is not a grant. It only ever narrows what the underlying account
    can already do, so a credential can never reach past its creator. Both
    ceilings are None for a human, which is what makes the browser path behave
    exactly as it did before this existed.

    Note what is absent: no role, and no list of workspaces copied from the
    credential. Those are looked up live, per request, from created_by_user_id.
    A credential that carried its own role would keep working after the person
    it was cut from lost the access it was cut from.
    """

    user_id: int
    user_info: dict
    auth_method: str  # "firebase" | "api_key" | "oauth"
    # Recorded against usage so a query made by an integration is still visible
    # in the Usage panel. Named for the credential rather than the API key so
    # an OAuth token can be attributed the same way without a migration.
    credential_id: Optional[int] = None
    workspace_ceiling: Optional[FrozenSet[int]] = None
    capability_ceiling: Optional[FrozenSet[Capability]] = None

    @property
    def is_human(self) -> bool:
        return self.auth_method == "firebase"

    def limit_workspaces(self, allowed: Iterable[int]) -> List[int]:
        """Narrow a live access list to what this credential may reach.

        `allowed` must come from accessible_workspace_ids, never from the
        credential. This intersects; it cannot add. That is the whole of the
        workspace half of authorization for a machine caller.
        """
        ids = list(allowed or [])
        if self.workspace_ceiling is None:
            return ids
        return [i for i in ids if i in self.workspace_ceiling]

    def permits(self, capability: Capability) -> bool:
        """Whether the credential allows this, before any role is consulted.

        The role check still has to pass separately. Both gates, always: this
        one stops an owner's read-only key from deleting a document, and the
        role check stops a staff member's key from doing what staff may not.
        """
        if self.capability_ceiling is None:
            return True
        return capability in self.capability_ceiling

    def legacy_user_data(self) -> dict:
        """The {"user_id", "user_info"} shape the existing 41 call sites expect."""
        return {"user_id": self.user_id, "user_info": self.user_info}


async def _resolve_firebase(credential: str, store: RepositoryManager) -> Principal:
    user_data = await authenticate_user(authorization=credential, store=store)
    return Principal(
        user_id=user_data["user_id"],
        user_info=user_data["user_info"],
        auth_method="firebase",
    )


async def _resolve_api_key(credential: str, store: RepositoryManager) -> Principal:
    token = credential.split("Bearer ", 1)[-1].strip()
    key = await store.api_key_repo.resolve(token)
    if key is None:
        # One refusal for unparseable, unknown, wrong secret, revoked and
        # expired alike. Distinguishing them tells a stranger which of those
        # they got right.
        logger.info("API key failed verification")
        raise _unauthorized()

    await store.api_key_repo.touch_last_used(key["id"], key.get("last_used_at"))

    return Principal(
        user_id=key["created_by_user_id"],
        user_info={"auth_method": "api_key", "key_id": key["id"]},
        auth_method="api_key",
        credential_id=key["id"],
        workspace_ceiling=frozenset({key["workspace_id"]}),
        capability_ceiling=capabilities_for_scopes(key["scopes"]),
    )


async def _resolve_access_token(credential: str, store: RepositoryManager) -> Principal:
    """An OAuth grant, resolved exactly the way an API key is.

    The point of this function is how little there is in it. A grant carries a
    user, a workspace and scopes, the same three things a key carries, so it
    produces the same Principal and everything downstream is unchanged. The
    authorization server in routes/oauth.py is a way of establishing identity,
    not a second way of deciding permission.
    """
    token = credential.split("Bearer ", 1)[-1].strip()
    grant = await store.oauth_repo.resolve_access(token)
    if grant is None:
        logger.info("Access token failed verification")
        raise _unauthorized()

    await store.oauth_repo.touch_last_used(grant["id"], grant.get("last_used_at"))

    return Principal(
        user_id=grant["user_id"],
        user_info={"auth_method": "oauth", "grant_id": grant["id"]},
        auth_method="oauth",
        credential_id=grant["id"],
        workspace_ceiling=frozenset({grant["workspace_id"]}),
        capability_ceiling=capabilities_for_scopes(grant["scopes"]),
    )


# Ordered. The first detector that claims the credential resolves it, which is
# what kept adding OAuth to a single entry here rather than a third branch
# inside a function that had already grown twice.
_RESOLVERS = (
    (lambda c: looks_like_api_key(c.split("Bearer ", 1)[-1].strip()), _resolve_api_key),
    (
        lambda c: looks_like_access_token(c.split("Bearer ", 1)[-1].strip()),
        _resolve_access_token,
    ),
    (lambda c: True, _resolve_firebase),
)


def _unauthorized() -> HTTPException:
    """401 with the pointer a client needs to go and get a token.

    RFC 9728. Without this header an MCP client meeting a 401 has no way to
    discover that there IS an authorization server, so it reports "unauthorized"
    and stops instead of starting the flow that would fix it. The one header is
    the difference between a dead end and a consent screen.
    """
    base = (os.getenv("APP_URL") or "http://localhost:3000").rstrip("/")
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={
            "WWW-Authenticate": (
                'Bearer resource_metadata='
                f'"{base}/.well-known/oauth-protected-resource"'
            )
        },
    )


async def authenticate_api_caller(
    authorization: str = Header(None),
    store: RepositoryManager = Depends(get_store),
) -> Principal:
    """Resolve any supported credential to a Principal.

    Only routes that have opted in take this dependency. That allowlist is
    enforced by a test, and it is load-bearing rather than tidy: the eleven
    tenant checks in test_every_route_is_scoped.py are not all ceiling-aware
    yet, so a Principal is safe on a route that has been taught about it and
    nowhere else.
    """
    if not authorization:
        logger.info("Request without an Authorization header")
        raise _unauthorized()

    for claims, resolve in _RESOLVERS:
        if claims(authorization):
            return await resolve(authorization, store)

    raise _unauthorized()
