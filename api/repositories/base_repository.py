"""
Base Repository interface defining common methods and db connection handling.
"""
from abc import ABC, abstractmethod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import logging
from typing import Callable, TypeVar, Generic, Optional, Any

from .unit_of_work import UnitOfWork, transactional_session

logger = logging.getLogger(__name__)


class BaseRepository(ABC):
    """Base repository class that all repositories will inherit from."""
    
    def __init__(self, database_url: str = None):
        """Initialize the repository with database connection.
        
        Args:
            database_url: The database connection URL. If None, use environment variable.
        """
        if database_url is None:
            import os
            database_url = os.environ.get('DATABASE_URL')
            if not database_url:
                raise ValueError("No database URL provided and DATABASE_URL environment variable not set")
        
        self.database_url = database_url
        self.engine = create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self.Session = sessionmaker(bind=self.engine)
    
    def get_session(self):
        """Get a new database session."""
        return self.Session()
    
    def get_unit_of_work(self):
        """Get a new unit of work for transaction management.
        
        Returns:
            UnitOfWork: A unit of work context manager
        """
        return UnitOfWork(self.Session)
    
    def transactional_session(self):
        """Get a transactional session context manager.
        
        Returns:
            Context manager for a transactional session
        
        Example:
            with repo.transactional_session() as session:
                session.add(some_entity)
                # No need for commit/rollback/close
        """
        return transactional_session(self.Session)
    
    def commit_and_close(self, session, operation_name: str):
        """Commit changes and close the session, with error handling.
        
        Args:
            session: The SQLAlchemy session
            operation_name: Name of the operation for logging purposes
        
        Returns:
            bool: Success status of the operation
        
        Note:
            This method is maintained for backward compatibility.
            New code should prefer the unit_of_work or transactional_session pattern.
        """
        try:
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error during {operation_name}: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def execute_transactional(self, operation_func: Callable, operation_name: str = "operation"):
        """Execute a function within a transaction boundary.
        
        Args:
            operation_func: Function that takes a session parameter and performs DB operations
            operation_name: Name of operation for logging purposes
            
        Returns:
            The result of operation_func, or None if an exception occurred
            
        Example:
            def add_entity(session, entity):
                session.add(entity)
                return entity.id
                
            result = repo.execute_transactional(lambda session: add_entity(session, new_entity))
        """
        with self.get_unit_of_work() as uow:
            try:
                result = operation_func(uow.session)
                logger.debug(f"Successfully executed {operation_name}")
                return result
            except Exception as e:
                logger.error(f"Error during {operation_name}: {e}", exc_info=True)
                return None
