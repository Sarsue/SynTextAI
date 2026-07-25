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
import uuid
from dotenv import load_dotenv
from pathlib import Path
from api.models.orm_models import AgentRun
from api.workflows.tasks import process_file_data
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


async def update_agent_run(
    run_id: uuid.UUID,
    *,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    last_error: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    locked_by: Optional[str] = None,
    locked_at: Optional[datetime] = None,
    lease_expires_at: Optional[datetime] = None,
) -> None:
    store = get_repository_manager()
    async with store.agent_run_repo.get_async_session() as session:
        run = await session.get(AgentRun, run_id)
        if not run:
            return
        run.status = status
        if result is not None:
            run.result = result
        if last_error is not None:
            run.last_error = last_error
        if started_at is not None:
            run.started_at = started_at
        if finished_at is not None:
            run.finished_at = finished_at
        if locked_by is not None:
            run.locked_by = locked_by
        if locked_at is not None:
            run.locked_at = locked_at
        if lease_expires_at is not None:
            run.lease_expires_at = lease_expires_at
        run.updated_at = datetime.utcnow()
        await session.commit()


async def process_agent_run(run_id: uuid.UUID) -> None:
    store = get_repository_manager()

    async with semaphore:
        if shutdown_event.is_set():
            return

        async with store.agent_run_repo.get_async_session() as session:
            run = await session.get(AgentRun, run_id)
            if not run:
                return
            payload = run.payload or {}
            run_type = run.run_type

        if run_type == "ingest_file":
            file_id = payload.get("file_id")
            user_id = payload.get("user_id")
            filename = payload.get("filename")
            file_url = payload.get("file_url")
            is_youtube = bool(payload.get("is_youtube"))
            language = payload.get("language") or "English"
            comprehension_level = payload.get("comprehension_level") or "Beginner"

            if not file_id or not user_id or not filename or not file_url:
                await update_agent_run(
                    run_id,
                    status="failed",
                    last_error="Missing required payload fields for ingest_file",
                    finished_at=datetime.utcnow(),
                )
                return

            inferred_gc_id = payload.get("user_gc_id") or _infer_user_gc_id_from_file_url(str(file_url)) or ""

            try:
                result = await process_file_data(
                    user_gc_id=str(inferred_gc_id),
                    file_id=int(file_id),
                    user_id=int(user_id),
                    filename=str(filename),
                    file_url=str(file_url),
                    is_youtube=is_youtube,
                    language=str(language),
                    comprehension_level=str(comprehension_level),
                )

                final_status = result.get(
                    "final_status",
                    "processed" if result.get("success", False) else "failed",
                )
                await update_file_status(int(file_id), final_status)

                await update_agent_run(
                    run_id,
                    status="succeeded" if result.get("success", False) else "failed",
                    result=result,
                    finished_at=datetime.utcnow(),
                )
                return
            except Exception as e:
                try:
                    await update_file_status(int(file_id), "failed", error=str(e))
                except Exception:
                    pass

                await update_agent_run(
                    run_id,
                    status="failed",
                    last_error=str(e)[:2000],
                    finished_at=datetime.utcnow(),
                )
                raise

        if run_type == "answer_query":
            from api.workflows.tasks import run_query_pipeline

            user_id = payload.get("user_id")
            history_id = payload.get("history_id")
            message = payload.get("message")
            language = payload.get("language") or "English"
            comprehension_level = payload.get("comprehension_level") or "beginner"
            workspace_id = payload.get("workspace_id")
            file_id = payload.get("file_id")

            if not user_id or not history_id or not message:
                await update_agent_run(
                    run_id,
                    status="failed",
                    last_error="Missing required payload fields for answer_query",
                    finished_at=datetime.utcnow(),
                )
                return

            try:
                formatted_history = await store.chat_repo.format_user_chat_history(int(history_id), int(user_id))
                result = await run_query_pipeline(
                    user_id=int(user_id),
                    message=str(message),
                    language=str(language),
                    comprehension_level=str(comprehension_level),
                    formatted_history=formatted_history,
                    workspace_id=int(workspace_id) if workspace_id is not None else None,
                    file_id=int(file_id) if file_id is not None else None,
                )
                response = result.get("response")
                if response:
                    await store.chat_repo.add_message(
                        content=str(response),
                        sender="bot",
                        user_id=int(user_id),
                        chat_history_id=int(history_id),
                    )
                    await notify_client(
                        user_id=int(user_id),
                        event_type="message_received",
                        data={
                            "status": "success",
                            "history_id": int(history_id),
                            "message": str(response),
                        },
                    )

                await update_agent_run(
                    run_id,
                    status="succeeded",
                    result=result,
                    finished_at=datetime.utcnow(),
                )
                return
            except Exception as e:
                await notify_client(
                    user_id=int(user_id),
                    event_type="message_received",
                    data={"status": "error", "error": str(e)},
                )
                await update_agent_run(
                    run_id,
                    status="failed",
                    last_error=str(e)[:2000],
                    finished_at=datetime.utcnow(),
                )
                raise

        await update_agent_run(
            run_id,
            status="failed",
            last_error=f"Unsupported run_type: {run_type}",
            finished_at=datetime.utcnow(),
        )
        return


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


async def fetch_pending_runs(limit: int = 10) -> List[uuid.UUID]:
    try:
        from sqlalchemy import select, or_

        store = get_repository_manager()
        worker_id = os.getenv("WORKER_ID") or str(os.getpid())

        async with store.agent_run_repo.get_async_session() as session:
            now = datetime.utcnow()
            stmt = (
                select(AgentRun)
                .where(
                    AgentRun.status == "queued",
                    or_(AgentRun.run_after.is_(None), AgentRun.run_after <= now),
                )
                .order_by(AgentRun.priority.asc(), AgentRun.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(limit)
            )

            res = await session.execute(stmt)
            runs = res.scalars().all()
            run_ids: List[uuid.UUID] = []
            for run in runs:
                run.status = "running"
                run.locked_by = str(worker_id)
                run.locked_at = now
                run.started_at = now
                run.lease_expires_at = now + timedelta(minutes=15)
                run.updated_at = now
                run_ids.append(run.id)

            await session.commit()
            return run_ids
    except Exception as e:
        logger.error(f"Error fetching pending agent runs: {str(e)}")
        return []


async def worker_loop() -> None:
    """Main worker loop that polls for files and processes them with fixed 30-second intervals"""
    while not shutdown_event.is_set():
        try:
            run_ids = await fetch_pending_runs(limit=10)

            if run_ids:
                logger.info(f"Found {len(run_ids)} agent runs to process")

                tasks = []
                for run_id in run_ids:
                    task = asyncio.create_task(process_agent_run(run_id))
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
                logger.info(f"No pending agent runs found. Next poll in {POLL_INTERVAL} seconds")

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
        from api.workflows.tasks import load_whisper_model_if_needed
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
