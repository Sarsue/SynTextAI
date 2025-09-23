"""
Database utility functions and classes.

This module provides utility functions and classes for working with the database,
including session management, connection handling, and common database operations.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional, Type, TypeVar, Union

from sqlalchemy import text
from sqlalchemy.exc import (
    SQLAlchemyError, OperationalError, InterfaceError, TimeoutError as SQLAlchemyTimeoutError
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine, create_async_engine, async_sessionmaker
)
from sqlalchemy.orm import sessionmaker, declarative_base

# Application imports
from ..core.config import settings
from urllib.parse import quote_plus

# Configure logging
logger = logging.getLogger(__name__)

# Type variables for generic type hints
T = TypeVar('T')

# SQLAlchemy base class for declarative models
Base = declarative_base()

# Global engine and session factory
_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_initialized = False


def get_db_url() -> str:
    """
    Construct the database URL from environment variables or configuration.
    
    Returns:
        str: The database URL without SSL mode in query parameters
        
    Note:
        SSL mode is handled in get_engine() via connect_args, not in the URL
    """
    # Get database configuration
    db_user = getattr(settings, 'DATABASE_USER', '')
    db_password = getattr(settings, 'DATABASE_PASSWORD', '')
    db_host = getattr(settings, 'DATABASE_HOST', 'localhost')
    db_port = getattr(settings, 'DATABASE_PORT', '5432')
    db_name = getattr(settings, 'DATABASE_NAME', '')
    
    # Construct the base URL - use postgresql+asyncpg:// for asyncpg
    db_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    # Note: SSL mode is handled in get_engine() via connect_args
    # Do not add sslmode to the URL query parameters
    
    return db_url


def get_engine() -> AsyncEngine:
    """
    Get the async SQLAlchemy engine, creating it if necessary.
    
    Returns:
        AsyncEngine: The SQLAlchemy async engine
        
    Raises:
        RuntimeError: If engine creation fails
    """
    global _engine
    
    if _engine is None:
        try:
            db_url = get_db_url()
            
            # Get engine options from settings
            engine_options = settings.get_engine_options()
            
            # Add asyncpg-specific connection parameters
            server_settings = {
                "application_name": "syntextai-api",
                "timezone": "UTC",
                "statement_timeout": str(getattr(settings, 'DB_STATEMENT_TIMEOUT', 30000))
            }
            
            connect_args = {
                "server_settings": server_settings,
                "command_timeout": getattr(settings, 'DB_CONNECT_TIMEOUT', 30),
                "timeout": getattr(settings, 'DB_CONNECT_TIMEOUT', 30)
            }
            
            # Handle SSL based on DATABASE_SSLMODE
            if hasattr(settings, 'DATABASE_SSLMODE'):
                if settings.DATABASE_SSLMODE == 'require':
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    connect_args["ssl"] = ssl_context
                elif settings.DATABASE_SSLMODE == 'disable':
                    connect_args["ssl"] = False
            
            # Add connection pool settings
            engine_options.update({
                "pool_pre_ping": getattr(settings, 'DB_POOL_PRE_PING', True),
                "pool_recycle": getattr(settings, 'DB_POOL_RECYCLE', 1800),
                "pool_size": getattr(settings, 'DB_POOL_SIZE', 5),
                "max_overflow": getattr(settings, 'DB_MAX_OVERFLOW', 10),
                "pool_timeout": getattr(settings, 'DB_POOL_TIMEOUT', 30),
                "pool_use_lifo": True,  # Use LIFO for better connection reuse
                "connect_args": connect_args
            })
            
            logger.info(f"Creating database engine with URL: {db_url.split('@')[-1]}")
            logger.debug(f"Database engine options: {engine_options}")
            
            _engine = create_async_engine(db_url, **engine_options)
            logger.info("Database engine created successfully")
            
        except Exception as e:
            logger.error("Failed to create database engine: %s", str(e), exc_info=True)
            raise RuntimeError("Failed to initialize database engine") from e
            
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get the async session factory, creating it if necessary.
    
    Returns:
        async_sessionmaker: The SQLAlchemy async session factory
    """
    global _async_session_factory
    
    if _async_session_factory is None:
        engine = get_engine()
        _async_session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            future=True,
        )
        logger.debug("Session factory created")
        
    return _async_session_factory


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get an async database session with automatic cleanup.
    
    This context manager handles session creation, transaction management,
    and cleanup automatically.
    
    Yields:
        AsyncSession: An async database session
        
    Example:
        async with get_async_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
    """
    session_factory = get_session_factory()
    session = session_factory()
    
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error("Error in database session: %s", str(e), exc_info=True)
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """
    Initialize the database connection and create tables.
    
    This function should be called during application startup.
    It will attempt to connect to the database with exponential backoff retries.
    """
    global _initialized
    
    if _initialized:
        logger.debug("Database already initialized, skipping...")
        return
        
    max_retries = getattr(settings, 'DB_CONNECTION_RETRIES', 5)
    base_delay = getattr(settings, 'DB_RETRY_DELAY', 1.0)
    max_delay = getattr(settings, 'DB_MAX_RETRY_DELAY', 30.0)
    
    for attempt in range(1, max_retries + 1):
        try:
            # Calculate exponential backoff with jitter
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = delay * 0.1  # Add ±10% jitter
            delay = delay - (jitter / 2) + (jitter * random.random())
            
            if attempt > 1:
                logger.info(f"Attempting database connection (attempt {attempt}/{max_retries})...")
            
            engine = get_engine()
            
            # Test the connection with a simple query
            async with engine.begin() as conn:
                start_time = time.monotonic()
                result = await conn.execute(text("SELECT 1"))
                query_time = (time.monotonic() - start_time) * 1000
                
                if result.scalar() != 1:
                    raise RuntimeError("Unexpected result from database health check")
                
                logger.info(f"Database connection successful (ping: {query_time:.2f}ms)")
                
            # If we get here, connection was successful
            _initialized = True
            logger.info("Database initialization complete")
            return
            
        except Exception as e:
            logger.error(
                "Database connection attempt %d/%d failed: %s",
                attempt, max_retries, str(e)
            )
            
            if attempt < max_retries:
                logger.info("Retrying in %.1f seconds... (attempt %d/%d)", 
                          delay, attempt + 1, max_retries)
                await asyncio.sleep(delay)
            else:
                logger.critical(
                    "Failed to initialize database after %d attempts. Last error: %s",
                    max_retries, str(e),
                    exc_info=True
                )
                raise RuntimeError(
                    f"Failed to initialize database after {max_retries} attempts: {str(e)}"
                ) from e


async def close_db() -> None:
    """
    Close the database connection and clean up resources.
    
    This function should be called during application shutdown.
    It ensures all database connections are properly closed and resources are released.
    """
    global _engine, _async_session_factory, _initialized
    
    if not _initialized:
        logger.debug("Database not initialized, nothing to close")
        return
        
    logger.info("Closing database connections...")
    
    try:
        if _engine is not None:
            logger.debug("Disposing database engine...")
            await _engine.dispose()
            _engine = None
            logger.debug("Database engine disposed")
            
        _async_session_factory = None
        _initialized = False
        
        logger.info("Database connections closed successfully")
        
    except Exception as e:
        logger.error("Error while closing database connections: %s", str(e), exc_info=True)
        raise RuntimeError("Failed to close database connections") from e


# Add database initialization to the module level
__all__ = [
    'get_engine',
    'get_session_factory',
    'get_async_session',
    'init_db',
    'close_db',
    'get_db_url',
    'Base',
]
