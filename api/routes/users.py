from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status, Response, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user_schemas import UserCreate, UserResponse, UserInDB, UserUpdate, UserRole
from ..repositories import AsyncUserRepository, RepositoryManager
from ..dependencies import get_repository_manager
from ..models.user import UserInDB
from ..middleware.auth import get_current_user

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/api/v1/users", tags=["users"])

async def get_current_user(request: Request) -> Dict[str, Any]:
    """Get the current authenticated user from request state."""
    if not hasattr(request.state, 'user'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return request.state.user

@router.post("", status_code=201, response_model=Dict[str, Any])
async def create_user(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Create a new user or return existing user if already registered.
    
    This endpoint is called after successful Firebase authentication.
    It checks if the user exists by email, and if not, creates a new user record.
    
    Args:
        request: The incoming HTTP request containing user info in state
        user: The authenticated user from Firebase
        
    Returns:
        Dict containing user creation status and user details
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Check if user already exists
            existing_user = await user_repo.get_user_by_email(user.email, session=session)
            if existing_user:
                logger.info(f"User with email {user.email} already exists")
                return {
                    "status": "success",
                    "message": "User already registered",
                    "user_id": str(existing_user.id),
                    "email": existing_user.email,
                    "is_new_user": False
                }
            
            # Create new user
            user_data = UserCreate(
                email=user.email,
                name=user.name or user.email.split('@')[0],
                firebase_uid=user.firebase_uid,
                is_verified=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            new_user = await user_repo.create_user(user_data, session=session)
            logger.info(f"Created new user with ID: {new_user.id}")
            
            return {
                "status": "success",
                "message": "User created successfully",
                "user_id": str(new_user.id),
                "email": new_user.email,
                "is_new_user": True
            }
            
    except IntegrityError as e:
        logger.error(f"Integrity error creating user {user.email}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists"
        )
    except Exception as e:
        logger.error(f"Error creating user {user.email}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while creating user"
        )

@router.delete("", status_code=200, response_model=Dict[str, str])
async def delete_user(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
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
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict with status, message, and email of the deleted user
    """
    # Log the deletion attempt with request context
    logger.info(
        "Initiating user deletion",
        extra={
            "user_id": str(user.id),
            "email": user.email,
            "action": "account_deletion",
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent")
        }
    )
    
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        async with repo_manager.session_scope() as session:
            user_repo = await repo_manager.user_repo
            
            # Verify the user exists and get fresh data
            current_user = await user_repo.get_user_by_id(user.id, session=session)
            if not current_user:
                logger.warning(f"User with ID {user.id} not found for deletion")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            
            # Perform the soft delete
            success = await user_repo.soft_delete_user(
                user_id=user.id,
                session=session
            )
            
            if not success:
                logger.error(
                    "Failed to delete user account",
                    extra={"user_id": str(user.id), "action": "account_deletion_failed"}
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to process account deletion"
                )
            
            # Commit the transaction
            await session.commit()
            
            # Log successful deletion
            logger.info(
                "User account successfully deleted",
                extra={
                    "user_id": str(user.id),
                    "email": user.email,
                    "action": "account_deletion_success"
                }
            )
            
            return {
                "status": "success",
                "message": "Your account and all associated data have been deleted.",
                "email": user.email
            }
            
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
        
    except Exception as e:
        # Log unexpected errors
        logger.critical(
            "Unexpected error during user deletion",
            extra={
                "user_id": str(user.id) if user else None,
                "error": str(e),
                "action": "account_deletion_failed"
            },
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing your request"
        )

# Export the router
users_router = router