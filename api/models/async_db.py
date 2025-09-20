"""
Centralized database connection management for SynTextAI.

This module provides a single source of truth for database connections,
handling SSL, connection pooling, and session management.
"""

import os
import ssl
import asyncio
import logging
from typing import AsyncGenerator, Optional, Union, TypeVar, Callable, Awaitable, Any
from contextlib import asynccontextmanager
from urllib.parse import quote_plus, urlparse, parse_qs, urlunparse

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
    AsyncSession,
    async_scoped_session,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import (
    OperationalError,
    TimeoutError,
    InterfaceError,
    SQLAlchemyError,
)
from sqlalchemy.sql import text
from sqlalchemy.orm import sessionmaker

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    retry_if_exception_type,
    before_sleep_log,
    retry_any,
    RetryCallState
)

# Type variable for generic return types
T = TypeVar('T')

# Global engine and session factory
_engine: Optional[AsyncEngine] = None
_async_session_factory = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# -------------------------------------------------------------------
# Database Configuration
# -------------------------------------------------------------------

# Get database configuration from environment variables
DB_CONFIG = {
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': int(os.getenv("DATABASE_PORT", "5432")),
    'database': os.getenv("DATABASE_NAME"),
    'sslmode': os.getenv("DATABASE_SSLMODE", "require").lower(),
    'sslrootcert': os.getenv("DATABASE_SSLROOTCERT"),
}

# Validate required configuration
if not all([DB_CONFIG['user'], DB_CONFIG['host'], DB_CONFIG['database']]):
    raise ValueError("Missing required database configuration in environment variables")

# Build database URL
DATABASE_URL = (
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{quote_plus(DB_CONFIG['password'] or '')}@"
    f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)

# Connection pool configuration
POOL_CONFIG = {
    'pool_size': int(os.getenv("DB_POOL_SIZE", "5")),
    'max_overflow': int(os.getenv("DB_MAX_OVERFLOW", "10")),
    'pool_timeout': int(os.getenv("DB_POOL_TIMEOUT", "30")),
    'pool_recycle': int(os.getenv("DB_POOL_RECYCLE", "300")),  # 5 minutes
    'pool_pre_ping': os.getenv("DB_POOL_PRE_PING", "true").lower() == "true",
    'echo': os.getenv('SQL_ECHO', 'false').lower() == 'true',
    'execution_options': {
        'isolation_level': 'AUTOCOMMIT',
    },
}

# Retry configuration
RETRY_CONFIG = {
    'stop': stop_after_attempt(int(os.getenv("DB_CONNECT_RETRIES", "3"))),
    'wait': wait_exponential(
        multiplier=1,
        min=1,
        max=10
    ),
    'retry': retry_any(
        retry_if_exception_type(OperationalError),
        retry_if_exception_type(TimeoutError),
        retry_if_exception_type(InterfaceError),
    ),
    'before_sleep': before_sleep_log(logger, logging.WARNING),
    'reraise': True,
}

# -------------------------------------------------------------------
# SSL Context Management
# -------------------------------------------------------------------

def _create_ssl_context() -> Optional[Union[ssl.SSLContext, bool]]:
    """
    Create and configure SSL context based on environment configuration.
    
    Returns:
        ssl.SSLContext: Configured SSL context
        bool: False if SSL is disabled
        None: If SSL is not required
    """
    sslmode = DB_CONFIG['sslmode']
    cafile = DB_CONFIG['sslrootcert']
    
    if sslmode == 'disable':
        logger.info("Database SSL is disabled")
        return False
        
    logger.info(f"Configuring database SSL with mode: {sslmode}")
    
    try:
        # Create a default SSL context
        ssl_context = ssl.create_default_context()
        
        if sslmode in ['require', 'prefer']:
            # Basic SSL without certificate verification
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context
            
        # For verify-ca and verify-full, we need the CA certificate
        if not cafile or not os.path.exists(cafile):
            logger.warning(
                "SSL mode requires CA certificate but none was provided. "
                "Falling back to basic SSL without verification."
            )
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context
            
        # Configure for verify-ca or verify-full with provided CA cert
        ssl_context.load_verify_locations(cafile=cafile)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        if sslmode == 'verify-full':
            ssl_context.check_hostname = True
            
        return ssl_context
        
    except Exception as e:
        logger.error(f"Error configuring SSL context: {e}")
        # Fall back to basic SSL on error
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

# -------------------------------------------------------------------
# Database Engine Management
# -------------------------------------------------------------------

