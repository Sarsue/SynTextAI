"""
Unit of Work pattern implementation for transaction management.

This pattern provides a consistent way to handle database transactions across
repository operations, reducing boilerplate code and ensuring proper commit/rollback handling.
"""
import logging
from typing import Callable, TypeVar, Generic, Optional, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

T = TypeVar('T')

class UnitOfWork:
    """
    Unit of Work implementation for SQLAlchemy sessions.
    
    This class encapsulates transaction management, providing automatic commit/rollback
    based on whether exceptions occur during the transaction.
    """
    
    def __init__(self, session_factory: Callable):
        """
        Initialize the UnitOfWork with a session factory.
        
        Args:
            session_factory: A callable that returns a new SQLAlchemy session
        """
        self.session_factory = session_factory
        self.session = None
        
    def __enter__(self):
        """Start a new transaction by creating a new session."""
        self.session = self.session_factory()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        End the transaction, with commit or rollback based on exception state.
        
        Args:
            exc_type: Exception type if an exception was raised, else None
            exc_val: Exception value if an exception was raised, else None
            exc_tb: Exception traceback if an exception was raised, else None
        """
        if exc_type is not None:
            # An exception occurred, rollback
            self.session.rollback()
            logger.error(f"Transaction rolled back due to: {exc_val}", exc_info=True)
        else:
            # No exception, commit
            try:
                self.session.commit()
            except Exception as e:
                self.session.rollback()
                logger.error(f"Error during commit: {e}", exc_info=True)
                # Re-raise the exception to make sure caller knows it failed
                raise
        finally:
            # Always close the session
            self.session.close()
            
    def execute_with_result(self, operation_func: Callable[[Any], T]) -> Optional[T]:
        """
        Execute an operation with the session and return its result.
        
        Args:
            operation_func: A function that takes a session and returns a result
            
        Returns:
            The result of the operation, or None if an exception occurred
        """
        try:
            return operation_func(self.session)
        except Exception as e:
            logger.error(f"Operation failed: {e}", exc_info=True)
            return None

@contextmanager
def transactional_session(session_factory: Callable):
    """
    A context manager for handling transactional sessions.
    
    This is an alternative to the UnitOfWork class that can be used with a 'with' statement.
    
    Args:
        session_factory: A callable that returns a new SQLAlchemy session
        
    Example:
        with transactional_session(Session) as session:
            session.add(some_object)
            # No need to manually commit or handle rollback
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Transaction error: {e}", exc_info=True)
        raise
    finally:
        session.close()
