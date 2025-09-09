"""
Async database session management with SQLAlchemy 2.0 and asyncpg.
"""

import os
import asyncio
import logging
from typing import AsyncGenerator, Optional, Generator, Any, Dict
from contextlib import asynccontextmanager, contextmanager
import atexit

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool
from sqlalchemy.exc import OperationalError, TimeoutError, SQLAlchemyError
from sqlalchemy import event, Engine as SyncEngine

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# -------------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------------
logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# -------------------------------------------------------------------
# Environment setup
# -------------------------------------------------------------------
load_dotenv()

DATABASE_URL = os.getenv("ASYNC_DATABASE_URL") or \
    f"postgresql+asyncpg://{os.getenv('DATABASE_USER')}:{os.getenv('DATABASE_PASSWORD')}@" \
    f"{os.getenv('DATABASE_HOST')}:{os.getenv('DATABASE_PORT')}/{os.getenv('DATABASE_NAME')}"

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "60"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "3"))
CONNECT_RETRY_DELAY = int(os.getenv("DB_RETRY_DELAY", "5"))

# Default connection pool name (used in pg_stat_activity.application_name)
# Connection timeouts
ASYNC_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "30"))  # Default 30 seconds
POOL_NAME = os.getenv("DB_POOL_NAME", "syntext")

# -------------------------------------------------------------------
# Engine creation with retry logic
# -------------------------------------------------------------------
@retry(
    stop=stop_after_attempt(CONNECT_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((OperationalError, TimeoutError, asyncio.TimeoutError)),
    reraise=True
)
async def create_db_engine():
    """Create async database engine with retry logic."""
    logger.info(f"Creating async DB engine with pool '{POOL_NAME}' (timeout={ASYNC_CONNECT_TIMEOUT}s)...")
    
    try:
        return create_async_engine(
            DATABASE_URL,
            echo=os.getenv("SQL_ECHO", "false").lower() == "true",
            future=True,
            pool_pre_ping=True,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT,
            pool_recycle=POOL_RECYCLE,
            pool_use_lifo=True,  # Better for most web apps
            connect_args={
                "timeout": ASYNC_CONNECT_TIMEOUT,  # asyncpg connection timeout
                "server_settings": {
                    "application_name": POOL_NAME,
                    "statement_timeout": "30000",  # 30s
                    "idle_in_transaction_session_timeout": "300000"  # 5 min
                }
            }
        )
    except Exception as e:
        logger.error(f"Error creating database engine: {e}", exc_info=True)
        raise

# Initialize engines and session factories
engine: Optional[AsyncEngine] = None
sync_engine: Optional[SyncEngine] = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
sync_session_factory: Optional[sessionmaker] = None

async def startup():
    """Initialize the database connection and session factory."""
    global engine, async_session_factory, sync_engine, sync_session_factory
    
    try:
        if engine is None:
            engine = create_db_engine()
            async_session_factory = async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=False
            )
        
        # Initialize sync engine if needed for compatibility
        if sync_engine is None:
            sync_engine = create_engine(
                DATABASE_URL.replace('+asyncpg', ''),
                pool_size=POOL_SIZE,
                max_overflow=MAX_OVERFLOW,
                pool_timeout=POOL_TIMEOUT,
                pool_recycle=POOL_RECYCLE,
                pool_pre_ping=True,
                pool_use_lifo=True,
                pool_reset_on_return='commit'
            )
            sync_session_factory = sessionmaker(
                bind=sync_engine,
                autocommit=False,
                autoflush=False,
                expire_on_commit=False
            )
            
            # Register cleanup
            atexit.register(cleanup_sync_engine)
            logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database connection: {e}", exc_info=True)
        raise

async def shutdown():
    """Properly close all database connections."""
    global engine, sync_engine
    if engine:
        await engine.dispose()
        engine = None
    
    if sync_engine:
        sync_engine.dispose()
        sync_engine = None

def cleanup_sync_engine():
    """Clean up sync engine resources."""
    global sync_engine
    if sync_engine:
        sync_engine.dispose()
        sync_engine = None
        logger.info("Database connection pool closed")

# -------------------------------------------------------------------
# Session factory
# -------------------------------------------------------------------
def get_async_session_factory():
    if engine is None:
        raise RuntimeError("Database engine is not initialized")
    return async_session_factory

# -------------------------------------------------------------------
# Session context managers
# -------------------------------------------------------------------
@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that provides a database session with auto-commit/rollback.
    
    Use this for write operations.
    """
    if not async_session_factory:
        raise RuntimeError("Database not initialized. Call startup() first.")
    
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Session rollback due to error: {e}", exc_info=True)
            raise

@asynccontextmanager
async def get_read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that provides a read-only database session.
    
    Automatically rolls back any changes when the session is closed.
    Use this for read-only operations.
    """
    if not async_session_factory:
        raise RuntimeError("Database not initialized. Call startup() first.")
    
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()  # Always rollback read-only sessions

@asynccontextmanager
async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for DB sessions with connection check + rollback on error."""
    if async_session_factory is None:
        await startup()
    
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database error: {e}", exc_info=True)
            raise
        finally:
            await session.close()

# Sync database session for compatibility
@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """Synchronous session context manager for compatibility."""
    if sync_session_factory is None:
        raise RuntimeError("Sync database not initialized. Call startup() first.")
    
    session = sync_session_factory()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Sync database error: {e}", exc_info=True)
        raise
    finally:
        session.close()

# FastAPI dependency for sync sessions
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for sync DB sessions."""
    with get_sync_session() as session:
        yield session
