"""
Database session management utilities with connection pooling.
"""
from typing import Generator, Optional
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
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

# Connection pool settings
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))  # Recycle connections after 1 hour

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
    pool_pre_ping=True,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true"
)

# Create a thread-safe session factory
SessionLocal = scoped_session(
    sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False  # Prevent detached instance errors
    )
)

# Create a Base class for declarative models
Base = declarative_base()
Base.query = SessionLocal.query_property()

# Optional: Add event listeners for connection tracking
@event.listens_for(engine, 'checkout')
def on_checkout(dbapi_connection, connection_record, connection_proxy):
    logger.debug(f"Connection checked out from pool: {id(dbapi_connection)}")

@event.listens_for(engine, 'checkin')
def on_checkin(dbapi_connection, connection_record):
    logger.debug(f"Connection returned to pool: {id(dbapi_connection)}")

@event.listens_for(engine, 'close')
def on_close(dbapi_connection, connection_record):
    logger.debug(f"Connection closed: {id(dbapi_connection)}")

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
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {str(e)}")
        raise
    finally:
        session.close()

def get_db() -> Generator[Session, None, None]:
    """
    Dependency function for FastAPI to get a database session.
    
    Yields:
        SQLAlchemy Session: A database session that's automatically closed after use.
    """
    with get_db_session() as session:
        yield session
