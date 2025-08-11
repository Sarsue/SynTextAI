"""
Base Repository interface defining common methods and db connection handling.
"""
from abc import ABC
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import logging

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
