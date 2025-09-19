"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over specialized repositories while enforcing user_id for access control.
"""
from typing import Optional, List, Dict, Any, AsyncGenerator, TypeVar, Callable, Awaitable
import logging
import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from api.models import User

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
    
    def __init__(self, database_url_or_engine: str | AsyncEngine, echo: Optional[bool] = None):
        self._database_url = database_url_or_engine if isinstance(database_url_or_engine, str) else None
        self._echo = echo
        self.engine: Optional[AsyncEngine] = database_url_or_engine if not isinstance(database_url_or_engine, str) else None
        self.async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False
        self._closed = False
        self._user_repo = None
        self._chat_repo = None
        self._file_repo = None

    async def initialize(self) -> None:
        """Initialize database engine (if not provided) and session factory."""
        if self._initialized or self._closed:
            return
        
        try:
            # If engine was already provided, just create the session factory
            if self.engine is not None:
                self.async_session_factory = async_sessionmaker(
                    bind=self.engine,
                    expire_on_commit=False,
                    class_=AsyncSession
                )
                self._initialized = True
                logger.info("Database connection pool initialized with provided engine")
                return
                
            # Otherwise, create the engine from URL
            if not self._database_url:
                raise ValueError("Either database URL or engine must be provided")
                
            pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
            max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
            pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "60"))
            pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
            connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "30"))
            
            from sqlalchemy.engine.url import make_url
            try:
                db_url = make_url(str(self._database_url))
            except Exception as e:
                logger.error(f"Failed to parse database URL: {self._database_url}")
                raise ValueError(f"Invalid database URL: {e}")
            
            connect_args = {
                "timeout": connect_timeout,
                "server_settings": {
                    "application_name": os.getenv("DB_APP_NAME", "syntext-worker"),
                    "statement_timeout": "30000",
                    "idle_in_transaction_session_timeout": "300000"
                }
            }

            # Handle both string and SQLAlchemy URL objects
            from urllib.parse import urlparse, parse_qs, urlunparse
            from sqlalchemy.engine import URL
            
            if isinstance(db_url, URL):
                # If it's already a URL object, convert to string without sslmode
                clean_url = str(db_url)
                # Extract sslmode from query parameters if present
                sslmode = 'require'
                if db_url.query and 'sslmode' in db_url.query:
                    sslmode = db_url.query['sslmode']
                    # Create a new URL without sslmode in query
                    query = {k: v for k, v in db_url.query.items() if k != 'sslmode'}
                    clean_url = str(db_url._replace(query=query))
            else:
                # Handle string URL
                parsed = urlparse(str(db_url))
                query = parse_qs(parsed.query)
                
                # Remove sslmode from query params if present
                sslmode = query.pop('sslmode', ['require'])[0]
                
                # Rebuild URL without sslmode in query
                clean_query = '&'.join(f"{k}={v[0]}" for k, v in query.items())
                clean_url = parsed._replace(query=clean_query).geturl()
            
            # Set SSL parameters based on sslmode
            ssl_params = {}
            if sslmode == 'require':
                ssl_params = {'ssl': 'require'}
            elif sslmode == 'verify-ca':
                ssl_params = {'ssl': 'verify-ca'}
            elif sslmode == 'verify-full':
                ssl_params = {'ssl': 'verify-full'}
                
            # Update connect args with SSL settings
            connect_args.update(ssl_params)
            
            try:
                self.engine = create_async_engine(
                    db_url,
                    echo=self._echo,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=pool_timeout,
                    pool_recycle=pool_recycle,
                    connect_args=connect_args,
                    pool_pre_ping=True  # Enable connection health checks
                )
                
                # Test the connection
                async with self.engine.connect() as conn:
                    await conn.execute("SELECT 1")
                    
            except Exception as e:
                logger.error(f"Failed to initialize database engine: {e}")
                if self.engine:
                    await self.engine.dispose()
                    self.engine = None
                raise

            self.async_session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False
            )

            self._initialized = True
            logger.info("RepositoryManager initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize RepositoryManager: {e}", exc_info=True)
            await self.close()
            raise

    async def __aenter__(self) -> "RepositoryManager":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Close database connections; safe to call multiple times."""
        if self._closed:
            return
        try:
            if self.engine:
                try:
                    await self.engine.dispose()
                    logger.info("Database engine disposed")
                except Exception as e:
                    logger.error(f"Error disposing database engine: {e}", exc_info=True)
                finally:
                    self.engine = None
                    self.async_session_factory = None
        finally:
            self._closed = True
            self._initialized = False

    def session_scope(self) -> SessionContextManager:
        """Return a session context manager for async DB operations."""
        if not self._initialized or self._closed:
            raise RuntimeError("RepositoryManager is not initialized or has been closed")
        return SessionContextManager(self.async_session_factory)

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Async context manager for sessions; backward-compatible."""
        async with self.session_scope() as session:
            yield session

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

def get_repository_manager(database_url: str | None = None) -> RepositoryManager:
    """Return a new RepositoryManager instance.
    
    This is the single entry point for creating a RepositoryManager instance.
    It ensures consistent behavior across the application by:
    1. Getting the database URL from settings if not provided
    2. Ensuring the URL is properly converted to a string
    3. Creating and initializing the RepositoryManager
    
    Args:
        database_url: Optional database URL. If not provided, will be fetched from settings.
                     This parameter is primarily for testing or special cases.
                     
    Returns:
        RepositoryManager: Initialized repository manager
        
    Raises:
        ValueError: If no database URL can be determined
    """
    from api.core.config import settings
    
    # Use provided URL or fall back to settings
    db_url = database_url or getattr(settings, 'DATABASE_URL', None)
    
    if not db_url:
        raise ValueError("No database URL provided and none found in settings")
        
    # Ensure URL is a string
    db_url = str(db_url)
    
    # Create and return the repository manager
    return RepositoryManager(database_url_or_engine=db_url)
