"""
Base repository manager for database operations.

This module provides the BaseRepositoryManager class which serves as the foundation
for all repository managers in the application. It handles database connection
management, session lifecycle, and provides common CRUD operations.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from typing import (
    Any, AsyncGenerator, Dict, Generic, List, Optional, Type, TypeVar, 
    Union, Callable, Awaitable, cast, overload, TypeVar, TypeAlias, Tuple
)

from sqlalchemy import select, update, delete, func, or_, and_, text, event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine, async_sessionmaker, create_async_engine,
    AsyncSessionTransaction, AsyncConnection
)
from sqlalchemy.exc import (
    SQLAlchemyError, OperationalError, IntegrityError, NoResultFound,
    MultipleResultsFound, DBAPIError, InterfaceError, TimeoutError as SQLAlchemyTimeoutError,
    InvalidRequestError
)
from sqlalchemy.orm import (
    Session, sessionmaker, selectinload, joinedload, load_only, 
    contains_eager, aliased, Query, RelationshipProperty
)
from sqlalchemy.orm.decl_api import DeclarativeMeta, DeclarativeBase
from sqlalchemy.orm.exc import UnmappedInstanceError, UnmappedClassError
from sqlalchemy.sql import Select, Update, Delete, Executable, ColumnElement
from sqlalchemy.sql.expression import Select as SelectOfScalar, FromClause, TextClause
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import sqltypes

# Application imports
from ..core.config import settings
from ..models.db_utils import (
    get_engine, get_session_factory, get_async_session,
    init_db, close_db
)

# Configure logging
logger = logging.getLogger(__name__)

# Type variables and aliases
T = TypeVar('T')
ModelType = TypeVar('ModelType', bound=DeclarativeBase)
CreateSchemaType = TypeVar('CreateSchemaType')
UpdateSchemaType = TypeVar('UpdateSchemaType')
SessionFactory: TypeAlias = async_sessionmaker[AsyncSession]

class BaseRepositoryManager:
    """
    Base class for repository managers that handle database operations.
    
    This class provides common database operations and session management
    functionality that can be inherited by specific repository managers.
    
    Features:
    - Connection pooling with configurable settings
    - Automatic session and transaction management
    - Retry mechanisms for transient failures
    - Comprehensive logging and metrics
    - Support for both sync and async operations
    """
    
    def __init__(
        self, 
        session_factory: Optional[SessionFactory] = None,
        engine: Optional[AsyncEngine] = None,
        **engine_kwargs
    ):
        """
        Initialize the repository manager with database connection settings.
        
        Args:
            session_factory: Optional async session factory.
            engine: Optional SQLAlchemy async engine.
            **engine_kwargs: Additional engine configuration options.
        """
        self._engine = engine
        self._session_factory = session_factory
        self._engine_kwargs = engine_kwargs
        self._initialized = False
        self._closed = False
        self._metrics = {
            'queries': 0,
            'transactions': 0,
            'errors': 0,
            'connection_attempts': 0,
            'last_error': None,
            'start_time': time.time()
        }
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """
        Close the repository manager and release all resources.
        
        This method:
        1. Closes all database connections in the connection pool
        2. Disposes of the engine
        3. Cleans up any remaining resources
        4. Marks the manager as closed
        
        After calling this method, the manager cannot be used anymore.
        
        Raises:
            Exception: If an error occurs during shutdown
        """
        if self._closed:
            return
            
        try:
            logger.info("Closing RepositoryManager...")
            
            # Close the database engine
            if self._engine is not None:
                await self._engine.dispose()
                self._engine = None
            
            # Reset other state
            self._session_factory = None
            self._initialized = False
            self._closed = True
            
            # Log final metrics
            uptime = time.time() - self._metrics['start_time']
            logger.info(
                "RepositoryManager closed after %.1f seconds. Stats: %d queries, "
                "%d transactions, %d errors",
                uptime, 
                self._metrics['queries'],
                self._metrics['transactions'],
                self._metrics['errors']
            )
            
        except Exception as e:
            self._metrics['errors'] += 1
            self._metrics['last_error'] = str(e)
            logger.error("Error closing RepositoryManager: %s", str(e), exc_info=True)
            raise

    @property
    def engine(self) -> AsyncEngine:
        """
        Get the SQLAlchemy async engine with connection pooling.
        
        The engine is created on first access with the following settings:
        - Connection pooling with configurable pool size and overflow
        - Automatic connection validation with pre-ping
        - Statement and connection timeouts
        - Server-side statement timeouts
        - Automatic reconnection on connection loss
        
        Returns:
            AsyncEngine: Configured SQLAlchemy async engine
            
        Raises:
            RuntimeError: If database connection cannot be established
        """
        if self._engine is None:
            try:
                # Default engine configuration
                engine_kwargs = {
                    'echo': settings.SQL_ECHO,
                    'echo_pool': settings.SQL_ECHO_POOL,
                    'pool_pre_ping': True,
                    'pool_recycle': settings.DB_POOL_RECYCLE,
                    'pool_size': settings.DB_POOL_SIZE,
                    'max_overflow': settings.DB_MAX_OVERFLOW,
                    'pool_timeout': settings.DB_POOL_TIMEOUT,
                    'pool_use_lifo': True,  # Better for serverless
                    'connect_args': {
                        'timeout': settings.DB_CONNECT_TIMEOUT,
                        'command_timeout': settings.DB_STATEMENT_TIMEOUT,
                        'keepalives': 1,
                        'keepalives_idle': 30,
                        'keepalives_interval': 10,
                        'keepalives_count': 5
                    },
                    **self._engine_kwargs
                }
                
                # Create the engine using the get_engine function from db_utils
                self._engine = get_engine()
                
                # Add event listeners
                self._add_engine_event_listeners()
                
                logger.info("Created new database engine with pool size=%d, max_overflow=%d",
                          settings.DB_POOL_SIZE, settings.DB_MAX_OVERFLOW)
                
            except Exception as e:
                logger.error("Failed to create database engine: %s", str(e), exc_info=True)
                raise RuntimeError("Failed to initialize database connection") from e
                
        return self._engine

    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Provide a transactional scope around a series of operations.
        
        This context manager handles the session lifecycle including:
        - Session creation with proper isolation level
        - Nested transaction support
        - Automatic retry for transient failures
        - Comprehensive error handling and logging
        - Resource cleanup
        
        Yields:
            AsyncSession: A database session ready for use
            
        Raises:
            RuntimeError: If the repository manager is closed
            SQLAlchemyError: For database-related errors
            
        Example:
            async with repo_manager.session_scope() as session:
                # Use the session
                result = await session.execute(select(User))
        """
        if self._closed:
            raise RuntimeError("RepositoryManager has been closed")
            
        session = None
        retry_count = 0
        max_retries = 3
        retry_delay = 0.5
        
        while True:
            try:
                # Create a new session if needed
                if session is None:
                    session = self.session_factory()
                    self._metrics['transactions'] += 1
                
                # Begin a transaction
                await session.begin()
                
                # Set session-level settings
                await session.execute(text("SET TIME ZONE 'UTC'"))
                if settings.SQL_ECHO:
                    await session.execute(text(f"SET statement_timeout = {settings.DB_STATEMENT_TIMEOUT * 1000}"))
                
                # Yield the session to the caller
                yield session
                
                # Commit the transaction if we're the outermost session
                if not session.in_transaction() or session.in_nested_transaction():
                    logger.debug("Skipping commit - not in a transaction or in nested transaction")
                else:
                    await session.commit()
                    logger.debug("Transaction committed successfully")
                
                # Success - break out of retry loop
                break
                
            except (OperationalError, InterfaceError, DBAPIError) as e:
                # Rollback the current transaction
                if session is not None and session.in_transaction():
                    await session.rollback()
                
                # Check if we should retry
                retry_count += 1
                self._metrics['errors'] += 1
                self._metrics['last_error'] = str(e)
                
                if retry_count > max_retries or not self._is_retryable_error(e):
                    logger.error("Database operation failed after %d attempts: %s", 
                               retry_count, str(e), exc_info=True)
                    raise
                
                # Log the retry
                logger.warning("Retryable error (attempt %d/%d): %s. Retrying in %.1fs...",
                            retry_count, max_retries, str(e), retry_delay)
                
                # Wait before retrying
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 5.0)  # Exponential backoff with max 5s
                
            except SQLAlchemyError as e:
                # Rollback and re-raise for non-retryable errors
                if session is not None and session.in_transaction():
                    await session.rollback()
                
                self._metrics['errors'] += 1
                self._metrics['last_error'] = str(e)
                logger.error("Database error in session: %s", str(e), exc_info=True)
                raise
                
            except Exception as e:
                # Rollback and re-raise for unexpected errors
                if session is not None and session.in_transaction():
                    await session.rollback()
                
                self._metrics['errors'] += 1
                self._metrics['last_error'] = str(e)
                logger.error("Unexpected error in session: %s", str(e), exc_info=True)
                raise
                
            finally:
                # Always close the session when we're done
                if session is not None:
                    try:
                        await session.close()
                    except Exception as e:
                        logger.error("Error closing session: %s", str(e), exc_info=True)
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """
        Determine if an error is transient and the operation can be retried.
        
        Args:
            error: The exception to check
            
        Returns:
            bool: True if the operation can be retried, False otherwise
        """
        if isinstance(error, OperationalError):
            # Check for connection-related errors
            error_msg = str(error).lower()
            return any(term in error_msg for term in [
                'connection', 'timeout', 'timed out', 'server closed',
                'terminating connection', 'could not connect', 'network error'
            ])
            
        if isinstance(error, InterfaceError):
            # Interface errors are typically retryable
            return True
            
        if isinstance(error, DBAPIError):
            # Check for deadlocks and serialization failures
            error_code = getattr(error.orig, 'pgcode', None)
            if error_code in [
                '40001',  # serialization_failure
                '40P01',  # deadlock_detected
                '55P03',  # lock_not_available
                '57014',  # query_canceled
                '57P01',  # admin_shutdown
                '57P02',  # crash_shutdown
                '57P03',  # cannot_connect_now
                '58P01'   # system_error
            ]:
                return True
                
        return False

    @property
    def session_factory(self) -> SessionFactory:
        """
        Get the async session factory, creating it if necessary.
        
        The session factory is responsible for creating new database sessions
        with the current engine configuration.
        
        Returns:
            SessionFactory: Configured async session factory
        """
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                future=True
            )
        return self._session_factory
    
    @session_factory.setter
    def session_factory(self, factory: SessionFactory) -> None:
        """
        Set a custom async session factory.
        
        Args:
            factory: The async session factory to use
        """
        self._session_factory = factory

    def _add_engine_event_listeners(self) -> None:
        """
        Add event listeners to the SQLAlchemy engine for monitoring and debugging.
        
        These listeners provide:
        - Query timing and logging
        - Connection pool metrics
        - Error tracking
        - Performance monitoring
        """
        if self._engine is None:
            return
        
        # Track connection checkouts/checkins
        @event.listens_for(self._engine.sync_engine, 'checkout')
        def on_checkout(dbapi_connection, connection_record, connection_proxy):
            self._metrics['connection_attempts'] += 1
            connection_record._checkout_time = time.time()
            
        @event.listens_for(self._engine.sync_engine, 'checkin')
        def on_checkin(dbapi_connection, connection_record):
            checkout_time = getattr(connection_record, '_checkout_time', None)
            if checkout_time:
                duration = time.time() - checkout_time
                if duration > 1.0:  # Log slow checkins
                    logger.warning(
                        "Connection was checked out for %.2f seconds",
                        duration
                    )
        
        # Log SQL statements if enabled
        if settings.SQL_ECHO:
            @event.listens_for(self._engine.sync_engine, 'before_cursor_execute')
            def before_cursor_execute(conn, cursor, statement, params, context, executemany):
                conn.info.setdefault('query_start_time', []).append(time.time())
                
            @event.listens_for(self._engine.sync_engine, 'after_cursor_execute')
            def after_cursor_execute(conn, cursor, statement, params, context, executemany):
                total = time.time() - conn.info['query_start_time'].pop(-1)
                self._metrics['queries'] += 1
                
                # Log slow queries
                if total > settings.SLOW_QUERY_THRESHOLD:
                    logger.warning(
                        "Slow query (%.2fs): %s",
                        total,
                        statement.replace('\n', ' ').strip()
                    )

    # Common CRUD operations
    async def create(self, model: Type[ModelType], **kwargs: Any) -> ModelType:
        """
        Create a new model instance and save it to the database.
        
        This method creates a new instance of the specified model with the provided
        field values, adds it to the current session, and commits the transaction.
        
        Args:
            model: The model class to create an instance of
            **kwargs: Field values for the new model instance
            
        Returns:
            ModelType: The newly created model instance
            
        Raises:
            SQLAlchemyError: If there's an error creating the record
            ValueError: If required fields are missing or invalid
            
        Example:
            >>> user = await repo.create(User, username='test', email='test@example.com')
        """
        if not kwargs:
            raise ValueError("No values provided for model creation")
            
        try:
            instance = model(**kwargs)
            async with self.session_scope() as session:
                session.add(instance)
                await session.flush()
                await session.refresh(instance)
                return instance
                
        except IntegrityError as e:
            logger.error("Integrity error creating %s: %s", model.__name__, str(e))
            raise
            
        except SQLAlchemyError as e:
            logger.error("Error creating %s: %s", model.__name__, str(e), exc_info=True)
            raise
    
    async def get_by_id(
        self, 
        model: Type[ModelType], 
        id: Any,
        options: Optional[List[Any]] = None,
        with_for_update: bool = False,
        lock_mode: Optional[str] = None
    ) -> Optional[ModelType]:
        """
        Retrieve a single model instance by its primary key.
        
        Args:
            model: The model class to query
            id: The primary key value to look up
            options: Optional list of SQLAlchemy loader options
            with_for_update: If True, acquires a row-level lock
            lock_mode: Optional lock mode ('update', 'nowait', 'skip_locked')
            
        Returns:
            Optional[ModelType]: The model instance if found, None otherwise
            
        Example:
            >>> user = await repo.get_by_id(User, 1)
        """
        if id is None:
            return None
            
        stmt = select(model).where(model.id == id)
        
        # Add lock if requested
        if with_for_update:
            stmt = stmt.with_for_update(
                of=model,
                nowait=lock_mode == 'nowait',
                skip_locked=lock_mode == 'skip_locked',
                key_share=lock_mode == 'key_share'
            )
        
        # Add eager loading options if provided
        if options:
            for option in options:
                stmt = stmt.options(option)
        
        try:
            async with self.session_scope() as session:
                result = await session.execute(stmt)
                return result.scalar_one_or_none()
                
        except SQLAlchemyError as e:
            logger.error("Error getting %s by id %s: %s", 
                       model.__name__, id, str(e), exc_info=True)
            raise
    
    async def update(
        self, 
        model: Type[ModelType], 
        id: Any, 
        values: Dict[str, Any],
        return_updated: bool = True
    ) -> Optional[ModelType]:
        """
        Update a model instance by ID.
        
        Args:
            model: The model class to update
            id: The primary key of the instance to update
            values: Dictionary of field names to new values
            return_updated: If True, returns the updated instance
            
        Returns:
            Optional[ModelType]: The updated model instance if return_updated is True, None otherwise
            
        Example:
            >>> user = await repo.update(User, 1, {"is_active": False})
        """
        if not values:
            raise ValueError("No values provided for update")
            
        try:
            async with self.session_scope() as session:
                stmt = (
                    update(model)
                    .where(model.id == id)
                    .values(**values)
                    .execution_options(synchronize_session="fetch")
                )
                
                result = await session.execute(stmt)
                
                if result.rowcount == 0:
                    return None
                    
                if return_updated:
                    # Get the updated instance
                    stmt = select(model).where(model.id == id)
                    result = await session.execute(stmt)
                    return result.scalar_one()
                return None
                
        except SQLAlchemyError as e:
            logger.error("Error updating %s with id %s: %s", 
                       model.__name__, id, str(e), exc_info=True)
            raise
    
    async def delete(
        self, 
        model: Type[ModelType], 
        id: Any,
        hard_delete: bool = False
    ) -> bool:
        """
        Delete a model instance by ID.
        
        Args:
            model: The model class to delete from
            id: The primary key of the instance to delete
            hard_delete: If True, performs a hard delete (default: soft delete)
            
        Returns:
            bool: True if the record was deleted, False if not found
            
        Example:
            >>> success = await repo.delete(User, 1)
        """
        try:
            async with self.session_scope() as session:
                # Check if the model supports soft delete
                has_is_deleted = hasattr(model, 'is_deleted')
                
                if not hard_delete and has_is_deleted:
                    # Perform soft delete
                    stmt = (
                        update(model)
                        .where(model.id == id)
                        .values(is_deleted=True)
                        .execution_options(synchronize_session="fetch")
                    )
                    result = await session.execute(stmt)
                    return result.rowcount > 0
                else:
                    # Perform hard delete
                    stmt = (
                        delete(model)
                        .where(model.id == id)
                    )
                    result = await session.execute(stmt)
                    return result.rowcount > 0
                    
        except SQLAlchemyError as e:
            logger.error("Error deleting %s with id %s: %s", 
                       model.__name__, id, str(e), exc_info=True)
            raise
    
    async def get_all(
        self,
        model: Type[ModelType],
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        options: Optional[List[Any]] = None
    ) -> List[ModelType]:
        """
        Retrieve all instances of a model, optionally filtered and ordered.
        
        Args:
            model: The model class to query
            filters: Dictionary of filter conditions
            order_by: Field name to order by (prefix with - for descending)
            limit: Maximum number of results to return
            offset: Number of records to skip
            options: List of SQLAlchemy loader options
            
        Returns:
            List[ModelType]: List of model instances
            
        Example:
            >>> active_users = await repo.get_all(User, {"is_active": True}, order_by="-created_at")
        """
        stmt = select(model)
        
        # Apply filters
        if filters:
            conditions = []
            for field, value in filters.items():
                if hasattr(model, field):
                    if value is None:
                        conditions.append(getattr(model, field).is_(None))
                    elif isinstance(value, (list, tuple)):
                        conditions.append(getattr(model, field).in_(value))
                    else:
                        conditions.append(getattr(model, field) == value)
            
            if conditions:
                stmt = stmt.where(and_(*conditions))
        
        # Apply ordering
        if order_by:
            if order_by.startswith('-'):
                stmt = stmt.order_by(getattr(model, order_by[1:]).desc())
            else:
                stmt = stmt.order_by(getattr(model, order_by))
        
        # Apply pagination
        if limit is not None:
            stmt = stmt.limit(limit)
            if offset is not None:
                stmt = stmt.offset(offset)
        
        # Add eager loading options
        if options:
            for option in options:
                stmt = stmt.options(option)
        
        try:
            async with self.session_scope() as session:
                result = await session.execute(stmt)
                return list(result.scalars().all())
                
        except SQLAlchemyError as e:
            logger.error("Error getting all %s: %s", model.__name__, str(e), exc_info=True)
            raise
    
    async def count(
        self,
        model: Type[ModelType],
        filters: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Count the number of instances of a model matching the given filters.
        
        Args:
            model: The model class to count
            filters: Optional filter conditions
            
        Returns:
            int: The count of matching records
            
        Example:
            >>> count = await repo.count(User, {"is_active": True})
        """
        stmt = select(func.count()).select_from(model)
        
        if filters:
            conditions = []
            for field, value in filters.items():
                if hasattr(model, field):
                    if value is None:
                        conditions.append(getattr(model, field).is_(None))
                    elif isinstance(value, (list, tuple)):
                        conditions.append(getattr(model, field).in_(value))
                    else:
                        conditions.append(getattr(model, field) == value)
            
            if conditions:
                stmt = stmt.where(and_(*conditions))
        
        try:
            async with self.session_scope() as session:
                result = await session.execute(stmt)
                return result.scalar_one()
                
        except SQLAlchemyError as e:
            logger.error("Error counting %s: %s", model.__name__, str(e), exc_info=True)
            raise
    
    async def exists(
        self,
        model: Type[ModelType],
        filters: Dict[str, Any]
    ) -> bool:
        """
        Check if any instance exists that matches the given filters.
        
        Args:
            model: The model class to check
            filters: Filter conditions
            
        Returns:
            bool: True if a matching record exists, False otherwise
            
        Example:
            >>> exists = await repo.exists(User, {"email": "user@example.com"})
        """
        if not filters:
            raise ValueError("At least one filter condition is required")
            
        stmt = select(1).select_from(model).limit(1)
        
        conditions = []
        for field, value in filters.items():
            if hasattr(model, field):
                if value is None:
                    conditions.append(getattr(model, field).is_(None))
                elif isinstance(value, (list, tuple)):
                    conditions.append(getattr(model, field).in_(value))
                else:
                    conditions.append(getattr(model, field) == value)
        
        if conditions:
            stmt = stmt.where(and_(*conditions))
        
        try:
            async with self.session_scope() as session:
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None
                
        except SQLAlchemyError as e:
            logger.error("Error checking existence of %s: %s", 
                       model.__name__, str(e), exc_info=True)
            raise
    
    async def execute(
        self,
        stmt: Executable,
        params: Optional[Dict[str, Any]] = None,
        execution_options: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Execute a raw SQL statement or SQLAlchemy core statement.
        
        Args:
            stmt: The SQLAlchemy statement to execute
            params: Optional parameters for the statement
            execution_options: Optional execution options
            
        Returns:
            The result of the execution
            
        Example:
            >>> stmt = text("SELECT * FROM users WHERE is_active = :active")
            >>> result = await repo.execute(stmt, {"active": True})
        """
        try:
            async with self.session_scope() as session:
                if execution_options:
                    stmt = stmt.execution_options(**execution_options)
                result = await session.execute(stmt, params or {})
                return result
                
        except SQLAlchemyError as e:
            logger.error("Error executing statement: %s", str(e), exc_info=True)
            raise

class RepositoryManager(BaseRepositoryManager):
    """
    Main repository manager that provides access to all repositories.
    
    This class is a singleton that should be accessed through get_repository_manager().
    It initializes and provides access to all repository instances.
    """
    
    def __init__(self, session_factory=None, engine=None, **engine_kwargs):
        """
        Initialize the repository manager and all repositories.
        
        Args:
            session_factory: Optional SQLAlchemy async session factory
            engine: Optional SQLAlchemy async engine
            **engine_kwargs: Additional engine configuration options
        """
        super().__init__(session_factory=session_factory, engine=engine, **engine_kwargs)
        
        # Initialize repositories
        self._user_repo = None
        self._file_repo = None
        self._chat_repo = None
        self._learning_material_repo = None
        
        # Lazy initialization flag
        self._repos_initialized = False
    
    async def _initialize_repositories(self):
        """Initialize all repository instances."""
        if not self._repos_initialized:
            from .async_user_repository import AsyncUserRepository
            from .async_file_repository import AsyncFileRepository
            from .async_chat_repository import AsyncChatRepository
            from .async_learning_material_repository import AsyncLearningMaterialRepository
            
            self._user_repo = AsyncUserRepository(self)
            self._file_repo = AsyncFileRepository(self)
            self._chat_repo = AsyncChatRepository(self)
            self._learning_material_repo = AsyncLearningMaterialRepository(self)
            
            self._repos_initialized = True
    
    @property
    async def user_repo(self) -> 'AsyncUserRepository':
        """Get the user repository."""
        await self._initialize_repositories()
        return self._user_repo
    
    @property
    async def file_repo(self) -> 'AsyncFileRepository':
        """Get the file repository."""
        await self._initialize_repositories()
        return self._file_repo
    
    @property
    async def chat_repo(self) -> 'AsyncChatRepository':
        """Get the chat repository."""
        await self._initialize_repositories()
        return self._chat_repo
    
    @property
    async def learning_material_repo(self) -> 'AsyncLearningMaterialRepository':
        """Get the learning material repository."""
        await self._initialize_repositories()
        return self._learning_material_repo

# Create a single instance of the RepositoryManager
_repository_manager: Optional[RepositoryManager] = None

# Lock for thread-safe singleton initialization
_repo_lock = asyncio.Lock()

async def get_repository_manager() -> RepositoryManager:
    """
    Get or create the singleton instance of RepositoryManager.
    
    This coroutine provides thread-safe, async initialization of the global
    RepositoryManager instance. The first call initializes the manager.
    
    Returns:
        BaseRepositoryManager: The singleton instance of the repository manager
        
    Raises:
        RuntimeError: If database initialization fails
        
    Example:
        >>> repo_manager = await get_repository_manager()
        >>> async with repo_manager.session_scope() as session:
        ...     # Use the session
        ...     result = await session.execute(select(User))
    """
    global _repository_manager
    
    if _repository_manager is None:
        async with _repo_lock:
            # Double-checked locking pattern
            if _repository_manager is None:
                try:
                    logger.info("Initializing RepositoryManager...")
                    _repository_manager = RepositoryManager()
                    
                    # Initialize the database connection
                    await init_db()
                    
                    # Verify the connection
                    async with _repository_manager.session_scope() as session:
                        await session.execute(text('SELECT 1'))
                    
                    logger.info("RepositoryManager initialized successfully")
                    
                except Exception as e:
                    logger.error("Failed to initialize RepositoryManager: %s", 
                               str(e), exc_info=True)
                    _repository_manager = None
                    raise RuntimeError("Database initialization failed") from e
    
    return _repository_manager
