"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over specialized repositories while enforcing user_id for access control.
"""
from typing import Optional, List, Dict, Any, AsyncGenerator, TypeVar, Callable, Awaitable
import logging
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import sessionmaker

from api.models import User
from api.models.async_db import get_session_factory
from .domain_models import Flashcard, QuizQuestion
from .session_manager import SessionContextManager

logger = logging.getLogger(__name__)
T = TypeVar("T")

class RepositoryManager:
    """
    Repository manager coordinating access to all repositories.
    
    Can be used as an async context manager:
    
    async with RepositoryManager(DATABASE_URL) as repo_manager:
        # Use repo_manager here
        pass
    """
    
    def __init__(self, session_factory: Optional[sessionmaker] = None):
        """Initialize the repository manager with an optional session factory.
        
        Args:
            session_factory: Optional SQLAlchemy async session factory. If not provided,
                           the default factory from async_db will be used.
        """
        self.async_session_factory = session_factory or get_session_factory()
        self._initialized = True
        self._closed = False
        self._user_repo = None
        self._chat_repo = None
        self._file_repo = None

    async def initialize(self) -> None:
        """Initialize the repository manager.
        
        This is a no-op in the consolidated version since initialization happens in __init__.
        Kept for backward compatibility.
        """
        if self._initialized or self._closed:
            return

    async def __aenter__(self) -> "RepositoryManager":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the repository manager.
        
        This is a no-op in the consolidated version since connection management
        is handled by the async_db module. Kept for backward compatibility.
        """
        if self._closed:
            return
            
        self._closed = True

    def session_scope(self) -> SessionContextManager:
        """Return a session context manager for async DB operations."""
        if not self._initialized or self._closed:
            raise RuntimeError("RepositoryManager is not initialized or has been closed")
        return SessionContextManager(self.async_session_factory)

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session with automatic cleanup."""
        if not self._initialized or self._closed:
            raise RuntimeError("RepositoryManager is not initialized or has been closed")
            
        session = self.async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def execute_in_session(self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Execute async operation in a session with automatic commit/rollback."""
        async with self.session_scope() as session:
            return await operation(session)

    @property
    def user_repo(self):
        if self._user_repo is None:
            from .async_user_repository import AsyncUserRepository
            self._user_repo = AsyncUserRepository(self)
        return self._user_repo

    @property
    def chat_repo(self):
        if self._chat_repo is None:
            from .async_chat_repository import AsyncChatRepository
            self._chat_repo = AsyncChatRepository(self)
        return self._chat_repo

    @property
    def file_repo(self):
        if self._file_repo is None:
            from .async_file_repository import AsyncFileRepository
            self._file_repo = AsyncFileRepository(self)
        return self._file_repo
        
    async def get_user_id_from_email(self, email: str) -> Optional[int]:
        """
        Get a user's ID from their email address.
        
        Args:
            email: Email address to look up
            
        Returns:
            Optional[int]: User ID if found, None otherwise
        """
        user = await self.user_repo.get_user_by_email(email)
        return user.id if user else None
        
    async def add_user(self, email: str, name: str) -> int:
        """
        Add a new user to the database.
        
        Args:
            email: User's email address
            name: User's display name
            
        Returns:
            int: The ID of the newly created user
            
        Raises:
            IntegrityError: If a user with the email already exists
            Exception: For other database errors
        """
        return await self.user_repo.add_user(email, name)
        
    async def delete_user_data(self, user_id: str, user_gc_id: str) -> bool:
        """
        Delete a user and all their associated data.
        
        Args:
            user_id: The ID of the user to delete
            user_gc_id: The Google Cloud ID of the user (unused, kept for backward compatibility)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            async with self.session_scope() as session:
                # Delete the user, which will cascade to related data due to cascade="all, delete-orphan"
                user = await session.get(User, user_id)
                if user:
                    await session.delete(user)
                    await session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {str(e)}", exc_info=True)
            if 'session' in locals():
                await session.rollback()
            return False

    # Learning material operations are handled by AsyncLearningMaterialRepository

def get_repository_manager() -> RepositoryManager:
    """Return a new RepositoryManager instance using the centralized database connection.
    
    This is the single entry point for creating a RepositoryManager instance.
    It uses the centralized database connection from async_db.py.
    
    Returns:
        RepositoryManager: Initialized repository manager using the shared connection
    """
    return RepositoryManager()
