from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from .repositories.repository_manager import RepositoryManager
from .utils import decode_firebase_token


def get_store():
    """FastAPI dependency to get a repository manager instance."""
    return RepositoryManager()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def authenticate_user(token: str = Depends(oauth2_scheme)):
    """FastAPI dependency to authenticate a user via Firebase token."""
    try:
        user_info = decode_firebase_token(token)
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_info
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
