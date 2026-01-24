#!/usr/bin/env python
"""
SynText AI File Processing Worker

This standalone worker script processes files asynchronously by:
1. Querying the database for files with 'uploaded' status
2. Processing files in parallel with controlled concurrency
3. Updating file statuses to track progress

Run this worker in a separate process from the API server
for scalable background processing.
"""

import asyncio
import json
import logging
import os
import sys
import signal
import time
import requests
from sqlalchemy import text
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from dotenv import load_dotenv
from pathlib import Path
from api.models.orm_models import File
from api.tasks import process_file_data
# Add the parent directory to sys.path to fix imports
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, base_dir)

# Load environment variables from .env file in the project root
env_path = os.path.join(base_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
    print(f"Loaded environment variables from {env_path}")
else:
    print(f"Warning: .env file not found at {env_path}")
    # Try to load from default location as fallback
    load_dotenv()

# Verify required database configuration
required_db_vars = [
    'DATABASE_NAME',
    'DATABASE_USER',
    'DATABASE_PASSWORD',
    'DATABASE_HOST',
    'DATABASE_PORT'
]

missing_vars = [var for var in required_db_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(
        f"Missing required database configuration: {', '.join(missing_vars)}"
    )

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('syntextai-worker')

# Maximum number of concurrent file processing tasks
# PRODUCTION: Set to 1 to prevent OOM with large PDFs (1000+ pages)
# Processing files sequentially ensures stable memory usage
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))

# Polling configuration - Simplified to fixed 30-second intervals
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))  # Check for files every N seconds

# API base URL for internal notifications (docker-compose sets this to http://syntextaiapp:3000)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3000").rstrip("/")


# Global semaphore for limiting concurrent file processing
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# Track running tasks to ensure graceful shutdown
running_tasks = []
shutdown_event = asyncio.Event()


# Global store instance to reuse across worker operations
_store = None


def _infer_user_gc_id_from_file_url(url: str) -> Optional[str]:
    try:
        if not url:
            return None
        parsed = urlparse(url)
        parts = [p for p in (parsed.path or "").split("/") if p]
        # Expected: /<bucket>/<user_gc_id>/...
        if len(parts) < 2:
            return None
        return parts[1] or None
    except Exception:
        return None


def get_repository_manager():
    """Get or create a RepositoryManager instance with proper database configuration"""
    global _store
    if _store is None:
        from api.models.async_db import get_database_url
        from api.repositories.repository_manager import RepositoryManager

        # Use centralized async database URL
        database_url = get_database_url()
        _store = RepositoryManager(database_url=database_url)
        logger.info("Created new RepositoryManager instance for worker")
    else:
        logger.debug("Reusing existing RepositoryManager instance")

    return _store

async def update_file_status(file_id: int, status: str, error: str = None) -> None:
    """Update file status in the database using async SQLAlchemy ORM"""
    try:
        from sqlalchemy.exc import SQLAlchemyError
        from api.models.orm_models import File

        store = get_repository_manager()

        async with store.file_repo.get_async_session() as session:
            try:
                file = await session.get(File, file_id)

                if not file:
                    logger.error(f"File with ID {file_id} not found")
                    return

                file.processing_status = status
                if error:
                    # Note: File model doesn't have error_message field
                    # Error details are logged but not stored in database
                    logger.warning(f"File {file_id} error: {error}")

                await session.commit()
                logger.info(f"Successfully updated file {file_id} status to {status}")

                try:
                    user_id = getattr(file, 'user_id', None)
                    if user_id:
                        await notify_client(
                            user_id=int(user_id),
                            event_type="file_status_update",
                            data={"file_id": int(file_id), "status": status},
                        )
                except Exception as notify_err:
                    logger.debug(f"Failed to notify client for file {file_id} status {status}: {notify_err}")

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Database error updating file {file_id} status: {str(e)}")
                raise

    except Exception as e:
        logger.exception(f"Error updating file {file_id} status: {str(e)}")
        raise


