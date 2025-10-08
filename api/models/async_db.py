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
    sslmode = os.getenv("DATABASE_SSLMODE", "require").lower()
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
        "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "30")),
        "application_name": f"syntextai-{os.getpid()}",
    }

    # Add SSL context if configured
    ssl_context = create_ssl_context()
    if ssl_context:
        connect_args["ssl"] = ssl_context

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
    database_config = {
        'dbname': os.getenv("DATABASE_NAME"),
        'user': os.getenv("DATABASE_USER"),
        'password': os.getenv("DATABASE_PASSWORD"),
        'host': os.getenv("DATABASE_HOST"),
        'port': os.getenv("DATABASE_PORT"),
    }

    return (
        f"postgresql+asyncpg://{database_config['user']}:{quote_plus(database_config['password'])}"
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

        logger.info(f"Creating database engine for {os.getenv('DATABASE_HOST')}:{os.getenv('DATABASE_PORT')}")
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


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session with automatic lifecycle management.

    Yields:
        AsyncSession: An async database session that is automatically closed after use.
    """
    if _async_session_factory is None:
        await init_db()

    session = _async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


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
