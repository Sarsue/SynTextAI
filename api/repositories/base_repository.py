"""
Base Repository interface defining common methods and db connection handling.
"""
from abc import ABC, abstractmethod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import logging

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
    
    def commit_and_close(self, session, operation_name: str):
        """Commit changes and close the session, with error handling.
        
        Args:
            session: The SQLAlchemy session
            operation_name: Name of the operation for logging purposes
        
        Returns:
            bool: Success status of the operation
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
