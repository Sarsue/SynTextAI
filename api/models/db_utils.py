import logging
import ssl
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine, create_async_engine, async_sessionmaker
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import text
from urllib.parse import quote_plus

from ..core.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def create_ssl_context() -> ssl.SSLContext:
    ssl_context = ssl.create_default_context()
    if getattr(settings, "DB_SSL_ROOT_CERT", None):
        ssl_context.load_verify_locations(cafile=settings.DB_SSL_ROOT_CERT)

    if getattr(settings, "DB_SSL_VERIFY", True) is False:
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

    return ssl_context


def get_engine() -> AsyncEngine:
    global _engine
    if _engine:
        return _engine

    db_url = (
        f"postgresql+asyncpg://{settings.DATABASE_USER}:{quote_plus(settings.DATABASE_PASSWORD)}"
        f"@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
    )

    engine_options = {
        "echo": getattr(settings, "SQL_ECHO", False),
        "pool_size": getattr(settings, "DB_POOL_SIZE", 5),
        "max_overflow": getattr(settings, "DB_MAX_OVERFLOW", 5),
        "pool_recycle": getattr(settings, "DB_POOL_RECYCLE", 300),
        "pool_pre_ping": True,
        "connect_args": {
            "command_timeout": getattr(settings, "DB_CONNECT_TIMEOUT", 30),
        },
    }

    ssl_context = None
    if getattr(settings, "DB_SSL_ROOT_CERT", None):
        ssl_context = create_ssl_context()
        engine_options["connect_args"]["ssl"] = ssl_context

    logger.info(f"Creating database engine for {settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}")
    _engine = create_async_engine(db_url, **engine_options)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
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
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        if result.scalar() != 1:
            raise RuntimeError("Database health check failed")
        logger.info("Database connection successful")


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
