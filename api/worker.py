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
from datetime import datetime
from typing import List, Set, Any, Dict, Optional
import aiohttp

# Application imports
from api.repositories.repository_manager import get_repository_manager
from api.agents.ingestion_agent import IngestionAgent

# Database configuration
database_config = {
    'dbname': os.getenv("DATABASE_NAME", "syntextai"),
    'user': os.getenv("DATABASE_USER", "postgres"),
    'password': os.getenv("DATABASE_PASSWORD", "postgres"),
    'host': os.getenv("DATABASE_HOST", "localhost"),
    'port': os.getenv("DATABASE_PORT", "5432"),
}

# Construct the database URL
DATABASE_URL = (
    f"postgresql+asyncpg://{database_config['user']}:{database_config['password']}"
    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
)

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

# Global state
stop_event = asyncio.Event()
active_tasks: List[asyncio.Task] = []

# Constants
MAX_CONCURRENT_TASKS = 3
MAX_RETRY_ATTEMPTS = 3
INITIAL_POLL_DELAY = 5  # Initial delay in seconds
MAX_POLL_DELAY = 60  # Maximum delay between polls in seconds
POLL_BACKOFF_FACTOR = 2  # How much to multiply delay by on each empty poll
BATCH_SIZE = 100  # Number of files to fetch in one batch
# Internal endpoint for notifications - same service
API_NOTIFY_URL = os.getenv("API_BASE_URL", "http://localhost:3000")

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("worker.log")
    ],
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Core Processing
# ------------------------------------------------------------------------------

async def process_file(file_id: int, repo_manager, semaphore: asyncio.Semaphore):
    """Process a single file with retry logic and status updates."""
    async with semaphore:
        attempt = 0
        last_error = None
        file_data = None
        
        try:
            # Get file data within a session
            async with repo_manager.session_scope() as session:
                file_repo = repo_manager.file_repo
                file_data = await file_repo.get_by_id(file_id)
                if not file_data:
                    raise ValueError(f"File with ID {file_id} not found")
                
                # Convert SQLAlchemy model to dict if needed
                if hasattr(file_data, '__dict__'):
                    file_data = {k: v for k, v in file_data.__dict__.items() if not k.startswith('_')}
                
                # Update file status to processing
                await file_repo.update_file_status(file_id, 'processing')
        except Exception as e:
            logger.error(f"Error initializing file processing for {file_id}: {str(e)}", exc_info=True)
            raise
        
        # Retry loop
        while attempt < MAX_RETRY_ATTEMPTS and not stop_event.is_set():
            try:
                attempt += 1
                logger.info(f"Processing file {file_id} (attempt {attempt}/{MAX_RETRY_ATTEMPTS})")
                
                # Process the file using the agent with timeout
                try:
                    # Initialize the ingestion agent
                    agent = IngestionAgent()
                    
                    result = await asyncio.wait_for(
                        agent.process({
                            "file_id": file_id,
                            "file_path": file_data.get("path"),
                            "source_type": file_data.get("file_type"),
                            "metadata": {
                                "title": file_data.get("name"),
                                "user_id": file_data.get("user_id"),
                                "organization_id": file_data.get("organization_id")
                            }
                        }),
                        timeout=300  # 5 minutes timeout
                    )
                    
                    # Update file status to completed
                    async with repo_manager.session_scope() as session:
                        await repo_manager.file_repo.update_file_status(file_id, 'completed')
                    
                    logger.info(f"File {file_id} processed successfully")
                    
                    # Notify success via WebSocket if available
                    await _notify_processing_status(file_id, "completed")
                    return
                    
                except asyncio.TimeoutError:
                    error_msg = f"Processing timed out after 5 minutes"
                    logger.error(f"{error_msg} for file {file_id}")
                    last_error = error_msg
                    async with repo_manager.session_scope() as session:
                        await repo_manager.file_repo.update_file_status(file_id, 'error', error_msg)
                
                except Exception as e:
                    last_error = str(e)
                    logger.error(
                        f"Error processing file {file_id} (attempt {attempt}): {last_error}",
                        exc_info=True
                    )
                
                # If we have more attempts left, wait before retrying
                if attempt < MAX_RETRY_ATTEMPTS and not stop_event.is_set():
                    backoff = min(BASE_BACKOFF ** attempt, 30)  # Cap backoff at 30 seconds
                    logger.info(f"Retrying in {backoff} seconds...")
                    await asyncio.sleep(backoff)
            
            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"Unexpected error in process_file for file {file_id}: {last_error}",
                    exc_info=True
                )
                break
        
        # If we get here, all attempts failed
        if stop_event.is_set():
            logger.info(f"Processing cancelled for file {file_id} (shutdown in progress)")
            return
            
        # Update file status to failed with the last error
        error_msg = f"Failed after {attempt} attempts: {last_error}" if last_error else "Unknown error"
        logger.error(f"Processing failed for file {file_id}: {error_msg}")
        
        try:
            async with repo_manager.session_scope() as session:
                await repo_manager.file_repo.update_file_status(
                    file_id, 
                    'failed', 
                    error_msg[:1000]  # Truncate long error messages
                )
            
            # Notify failure via WebSocket if available
            await _notify_processing_status(file_id, "failed", error_msg)
            
        except Exception as update_error:
            logger.error(
                f"Failed to update file status for {file_id}: {str(update_error)}",
                exc_info=True
            )


