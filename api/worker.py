"""
Background worker for processing files asynchronously.
Handles ingestion, database updates, and notifications.
"""

import aiohttp
import asyncio
import json
import logging
import os
import random
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional, Set, AsyncGenerator, Any, Dict
from urllib.parse import quote_plus

# Third-party imports
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from dotenv import load_dotenv
import os
import ssl
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError, OperationalError, InterfaceError
from sqlalchemy.sql import select, literal
from api.models.db_utils import create_ssl_context

# Load environment variables
load_dotenv()

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("worker")

# Application imports
from api.repositories.base_repository_manager import RepositoryManager
from api.agents.ingestion_agent import IngestionAgent
from api.models.orm_models import File
from api.core.config import settings

# Worker configuration
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "300"))  # 5 minutes
DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
DB_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

# Limit concurrent tasks to prevent pool exhaustion
MAX_CONCURRENT_TASKS = min(
    int(os.getenv("WORKER_MAX_CONCURRENT_TASKS", "3")),
    DB_POOL_SIZE + DB_MAX_OVERFLOW - 2
)
BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "100"))
API_NOTIFY_URL = os.getenv("API_BASE_URL", "http://localhost:3000")

NOTIFY_RETRY_CONFIG = {
    "stop": stop_after_attempt(3),
    "wait": wait_exponential(multiplier=1, min=1, max=10),
    "retry": retry_if_exception_type((asyncio.TimeoutError, ConnectionError)),
    "before_sleep": before_sleep_log(logger, logging.WARNING),
}


@dataclass
class WorkerMetrics:
    processed_count: int = 0
    failed_count: int = 0
    active_tasks: int = 0
    last_successful_connection: Optional[datetime] = None
    last_error: Optional[str] = None
    queue_size: int = 0