async def notify_client(user_id: int, event_type: str, data: Dict[str, Any]) -> None:
    """Notify the API to relay an event to the frontend over WebSocket.

    We route notifications by DB user_id. The API registers each WebSocket connection
    under both firebase uid AND db user id, so the worker doesn't need to parse URLs.
    """
    if not user_id:
        return

    url = f"{API_BASE_URL}/api/v1/internal/notify-client"
    payload = {
        "user_id": str(int(user_id)),
        "event_type": event_type,
        "data": data,
    }

    def _post():
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.warning(f"Failed to notify client for user {user_id}: {e}")

    await asyncio.to_thread(_post)


async def process_file(file_id: int, user_id: int, user_gc_id: str, filename: str, 
                     file_url: str, language: str = "English", 
                     comprehension_level: str = "Beginner") -> None:
    """Process a single file with concurrency control"""
    logger.info(f"[TRACE] Starting process_file for file_id: {file_id}, filename: {filename}")
    
    try:
        # Use a semaphore to limit concurrent processing
        async with semaphore:
            if shutdown_event.is_set():
                logger.info(f"[TRACE] Shutdown requested, skipping processing of file {file_id}")
                return
                
            # Determine if this is a YouTube URL
            is_youtube = any(s in file_url.lower() for s in ['youtube.com', 'youtu.be'])
            logger.info(f"[TRACE] File type detection - is_youtube: {is_youtube}")
                
            # Process the file with up to 3 attempts (exponential backoff)
            last_error: Optional[Exception] = None
            for attempt in range(3):
                try:
                    # Let process_file_data handle phase status updates; we only set final state
                    result = await process_file_data(
                        user_gc_id=user_gc_id,
                        file_id=file_id,
                        user_id=user_id,
                        filename=filename,
                        file_url=file_url,
                        is_youtube=is_youtube,
                        language=language,
                        comprehension_level=comprehension_level
                    )

                    final_status = result.get('final_status', 'processed' if result.get('success', False) else 'failed')
                    await update_file_status(file_id, final_status)
                    await notify_client(
                        user_id=user_id,
                        event_type="file_status_update",
                        data={"file_id": int(file_id), "status": final_status},
                    )
                    logger.info(f"Completed processing file {filename} (ID: {file_id}) with status {final_status}")
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    logger.error(f"Attempt {attempt+1} failed for file {file_id}: {e}", exc_info=True)
                    if attempt < 2:
                        # Backoff: 1s, 2s
                        await asyncio.sleep(2 ** attempt)
                        continue
                    # Final failure: mark failed and propagate
                    try:
                        await update_file_status(file_id, "failed", error=str(e))
                        await notify_client(
                            user_id=user_id,
                            event_type="file_status_update",
                            data={"file_id": int(file_id), "status": "failed"},
                        )
                    finally:
                        pass
                    raise
                
    except Exception as e:
        logger.error(f"[FATAL] Unhandled error in process_file for file {file_id}: {str(e)}", exc_info=True)
        # Make one final attempt to mark as failed
        try:
            await update_file_status(file_id, "failed")
            await notify_client(
                user_id=user_id,
                event_type="file_status_update",
                data={"file_id": int(file_id), "status": "failed"},
            )
        except Exception as final_err:
            logger.error(f"[FATAL] Failed final status update for file {file_id}: {str(final_err)}")
        raise


