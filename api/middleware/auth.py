"""
Authentication middleware for FastAPI applications.
Handles JWT token validation and user authentication using a provided RepositoryManager.
"""

import logging
from typing import Optional

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from ..utils import utils
from ..repositories import RepositoryManager

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Global authentication middleware that validates JWT tokens for all incoming requests.
    Uses a passed-in RepositoryManager instance to fetch and verify user data.
    """

    def __init__(self, app, repo_manager: RepositoryManager):
        super().__init__(app)
        self.repo_manager = repo_manager

    async def dispatch(self, request: Request, call_next):
        # Skip authentication for public endpoints
        if self._is_public_endpoint(request.url.path):
            return await call_next(request)

        # Extract the Authorization header
        auth_header: Optional[str] = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning(f"Missing or invalid Authorization header for path: {request.url.path}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.split(" ", 1)[1].strip()

        # Decode Firebase / JWT token
        success, user_info = utils.get_user_id(token)
        if not success or not user_info.get("email"):
            logger.warning(f"Invalid or expired token: {user_info}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            # Ensure repository manager is initialized
            if not getattr(self.repo_manager, "_initialized", False):
                if hasattr(self.repo_manager, "_initialize_repositories"):
                    await self.repo_manager._initialize_repositories()
                    self.repo_manager._initialized = True
                    logger.info("RepositoryManager initialized inside AuthMiddleware.")
                else:
                    logger.error("RepositoryManager missing initialization method.")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Server misconfiguration: RepositoryManager not initialized"
                    )

            # Fetch user from DB
            user = await self.repo_manager.user_repo.get_by_email(
                email=user_info["email"],
                include_inactive=True
            )
            if not user:
                logger.warning(f"User not found: {user_info['email']}")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

            # Attach authenticated user info to request.state
            request.state.user = {
                "id": str(user.id),
                "email": user.email,
                "user_gc_id": user_info.get("user_id"),
                "is_active": user.is_active,
            }

            logger.debug(f"Authenticated request for user: {user.email} | Path: {request.url.path}")
            return await call_next(request)

        except HTTPException:
            raise  # Reraise HTTP exceptions to be handled by FastAPI
        except Exception as e:
            logger.exception(f"Unexpected error during authentication: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error during authentication"
            )
    # ----------------------------------------------------------------------

    def _is_public_endpoint(self, path: str) -> bool:
        """Check if the requested path is a public endpoint"""
        public_paths = (
            '/api/v1/health',
            '/api/v1/auth',
            '/api/v1/docs',
            '/api/v1/openapi.json',
            '/api/v1/redoc'
        )
        return any(path.startswith(p) for p in public_paths)


# ----------------------------------------------------------------------

# FastAPI dependency for routes
async def get_current_user(request: Request) -> dict:
    """
    Dependency to retrieve the authenticated user from request.state.
    Ensures that user info is available in the request context.
    """
    user = getattr(request.state, "user", None)
    if not user:
        logger.warning("Unauthenticated access attempt detected.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
