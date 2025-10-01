from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional, Any, cast
import logging
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError

from ..repositories import RepositoryManager, get_repository_manager

# Get repository manager dependency
async def get_repo_manager() -> RepositoryManager:
    repo_manager = await get_repository_manager()
    if not repo_manager._repos_initialized:
        await repo_manager._initialize_repositories()
    return repo_manager
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

    # Get user repository and check if user already exists by email
    try:
        user_repo = await repo_manager.user_repo
        existing_user = await user_repo.get_by_email(email)
        if existing_user:
            logger.info(f"User with email {email} already exists. Returning 200 OK.")
            return JSONResponse(
                content={"message": "User already registered", "email": email, "user_id": str(existing_user.id)}, 
                status_code=200
            )
    except Exception as e:
        logger.error(f"Error accessing user repository: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while accessing user data")

    # Create new user
    try:
        logger.info(f"Creating new user with email {email}")
        user_create = UserCreate(
            email=email,
            name=name,
            firebase_uid=firebase_uid,
            is_verified=True
        )
        
        # Get user repository and create user
        user_repo = await repo_manager.user_repo
        new_user = await user_repo.create(user_create)
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

# Route to delete a user
@users_router.delete("", status_code=200, response_model=Dict[str, str])
async def delete_user(
    request: Request,
    user_data: Dict = Depends(authenticate_user),
    repo_manager: RepositoryManager = Depends(get_repo_manager)
) -> Dict[str, str]:
    """
    Delete a user account and all associated data.
    
    This endpoint performs a soft delete of the user account by:
    1. Marking the user as inactive
    2. Anonymizing personal data
    3. Cleaning up related resources (subscriptions, files, etc.)
    
    The frontend should handle the response by:
    - Showing a success message
    - Clearing local storage/session
    - Redirecting to the home page
    
    Request Headers:
        - Authorization: Bearer <firebase_token>
        
    Returns:
        {
            "status": "success"|"error",
            "message": "Operation status message",
            "email": "user@example.com"
        }
    """
    user_id = user_data["user_id"]
    user_email = user_data["user_info"]['email']
    
    # Log the deletion attempt with request context
    logger.info(
        "Initiating user deletion",
        extra={
            "user_id": user_id,
            "email": user_email,
            "action": "account_deletion",
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent")
        }
    )
    
    try:
        # Get user repository
        user_repo = await repo_manager.user_repo
        
        # Perform the deletion
        success = await user_repo.delete_user_account(user_id)
        
        if not success:
            logger.error(
                "Failed to delete user account",
                extra={"user_id": user_id, "action": "account_deletion_failed"}
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to process account deletion"
            )
        
        # Log successful deletion
        logger.info(
            "User account successfully deleted",
            extra={
                "user_id": user_id,
                "email": user_email,
                "action": "account_deletion_success"
            }
        )
        
        return {
            "status": "success",
            "message": "Your account and all associated data have been deleted.",
            "email": user_email
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
        
    except Exception as e:
        # Log unexpected errors
        logger.critical(
            "Unexpected error during user deletion",
            extra={
                "user_id": user_id,
                "error": str(e),
                "action": "account_deletion_failed"
            },
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred while processing your request"
        )