async def fetch_pending_files() -> List[Dict[str, Any]]:
    """Fetch files with 'uploaded' status from the database and mark them as processing"""
    try:
        from api.models.orm_models import File
        from sqlalchemy import select

        # Use the shared repository manager function
        store = get_repository_manager()

        # Use async transaction to atomically fetch and update files
        async with store.file_repo.get_async_session() as session:
            try:
                count_stmt = select(text("count(*)")).select_from(File).where(File.processing_status == 'uploaded')
                count_res = await session.execute(count_stmt)
                uploaded_count = int(count_res.scalar() or 0)
                if uploaded_count == 0:
                    latest_stmt = (
                        select(File.id, File.processing_status, File.file_name)
                        .order_by(File.created_at.desc())
                        .limit(1)
                    )
                    latest_res = await session.execute(latest_stmt)
                    latest_row = latest_res.first()
                    logger.info(f"No pending files found. uploaded_count=0 latest={latest_row}")
            except Exception:
                pass

            # Find files that need processing - simplified query without joinedload to avoid filtering issues
            stmt = (
                select(File)
                .where(File.processing_status == 'uploaded')
                .order_by(File.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(10)
            )

            result = await session.execute(stmt)
            files_to_process = result.scalars().all()

            # Update status to extracting to claim the files.
            # We avoid introducing a separate 'processing' status because the frontend
            # and API progress mapping expect the existing lifecycle states.
            for file in files_to_process:
                file.processing_status = 'extracting'

            # Commit the transaction to release the lock and persist status changes
            await session.commit()

            # Convert files to list of dictionaries
            pending_files = []
            for file in files_to_process:
                inferred_gc_id = None
                if file.file_url and "storage.googleapis.com" in (file.file_url or ""):
                    inferred_gc_id = _infer_user_gc_id_from_file_url(file.file_url)
                pending_files.append({
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "user_id": file.user_id,
                    "user_gc_id": inferred_gc_id or "",
                    "created_at": file.created_at
                })

            logger.info(f"Fetched {len(pending_files)} files for processing")
            return pending_files

    except Exception as e:
        logger.error(f"Error fetching pending files: {str(e)}")
        return []


async def worker_loop() -> None:
    """Main worker loop that polls for files and processes them with fixed 30-second intervals"""
    while not shutdown_event.is_set():
        try:
            # Fetch pending files
            pending_files = await fetch_pending_files()

            if pending_files:
                logger.info(f"Found {len(pending_files)} files to process")

                # Create tasks for each file
                tasks = []
                for file in pending_files:
                    # Create task for processing this file
                    task = asyncio.create_task(
                        process_file(
                            file_id=file["id"],
                            user_id=file["user_id"],
                            user_gc_id=file["user_gc_id"],
                            filename=file["file_name"],
                            file_url=file["file_url"],
                        )
                    )
                    tasks.append(task)
                    running_tasks.append(task)

                # Wait for all tasks to complete
                completed_tasks, _ = await asyncio.wait(
                    tasks,
                    return_when=asyncio.ALL_COMPLETED
                )

                # Clean up completed tasks
                for task in completed_tasks:
                    if task in running_tasks:
                        running_tasks.remove(task)
            else:
                logger.info(f"No pending files found. Next poll in {POLL_INTERVAL} seconds")

            # Wait before polling again with fixed interval
            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Error in worker loop: {str(e)}")
            # On error, still use the fixed poll interval before trying again
            await asyncio.sleep(POLL_INTERVAL)


def handle_shutdown(sig, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received shutdown signal {sig}")

    # Set the shutdown event to stop creating new tasks
    shutdown_event.set()

    # Note: we're not forcibly cancelling running tasks
    # They will continue processing but no new tasks will be started

    logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")


async def main():
    """Main entry point for the worker"""
    logger.info(f"Starting SynText AI Worker (Max Concurrent Tasks: {MAX_CONCURRENT_TASKS})")
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    # Preload models to avoid OOM crashes during first file processing
    logger.info("Preloading models...")
    
    # Note: Embedding model preload removed - using HTTP API (Voyage AI)
    # No local model to preload
    logger.info("✅ Using HTTP-based embeddings (Voyage AI) - no model preload needed")
    
    # Preload Whisper model
    try:
        from api.tasks import load_whisper_model_if_needed
        whisper = load_whisper_model_if_needed()
        if whisper:
            logger.info("✅ Whisper model preloaded")
        else:
            logger.warning("⚠️ Whisper model not available")
    except Exception as e:
        logger.error(f"❌ Failed to preload Whisper model: {e}")
        logger.warning("Worker will continue but YouTube transcription may fail")
    
    # Start the worker loop
    try:
        await worker_loop()
        
    except Exception as e:
        logger.exception(f"Fatal error in worker: {str(e)}")
        
    finally:
        # Wait for remaining tasks to complete on shutdown
        if running_tasks:
            logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")
            await asyncio.wait(running_tasks)

        # Clean up shared database resources
        logger.info("Cleaning up shared database resources...")
        from api.repositories.async_base_repository import cleanup_shared_db_resources
        await cleanup_shared_db_resources()

        logger.info("SynText AI Worker shutdown complete")


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
