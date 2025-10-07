"""
Production Worker: Asynchronous file processor.
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
from typing import List, Optional, Set, AsyncGenerator, Dict
from urllib.parse import quote_plus

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import select, literal

# Load environment variables
load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker")

# Application imports
from api.repositories import get_repository_manager
from api.agents.ingestion_agent import IngestionAgent
from api.models.file import File
from api.core.config import settings
from api.models.db_utils import get_engine
# Worker configuration
MAX_CONCURRENT_TASKS = int(os.getenv("WORKER_MAX_CONCURRENT_TASKS", "3"))
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
    """Manages worker state, repositories, and ingestion agent."""

    def __init__(self):
        self.repo_manager = None
        self.agent = None
        self.active_tasks: Set[asyncio.Task] = set()
        self.metrics = WorkerMetrics()
        self._shutdown_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize repositories and ingestion agent."""
        if self._initialized:
            return True

        logger.info("Initializing worker context...")
        try:
            engine =  get_engine()
            self.repo_manager = await get_repository_manager(engine=engine)
            self.agent = IngestionAgent()
            self._initialized = True
            logger.info("Worker context initialized successfully")
            return True
        except Exception as e:
            logger.critical(f"Failed to initialize WorkerContext: {e}", exc_info=True)
            return False

    async def shutdown(self):
        """Gracefully shutdown worker and cleanup resources."""
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()

        logger.info("Shutting down worker...")
        try:
            for task in list(self.active_tasks):
                if not task.done():
                    task.cancel()
            if self.active_tasks:
                await asyncio.wait(self.active_tasks, timeout=30.0)

            if self.repo_manager:
                try:
                    await self.repo_manager.close()
                except Exception as e:
                    logger.error(f"Error closing repository manager: {e}", exc_info=True)
                self.repo_manager = None

            self.active_tasks.clear()
            logger.info("Worker shutdown completed")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)


@retry(**NOTIFY_RETRY_CONFIG)
async def _notify_processing_status(file_id: int, status: str, error: str = None) -> bool:
    """Send file processing status to API with retries."""
    payload = {"file_id": file_id, "status": status}
    if error:
        payload["error"] = error
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_NOTIFY_URL}/api/v1/notifications/status", json=payload, timeout=10) as resp:
                resp.raise_for_status()
                return True
    except Exception as e:
        logger.warning(f"Failed to send status for file {file_id}: {e}")
        raise


async def process_file(context: WorkerContext, file_id: int):
    """Process a single file using the ingestion agent and update repository."""
    async with context._semaphore:
        try:
            logger.info(f"Processing file {file_id}")
            await context.agent.process_file(file_id)
            context.metrics.processed_count += 1
            await _notify_processing_status(file_id, "completed")
        except Exception as e:
            context.metrics.failed_count += 1
            error_msg = str(e)
            logger.error(f"Error processing file {file_id}: {error_msg}", exc_info=True)
            if context.repo_manager:
                try:
                    file_repo = await context.repo_manager.get_file_repo()
                    await file_repo.update_file_status(file_id, "failed", error_msg)
                except Exception as repo_err:
                    logger.error(f"Failed to update status for file {file_id}: {repo_err}", exc_info=True)
            await _notify_processing_status(file_id, "failed", error_msg)


async def fetch_pending_files(context: WorkerContext) -> List[Dict]:
    """Fetch pending files from the repository and mark them as processing."""
    try:
        file_repo = await context.repo_manager.get_file_repo()
        pending_files = await file_repo.get_pending_files(limit=BATCH_SIZE)
        return pending_files
    except Exception as e:
        logger.error(f"Error fetching pending files: {e}", exc_info=True)
        return []


async def handle_shutdown(sig: signal.Signals, context: WorkerContext):
    """Signal handler for graceful shutdown."""
    logger.info(f"Received signal {sig.name}, shutting down...")
    await context.shutdown()


async def worker_loop(context: WorkerContext):
    """Main worker loop: fetch pending files and process them."""
    logger.info("Starting worker loop...")
    base_delay, max_delay = 1.0, 60.0
    current_delay = base_delay

    while not context._shutdown_event.is_set():
        try:
            pending_files = await fetch_pending_files(context)
            if not pending_files:
                sleep_time = min(current_delay * random.uniform(0.5, 1.5), max_delay)
                await asyncio.sleep(sleep_time)
                current_delay = min(current_delay * 2, max_delay)
                continue

            current_delay = base_delay
            context.metrics.queue_size = len(pending_files)

            tasks = []
            for file_record in pending_files:
                file_id = file_record["id"]
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
            logger.error(f"Error in worker loop: {e}", exc_info=True)
            await asyncio.sleep(min(base_delay * random.uniform(1, 2), max_delay))


async def main():
    context = WorkerContext()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handle_shutdown(s, context)))
        except (NotImplementedError, RuntimeError):
            pass

    if not await context.initialize():
        logger.critical("Failed to initialize worker context")
        return

    try:
        await worker_loop(context)
    except asyncio.CancelledError:
        logger.info("Worker task cancelled")
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
