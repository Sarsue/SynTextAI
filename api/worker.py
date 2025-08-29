"""
Background worker for processing files asynchronously.
Handles ingestion, retry logic, database updates, and notifications.
"""

import asyncio
import logging
import os
import signal
import sys
import traceback
from typing import List

from api.repositories.repository_manager import get_repository_manager
from api.services.file_processing_service import FileProcessingService
from api.services.ingestion_agent import IngestionAgent
from api.websocket.websocket_manager import WebSocketManager  # ✅ standardized import

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

MAX_CONCURRENT_TASKS = 3
MAX_RETRIES = 3
BASE_BACKOFF = 2
API_NOTIFY_URL = os.getenv("API_NOTIFY_URL", "http://api:8000/api/files/notify")

# Global references for shutdown
stop_event = asyncio.Event()
active_tasks: List[asyncio.Task] = []
websocket_manager: WebSocketManager | None = None


# ------------------------------------------------------------------------------
# Core Processing
# ------------------------------------------------------------------------------

async def process_file(file_id: int, repo_manager, semaphore: asyncio.Semaphore):
    """Process a single file with retry logic and status updates."""
    async with semaphore:
        try:
            logger.info(f"Starting processing for file {file_id}")

            file_repo = repo_manager.file_repo
            await file_repo.update_file(file_id, status="processing")

            agent = IngestionAgent(repo_manager=repo_manager)
            await agent.process_file(file_id)

            await file_repo.update_file(file_id, status="completed")
            logger.info(f"File {file_id} processed successfully")

            if websocket_manager:
                try:
                    await websocket_manager.broadcast_json(
                        {"type": "file_completed", "file_id": file_id}
                    )
                except Exception:
                    logger.error(
                        f"WebSocket broadcast failed for file {file_id}",
                        exc_info=True,
                    )

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Error processing file {file_id}: {e}\n{tb}")
            await repo_manager.file_repo.update_file(
                file_id, status="failed", processing_error=str(e)[:1000]
            )
            if websocket_manager:
                try:
                    await websocket_manager.broadcast_json(
                        {
                            "type": "file_failed",
                            "file_id": file_id,
                            "error": str(e),
                        }
                    )
                except Exception:
                    logger.error(
                        f"WebSocket broadcast failed for file {file_id} (failure notice)",
                        exc_info=True,
                    )


async def worker_loop():
    """Main worker loop fetching and processing files continuously."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    repo_manager = get_repository_manager()

    try:
        while not stop_event.is_set():
            pending_files = await repo_manager.file_repo.get_pending_files(
                limit=MAX_CONCURRENT_TASKS
            )

            if not pending_files:
                await asyncio.sleep(5)
                continue

            for file_id in pending_files:
                task = asyncio.create_task(process_file(file_id, repo_manager, semaphore))
                active_tasks.append(task)

            # Clean finished tasks
            active_tasks[:] = [t for t in active_tasks if not t.done()]
    finally:
        await repo_manager.close()


# ------------------------------------------------------------------------------
# Shutdown Handling
# ------------------------------------------------------------------------------

def shutdown_handler():
    """Signal handler for graceful shutdown."""
    logger.info("Shutdown signal received. Stopping worker...")
    stop_event.set()


async def shutdown():
    """Gracefully cancel tasks and close resources."""
    logger.info("Waiting for active tasks to finish...")
    if active_tasks:
        await asyncio.gather(*active_tasks, return_exceptions=True)
    if websocket_manager:
        await websocket_manager.close()
    logger.info("Shutdown complete.")


# ------------------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------------------

async def main():
    global websocket_manager
    websocket_manager = WebSocketManager()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    try:
        await worker_loop()
    except asyncio.CancelledError:
        pass
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user.")
