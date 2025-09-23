"""
Legacy database connection management for SynTextAI.

NOTE: This module is deprecated in favor of the new db_utils module.
It's maintained for backward compatibility but will be removed in a future version.

For new code, use the utilities from `app.api.models.db_utils` instead.
"""
import logging
import os
import warnings

# Configure logger
logger = logging.getLogger(__name__)
from typing import AsyncGenerator, Dict, Optional, Any, Callable, Awaitable, TypeVar
from contextlib import asynccontextmanager
from urllib.parse import quote_plus
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from ..core.config import settings

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.exc import (
    SQLAlchemyError, OperationalError, InterfaceError, TimeoutError as SQLAlchemyTimeoutError
)
from sqlalchemy.sql import text

# Import new database utilities
from .db_utils import (
    get_engine,
    get_session_factory,
    get_async_session as _get_async_session,
    init_db as _init_db,
    close_db as _close_db,
)

# Configure logging
logger = logging.getLogger(__name__)

# Warn about deprecation
warnings.warn(
    "The async_db module is deprecated. Use app.api.models.db_utils instead.",
    DeprecationWarning,
    stacklevel=2
)

# Type variable for generic return types
T = TypeVar('T')

# Legacy global variables for backward compatibility
_engine = None
_async_session_factory = None
_initialized = False

# Default values for backward compatibility
DEFAULT_POOL_SIZE = 20
DEFAULT_MAX_OVERFLOW = 10
DEFAULT_POOL_RECYCLE = 3600
DEFAULT_POOL_TIMEOUT = 30
DEFAULT_POOL_PRE_PING = True
DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_MAX_RETRY_DELAY = 30.0

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

# -------------------------------------------------------------------
# SSL Context Management
# -------------------------------------------------------------------

def _create_ssl_context() -> tuple:
    """
    Create and configure SSL context based on environment configuration.
    
    Returns:
        ssl.SSLContext: Configured SSL context
        bool: Whether to use SSL
    """
    sslmode = DB_CONFIG['sslmode']
    cafile = DB_CONFIG['sslrootcert']
    
    if sslmode == 'disable':
        logger.info("Database SSL is disabled")
        return None, False
        
    logger.info(f"Configuring database SSL with mode: {sslmode}")
    
    try:
        # Create a default SSL context
        ssl_context = ssl.create_default_context()
        
        if sslmode in ['require', 'prefer']:
            # Basic SSL without certificate verification
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context, True
            
        # For verify-ca and verify-full, we need the CA certificate
        if not cafile or not os.path.exists(cafile):
            logger.warning(
                "SSL mode requires CA certificate but none was provided. "
                "Falling back to basic SSL without verification."
            )
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context, True
            
        # Configure for verify-ca or verify-full with provided CA cert
        ssl_context.load_verify_locations(cafile=cafile)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        if sslmode == 'verify-full':
            ssl_context.check_hostname = True
            
        return ssl_context, True
        
    except Exception as e:
        logger.error(f"Error configuring SSL context: {e}")
        # Fall back to basic SSL on error
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context, True

# -------------------------------------------------------------------
# Database Engine Management
# -------------------------------------------------------------------

def _get_connect_args() -> Dict[str, Any]:
    """Get connection arguments including SSL configuration.
    
    Returns:
        dict: Connection arguments for SQLAlchemy engine
    """
    connect_args: Dict[str, Any] = {}
    
    # Get SSL context from settings
    ssl_context = settings.get_ssl_context()
    if ssl_context:
        connect_args["ssl"] = ssl_context
    
    # Add PostgreSQL specific settings
    connect_args.update({
        "connect_timeout": settings.DB_CONNECT_TIMEOUT,
        "keepalives_idle": settings.DB_KEEPALIVES_IDLE,
        "keepalives_interval": settings.DB_KEEPALIVES_INTERVAL,
        "keepalives_count": settings.DB_KEEPALIVES_COUNT,
        "application_name": f"{settings.APP_NAME.lower().replace(' ', '-')}-{os.getpid()}",
        "prepared_statement_cache_size": 100,
    })
    
    # Add statement timeout if configured
    if settings.DB_STATEMENT_TIMEOUT:
        connect_args["command_timeout"] = settings.DB_STATEMENT_TIMEOUT // 1000  # Convert ms to seconds
    
    logger.debug(
        "Database connection args: %s",
        {k: v for k, v in connect_args.items() if k not in ('ssl', 'ssl_context')}
    )
    
    return connect_args