class WorkerContext:
    """Manages worker state and resources with connection pooling."""

    def __init__(self):
        self.repo_manager: Optional[RepositoryManager] = None
        self.agent: Optional[IngestionAgent] = None
        self.active_tasks: Set[asyncio.Task] = set()
        self.metrics = WorkerMetrics()
        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self._db_initialized = False
        self._engine = None
        self._session_factory = None

    async def initialize(self) -> bool:
        """Initialize the worker context with database connection and other resources."""
        if self._db_initialized:
            return True

        logger.info("Initializing worker context...")
        db_info = f"{settings.DATABASE_USER}@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
        logger.info(f"Connecting to database: {db_info}")

        max_retries = 3
        retry_delay = 2

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Attempt {attempt}/{max_retries} to create database engine...")

                # Build database URL from components with proper escaping
                db_url = (
                    f"postgresql+asyncpg://{settings.DATABASE_USER}:{quote_plus(settings.DATABASE_PASSWORD or '')}"
                    f"@{settings.DATABASE_HOST}:{settings.DATABASE_PORT}/{settings.DATABASE_NAME}"
                )

                # Configure SSL context
                ssl_context = None
                db_ssl = getattr(settings, "DB_SSL", True)
                
                if db_ssl:
                    try:
                        ssl_context = create_ssl_context()
                        logger.info("SSL context created successfully")
                        
                        # Verify SSL certificate exists if SSL is required
                        if getattr(settings, "DB_SSL_VERIFY", True) and getattr(settings, "DB_SSL_ROOT_CERT", None):
                            if not os.path.exists(settings.DB_SSL_ROOT_CERT):
                                logger.warning(f"SSL certificate not found at {settings.DB_SSL_ROOT_CERT}")
                    except Exception as e:
                        logger.error(f"Error creating SSL context: {e}", exc_info=True)
                        if getattr(settings, "DB_SSL_VERIFY", True):
                            raise
                        logger.warning("Proceeding without SSL verification")
                
                # Configure engine with same settings as main app
                engine_options = {
                    "echo": getattr(settings, "SQL_ECHO", False),
                    "future": True,
                    "pool_size": DB_POOL_SIZE,
                    "max_overflow": DB_MAX_OVERFLOW,
                    "pool_recycle": 1800,  # Recycle connections after 30 minutes
                    "pool_pre_ping": True,  # Enable connection health checks
                    "pool_timeout": 30,     # Wait up to 30 seconds for a connection
                    "connect_args": {
                        "ssl": ssl_context,
                        "command_timeout": 60,  # 60 second command timeout
                        "server_settings": {
                            "application_name": "syntextai-worker",
                            "statement_timeout": "30000"  # 30 second statement timeout
                        }
                    },
                    "execution_options": {"compiled_cache": {}},
                }

                logger.info(f"Creating database engine with options: { {k: v for k, v in engine_options.items() if k != 'connect_args'} }")
                self._engine = create_async_engine(db_url, **engine_options)

                # Test connection with proper async/await
                async with self._engine.connect() as conn:
                    # Option 1: Using execute + scalar
                    result = await conn.execute(select(1))
                    value = result.scalar()
                    # Alternative: Could also use await conn.scalar(select(1))
                    
                    if value != 1:
                        raise ValueError(f"Unexpected test query result: {value}")
                    
                    logger.info("Successfully connected to database")

                # Create async session factory
                self._session_factory = async_sessionmaker(
                    bind=self._engine,
                    expire_on_commit=False,
                    class_=AsyncSession
                )

                # Initialize repository manager and agent
                self.repo_manager = RepositoryManager(
                    session_factory=self._session_factory,
                    engine=self._engine
                )
                self.agent = IngestionAgent(self.repo_manager)

                self._db_initialized = True
                logger.info("Worker context initialized successfully")
                return True

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed: {e}", exc_info=True)
                await self._safe_close_engine()
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.critical(f"Failed to initialize database after {max_retries} attempts")
                    return False

        return False


    async def check_database_connection(self) -> bool:
        if not self.repo_manager:
            logger.error("Repository manager not initialized")
            return False
        try:
            async with self.db_session() as session:
                result = await session.scalar(select(literal(1)))
                return result == 1
        except Exception as e:
            logger.error(f"Database connection check failed: {e}", exc_info=True)
            return False

    async def _safe_close_engine(self) -> None:
        if self._engine:
            engine = self._engine
            self._engine = None
            self._db_initialized = False
            try:
                await engine.dispose()
                logger.info("Database engine disposed successfully")
            except Exception as e:
                logger.error(f"Error closing database engine: {e}", exc_info=True)
            self._session_factory = None

    async def _safe_close_repository_manager(self) -> None:
        if self.repo_manager is not None:
            try:
                if hasattr(self.repo_manager, "close") and callable(self.repo_manager.close):
                    try:
                        await self.repo_manager.close()
                    except Exception as e:
                        logger.error(f"Error in repo_manager.close(): {e}", exc_info=True)
            finally:
                self.repo_manager = None
                self._db_initialized = False

    @asynccontextmanager
    async def db_session(self, begin_transaction: bool = True) -> AsyncGenerator[AsyncSession, None]:
        """Create an async database session with optional transaction management.
        
        Args:
            begin_transaction: If True, wraps the session in a transaction.
            
        Yields:
            AsyncSession: An async database session.
            
        Raises:
            RuntimeError: If the database is not initialized.
        """
        if not self._db_initialized or not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        session = self._session_factory()
        try:
            if begin_transaction:
                async with session.begin():
                    yield session
            else:
                yield session
        except Exception as e:
            logger.error(f"Error in database session: {e}", exc_info=True)
            await session.rollback()
            raise
        finally:
            try:
                await session.close()
            except Exception as e:
                logger.error(f"Error closing database session: {e}", exc_info=True)
                raise

    async def shutdown(self):
        if self._shutdown_event.is_set():
            return
        logger.info("Initiating worker shutdown sequence...")
        self._shutdown_event.set()

        try:
            for task in list(self.active_tasks):
                if not task.done():
                    task.cancel()
            if self.active_tasks:
                await asyncio.wait(self.active_tasks, timeout=30.0)

            await self._safe_close_repository_manager()
            await self._safe_close_engine()
            self.active_tasks.clear()
            self._session_factory = None
            logger.info("Worker shutdown completed successfully")

        except Exception as e:
            logger.critical(f"Error during shutdown: {e}", exc_info=True)
        finally:
            self.active_tasks.clear()
            self._db_initialized = False


@retry(**NOTIFY_RETRY_CONFIG)
async def _notify_processing_status(file_id: int, status: str, error: str = None) -> bool:
    payload = {"file_id": file_id, "status": status}
    if error:
        payload["error"] = error
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_NOTIFY_URL}/api/v1/notifications/status", json=payload, timeout=10) as resp:
                resp.raise_for_status()
                return True
    except Exception as e:
        logger.warning(f"Failed to send status update for file {file_id}: {e}")
        raise


