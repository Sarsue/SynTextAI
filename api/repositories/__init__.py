"""
Repositories package for the SynTextAI application.

This package provides a clean interface for database access using the repository pattern.
It uses SQLAlchemy's async features for non-blocking database operations.

Key Components:
1. RepositoryManager: Central manager for all repositories
2. Base Classes: Common functionality for all repositories
3. Repository Implementations: Specific repositories for each domain model

Example Usage:
    # Get the repository manager
    from . import get_repository_manager
    
    # Get a repository and use it
    repo_manager = await get_repository_manager()
    user_repo = await repo_manager.user_repo
    user = await user_repo.get_user_by_id(user_id=1)
    
    # Or use as a context manager
    async with await get_repository_manager() as repo_manager:
        user_repo = await repo_manager.user_repo
        user = await user_repo.get_user_by_id(user_id=1)
"""
from typing import TYPE_CHECKING, TypeVar, Type, Any, Dict, Generic, Optional

# Core components
from .repository_manager import get_repository_manager, RepositoryManager, BaseRepositoryManager

# Type variables for generic repository types
ModelType = TypeVar("ModelType")
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")

# Repository implementations
from .async_user_repository import AsyncUserRepository
from .async_chat_repository import AsyncChatRepository
from .async_file_repository import AsyncFileRepository
from .async_learning_material_repository import AsyncLearningMaterialRepository

# Export commonly used types and classes
__all__ = [
    'get_repository_manager',
    'RepositoryManager',
    'BaseRepositoryManager',
    
    # Base repository class
    'AsyncBaseRepository',
    'ModelType',
    'CreateSchemaType',
    'UpdateSchemaType',
    
    # Repository implementations
    'AsyncUserRepository',
    'AsyncFileRepository',
    'AsyncChatRepository',
    'AsyncLearningMaterialRepository',
    
    # Type variables
    'ModelT',
    'CreateSchemaT',
    'UpdateSchemaT',
    
    # Repository implementations
    'AsyncUserRepository',
    'AsyncChatRepository',
    'AsyncFileRepository',
    'AsyncLearningMaterialRepository'
]

# Export type information for static type checkers
if TYPE_CHECKING:
    __all__.extend([
        'RepositoryManagerType',
        'AsyncBaseRepositoryType'
    ])
