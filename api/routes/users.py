from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from typing import Dict
from ..utils.utils import decode_firebase_token
from api.repositories.repository_manager import RepositoryManager
import logging
import stripe
from ..core.config import settings

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Initialize Stripe if API key is available
if settings.STRIPE_SECRET:
    stripe.api_key = settings.STRIPE_SECRET

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

    user = await store.user_repo.get_user_by_email(user_info['email'])
    if not user:
        logger.error(f"No user found with email: {user_info['email']}")
        raise HTTPException(status_code=404, detail="User not found")
    user_id = user.id

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
@users_router.post("", status_code=201) # This is the POST /api/v1/users endpoint
async def create_user( # Function name remains 'create_user'
    user_info: Dict = Depends(get_firebase_user_info_from_token), # Use the new dependency
    store: RepositoryManager = Depends(get_store)
):
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
    existing_user = await store.user_repo.get_user_by_email(email)
    if existing_user:
        logger.info(f"POST /users: User with email {email} already exists with ID {existing_user.id}. Returning 200 OK.")
        existing_user_id = existing_user.id
        return JSONResponse(
            content={"message": "User already registered", "email": email, "user_id": existing_user_id}, 
            status_code=200
        )

    # If user does not exist, create them
    try:
        logger.info(f"POST /users: Creating new user with email {email} and name {name}.")
        # Await the async add_user method
        new_user_id = await store.add_user(email, name, firebase_uid=firebase_uid)
        logger.info(f"POST /users: Successfully created new user with ID: {new_user_id}")
        return JSONResponse(
            content={"message": "User created successfully", "email": email, "user_id": new_user_id},
            status_code=201
        )
    except IntegrityError: 
        # This case should ideally be caught by the explicit check above.
        # If it happens, it means there's a race condition or get_user_by_email didn't find it but add_user did.
        logger.error(f"POST /users: IntegrityError while creating user {email}. This implies a race condition or inconsistent check.")
        # Returning 409 Conflict is more appropriate here than 400.
        raise HTTPException(status_code=409, detail=f"User with email {email} already exists (IntegrityError).")
    except Exception as e:
        logger.error(f"POST /users: Unexpected error creating user {email}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred during user creation.")

# User data deletion has been moved to RepositoryManager.delete_user_data
# This function is kept for backward compatibility but is now a thin wrapper
async def delete_user_data(user_id: str, user_gc_id: str, store: RepositoryManager) -> bool:
    """
    Wrapper function for backward compatibility.
    User data deletion logic has been moved to RepositoryManager.
    """
    logger.warning("delete_user_data helper is deprecated. Use RepositoryManager.delete_user_data instead.")
    return await store.delete_user_data(user_id=user_id, user_gc_id=user_gc_id)

# Route to delete a user
@users_router.delete("", status_code=200)
async def delete_user(
    user_data: Dict = Depends(authenticate_user),
    store: RepositoryManager = Depends(get_store)
):
    try:
        user_id = user_data["user_id"]
        user_info = user_data["user_info"]
        user_gc_id = str(user_id)  # Using user_id as GC ID

        # Delete user data using RepositoryManager
        success = await store.delete_user_data(
            user_id=user_id,
            user_gc_id=user_gc_id
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete user data"
            )
            
        return {
            "message": "User account and all associated data have been deleted.",
            "email": user_info['email']
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user account: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the user account"
        )