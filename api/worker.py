"""
Background worker for processing files asynchronously.
Handles ingestion, database updates, and notifications.
"""

import aiohttp
import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional, Set
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from dotenv import load_dotenv

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
from api.repositories.repository_manager import RepositoryManager
from api.agents.ingestion_agent import IngestionAgent
from sqlalchemy.exc import SQLAlchemyError, OperationalError

# Database configuration
DATABASE_URL = (
    f"postgresql+asyncpg://{os.getenv('DATABASE_USER')}:{os.getenv('DATABASE_PASSWORD')}"
    f"@{os.getenv('DATABASE_HOST')}:{os.getenv('DATABASE_PORT')}/{os.getenv('DATABASE_NAME')}"
)

# Worker configuration
MAX_CONCURRENT_TASKS = int(os.getenv("WORKER_MAX_CONCURRENT_TASKS", "3"))
INITIAL_POLL_DELAY = int(os.getenv("WORKER_INITIAL_POLL_DELAY", "5"))
MAX_POLL_DELAY = int(os.getenv("WORKER_MAX_POLL_DELAY", "60"))
POLL_BACKOFF_FACTOR = float(os.getenv("WORKER_POLL_BACKOFF", "2.0"))
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
    """Manages worker state and resources."""

    def __init__(self):
        self.repo_manager: Optional[RepositoryManager] = None
        self.agent: Optional[IngestionAgent] = None
        self.active_tasks: Set[asyncio.Task] = set()
        self.metrics = WorkerMetrics()
        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self._db_initialized = False

    async def initialize(self) -> bool:
        """Initialize worker context and dependencies."""
        try:
            self.repo_manager = RepositoryManager(
                DATABASE_URL,
                echo=os.getenv("SQL_ECHO", "false").lower() == "true",
            )
            await self.repo_manager.initialize()

            self.agent = IngestionAgent(self.repo_manager)

            if await self.check_database_connection():
                self._db_initialized = True
                self.metrics.last_successful_connection = datetime.utcnow()
                logger.info("Worker context initialized successfully")
                return True
            return False
        except Exception as e:
            self.metrics.last_error = str(e)
            logger.critical(f"Failed to initialize worker context: {e}", exc_info=True)
            await self._safe_close_repository_manager()
            return False

    async def check_database_connection(self) -> bool:
        """Check if database is accessible using ORM."""
        if not self.repo_manager:
            logger.error("Repository manager not initialized")
            return False
        try:
            from sqlalchemy import select
            from sqlalchemy.sql.expression import literal

            async with self.repo_manager.session_scope() as session:
                result = await session.scalar(select(literal(1)))
                return result == 1
        except Exception as e:
            logger.error(f"Database connection check failed: {e}", exc_info=True)
            return False

    async def _safe_close_repository_manager(self) -> None:
        if self.repo_manager:
            try:
                await self.repo_manager.close()
                logger.info("Repository manager closed successfully")
            except Exception as e:
                logger.error(f"Error closing repository manager: {e}", exc_info=True)
            finally:
                self.repo_manager = None
                self._db_initialized = False

    @asynccontextmanager
    async def db_session(self, timeout: float = 10.0):
        """
        Async context manager for safely acquiring a DB session.

        Waits for the database to be initialized, or raises after `timeout` seconds.
        Usage:
            async with context.db_session() as session:
                # use session safely
        """
        if not self._db_initialized:
            # wait until db is initialized (or timeout)
            start = asyncio.get_event_loop().time()
            while not self._db_initialized:
                if asyncio.get_event_loop().time() - start > timeout:
                    raise RuntimeError("Database not initialized after waiting")
                await asyncio.sleep(0.1)

        async with self.repo_manager.session_scope() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def shutdown(self):
        """Clean up worker resources."""
        self._shutdown_event.set()

        for task in self.active_tasks:
            if not task.done():
                task.cancel()

        if self.active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wait(self.active_tasks, return_when=asyncio.ALL_COMPLETED)),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for tasks to complete")

        await self._safe_close_repository_manager()
        logger.info("Worker context shut down")


@retry(**NOTIFY_RETRY_CONFIG)
async def _notify_processing_status(file_id: int, status: str, error: str = None) -> bool:
    """Send processing status update to the API with retry logic."""
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


async def process_file(context: WorkerContext, file_id: int) -> None:
    """Process a single file."""
    try:
        async with context._semaphore:
            await _notify_processing_status(file_id, "processing")
            context.metrics.active_tasks += 1
            success = await context.agent.process_file(file_id)
            if success:
                context.metrics.processed_count += 1
                await _notify_processing_status(file_id, "completed")
            else:
                context.metrics.failed_count += 1
                error_msg = "Processing completed but result was not successful"
                await _notify_processing_status(file_id, "failed", error_msg)
    except Exception as e:
        context.metrics.failed_count += 1
        await _notify_processing_status(file_id, "failed", str(e))
        logger.error(f"File {file_id} processing failed: {e}", exc_info=True)
    finally:
        context.metrics.active_tasks = max(context.metrics.active_tasks - 1, 0)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((SQLAlchemyError, OperationalError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def fetch_pending_files(context: WorkerContext) -> List[int]:
    """Fetch a batch of pending files."""
    try:
        async with context.db_session() as session:
            file_repo = context.repo_manager.file_repo
            # Get pending files using get_multi with filter
            pending_files = await file_repo.get_multi(
                skip=0,
                limit=BATCH_SIZE,
                processing_status="pending"
            )
            
            if not pending_files:
                return []
                
            # Update status for each file
            file_ids = [f.id for f in pending_files]
            for file_id in file_ids:
                await file_repo.update(file_id, {"processing_status": "processing"})
            return file_ids
            
    except Exception as e:
        logger.error(f"Error in fetch_pending_files: {e}", exc_info=True)
        return []


async def worker_loop(context: WorkerContext) -> None:
    poll_delay = INITIAL_POLL_DELAY
    while not context._shutdown_event.is_set():
        pending_files = await fetch_pending_files(context)
        context.metrics.queue_size = len(pending_files)

        if not pending_files:
            await asyncio.sleep(poll_delay)
            poll_delay = min(poll_delay * POLL_BACKOFF_FACTOR, MAX_POLL_DELAY)
            continue

        poll_delay = INITIAL_POLL_DELAY
        for i in range(0, len(pending_files), MAX_CONCURRENT_TASKS):
            if context._shutdown_event.is_set():
                break
            batch = pending_files[i:i + MAX_CONCURRENT_TASKS]
            tasks = [asyncio.create_task(process_file(context, fid)) for fid in batch]
            context.active_tasks.update(tasks)
            done, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED, timeout=300)
            context.active_tasks.difference_update(done)
            for task in done:
                if task.exception():
                    logger.error(f"Task failed with exception: {task.exception()}", exc_info=True)
            for task in pending:
                task.cancel()
                await asyncio.wait([task], timeout=5)
            await asyncio.sleep(1)


async def shutdown(sig: signal.Signals, context: WorkerContext) -> None:
    logger.info(f"Received signal {sig.name}, shutting down...")
    await context.shutdown()


async def main() -> None:
    context = WorkerContext()
    if not await context.initialize():
        logger.critical("Failed to initialize worker context")
        return

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, context)))
        except NotImplementedError:
            # Windows does not support signal handlers
            pass

    logger.info("Starting worker loop...")
    await worker_loop(context)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
