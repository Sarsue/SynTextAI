"""Issuing, listing and revoking the credentials an integration holds.

WHO MAY CALL THIS

`authenticate_user`, not `authenticate_api_caller`. Deliberately: minting a
credential is not something a credential may do. Allowing it would let one key
create a second one, and the second would outlive any revocation of the first —
a credential that can reproduce cannot be withdrawn.

So this whole module is browser-only, and the ceiling logic that the search route
needs does not appear here at all.

WHAT IS RETURNED

The token exists in one response, the 201 from create, and nowhere else. Nothing
stores it, so nothing can show it again. That is why `name` is required rather
than optional: after this response the name is the only thing distinguishing two
rows from each other when somebody decides which to revoke.
"""
from datetime import datetime
from typing import List, Optional
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from ..core.auth import authenticate_user, get_store
from ..core.permissions import Capability, assert_workspace_capability
from ..repositories.repository_manager import RepositoryManager

logger = logging.getLogger(__name__)

api_keys_router = APIRouter(prefix="/api/v1/workspaces", tags=["api-keys"])


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    # Absent means no expiry, and that is the default offered in the UI. Forced
    # rotation nobody asked for mostly produces integrations that break
    # silently; revocation plus a last-used column answers the same worry
    # without the breakage.
    expires_at: Optional[datetime] = None


class ApiKeySummary(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: List[str]
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class CreatedApiKey(ApiKeySummary):
    # The only response that ever carries this.
    token: str


@api_keys_router.post(
    "/{workspace_id}/api-keys",
    response_model=CreatedApiKey,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    workspace_id: int = Path(...),
    body: CreateApiKeyRequest = Body(...),
    user_data: dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
) -> CreatedApiKey:
    """Issue a key for this workspace. The token is shown once, here."""
    user_id = user_data["user_id"]
    await assert_workspace_capability(
        store, user_id, workspace_id, Capability.MANAGE_API_KEYS
    )

    if body.expires_at is not None and body.expires_at <= datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An expiry date has to be in the future.",
        )

    created = await store.api_key_repo.create_api_key(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=body.name.strip(),
        expires_at=body.expires_at,
    )
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the key. Try again.",
        )
    # Never the token, and never the prefix either: the prefix identifies one
    # credential, and this line runs on a path a customer's name reaches.
    logger.info("API key created for workspace %s", workspace_id)
    return CreatedApiKey(**created)


@api_keys_router.get("/{workspace_id}/api-keys", response_model=List[ApiKeySummary])
async def list_api_keys(
    workspace_id: int = Path(...),
    user_data: dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
) -> List[ApiKeySummary]:
    """Every key on this workspace, revoked ones included."""
    user_id = user_data["user_id"]
    await assert_workspace_capability(
        store, user_id, workspace_id, Capability.MANAGE_API_KEYS
    )
    rows = await store.api_key_repo.list_for_workspace(workspace_id)
    return [ApiKeySummary(**{k: v for k, v in r.items() if k != "created_by_user_id"}) for r in rows]


@api_keys_router.delete(
    "/{workspace_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_api_key(
    workspace_id: int = Path(...),
    key_id: int = Path(...),
    user_data: dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store),
) -> None:
    """Withdraw a key. Immediate: the next request carrying it is refused."""
    user_id = user_data["user_id"]
    await assert_workspace_capability(
        store, user_id, workspace_id, Capability.MANAGE_API_KEYS
    )
    # 404 rather than 403 when the key belongs to another workspace, which is
    # the rule everywhere here for a resource named by an id.
    if not await store.api_key_repo.revoke(key_id, workspace_id):
        raise HTTPException(status_code=404, detail="Key not found")
