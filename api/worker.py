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
import aiohttp
from sqlalchemy import text
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from pathlib import Path
from api.models.orm_models import File

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
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

# Polling configuration
INITIAL_POLL_INTERVAL = 10  # Start with 10 seconds
MAX_POLL_INTERVAL = 300     # 5 minutes maximum
POLL_BACKOFF_FACTOR = 1.5   # 1.5x backoff factor

# API configuration for notifications
API_NOTIFY_URL = os.getenv("API_BASE_URL", "http://localhost:3000")
async def send_notification_to_api(user_gc_id: str, event_type: str, data: dict):
    """Send a notification to the main API via HTTP POST"""
    payload = {
        "user_id": user_gc_id,
        "event_type": event_type,
        "data": data
    }
    try:
        async with aiohttp.ClientSession() as session:
            # Use the configured API URL instead of hardcoded localhost
            base_url = API_NOTIFY_URL.rstrip('/')
            url = f"{base_url}/api/v1/internal/notify-client"
            logger.debug(f"Sending notification to {url} for user {user_gc_id}")

            async with session.post(url, json=payload, timeout=10) as response:
                if response.status == 202:  # Accepted
                    logger.info(f"Successfully sent notification for {event_type} to user {user_gc_id} via API")
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to send notification via API. Status: {response.status}, Response: {response_text}")
    except asyncio.TimeoutError:
        logger.error(f"Timeout sending notification to API for user {user_gc_id}")
    except aiohttp.ClientConnectionError as e:
        logger.error(f"Connection error sending notification to API for user {user_gc_id}: {e}")
    except Exception as e:
        logger.error(f"Exception while sending notification to API for user {user_gc_id}: {e}")

# Global semaphore for limiting concurrent file processing
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# Track the current poll interval
current_poll_interval = INITIAL_POLL_INTERVAL

# Track running tasks to ensure graceful shutdown
running_tasks = []
shutdown_event = asyncio.Event()


# Global store instance to reuse across worker operations
_store = None


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
                    file.error_message = error

                await session.commit()
                logger.info(f"Successfully updated file {file_id} status to {status}")

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"Database error updating file {file_id} status: {str(e)}")
                raise
                
    except Exception as e:
        logger.exception(f"Error updating file {file_id} status: {str(e)}")
        raise

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
                
            # Update status to PROCESSING and notify frontend
            await update_file_status(file_id, "processing")
            await send_notification_to_api(user_gc_id, 'file_status_update', {
                'file_id': file_id,
                'status': 'processing',
                'progress': 10,
            })

            try:
                # Process the file
                from api.tasks import process_file_data
                await process_file_data(
                    user_gc_id=user_gc_id,
                    user_id=user_id,  # Pass as int, not string
                    file_id=file_id,  # Pass as int, not string
                    filename=filename,
                    file_url=file_url,
                    is_youtube=is_youtube,
                    language=language,
                    comprehension_level=comprehension_level
                )

                # Update status to PROCESSED and notify frontend
                await update_file_status(file_id, "processed")
                await send_notification_to_api(user_gc_id, 'file_status_update', {
                    'file_id': file_id,
                    'status': 'processed',
                    'progress': 100,
                })
                logger.info(f"Successfully completed processing file {filename} (ID: {file_id})")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Error processing file {file_id}: {error_msg}", exc_info=True)
                # Update status to FAILED and notify frontend
                await update_file_status(file_id, "failed", error=error_msg)
                await send_notification_to_api(user_gc_id, 'file_status_update', {
                    'file_id': file_id,
                    'status': 'failed',
                    'error': error_msg,
                })
                # Re-raise the original error to allow retry logic to work
                raise
                
    except Exception as e:
        logger.error(f"[FATAL] Unhandled error in process_file for file {file_id}: {str(e)}", exc_info=True)
        # Make one final attempt to mark as failed
        try:
            await update_file_status(file_id, "failed")
        except Exception as final_err:
            logger.error(f"[FATAL] Failed final status update for file {file_id}: {str(final_err)}")
        raise


async def fetch_pending_files() -> List[Dict[str, Any]]:
    """Fetch files with 'uploaded' status from the database and mark them as processing"""
    try:
        from api.models.orm_models import File
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        # Use the shared repository manager function
        store = get_repository_manager()

        # Use async transaction to atomically fetch and update files
        async with store.file_repo.get_async_session() as session:
            # Find files that need processing with row locking using async syntax
            stmt = (
                select(File)
                .options(joinedload(File.user, innerjoin=True))
                .where(File.processing_status == 'uploaded')
                .order_by(File.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(10)
            )

            result = await session.execute(stmt)
            files_to_process = result.scalars().all()

            # Update status to processing
            for file in files_to_process:
                file.processing_status = 'processing'

            # Commit the transaction to release the lock
            await session.commit()

            # Convert files to list of dictionaries
            pending_files = []
            for file in files_to_process:
                # Try to get user_gc_id from file URL if available, otherwise use empty string
                # GCS URLs are structured as: https://storage.googleapis.com/bucket/user_gc_id/filename
                user_gc_id = ''
                if file.file_url and "storage.googleapis.com" in file.file_url:
                    try:
                        # Extract user_gc_id from GCS URL path
                        path_parts = file.file_url.split('/')
                        if len(path_parts) >= 4:
                            user_gc_id = path_parts[-2]  # Second to last part should be user_gc_id
                    except Exception as e:
                        logger.warning(f"Could not extract user_gc_id from file URL {file.file_url}: {e}")

                pending_files.append({
                    "id": file.id,
                    "file_name": file.file_name,
                    "file_url": file.file_url,
                    "user_id": file.user_id,
                    "user_gc_id": user_gc_id,
                    "created_at": file.created_at
                })

            logger.info(f"Fetched {len(pending_files)} files for processing")
            return pending_files

    except Exception as e:
        logger.error(f"Error fetching pending files: {str(e)}")
        return []


async def worker_loop() -> None:
    """Main worker loop that polls for files and processes them with exponential backoff"""
    global current_poll_interval

    while not shutdown_event.is_set():
        try:
            # Fetch pending files
            pending_files = await fetch_pending_files()

            if pending_files:
                logger.info(f"Found {len(pending_files)} files to process")

                # Reset poll interval since we found work
                if current_poll_interval > INITIAL_POLL_INTERVAL:
                    logger.info(f"Resetting poll interval from {current_poll_interval}s to {INITIAL_POLL_INTERVAL}s")
                    current_poll_interval = INITIAL_POLL_INTERVAL

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
                logger.info(f"No pending files found. Next poll in {current_poll_interval:.1f} seconds")
                
                # Calculate next poll interval with exponential backoff
                next_interval = min(
                    current_poll_interval * POLL_BACKOFF_FACTOR,
                    MAX_POLL_INTERVAL
                )
                
                # Only log when the interval changes
                if next_interval > current_poll_interval:
                    logger.info(f"Increasing poll interval to {next_interval:.1f} seconds")
                
                current_poll_interval = next_interval
            
            # Wait before polling again with the current interval
            await asyncio.sleep(current_poll_interval)
            
        except Exception as e:
            logger.exception(f"Error in worker loop: {str(e)}")
            # On error, use the current poll interval before trying again
            await asyncio.sleep(current_poll_interval)


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
            
        logger.info("SynText AI Worker shutdown complete")


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
