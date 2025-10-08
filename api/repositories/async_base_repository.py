"""
Async base repository class that all async repositories will inherit from.

This module mirrors the sync base_repository.py but provides async functionality
while maintaining identical method signatures and return types.
"""
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import logging
import os
from typing import Callable, TypeVar, Generic, Optional, Any, AsyncGenerator

from ..models.async_db import get_engine, get_session_factory, get_async_session, init_db, close_db

logger = logging.getLogger(__name__)


class AsyncBaseRepository(ABC):
    """Async base repository class that all async repositories will inherit from."""

    def __init__(self, database_url: str = None):
        """Initialize the async repository with database connection.

        Args:
            database_url: The database connection URL. If None, use environment variable.
        """
        if database_url is None:
            database_url = os.environ.get('DATABASE_URL')
            if not database_url:
                raise ValueError("No database URL provided and DATABASE_URL environment variable not set")

        self.database_url = database_url
        self.engine = get_engine()
        self.session_factory = get_session_factory()
        self.session_scope = get_async_session

    async def get_session(self) -> AsyncSession:
        """Get a new async database session."""
        return self.session_factory()

    def get_async_session(self):
        """Get an async database session manager.

        Returns:
            AsyncSessionManager: An async context manager for database sessions.
        """
        return self.session_scope()

    async def commit_and_close(self, session: AsyncSession, operation_name: str) -> bool:
        """Commit changes and close the async session, with error handling.

        Args:
            session: The async session to commit and close
            operation_name: Name of the operation for logging purposes

        Returns:
            bool: Success status of the operation
        """
        try:
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            logger.error(f"Error committing {operation_name}: {e}", exc_info=True)
            return False

    async def execute_with_session(self, operation_func: Callable[[AsyncSession], Any], operation_name: str) -> Any:
        """Execute an operation with an async session and handle errors.

        Args:
            operation_func: Function that takes a session and returns a result
            operation_name: Name of the operation for logging purposes

        Returns:
            Any: The result of the operation, or None if it failed
        """
        try:
            async with self.get_async_session() as session:
                result = await operation_func(session)
                await session.commit()
                logger.debug(f"Successfully executed {operation_name}")
                return result
        except Exception as e:
            logger.error(f"Error during {operation_name}: {e}", exc_info=True)
            return None


class AsyncRepositoryError(Exception):
    """Exception raised by async repository operations."""
    pass