async def _notify_processing_status(file_id: int, status: str, error: str = None):
    """Helper function to send file processing status updates via HTTP POST."""
    if not API_NOTIFY_URL:
        logger.warning("API_NOTIFY_URL not configured, skipping notification")
        return
        
    try:
        import aiohttp
        
        payload = {
            "file_id": file_id,
            "status": status,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if error:
            payload["error"] = error
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_NOTIFY_URL}/internal/notify-client",
                json={
                    "user_id": None,  # Will be set by the API based on file ownership
                    "event_type": "file_processed",
                    "data": payload
                },
                timeout=10
            ) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to send notification for file {file_id}: "
                        f"HTTP {response.status}"
                    )
    except Exception as e:
        logger.error(
            f"Failed to send notification for file {file_id}: {str(e)}",
            exc_info=True
        )


async def worker_loop():
    """Main worker loop fetching and processing files continuously."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
    
    # Initialize repository manager with database URL
    repo_manager = get_repository_manager(DATABASE_URL)
    
    # Track active tasks and their status
    active_tasks: Dict[int, asyncio.Task] = {}
    
    try:
        while not stop_event.is_set():
            try:
                # Get pending files that aren't already being processed
                current_delay = INITIAL_POLL_DELAY
                while not stop_event.is_set():
                    async with repo_manager.session_scope() as session:
                        pending_files = await repo_manager.file_repo.get_pending_files(
                            limit=BATCH_SIZE,
                            exclude_ids=list(active_tasks.keys())  # Skip files already being processed
                        )
                    
                    if pending_files:
                        break
                        
                    # No files to process, implement exponential backoff
                    logger.debug(f"No pending files found. Waiting {current_delay} seconds...")
                    await asyncio.sleep(current_delay)
                    current_delay = min(current_delay * POLL_BACKOFF_FACTOR, MAX_POLL_DELAY)
                
                # Process files up to the concurrency limit
                processed_count = 0
                for file_data in pending_files:
                    if stop_event.is_set() or processed_count >= MAX_CONCURRENT_TASKS:
                        break
                        
                    file_id = file_data['id']
                    
                    # Skip if already being processed
                    if file_id in active_tasks and not active_tasks[file_id].done():
                        continue
                    
                    # Create and track the task
                    task = asyncio.create_task(
                        process_file(file_id, repo_manager, semaphore)
                    )
                    active_tasks[file_id] = task
                    processed_count += 1
                    
                    # Add callback to clean up when task is done
                    def cleanup_task(fut, fid=file_id):
                        if fid in active_tasks:
                            del active_tasks[fid]
                    task.add_done_callback(cleanup_task)
                
                # Clean up completed tasks
                active_tasks = {k: v for k, v in active_tasks.items() if not v.done()}
                
                # If we couldn't process any files (all pending are already being processed),
                # wait a bit before checking again
                if len(active_tasks) >= MAX_CONCURRENT_TASKS:
                    await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                raise
                
            except Exception as e:
                logger.error(
                    f"Unexpected error in worker loop: {str(e)}",
                    exc_info=True
                )
                await asyncio.sleep(RETRY_DELAY)
                
    except asyncio.CancelledError:
        logger.info("Worker loop cancelled")
        raise
        
    except Exception as e:
        logger.critical(
            f"Critical error in worker loop: {str(e)}",
            exc_info=True
        )
        raise
        
    finally:
        # Cancel any remaining tasks
        for task in active_tasks.values():
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete or be cancelled
        if active_tasks:
            await asyncio.wait(
                list(active_tasks.values()),
                timeout=10.0,
                return_when=asyncio.ALL_COMPLETED
            )
        
        # Close the repository manager
        try:
            await repo_manager.close()
        except Exception as e:
            logger.error(f"Error closing repository manager: {str(e)}", exc_info=True)


# ------------------------------------------------------------------------------
# Shutdown Handling
# ------------------------------------------------------------------------------

def shutdown_handler():
    """Signal handler for graceful shutdown."""
    logger.info("Shutdown signal received. Stopping worker...")
    stop_event.set()


async def shutdown():
    """Gracefully cancel tasks and close resources."""
    logger.info("Initiating graceful shutdown...")
    
    # Cancel all active tasks
    if active_tasks:
        logger.info(f"Cancelling {len(active_tasks)} active tasks...")
        for task in active_tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete or be cancelled
        try:
            await asyncio.wait(active_tasks, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for tasks to complete")
    
    # Clean up any remaining resources
    logger.info("Worker shutdown complete")
    
    logger.info("Shutdown complete.")


# ------------------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------------------

async def main():
    # Set up signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            # Windows doesn't support signal handlers like this
            logger.warning(f"Could not add signal handler for {sig}")
    
    try:
        logger.info("Starting worker...")
        await worker_loop()
    except asyncio.CancelledError:
        logger.info("Worker was cancelled")
    except Exception as e:
        logger.critical(f"Unexpected error in worker: {str(e)}", exc_info=True)
        raise
    finally:
        logger.info("Shutting down worker...")
        await shutdown()
        logger.info("Worker stopped")


if __name__ == "__main__":
    try:
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler("worker.log")
            ]
        )
        
        # Set log level for specific noisy loggers
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
        
        logger.info("Starting SynTextAI worker...")
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Worker process terminated")
