"""
Database session management utilities with connection pooling.
"""
import atexit
from typing import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, scoped_session
from sqlalchemy.pool import QueuePool
import os
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Load environment variables
load_dotenv()

# Database configuration
database_config = {
    'dbname': os.getenv("DATABASE_NAME"),
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': os.getenv("DATABASE_PORT"),
}

# Connection pool settings - optimized for DigitalOcean Managed Databases
# Conservative settings to prevent connection leaks
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "3"))  # Base number of connections
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))  # Max overflow connections
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "5"))  # Connection timeout in seconds
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "180"))  # Recycle connections after 3 minutes
MAX_USAGE = int(os.getenv("DB_MAX_USAGE", "500"))  # Maximum number of times a connection can be used
POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"  # Enable connection health checks
POOL_USE_LIFO = True  # Use LIFO to better utilize connection pooling
POOL_RESET_ON_RETURN = 'commit'  # Reset connections when returned to pool

# Log pool configuration
logger.info(
    f"Database connection pool - size: {POOL_SIZE}, "
    f"max_overflow: {MAX_OVERFLOW}, "
    f"timeout: {POOL_TIMEOUT}s, "
    f"recycle: {POOL_RECYCLE}s, "
    f"pre_ping: {POOL_PRE_PING}, "
    f"max_usage: {MAX_USAGE}s"
)

DATABASE_URL = (
    f"postgresql://{database_config['user']}:{database_config['password']}"
    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
)

# Create SQLAlchemy engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=POOL_PRE_PING,
    pool_use_lifo=POOL_USE_LIFO,  # Use last-in-first-out for better connection reuse
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    echo_pool=os.getenv("SQL_ECHO_POOL", "false").lower() == "true",
    hide_parameters=os.getenv("SQL_HIDE_PARAMETERS", "true").lower() == "true",
    # Connection settings
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    },
    # Set statement timeout using the connect event
    execution_options={
        "isolation_level": "READ COMMITTED"
    }
)

# Set statement timeout for all connections
@event.listens_for(engine, 'connect')
def set_statement_timeout(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET statement_timeout = 30000")  # 30 seconds
    cursor.close()

# Create a thread-safe session factory with explicit connection management
SessionLocal = scoped_session(
    sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,  # Prevent detached instance errors
        class_=Session
    )
)

# Create a Base class for declarative models
Base = declarative_base()
Base.query = SessionLocal.query_property()

# Connection event listeners for better tracking and cleanup
@event.listens_for(engine, 'checkout')
def on_checkout(dbapi_connection, connection_record, connection_proxy):
    logger.debug(f"Connection checked out from pool: {id(dbapi_connection)}")

@event.listens_for(engine, 'checkin')
def on_checkin(dbapi_connection, connection_record):
    logger.debug(f"Connection returned to pool: {id(dbapi_connection)}")

@event.listens_for(engine, 'close')
def on_close(dbapi_connection, connection_record):
    logger.debug(f"Connection closed: {id(dbapi_connection)}")

@event.listens_for(engine, 'engine_disposed')
def receive_engine_disposed(engine):
    """Log when the engine is disposed."""
    logger.info("Database engine has been disposed")

def cleanup():
    """Close all connections when the application exits."""
    logger.info("Closing all database connections...")
    try:
        # Remove all scoped sessions first
        SessionLocal.remove()
        # Then dispose of the engine
        engine.dispose()
        logger.info("Database connections closed successfully")
    except Exception as e:
        logger.error(f"Error during database cleanup: {str(e)}", exc_info=True)
    finally:
        # Ensure the engine is disposed even if there's an error
        try:
            engine.dispose()
        except Exception as e:
            logger.error(f"Error disposing engine: {str(e)}", exc_info=True)

# Register cleanup function
atexit.register(cleanup)

# Context manager for database sessions
@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager that provides a database session that's properly closed after use.
    
    Usage:
        with get_db_session() as session:
            # Use session here
            result = session.query(MyModel).all()
    """
    session = None
    try:
        session = SessionLocal()
        yield session
        session.commit()
    except Exception as e:
        logger.error(f"Database error: {str(e)}", exc_info=True)
        if session:
            session.rollback()
        raise
    finally:
        if session:
            try:
                session.close()
            except Exception as e:
                logger.error(f"Error closing session: {str(e)}", exc_info=True)
        # Always remove the scoped session to prevent connection leaks
        try:
            SessionLocal.remove()
        except Exception as e:
            logger.error(f"Error removing scoped session: {str(e)}", exc_info=True)

# Dependency for FastAPI
def get_db() -> Generator[Session, None, None]:
    """
    Dependency function for FastAPI to get a database session.
    
    Yields:
        SQLAlchemy Session: A database session that's automatically closed after use.
    """
    with get_db_session() as session:
        try:
            yield session
        except Exception as e:
            logger.error(f"Error in database session: {str(e)}", exc_info=True)
            raise