def _get_connect_args() -> dict:
    """Get connection arguments including SSL configuration.
    
    Returns:
        dict: Connection arguments for SQLAlchemy engine
    """
    ssl_context = _create_ssl_context()
    connect_args = {}
    
    # Add server settings
    connect_args['server_settings'] = {
        'application_name': os.getenv("DB_APP_NAME", "syntext-api"),
        'statement_timeout': str(int(os.getenv("DB_STATEMENT_TIMEOUT", "30000"))),  # 30 seconds
        'idle_in_transaction_session_timeout': str(int(os.getenv("DB_IDLE_TIMEOUT", "300000")))  # 5 minutes
    }
    
    # Add SSL context if available
    if ssl_context is not None:
        if isinstance(ssl_context, ssl.SSLContext):
            connect_args['ssl'] = ssl_context
        else:  # bool
            connect_args['ssl'] = ssl_context
    
    # Add command timeout (in seconds)
    if 'DB_COMMAND_TIMEOUT' in os.environ:
        connect_args['command_timeout'] = int(os.environ['DB_COMMAND_TIMEOUT'])
            
    return connect_args

@retry(**RETRY_CONFIG)
async def create_engine() -> AsyncEngine:
    """
    Create and configure an async SQLAlchemy engine with retry logic.
    
    Returns:
        AsyncEngine: Configured SQLAlchemy async engine
    """
    global _engine
    
    if _engine is not None:
        return _engine
        
    try:
        # Get connection arguments
        connect_args = _get_connect_args()
        
        # Create a copy of POOL_CONFIG without connect_args
        engine_config = {
            k: v for k, v in POOL_CONFIG.items()
            if k != 'connect_args'
        }
        
        # Add connection arguments
        engine_config['connect_args'] = connect_args
        
        # Create the async engine
        _engine = create_async_engine(
            DATABASE_URL,
            **engine_config
        )
        
        logger.info("Database engine created successfully")
        return _engine
        
    except Exception as e:
        logger.error(f"Failed to create database engine: {e}")
        _engine = None
        raise

async def get_engine() -> AsyncEngine:
    """
    Get or create the database engine.
    
    Returns:
        AsyncEngine: The database engine
        
    Raises:
        RuntimeError: If the engine cannot be created
    """
    global _engine
    
    if _engine is None:
        _engine = await create_engine()
        
        # Verify the connection
        try:
            async with _engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                logger.info("Database connection verified")
        except Exception as e:
            logger.error(f"Failed to verify database connection: {e}")
            _engine = None
            raise RuntimeError("Failed to establish database connection") from e
            
    return _engine

async def close_engine() -> None:
    """Close the database engine and clean up resources."""
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
        logger.info("Database engine closed")

# -------------------------------------------------------------------
# Session Management
# -------------------------------------------------------------------

def get_session_factory() -> async_sessionmaker:
    """
    Get the async session factory, creating it if necessary.
    
    Returns:
        async_sessionmaker: Configured async session factory
    """
    global _async_session_factory
    
    if _async_session_factory is None:
        if _engine is None:
            raise RuntimeError("Database engine not initialized. Call create_engine() first.")
            
        _async_session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        
    return _async_session_factory

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session with automatic cleanup.
    
    Example:
        async with get_session() as session:
            result = await session.execute(text("SELECT 1"))
            
    Yields:
        AsyncSession: Database session
    
    Raises:
        RuntimeError: If the database engine is not initialized
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call startup() first.")
        
    session = _async_session_factory()
    
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Database operation failed: {e}")
        raise
    finally:
        await session.close()

# -------------------------------------------------------------------
# Application Lifecycle
# -------------------------------------------------------------------

async def startup() -> None:
    """Initialize the database connection and session factory."""
    global _engine, _async_session_factory
    
    if _engine is None:
        try:
            logger.info("Initializing database engine...")
            _engine = await create_engine()
            
            # Test the connection
            from sqlalchemy import text
            async with _engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                logger.info(f"Database connection test: {result.scalar() == 1}")
            
            # Create session factory
            _async_session_factory = async_sessionmaker(
                bind=_engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=False,
            )
            logger.info("Database engine and session factory initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {str(e)}")
            if _engine:
                await _engine.dispose()
            raise

async def shutdown() -> None:
    """Close database connection and clean up resources."""
    global _engine, _async_session_factory
    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
    _engine = None
    _async_session_factory = None


# -------------------------------------------------------------------
# Session manager
# -------------------------------------------------------------------
@asynccontextmanager
async def get_async_session(commit_on_exit: bool = True) -> AsyncGenerator[AsyncSession, None]:
    """Provide a session with optional commit at exit."""
    global _async_session_factory
    if _async_session_factory is None:
        await startup()

    async with _async_session_factory() as session:
        try:
            yield session
            if commit_on_exit:
                await session.commit()
        except Exception:
            await session.rollback()
            raise


# Convenience wrappers
def get_async_session_factory() -> async_sessionmaker:
    """
    Get the async session factory.
    
    Returns:
        async_sessionmaker: The configured async session factory
        
    Raises:
        RuntimeError: If the database is not initialized
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call startup() first.")
    return _async_session_factory


@asynccontextmanager
async def get_read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """Session that always rolls back (read-only)."""
    async with get_async_session(commit_on_exit=False) as session:
        try:
            yield session
        finally:
            await session.rollback()
