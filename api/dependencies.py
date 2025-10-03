"""
Dependency injection for the FastAPI application.

This module provides FastAPI dependencies for accessing the repository manager
and other shared resources in a request-safe manner.
"""
from contextlib import asynccontextmanager
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from typing import Dict, Any, Optional, AsyncGenerator
from .repositories import get_repository_manager, RepositoryManager
from .utils import decode_firebase_token
import logging

# Configure logging
logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_repository_manager_ctx() -> AsyncGenerator[RepositoryManager, None]:
    """
    Context manager for the repository manager.
    
    This ensures proper cleanup of the repository manager when done.
    Should be used in an async context:
    
    async with get_repository_manager_ctx() as repo_manager:
        # Use repo_manager here
        pass
    """
    repo_manager = await get_repository_manager()
    try:
        yield repo_manager
    except Exception as e:
        logger.error(f"Error in repository manager context: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Repository manager operation failed"
        ) from e
    finally:
        await repo_manager.close()


async def get_request_store(request: Request) -> RepositoryManager:
    """
    Get the repository manager from the request's app state.
    
    This is for use in routes that can't use the async dependency injection.
    The repository manager is stored in the app state on first access.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        RepositoryManager: The repository manager instance
        
    Raises:
        HTTPException: If the repository manager cannot be initialized
    """
    if not hasattr(request.app.state, 'repo_manager') or request.app.state.repo_manager is None:
        try:
            repo_manager = await get_repository_manager()
            request.app.state.repo_manager = repo_manager
        except Exception as e:
            logger.error(f"Error getting repository manager: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to get repository manager"
            ) from e
    return request.app.state.repo_manager


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def authenticate_user(
    request: Request,
    token: str = Depends(oauth2_scheme)
) -> Dict[str, Any]:
    """
    FastAPI dependency to authenticate a user via Firebase token.
    
    Args:
        request: The FastAPI request object
        token: The Firebase authentication token
        
    Returns:
        Dict containing user information and store references
        
    Raises:
        HTTPException: If authentication fails
    """
    try:
        if not token:
            logger.error("No authentication token provided")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authentication token provided",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        success, user_info = decode_firebase_token(token)
        if not success or not user_info:
            logger.error(f"Failed to decode Firebase token: {user_info.get('error', 'Unknown error')}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=user_info.get('error', 'Invalid authentication token'),
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        # Get the repository manager
        repo_manager = await get_repository_manager()
        
        # Get the internal user ID from the Firebase UID
        user_repo = await repo_manager.user_repo
        user = await user_repo.get_user_by_firebase_id(user_info.get('uid'))
        
        if not user:
            logger.error(f"No user found for Firebase UID: {user_info.get('uid')}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        # Add user info to the request state for use in route handlers
        request.state.user = {
            'user_id': user.id,
            'email': user_info.get('email'),
            'firebase_uid': user_info.get('uid')
        }
        
        # Add repository manager to the request state
        request.state.repo_manager = repo_manager
        
        logger.info(f"Authenticated user: {user_info.get('email', 'unknown')} (ID: {user.id})")
        return user_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during authentication: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during authentication",
            headers={"WWW-Authenticate": "Bearer"},
        )
