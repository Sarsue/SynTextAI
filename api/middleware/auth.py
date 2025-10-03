"""
Authentication middleware for FastAPI applications.
Handles JWT token validation and user authentication.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, HTTPException, status
from typing import Dict, Any, Optional
import logging

from ..utils.utils import get_user_id
from ..repositories import get_repository_manager

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Global authentication middleware that validates JWT tokens for all requests.
    Implements user caching and proper error handling.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints
        if self._is_public_endpoint(request.url.path):
            return await call_next(request)
            
        # Extract token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("Missing or invalid Authorization header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.split(" ")[1]

        # Decode Firebase token
        success, user_info = get_user_id(token)
        if not success or not user_info.get("email"):
            logger.warning(f"Invalid or expired token for user: {user_info.get('email', 'unknown')}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Get repository manager and user data
        try:
            repo_manager = await get_repository_manager()
            if not getattr(repo_manager, "_initialized", False):
                if hasattr(repo_manager, "_initialize_repositories"):
                    await repo_manager._initialize_repositories()
                else:
                    logger.error("Repository manager is not properly initialized")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Server configuration error"
                    )

            # Lookup user in DB
            user = await repo_manager.user_repo.get_by_email(
                email=user_info["email"], 
                include_inactive=True
            )

            if not user:
                logger.warning(f"User not found: {user_info['email']}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )

            if not getattr(user, "is_active", True):
                logger.warning(f"Inactive user: {user_info['email']}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is deactivated"
                )

            # Attach user to request state
            request.state.user = {
                "id": str(user.id),
                "email": user.email,
                "user_gc_id": user_info.get("user_id"),
                "is_active": user.is_active,
            }

            logger.info(f"Authenticated user: {user.email}")
            return await call_next(request)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error during authentication"
            )
    
    def _is_public_endpoint(self, path: str) -> bool:
        """Check if the requested path is a public endpoint"""
        public_paths = {
            '/api/v1/health',
            '/api/v1/auth',
            '/api/v1/docs',
            '/api/v1/openapi.json',
            '/api/v1/redoc'
        }
        return any(path.startswith(p) for p in public_paths)

# This will be initialized in app.py after the FastAPI app is created
auth_middleware = None

async def get_current_user(request: Request) -> dict:
    """
    Dependency to get the current user from request state.
    
    Args:
        request: The incoming request
        
    Returns:
        dict: User information if authenticated
        
    Raises:
        HTTPException: If user is not authenticated
    """
    if not hasattr(request.state, 'user') or not request.state.user:
        logger.warning("Unauthenticated access attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return request.state.user
