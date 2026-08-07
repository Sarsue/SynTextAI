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
from typing import Any, Dict

from fastapi import Depends, Header, HTTPException, Request, status

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
