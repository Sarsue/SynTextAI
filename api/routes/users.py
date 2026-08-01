from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from typing import Dict
from ..core.utils import decode_firebase_token
from api.workflows.tasks import delete_user_task
from api.repositories.repository_manager import RepositoryManager
import logging

from api.core.limits import resolve_entitlement
from api.core.seats import sync_seats_to_stripe


async def _bill_for_joins(store: RepositoryManager, joined: list) -> None:
    """Charge for seats taken by invites this sign-in accepted.

    Seats were synced only by the explicit accept route, which is the path
    almost nobody takes: signing in accepts every invite waiting for the
    address, so by the time the link is revisited the token is spent and that
    route never runs. The result was a member who joined the ordinary way
    costing nothing, forever. Found with two members on a subscription whose
    quantity was one.

    Never raises. The person has already joined; a Stripe hiccup must not turn
    that into a failed sign-in, and the next sync or the webhook corrects drift.
    """
    for organization_id in joined or []:
        await sync_seats_to_stripe(
            store, organization_id, reason="invite accepted at sign in"
        )

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI router
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# Helper function to authenticate user and retrieve user ID
async def authenticate_user(authorization: str = Header(None), store: RepositoryManager = Depends(get_store)):
    if not authorization or not authorization.startswith("Bearer "):
        logger.error("Invalid or missing Authorization token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    if not success:
        logger.error("Failed to authenticate user with token")
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = await store.user_repo.get_user_id_from_email(user_info['email'])
    if not user_id:
        logger.error(f"No user ID found for email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Authenticated user_id: {user_id}")
    return {"user_id": user_id, "user_info": user_info}

async def get_firebase_user_info_from_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("POST /users: Invalid or missing Authorization token for new user registration flow.")
        raise HTTPException(status_code=401, detail="Unauthorized: Missing or invalid token")
    token = authorization.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    if not success or not user_info: # Ensure user_info is not None
        logger.warning(f"POST /users: Failed to decode Firebase token or token yielded no user_info.")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or unparseable token")
    return user_info


# Route to create a new user
async def _start_organization(store: RepositoryManager, user_id: int, email: str):
    """Create an organization owned by this user, with a first workspace."""
    org_label = (email.split("@")[0] or "My").strip()
    organization_id = await store.org_repo.create_organization(
        name=f"{org_label}'s Organization",
        owner_user_id=user_id,
    )
    if not organization_id:
        logger.error(f"Failed to create organization for user {user_id}")
        return None
    await store.workspace_repo.create_workspace(
        user_id=user_id, name="My Workspace", organization_id=organization_id
    )
    logger.info(f"{email} started organization {organization_id} as owner")
    return organization_id


@users_router.post("", status_code=201) # This is the POST /api/v1/users endpoint
async def create_user(
    intent: str = Query(
        "signin",
        description="'signup' to start an organization you own, 'signin' to enter ones you belong to",
    ),
    user_info: Dict = Depends(get_firebase_user_info_from_token),
    store: RepositoryManager = Depends(get_store)
):
    """Register the caller, and act on what they came here to do.

    Signing up and signing in are different intents and were indistinguishable
    here: the endpoint keyed off whether a user row already existed, so anyone
    who did returned early no matter which button they pressed. An invited
    member could therefore never start a company of their own — every route in
    led back to the organization that had invited them.

    signup   Start an organization you own. Works whether or not you already
             belong to somebody else's, which is the case this could not
             express before. Idempotent: if you already own one, nothing
             happens.
    signin   Enter what you already belong to. Accepts any invite waiting for
             this address, and never creates an organization.
    """
    email = user_info.get('email')
    # Firebase tokens usually include 'name', but ensure a fallback or check if it's essential
    name = user_info.get('name') 
    firebase_uid = user_info.get('uid') # Or 'user_id' depending on your decode_firebase_token output for Firebase UID

    if not email:
        logger.error("POST /users: Email missing from Firebase token info.")
        raise HTTPException(status_code=400, detail="Email missing from token.")
    
    if not name: # Decide if name is critical, or use email as a placeholder
        logger.warning(f"POST /users: Name missing from Firebase token for email {email}. Using email as name.")
        name = email 

    # Check if user already exists by email
    wants_own_organization = (intent or "signin").lower() == "signup"

    existing_user_id = await store.user_repo.get_user_id_from_email(email)
    if existing_user_id:
        # Existing account. What happens next depends on why they are here.
        joined = await store.workspace_repo.accept_pending_invites_for_email(
            existing_user_id, email
        )
        if joined:
            logger.info(f"POST /users: {email} joined organization(s) {joined} by invitation")
            await _bill_for_joins(store, joined)

        organization_id = None
        if wants_own_organization:
            memberships = await store.org_repo.get_memberships(existing_user_id)
            owned = next((m for m in memberships if m["role"] == "owner"), None)
            if owned:
                organization_id = owned["organization_id"]
                logger.info(f"POST /users: {email} already owns organization {organization_id}")
            else:
                organization_id = await _start_organization(store, existing_user_id, email)

        return JSONResponse(
            content={
                "message": "User already registered",
                "email": email,
                "user_id": existing_user_id,
                "organization_id": organization_id,
            },
            status_code=200,
        )

    # If user does not exist, create them
    try:
        logger.info(f"POST /users: Creating new user with email {email} and name {name}.")
        # Ensure add_user can handle potential missing fields or has defaults
        # Also, consider if your store.add_user needs/accepts firebase_uid
        # add_user returns the new user's id, not an object. The previous code
        # read new_user.id and new_user.email, which raised AttributeError and
        # was swallowed, so no new account ever got its default workspace.
        new_user_id = await store.user_repo.add_user(email, name)
        if not new_user_id:
            raise HTTPException(status_code=500, detail="Could not create user.")
        logger.info(f"POST /users: Created user {new_user_id} ({email})")

        # One decision, made once: is this person joining a company or starting
        # one?
        #
        # Signing up with an address that was invited means joining, so the
        # invite is accepted here rather than depending on them clicking the
        # link at the right moment. The link is how somebody discovers they were
        # invited, not a step the join hinges on. Anyone else is starting their
        # own company and owns it.
        joined = await store.workspace_repo.accept_pending_invites_for_email(new_user_id, email)
        if joined:
            logger.info(f"POST /users: {email} joined organization(s) {joined} by invitation")
            await _bill_for_joins(store, joined)

        # An organization is started only by somebody who came to start one.
        # It used to be created for anyone without a pending invite, which made
        # signing in enough to become an owner.
        organization_id = None
        if wants_own_organization:
            organization_id = await _start_organization(store, new_user_id, email)

        return JSONResponse(
            content={
                "message": "User registered",
                "email": email,
                "user_id": new_user_id,
                "organization_id": organization_id,
                "joined_organizations": joined,
            },
            status_code=201,
        )
    except IntegrityError: 
        # This case should ideally be caught by the explicit check above.
        # If it happens, it means there's a race condition or get_user_id_from_email didn't find it but add_user did.
        logger.error(f"POST /users: IntegrityError while creating user {email}. This implies a race condition or inconsistent check.")
        # Returning 409 Conflict is more appropriate here than 400.
        raise HTTPException(status_code=409, detail=f"User with email {email} already exists (IntegrityError).")
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"POST /users: Unexpected error creating user {email}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred during user creation.")

