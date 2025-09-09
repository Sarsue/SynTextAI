"""
Repository manager that provides a unified interface to all repositories.

Acts as a facade over specialized repositories while enforcing user_id for access control.
"""
from typing import Optional, List, Dict, Any, AsyncGenerator, TypeVar, Callable, Awaitable
import logging
import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine

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
    
    def __init__(self, database_url: str, echo: Optional[bool] = None):
        self._database_url = database_url
        self._echo = echo
        self.engine: Optional[AsyncEngine] = None
        self.async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False
        self._closed = False
        self._user_repo = None
        self._chat_repo = None
        self._file_repo = None

    async def initialize(self) -> None:
        """Initialize database engine and session factory."""
        if self._initialized or self._closed:
            return
        
        try:
            pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
            max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
            pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "60"))
            pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))
            connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "30"))
            
            from sqlalchemy.engine.url import make_url
            db_url = make_url(str(self._database_url))
            
            connect_args = {
                "timeout": connect_timeout,
                "server_settings": {
                    "application_name": os.getenv("DB_APP_NAME", "syntext-worker"),
                    "statement_timeout": "30000",
                    "idle_in_transaction_session_timeout": "300000"
                }
            }

            self.engine = create_async_engine(
                db_url,
                echo=os.getenv("SQL_ECHO", "false").lower() == "true" if self._echo is None else self._echo,
                future=True,
                pool_pre_ping=True,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_use_lifo=True,
                connect_args=connect_args
            )

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

    # Learning material operations are handled by AsyncLearningMaterialRepository

def get_repository_manager(database_url: str) -> RepositoryManager:
    """Return a new RepositoryManager instance."""
    return RepositoryManager(database_url=str(database_url))
