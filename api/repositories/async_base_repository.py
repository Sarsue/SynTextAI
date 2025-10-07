"""
Async Base Repository interface defining common async methods and database connection handling.

This module mirrors the sync base_repository.py but provides async functionality
while maintaining identical method signatures and return types.
"""
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import logging
import os
from typing import Callable, TypeVar, Generic, Optional, Any, AsyncGenerator

from ..models.async_db import get_async_db

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
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.AsyncSession = async_sessionmaker(bind=self.engine, class_=AsyncSession)

    async def get_session(self) -> AsyncSession:
        """Get a new async database session."""
        return self.AsyncSession()

    async def get_async_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session as context manager.

        This mirrors the sync get_db() function signature exactly.

        Yields:
            AsyncSession: An async database session that is automatically closed after use.
        """
        async with self.AsyncSession() as session:
            try:
                yield session
            finally:
                await session.close()

    async def commit_and_close(self, session: AsyncSession, operation_name: str) -> bool:
        """Commit changes and close the async session, with error handling.

        Args:
            session: The async SQLAlchemy session
            operation_name: Name of the operation for logging purposes

        Returns:
            bool: Success status of the operation
        """
        try:
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            logger.error(f"Error during {operation_name}: {e}", exc_info=True)
            return False
        finally:
            await session.close()

    async def execute_transactional_async(self, operation_func: Callable, operation_name: str = "operation"):
        """Execute an async function within a transaction boundary.

        Args:
            operation_func: Async function that takes a session parameter and performs DB operations
            operation_name: Name of operation for logging purposes

        Returns:
            The result of operation_func, or None if an exception occurred

        Example:
            async def add_entity(session, entity):
                session.add(entity)
                await session.flush()
                return entity.id

            result = await repo.execute_transactional_async(
                lambda session: add_entity(session, new_entity)
            )
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