# Route to delete a user
@users_router.delete("", status_code=200)
async def delete_user(
    background_tasks: BackgroundTasks,
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]
        user_gc_id = user_info['user_id']

        # Trigger Celery task to delete user and associated files
        background_tasks.add_task(delete_user_task, user_id, user_gc_id)
        return {"message": "User deletion in progress", "email": user_info['email']}
    except IntegrityError:
        logger.error(f"Database error while deleting user {user_info['email']}")
        raise HTTPException(status_code=500, detail="Failed to delete user due to database constraints")
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

@users_router.get("/quota", status_code=200)
async def get_user_quota(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    """Return real usage/quota information for the authenticated user.

    This is intended for UI display and should reflect backend enforcement.
    """
    try:
        user_id = user_data["user_id"]
        files_used = await store.file_repo.count_files_for_user(user_id)
        storage_used_bytes = await store.file_repo.total_storage_bytes_for_user(user_id)
        workspaces_used = await store.workspace_repo.count_workspaces_for_user(user_id)

        # Usage, not allowance. Nothing here is capped: a subscribed
        # organization is limited only by the seats on its plan, and an
        # unsubscribed one cannot reach the app at all. The limits reported
        # before described a free tier that no longer exists, so a paying
        # customer was shown "0 / 5 documents" against nothing.
        entitlement = await resolve_entitlement(store, user_id)

        return {
            "entitled": entitlement["entitled"],
            "plan": entitlement["status"],
            "files_used": files_used,
            "files_limit": None,
            "storage_used_bytes": storage_used_bytes,
            "storage_limit_bytes": None,
            "workspaces_used": workspaces_used,
            "workspaces_limit": None,
        }
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        logger.error(f"Error fetching quota for user {user_data.get('user_id')}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not fetch quota")