async def process_file(context: WorkerContext, file_id: int):
    try:
        async with context._semaphore:
            logger.info(f"Processing file {file_id}")
            try:
                await context.agent.process_file(file_id)
                logger.info(f"Successfully processed file {file_id}")
                context.metrics.processed_count += 1
                asyncio.create_task(_notify_processing_status(file_id, "completed"))
            except Exception as e:
                error_msg = f"Error in agent processing for file {file_id}: {e}"
                logger.error(error_msg, exc_info=True)
                context.metrics.failed_count += 1
                asyncio.create_task(_notify_processing_status(file_id, "failed", error_msg))
                raise
    except asyncio.CancelledError:
        logger.info(f"Processing of file {file_id} was cancelled")
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing file {file_id}: {e}", exc_info=True)
        raise


async def fetch_pending_files(context: WorkerContext):
    """Fetch a batch of pending files from the database."""
    async with context.db_session() as session:
        try:
            # Use with_for_update to lock rows and skip already locked ones
            stmt = (
                select(File)
                .where(File.processing_status == "pending")
                .order_by(File.created_at.asc())
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            
            # Execute the query and get results
            result = await session.execute(stmt)
            files = result.scalars().all()
            
            if not files:
                return []

            # Prepare file records and IDs for update
            file_records = []
            file_ids = [file.id for file in files]
            
            for file in files:
                file_records.append({
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "file_type": file.file_type,
                    "processing_status": file.processing_status,
                    "user_id": file.user_id,
                    "created_at": file.created_at.isoformat() if file.created_at else None
                })

            # Update status to 'processing' for the fetched files
            if file_ids:
                update_stmt = (
                    update(File)
                    .where(File.id.in_(file_ids))
                    .values(processing_status="processing")
                    .execution_options(synchronize_session='fetch')
                )
                await session.execute(update_stmt)
                await session.commit()
                
            return file_records
            
        except Exception as e:
            logger.error(f"Error in fetch_pending_files: {e}", exc_info=True)
            await session.rollback()
            return []


async def handle_shutdown(sig: signal.Signals, context: WorkerContext) -> None:
    logger.info(f"Received signal {sig.name}, shutting down...")
    await context.shutdown()


async def worker_loop(context: WorkerContext):
    logger.info("Starting worker loop...")
    base_delay, max_delay = 1.0, 60.0
    current_delay = base_delay
    consecutive_empty, consecutive_errors = 0, 0

    while not context._shutdown_event.is_set():
        try:
            pending_files = await fetch_pending_files(context)
            if not pending_files:
                consecutive_empty += 1
                consecutive_errors = 0
                if consecutive_empty > 1:
                    current_delay = min(base_delay * (2 ** (consecutive_empty - 1)), max_delay)
                sleep_time = min(current_delay * random.uniform(0.5, 1.5), max_delay)
                await asyncio.sleep(sleep_time)
                continue

            current_delay = base_delay
            consecutive_empty = consecutive_errors = 0
            context.metrics.queue_size = len(pending_files)

            tasks = []
            for file_record in pending_files:
                file_id = file_record["id"]
                logger.info(f"Queueing file {file_id} for processing")
                context.metrics.active_tasks += 1
                task = asyncio.create_task(process_file(context, file_id))
                task.add_done_callback(lambda t: context.metrics.__setattr__("active_tasks", max(0, context.metrics.active_tasks - 1)))
                context.active_tasks.add(task)
                tasks.append(task)

            if tasks:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=1.0)

        except asyncio.CancelledError:
            logger.info("Worker loop cancelled, shutting down...")
            raise
        except Exception as e:
            consecutive_errors += 1
            consecutive_empty = 0
            backoff = min(base_delay * (2 ** min(consecutive_errors - 1, 10)), max_delay)
            sleep_time = min(backoff * random.uniform(0.5, 1.5), max_delay)
            logger.error(f"Error in worker loop, retrying in {sleep_time:.1f}s: {e}", exc_info=True)
            await asyncio.sleep(sleep_time)


async def main() -> None:
    context = WorkerContext()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_shutdown(s, context)))
        except (NotImplementedError, RuntimeError):
            pass
    try:
        if not await context.initialize():
            logger.critical("Failed to initialize worker context")
            return
        await worker_loop(context)
    except asyncio.CancelledError:
        logger.info("Worker task was cancelled")
    except Exception as e:
        logger.critical(f"Unexpected error in worker: {e}", exc_info=True)
    finally:
        await context.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
