"""
Repositories package for the SynTextAI application.

This package contains modular repositories that implement the repository pattern,
separating domain models from database operations following the Single Responsibility Principle.
"""
from .repository_manager import RepositoryManager, get_repository_manager
from .async_base_repository import AsyncBaseRepository
from .async_user_repository import AsyncUserRepository
from .async_chat_repository import AsyncChatRepository
from .async_file_repository import AsyncFileRepository
from .async_learning_material_repository import AsyncLearningMaterialRepository
from .session_manager import SessionContextManager

__all__ = [
    'RepositoryManager',
    'get_repository_manager',
    'AsyncBaseRepository',
    'AsyncUserRepository',
    'AsyncChatRepository',
    'AsyncFileRepository',
    'AsyncLearningMaterialRepository',
    'SessionContextManager'
]