@retry(
    stop=stop_after_attempt(settings.DB_CONNECTION_RETRIES),
    wait=wait_exponential(
        multiplier=1,
        min=settings.DB_RETRY_DELAY,
        max=settings.DB_MAX_RETRY_DELAY
    ),
    retry=retry_if_exception_type((OperationalError, InterfaceError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True
)
async def create_engine() -> AsyncEngine:
    """
    Create and configure an async SQLAlchemy engine with retry logic.
    
    Returns:
        AsyncEngine: Configured SQLAlchemy async engine with connection pooling
        
    Raises:
        ValueError: If database configuration is invalid
        SQLAlchemyError: If engine creation fails after retries
    """
    global _engine
    
    if _engine is not None:
        return _engine
    
    try:
        # Get database URL from settings
        database_url = settings.async_database_url
        if not database_url:
            raise ValueError("Database URL is not configured")
            
        logger.info("Creating database engine")
        
        # Get connection arguments and engine options
        connect_args = _get_connect_args()
        engine_options = settings.get_engine_options()
        engine_options["connect_args"] = connect_args
        
        # Create the engine
        _engine = create_async_engine(database_url, **engine_options)
        
        # Add event listeners for connection management
        @event.listens_for(_engine.sync_engine, "engine_connect")
        def ping_connection(dbapi_connection, connection_record):
            """Ping the database connection before using it."""
            if settings.DB_POOL_PRE_PING:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("SELECT 1")
                except Exception as e:
                    # If the ping fails, raise an error to trigger a reconnection
                    logger.warning("Database ping failed, forcing reconnect")
                    raise DisconnectionError() from e
                finally:
                    cursor.close()
        
        logger.info("Database engine created successfully")
        return _engine
        
    except Exception as e:
        logger.error("Failed to create database engine: %s", str(e), exc_info=True)
        _engine = None
        raise

# -------------------------------------------------------------------
# Application Lifecycle
# -------------------------------------------------------------------

async def startup():
    """
    Initialize the database connection and session factory.
    
    This is a legacy function that wraps the new init_db() function
    for backward compatibility.
    
    Raises:
        RuntimeError: If database initialization fails
    """
    global _engine, _async_session_factory, _initialized
    
    if _initialized:
        logger.debug("Database already initialized, skipping startup")
        return True
    
    logger.warning(
        "The startup() function is deprecated. "
        "Use app.api.models.db_utils.init_db() instead."
    )
    
    try:
        # Initialize the database using the new utility
        await _init_db()
        
        # Update legacy globals for backward compatibility
        _engine = get_engine()
        _async_session_factory = get_session_factory()
        _initialized = True
        
        logger.info("Database initialized successfully (legacy mode)")
        return True
        
    except Exception as e:
        logger.error("Failed to initialize database: %s", str(e), exc_info=True)
        _initialized = False
        raise RuntimeError(f"Failed to initialize database: {str(e)}") from e


async def shutdown():
    """
    Close database connections and clean up resources.
    
    This is a legacy function that wraps the new close_db() function
    for backward compatibility.
    """
    global _engine, _async_session_factory, _initialized
    
    if not _initialized:
        return
    
    logger.warning(
        "The shutdown() function is deprecated. "
        "Use app.api.models.db_utils.close_db() instead."
    )
    
    try:
        # Close the database using the new utility
        await _close_db()
        
        # Update legacy globals
        _engine = None
        _async_session_factory = None
        _initialized = False
        
        logger.info("Database shutdown complete (legacy mode)")
        
    except Exception as e:
        logger.error("Error during database shutdown: %s", str(e), exc_info=True)
        raise
    
    finally:
        # Reset global state
        _engine = None
        _async_session_factory = None
        _initialized = False
        
        logger.info("Database shutdown complete")

# Convenience wrapper
async def get_async_session_factory() -> Callable[..., AsyncSession]:
    """
    Get the async session factory.
    
    This is a legacy function that wraps the new get_session_factory() function
    for backward compatibility.
    
    Returns:
        async_sessionmaker: A callable that creates new async sessions
        
    Example:
        session_factory = await get_async_session_factory()
        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
    """
    warnings.warn(
        "get_async_session_factory() is deprecated. "
        "Use app.api.models.db_utils.get_session_factory() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    
    if not _initialized:
        await startup()
    
    return get_session_factory()

# Convenience wrapper
@asynccontextmanager
async def get_read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a read-only database session that automatically rolls back changes.
    
    This is a legacy function that provides a read-only session
    for backward compatibility.
    
    Yields:
        AsyncSession: A database session that won't persist changes
        
    Example:
        async with get_read_only_session() as session:
            result = await session.execute(text("SELECT * FROM users"))
            users = result.scalars().all()
    """
    warnings.warn(
        "get_read_only_session() is deprecated. "
        "Use app.api.models.db_utils.get_async_session() with manual transaction control instead.",
        DeprecationWarning,
        stacklevel=2
    )
    
    if not _initialized:
        await startup()
    
    session_factory = get_session_factory()
    session = session_factory()
    
    try:
        # Set the session to read-only mode
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.begin()
        
        yield session
        
    except SQLAlchemyError as e:
        logger.error(f"Database error in read-only session: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error in read-only session: {e}", exc_info=True)
        raise SQLAlchemyError(f"Read-only session failed: {str(e)}") from e
