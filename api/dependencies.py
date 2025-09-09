from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from typing import Dict, Any, Tuple
from .repositories.repository_manager import RepositoryManager, get_repository_manager
from .repositories.async_learning_material_repository import AsyncLearningMaterialRepository
from .utils import decode_firebase_token


async def get_store() -> Tuple[RepositoryManager, AsyncLearningMaterialRepository]:
    """FastAPI dependency to get repository manager and learning material repository instances."""
    repo_manager = get_repository_manager()
    await repo_manager.initialize()
    learning_material_repo = AsyncLearningMaterialRepository(repo_manager)
    return repo_manager, learning_material_repo


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


async def authenticate_user(
    token: str = Depends(oauth2_scheme),
    store: Tuple[RepositoryManager, AsyncLearningMaterialRepository] = Depends(get_store)
) -> Dict[str, Any]:
    """FastAPI dependency to authenticate a user via Firebase token."""
    try:
        user_info = decode_firebase_token(token)
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Add store to the user info for route handlers to use
        user_info['store'] = store[0]  # RepositoryManager
        user_info['learning_material_repo'] = store[1]  # AsyncLearningMaterialRepository
        return user_info
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
