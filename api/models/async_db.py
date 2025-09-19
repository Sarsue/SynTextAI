"""
Async database session management with SQLAlchemy 2.0 and asyncpg.
"""

import os
import ssl
import asyncio
import logging
from typing import AsyncGenerator, Optional, Union
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, TimeoutError

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

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

# Get environment variables with defaults
DB_CONFIG = {
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': int(os.getenv("DATABASE_PORT", "5432")),
    'database': os.getenv("DATABASE_NAME"),
    'sslmode': os.getenv("DATABASE_SSLMODE", "require")
}

# URL-encode the password
encoded_password = quote_plus(DB_CONFIG['password'])

# Build database URL
DATABASE_URL = (
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{encoded_password}@"
    f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)

# Pool configuration
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "300"))  # 5 minutes
CONNECT_RETRIES = int(os.getenv("DB_CONNECT_RETRIES", "3"))
ASYNC_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "30"))
POOL_NAME = os.getenv("DB_POOL_NAME", "syntext")

# -------------------------------------------------------------------
# Engine creation with retry logic
# -------------------------------------------------------------------
def _build_ssl_context(sslmode: str) -> Optional[Union[ssl.SSLContext, bool]]:
    """Create SSL context based on sslmode with support for self-signed certs."""
    sslmode = (sslmode or "require").lower()
    
    if sslmode == "disable":
        logger.info("SSL is disabled for database connection")
        return False
        
    logger.info(f"Creating SSL context with mode: {sslmode}")
    
    # Create a default SSL context
    ssl_context = ssl.create_default_context()
    
    # Handle different SSL modes
    if sslmode == "require":
        # Basic SSL without certificate verification
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context
        
    # For verify-ca and verify-full, try to load the CA certificate
    cafile = os.getenv("DB_SSLROOTCERT")
    if not cafile:
        # Try default location
        cafile = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'api', 'config', 'ca-certificate.crt'
        )
        
    if os.path.exists(cafile):
        logger.info(f"Using CA certificate: {cafile}")
        ssl_context.load_verify_locations(cafile=cafile)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        
        if sslmode == "verify-full":
            ssl_context.check_hostname = True
        else:  # verify-ca
            ssl_context.check_hostname = False
            
        return ssl_context
        
    # Fallback to basic SSL if no CA cert found
    logger.warning("CA certificate not found, falling back to basic SSL")
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


@retry(
    stop=stop_after_attempt(CONNECT_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((OperationalError, TimeoutError, asyncio.TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def create_db_engine() -> AsyncEngine:
    """Create async database engine with proper configuration and retry logic."""
    # URL-encode the password
    encoded_password = quote_plus(DB_CONFIG['password'] or "")
    
    # Build connection string
    db_url = (
        f"postgresql+asyncpg://{DB_CONFIG['user']}:{encoded_password}@"
        f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    )
    
    # Get SSL context
    ssl_context = _build_ssl_context(DB_CONFIG['sslmode'])
    
    # Connection arguments
    connect_args = {
        "timeout": ASYNC_CONNECT_TIMEOUT,
        "server_settings": {
            "application_name": POOL_NAME,
            "statement_timeout": "30000",  # 30s
            "idle_in_transaction_session_timeout": "300000",  # 5 min
        },
    }
    
    if ssl_context is not None:
        connect_args["ssl"] = ssl_context
    
    # Create safe URL for logging (without password)
    safe_url = db_url.replace(encoded_password, "***")
    logger.info(f"Creating async DB engine for {safe_url} (timeout={ASYNC_CONNECT_TIMEOUT}s)")
    
    # Create the engine with our configuration
    return create_async_engine(
        db_url,
        echo=os.getenv("SQL_ECHO", "false").lower() == "true",
        pool_pre_ping=True,  # Check connection health
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_recycle=POOL_RECYCLE,
        connect_args=connect_args,
    )
    
    return engine


# -------------------------------------------------------------------
# Globals
# -------------------------------------------------------------------
engine: Optional[AsyncEngine] = None
async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

# -------------------------------------------------------------------
# Startup / Shutdown
# -------------------------------------------------------------------
async def startup():
    """Initialize the database connection and session factory."""
    global engine, async_session_factory
    
    if engine is None:
        try:
            logger.info("Initializing database engine...")
            engine = await create_db_engine()
            
            # Test the connection
            from sqlalchemy import text
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                logger.info(f"Database connection test: {result.scalar() == 1}")
            
            # Create session factory
            async_session_factory = async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=False,
            )
            logger.info("Database engine and session factory initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize database: {str(e)}")
            if engine:
                await engine.dispose()
            raise


async def shutdown():
    """Properly close all database connections and clean up resources."""
    global engine, async_session_factory
    if engine:
        await engine.dispose()
        logger.info("Database engine disposed")
    engine = None
    async_session_factory = None


# -------------------------------------------------------------------
# Session manager
# -------------------------------------------------------------------
@asynccontextmanager
async def get_async_session(commit_on_exit: bool = True) -> AsyncGenerator[AsyncSession, None]:
    """Provide a session with optional commit at exit."""
    if async_session_factory is None:
        await startup()

    async with async_session_factory() as session:
        try:
            yield session
            if commit_on_exit:
                await session.commit()
        except Exception:
            await session.rollback()
            raise


# Convenience wrappers
def get_async_session_factory():
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call startup() first.")
    return async_session_factory


@asynccontextmanager
async def get_read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """Session that always rolls back (read-only)."""
    async with get_async_session(commit_on_exit=False) as session:
        try:
            yield session
        finally:
            await session.rollback()
