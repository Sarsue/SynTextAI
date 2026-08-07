"""
Async base repository class that all async repositories will inherit from.

This module mirrors the sync base_repository.py but provides async functionality
while maintaining identical method signatures and return types.
"""
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import logging
import os
from typing import Callable, TypeVar, Optional, Any

from ..models.async_db import get_engine, get_session_factory, get_async_session, init_db

logger = logging.getLogger(__name__)


class AsyncBaseRepository(ABC):
    """Async base repository class that all async repositories will inherit from."""

    # Class-level shared engine and session factory to avoid connection leaks
    _shared_engine = None
    _shared_session_factory = None

    def __init__(self, database_url: str = None):
        """Initialize the async repository with shared database connection.

        Args:
            database_url: The database connection URL. If None, use environment variable.
        """
        if database_url is None:
            database_url = os.environ.get('DATABASE_URL')
            if not database_url:
                raise ValueError("No database URL provided and DATABASE_URL environment variable not set")

        self.database_url = database_url

        # Use shared engine and session factory instead of creating new ones
        if AsyncBaseRepository._shared_engine is None:
            AsyncBaseRepository._shared_engine = get_engine()
        if AsyncBaseRepository._shared_session_factory is None:
            AsyncBaseRepository._shared_session_factory = get_session_factory()

        self.engine = AsyncBaseRepository._shared_engine
        self.session_factory = AsyncBaseRepository._shared_session_factory
        self.session_scope = get_async_session

    def get_async_session(self):
        """Get an async database session manager.

        Returns:
            AsyncSessionManager: An async context manager for database sessions.
        """
        return self.session_scope()

    @classmethod
    async def cleanup_shared_resources(cls):
        """Clean up shared database resources."""
        if cls._shared_engine is not None:
            await cls._shared_engine.dispose()
            logger.info("Shared database engine disposed")
            cls._shared_engine = None

        cls._shared_session_factory = None
        logger.info("Shared database resources cleaned up")


class AsyncRepositoryError(Exception):
    """Exception raised by async repository operations."""
    pass


# Cleanup function for shared resources
async def cleanup_shared_db_resources():
    """Clean up shared database resources."""
    await AsyncBaseRepository.cleanup_shared_resources()
