"""
Async database session management utilities.

This module provides async database functionality that mirrors the sync db.py module
while maintaining identical method signatures and return types.
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration (identical to sync version)
database_config = {
    'dbname': os.getenv("DATABASE_NAME"),
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': os.getenv("DATABASE_PORT"),
}

DATABASE_URL = (
    f"postgresql+asyncpg://{database_config['user']}:{database_config['password']}"
    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
)

# Create async SQLAlchemy engine and session factory
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,  # Recycle connections every hour
    echo=False  # Set to True for SQL query logging in development
)
AsyncSessionLocal = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=AsyncSession
)

async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Async dependency to get a database session for use with FastAPI endpoints.

    This mirrors the sync get_db() function signature exactly.

    Yields:
        AsyncSession: An async database session that is automatically closed after use.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
