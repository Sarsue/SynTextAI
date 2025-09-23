from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Any, cast
import logging
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError

from ..repositories import RepositoryManager, get_repository_manager

# Get repository manager dependency
async def get_repo_manager() -> RepositoryManager:
    return await get_repository_manager()
from ..models.orm_models import User
from ..models.user_schemas import UserCreate, UserResponse, UserInDB, UserUpdate, UserRole
from ..dependencies import authenticate_user, decode_firebase_token

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])

# Repository manager is provided by get_repository_manager() from repository_manager

async def get_firebase_user_info_from_token(authorization: str = Header(None)) -> Dict[str, Any]:
    """Extract and validate Firebase user info from authorization token.
    
    Args:
        authorization: The authorization header value
        
    Returns:
        Dict containing user info from Firebase token
        
    Raises:
        HTTPException: If token is invalid or missing required claims
    """
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("Invalid or missing Authorization token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization token"
        )
        
    token = authorization.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)
    
    if not success or not user_info:
        logger.warning("Failed to decode Firebase token or token is invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unparseable token"
        )
        
    if not user_info.get('email'):
        logger.warning("Token is missing email claim")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token is missing required email claim"
        )
        
    return cast(Dict[str, Any], user_info)


# Route to create a new user
@users_router.post("", status_code=201)
async def create_user(
    user_info: Dict[str, Any] = Depends(get_firebase_user_info_from_token),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
) -> JSONResponse:
    email = user_info.get('email')
    name = user_info.get('name', email)  # Fallback to email if name not provided
    firebase_uid = user_info.get('uid')

    if not email:
        logger.error("POST /users: Email missing from Firebase token info.")
        raise HTTPException(status_code=400, detail="Email missing from token.")

    # Check if user already exists by email
    existing_user = await repo_manager.user_repo.get_user_by_email(email)
    if existing_user:
        logger.info(f"User with email {email} already exists. Returning 200 OK.")
        return JSONResponse(
            content={"message": "User already registered", "email": email, "user_id": str(existing_user.id)}, 
            status_code=200
        )

    # Create new user
    try:
        logger.info(f"Creating new user with email {email}")
        user_create = UserCreate(
            email=email,
            name=name,
            firebase_uid=firebase_uid,
            is_active=True,
            is_verified=True
        )
        
        # Use RepositoryManager to create user
        new_user = await repo_manager.user_repo.create(user_create)
        logger.info(f"Successfully created new user with ID: {new_user.id}")
        
        return JSONResponse(
            content={"message": "User created successfully", "email": email, "user_id": str(new_user.id)},
            status_code=201
        )
    except IntegrityError as e:
        logger.error(f"IntegrityError while creating user {email}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=409, detail=f"User with email {email} already exists.")
    except Exception as e:
        logger.error(f"Unexpected error creating user {email}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred during user creation.")

async def delete_user_task(
    user_id: str, 
    user_email: str, 
    repo_manager: RepositoryManager
) -> None:
    """Background task to handle user data deletion."""
    try:
        logger.info(f"Starting user data deletion for {user_email}")
        success = await repo_manager.delete_user_data(user_id=user_id, user_gc_id=user_id)
        if not success:
            logger.error(f"Failed to delete user data for {user_email}")
            raise HTTPException(
                status_code=500,
                detail="Failed to delete user data"
            )
        logger.info(f"Successfully deleted data for user {user_email}")
    except Exception as e:
        logger.error(f"Error in delete_user_task for {user_email}: {str(e)}", exc_info=True)
        raise

# Route to delete a user
@users_router.delete("", status_code=200, response_model=Dict[str, str])
async def delete_user(
    background_tasks: BackgroundTasks,
    user_data: Dict[str, Any] = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
) -> Dict[str, str]:
    try:
        user_id = user_data["user_id"]
        user_email = user_data["user_info"]['email']

        # Verify user exists and get email
        user = await repo_manager.user_repo.get(user_id)
        if not user:
            logger.warning(f"User {user_id} not found for deletion")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
            
        # Use email from user record if not provided
        user_email = user_email or user.email

        # Trigger background task to delete user and associated files
        background_tasks.add_task(delete_user_task, str(user_id), user_email, repo_manager)
        
        return {
            "status": "success",
            "message": "User deletion in progress", 
            "email": user_email
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in delete_user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")