# base_repository_manager.py
from __future__ import annotations
import asyncio, logging, time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional, Callable
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError, DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)
DEFAULT_SLOW_QUERY_THRESHOLD = 1.0
DEFAULT_SESSION_EXPIRE_ON_COMMIT = False

def _is_retryable_error(e: Exception) -> bool:
    if isinstance(e, (OperationalError, InterfaceError)):
        return True
    if isinstance(e, DBAPIError):
        try:
            code = getattr(e.orig, "pgcode", None)
            if code in {"40001","40P01","55P03","57014"}:
                return True
        except Exception:
            pass
    return False

class BaseRepositoryManager:
    def __init__(
        self,
        database_url: Optional[str] = None,
        engine: Optional[AsyncEngine] = None,
        session_factory: Optional[async_sessionmaker] = None,
        *,
        enable_set_timezone: bool = True,
        slow_query_threshold: float = DEFAULT_SLOW_QUERY_THRESHOLD,
        engine_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._provided_engine = engine is not None
        self._provided_session_factory = session_factory is not None
        self._database_url = database_url
        self._engine: Optional[AsyncEngine] = engine
        self._session_factory: Optional[async_sessionmaker] = session_factory
        self._engine_kwargs = engine_kwargs or {}
        self._enable_set_timezone = enable_set_timezone
        self._slow_query_threshold = slow_query_threshold
        self._lock = asyncio.Lock()
        self._closed = False
        self._metrics = {"queries":0,"transactions":0,"errors":0,"connection_attempts":0,"last_error":None,"start_time":time.time()}

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            if not self._database_url:
                raise RuntimeError("No engine provided and database_url not set")
            self._engine = create_async_engine(self._database_url, **self._engine_kwargs)
            self._add_engine_event_listeners()
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(bind=self.engine, expire_on_commit=DEFAULT_SESSION_EXPIRE_ON_COMMIT, class_=AsyncSession)
        return self._session_factory

    def _add_engine_event_listeners(self) -> None:
        if self._engine is None:
            return
        try:
            sync_engine = self._engine.sync_engine
        except Exception:
            return
        @event.listens_for(sync_engine,"checkout")
        def _on_checkout(dbapi_conn, connection_record, connection_proxy):
            self._metrics["connection_attempts"] += 1
            connection_record._checkout_time = time.time()
        @event.listens_for(sync_engine,"checkin")
        def _on_checkin(dbapi_conn, connection_record):
            start = getattr(connection_record,"_checkout_time",None)
            if start is not None:
                duration = time.time()-start
                if duration>1.0:
                    logger.warning("Connection checked out for %.2fs",duration)
        @event.listens_for(sync_engine,"before_cursor_execute")
        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("query_start_time",[]).append(time.time())
        @event.listens_for(sync_engine,"after_cursor_execute")
        def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            try:
                start_times = conn.info.get("query_start_time") or []
                start = start_times.pop(-1) if start_times else None
                if start:
                    total = time.time()-start
                    self._metrics["queries"] += 1
                    if total>self._slow_query_threshold:
                        logger.warning("Slow query (%.2fs): %s", total, statement.replace("\n"," ").strip())
            except Exception:
                logger.debug("Error in query timing handler", exc_info=True)

    @asynccontextmanager
    async def session_scope(self) -> AsyncGenerator[AsyncSession,None]:
        if self._closed:
            raise RuntimeError("RepositoryManager is closed")
        session = self.session_factory()
        try:
            self._metrics["transactions"] += 1
            if self._enable_set_timezone:
                try: await session.execute(text("SET TIME ZONE 'UTC'"))
                except Exception: pass
            yield session
            try:
                if session.in_transaction(): await session.commit()
            except Exception: raise
        except Exception as e:
            self._metrics["errors"] += 1
            self._metrics["last_error"] = str(e)
            try:
                if session.in_transaction(): await session.rollback()
            except Exception:
                logger.exception("Error during rollback", exc_info=True)
            raise
        finally:
            try: await session.close()
            except Exception:
                logger.exception("Error closing session", exc_info=True)

    async def execute_with_retry(self, func: Callable[...,Any], *args, max_attempts:int=3, base_delay:float=0.5, **kwargs):
        attempt = 0
        while True:
            try:
                attempt += 1
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt>=max_attempts or not _is_retryable_error(e):
                    self._metrics["errors"] += 1
                    self._metrics["last_error"] = str(e)
                    raise
                delay = min(base_delay*(2**(attempt-1)),5.0)
                logger.warning("Transient DB error (attempt %d/%d): %s — retry %.2fs", attempt,max_attempts,str(e),delay)
                await asyncio.sleep(delay)

    async def close(self) -> None:
        if self._closed: return
        self._closed = True
        if self._engine is not None and not self._provided_engine:
            try: await self._engine.dispose()
            except Exception: logger.exception("Error disposing engine", exc_info=True)
        self._engine = None
        self._session_factory = None

    def get_metrics(self) -> Dict[str,Any]:
        m = dict(self._metrics)
        m["uptime_seconds"] = time.time()-self._metrics["start_time"]
        return m
