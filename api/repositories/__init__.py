"""
Repositories package for the SynTextAI application.

This package implements the Repository Pattern to provide a clean separation between
the domain model and data mapping layers. It abstracts the data access logic and
provides a collection-like interface for accessing domain objects.

Key Components:
    - BaseRepositoryManager: Singleton that manages database connections and sessions
    - AsyncBaseRepository: Base class for all async repositories
    - Specialized repositories for each domain entity (User, Chat, File, etc.)

Usage:
    # Get the repository manager (singleton)
    repo_manager = get_repository_manager()
    
    # Initialize the repository manager (typically done at app startup)
    await repo_manager.initialize()
    
    # Use repositories through the manager
    user_repo = await repo_manager.user_repo
    user = await user_repo.get_user_by_id(user_id=1)
    
    # Or use as a context manager
    async with RepositoryManager() as repo_manager:
        user_repo = await repo_manager.user_repo
        user = await user_repo.get_user_by_id(user_id=1)
"""
from typing import TYPE_CHECKING, Type, TypeVar

# Import core repository components
from .base_repository_manager import (
    RepositoryManager,
    get_repository_manager,
    BaseRepositoryManager
)

# Import base repository class
from .async_base_repository import AsyncBaseRepository, ModelType, CreateSchemaType, UpdateSchemaType

# Import all repository implementations
from .async_user_repository import AsyncUserRepository
from .async_chat_repository import AsyncChatRepository
from .async_file_repository import AsyncFileRepository
from .async_learning_material_repository import AsyncLearningMaterialRepository

# Add type stubs for better IDE support
if TYPE_CHECKING:
    from .base_repository_manager import RepositoryManager as RepositoryManagerType
    from .async_base_repository import AsyncBaseRepository as AsyncBaseRepositoryType
    from .async_user_repository import AsyncUserRepository as AsyncUserRepositoryType
    from .async_file_repository import AsyncFileRepository as AsyncFileRepositoryType
    from .async_chat_repository import AsyncChatRepository as AsyncChatRepositoryType
    from .async_learning_material_repository import AsyncLearningMaterialRepository as AsyncLearningMaterialRepositoryType

# Type variables for generic repository types
T = TypeVar('T')
ModelT = TypeVar('ModelT', bound=ModelType)
CreateSchemaT = TypeVar('CreateSchemaT', bound=CreateSchemaType)
UpdateSchemaT = TypeVar('UpdateSchemaT', bound=UpdateSchemaType)

# Public API
__all__ = [
    # Core components
    'BaseRepositoryManager',
    'RepositoryManager',
    'get_repository_manager',
    
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
