"""
Session management utilities for database operations.
"""
from typing import Any, AsyncGenerator, Callable, TypeVar, Optional
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')

class SessionContextManager:
    """
    Context manager for database sessions with automatic commit/rollback.
    
    This class provides a context manager that handles the session lifecycle,
    including committing transactions on success and rolling back on exceptions.
    """
    
    def __init__(self, session_factory: Callable[..., AsyncSession]):
        """
        Initialize with a session factory.
        
        Args:
            session_factory: Callable that returns a new AsyncSession
        """
        self.session_factory = session_factory
        self.session: Optional[AsyncSession] = None
    
    async def __aenter__(self) -> AsyncSession:
        """Enter the context and return a new session."""
        self.session = self.session_factory()
        return self.session
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        Exit the context, committing or rolling back the session.
        
        Args:
            exc_type: Exception type if an exception was raised, None otherwise
            exc_val: Exception value if an exception was raised, None otherwise
            exc_tb: Exception traceback if an exception was raised, None otherwise
        """
        if self.session is None:
            return
            
        try:
            if exc_type is not None:
                # An exception occurred, rollback the transaction
                logger.debug("Rolling back transaction due to exception", 
                           exc_info=(exc_type, exc_val, exc_tb))
                await self.session.rollback()
            else:
                # No exception, commit the transaction
                await self.session.commit()
        except Exception as e:
            # Log any errors during commit/rollback
            logger.error("Error during session commit/rollback", exc_info=True)
            await self.session.rollback()
            raise
        finally:
            # Always close the session
            await self.session.close()
            self.session = None

    async def execute_in_session(
        self, 
        operation: Callable[[AsyncSession], Any]
    ) -> Any:
        """
        Execute an operation within a session with automatic commit/rollback.
        
        Args:
            operation: Async function that takes a session and returns a result
            
        Returns:
            The result of the operation
            
        Raises:
            Exception: If the operation raises an exception
        """
        async with self as session:
            try:
                return await operation(session)
            except Exception as e:
                logger.error(f"Error executing operation in session: {e}", exc_info=True)
                raise
