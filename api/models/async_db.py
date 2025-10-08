"""
Production-ready async database connection management for SynTextAI.

This module provides robust async database connectivity with proper SSL handling,
connection pooling, retry logic, and health checks - adopting best practices
from the previous implementation while maintaining centralized configuration.
"""
import logging
import ssl
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from urllib.parse import quote_plus

# Robust imports with fallbacks
try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False

from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine, create_async_engine, async_sessionmaker
)
from sqlalchemy.exc import (
    SQLAlchemyError, OperationalError, InterfaceError, TimeoutError as SQLAlchemyTimeoutError
)
from sqlalchemy.sql import text

logger = logging.getLogger(__name__)

# Global state management
_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

# Production-ready default configuration
DEFAULT_POOL_SIZE = 20
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_RECYCLE = 3600  # 1 hour
DEFAULT_POOL_TIMEOUT = 30
DEFAULT_POOL_PRE_PING = True
DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_MAX_RETRY_DELAY = 30.0


def create_ssl_context() -> Optional[ssl.SSLContext]:
    """Create SSL context based on environment configuration.

    Returns:
        ssl.SSLContext or None: Configured SSL context or None if SSL disabled
    """
    # Default SSL mode to 'disable' for localhost, 'require' otherwise
    db_host = os.getenv("DATABASE_HOST", "localhost")
    default_ssl_mode = 'disable' if db_host in ['localhost', '127.0.0.1'] else 'require'
    sslmode = os.getenv("DATABASE_SSLMODE", default_ssl_mode).lower()
    
    cafile = os.getenv("DATABASE_SSLROOTCERT")

    if sslmode == 'disable':
        logger.info("Database SSL is disabled")
        return None

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


def get_connect_args() -> dict:
    """Get connection arguments including SSL configuration.

    Returns:
        dict: Connection arguments for SQLAlchemy engine
    """
    connect_args = {
        "server_settings": {
            "application_name": f"syntextai-{os.getpid()}",
        }
    }

    # Add SSL context if configured
    ssl_context = create_ssl_context()
    if ssl_context:
        connect_args["ssl"] = ssl_context

    # For asyncpg, timeout is handled differently
    timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "30"))
    if timeout > 0:
        connect_args["timeout"] = timeout

    logger.debug(
        "Database connection args configured: %s",
        {k: v for k, v in connect_args.items() if k not in ('ssl',)}
    )

    return connect_args


def get_database_url() -> str:
    """Get database URL for async connections.

    This centralizes database URL construction and ensures consistency
    across the application.

    Returns:
        str: Database URL for asyncpg connections
    """
    # For development/testing, use default values if environment variables are not set
    database_config = {
        'dbname': os.getenv("DATABASE_NAME", "syntextai"),
        'user': os.getenv("DATABASE_USER", "postgres"),
        'password': os.getenv("DATABASE_PASSWORD", "password"),
        'host': os.getenv("DATABASE_HOST", "localhost"),
        'port': os.getenv("DATABASE_PORT", "5432"),
    }
    
    # Validate that we have reasonable values
    if not database_config['dbname'] or database_config['dbname'] == 'None':
        database_config['dbname'] = 'syntextai'
    if not database_config['user'] or database_config['user'] == 'None':
        database_config['user'] = 'postgres'
    if not database_config['host'] or database_config['host'] == 'None':
        database_config['host'] = 'localhost'
    
    # Ensure port is valid
    try:
        port_int = int(database_config['port'])
        if port_int <= 0 or port_int > 65535:
            database_config['port'] = "5432"
    except (ValueError, TypeError):
        database_config['port'] = "5432"
    
    # Ensure password is properly encoded for URL construction
    password = database_config['password'] or ""
    password_str = str(password)
    
    logger.info(f"Using database config: host={database_config['host']}, port={database_config['port']}, db={database_config['dbname']}, user={database_config['user']}")
    
    return (
        f"postgresql+asyncpg://{database_config['user']}:{quote_plus(password_str)}"
        f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
    )


def get_engine_options() -> dict:
    """Get comprehensive engine options for production use.

    Returns:
        dict: SQLAlchemy engine configuration options
    """
    return {
        "echo": os.getenv("SQL_ECHO", "false").lower() == "true",
        "pool_size": int(os.getenv("DB_POOL_SIZE", str(DEFAULT_POOL_SIZE))),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", str(DEFAULT_MAX_OVERFLOW))),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", str(DEFAULT_POOL_RECYCLE))),
        "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", str(DEFAULT_POOL_TIMEOUT))),
        "pool_pre_ping": os.getenv("DB_POOL_PRE_PING", "true").lower() == "true",
        "connect_args": get_connect_args(),
    }


@retry(
    stop=stop_after_attempt(int(os.getenv("DB_CONNECTION_RETRIES", str(DEFAULT_RETRY_ATTEMPTS)))),
    wait=wait_exponential(
        multiplier=1,
        min=float(os.getenv("DB_RETRY_DELAY", str(DEFAULT_RETRY_DELAY))),
        max=float(os.getenv("DB_MAX_RETRY_DELAY", str(DEFAULT_MAX_RETRY_DELAY)))
    ),
    retry=retry_if_exception_type((OperationalError, InterfaceError, SQLAlchemyTimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING) if TENACITY_AVAILABLE else None,
    reraise=True
)
def create_engine_with_retry() -> AsyncEngine:
    """Create async SQLAlchemy engine with retry logic.

    Returns:
        AsyncEngine: Configured async engine with connection pooling

    Raises:
        SQLAlchemyError: If engine creation fails after retries
    """
    global _engine

    if _engine is not None:
        return _engine

    try:
        database_url = get_database_url()
        engine_options = get_engine_options()

        logger.info(f"Creating database engine for {os.getenv('DATABASE_HOST', 'localhost')}:{os.getenv('DATABASE_PORT', '5432')}")
        _engine = create_async_engine(database_url, **engine_options)

        logger.info("Database engine created successfully")
        return _engine

    except Exception as e:
        logger.error("Failed to create database engine: %s", str(e), exc_info=True)
        _engine = None
        raise


def get_engine() -> AsyncEngine:
    """Get or create the async database engine.

    Returns:
        AsyncEngine: The configured async engine
    """
    if _engine is None:
        return create_engine_with_retry()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory.

    Returns:
        async_sessionmaker: Factory for creating async sessions
    """
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False
        )
    return _async_session_factory


class AsyncSessionManager:
    """Async context manager for database sessions."""
    
    def __init__(self):
        self.session = None
    
    async def __aenter__(self) -> AsyncSession:
        if _async_session_factory is None:
            await init_db()
        self.session = _async_session_factory()
        return self.session
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type is not None:
                    await self.session.rollback()
                else:
                    await self.session.commit()
            finally:
                await self.session.close()


def get_async_session() -> AsyncSessionManager:
    """Get an async database session manager.

    Returns:
        AsyncSessionManager: An async context manager for database sessions.
    """
    return AsyncSessionManager()


async def init_db() -> None:
    """Initialize database connection and validate connectivity.

    Raises:
        RuntimeError: If database health check fails
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            if result.scalar() != 1:
                raise RuntimeError("Database health check failed")
        logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise RuntimeError(f"Database health check failed: {e}")


async def close_db() -> None:
    """Close database connections and clean up resources."""
    global _engine, _async_session_factory

    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None

    _async_session_factory = None
    logger.info("Database cleanup complete")
