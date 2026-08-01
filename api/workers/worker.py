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
from contextlib import asynccontextmanager
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import uuid
from dotenv import load_dotenv
from pathlib import Path
from api.core.timing import emit
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

# --------------------------------------------------------------------------
# Concurrency
#
# Ingestion and querying have completely different cost profiles, so they get
# separate budgets. A single global limit meant a chat question queued behind a
# document upload: with the old MAX_CONCURRENT_TASKS=1, one tenant uploading a
# 500-page PDF blocked every other tenant from getting an answer at all.
#
#   queries  are IO-bound: retrieval plus HTTP calls to the embedding and chat
#            endpoints. No local model is loaded (the "cross-encoder" reranker
#            is embedding-similarity based), so these are cheap and several run
#            at once.
#   ingests  are the memory-hungry side, so they stay tightly capped, and a
#            file at or above HEAVY_FILE_BYTES additionally takes an exclusive
#            slot so two big documents never overlap.
#
# On the original "sequential processing prevents OOM" rationale: PDFProcessor
# already batches 50 pages at a time, so peak memory per job is bounded by the
# batch rather than by total document size. That makes the global limit of 1
# stricter than it needed to be. The heavy slot here is the belt-and-braces
# guard, not the primary protection.
#
# The worker container is capped at 2GB. Watch actual RSS against the TIMING
# logs before raising INGEST_CONCURRENCY further.
# --------------------------------------------------------------------------
QUERY_CONCURRENCY = int(os.getenv("QUERY_CONCURRENCY", "4"))
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "2"))

# A file at or above this size takes the exclusive heavy slot.
HEAVY_FILE_BYTES = int(os.getenv("HEAVY_FILE_BYTES", str(8 * 1024 * 1024)))

# Per-tenant cap on simultaneously running jobs, so one user queueing twenty
# files cannot starve everyone else. Queries and ingests count together.
MAX_RUNS_PER_USER = int(os.getenv("MAX_RUNS_PER_USER", "3"))

# Total in-flight ceiling across all run types.
MAX_INFLIGHT = int(os.getenv("MAX_INFLIGHT", str(QUERY_CONCURRENCY + INGEST_CONCURRENCY)))

# Idle poll interval. The loop also wakes as soon as any running task finishes,
# so this only governs how long it sleeps when there is nothing to do.
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

# Runs whose lease expired are considered abandoned (worker killed mid-job) and
# are returned to the queue.
LEASE_MINUTES = int(os.getenv("LEASE_MINUTES", "15"))

# API base URL for internal notifications (docker-compose sets this to http://syntextaiapp:3000)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3000").rstrip("/")


query_semaphore = asyncio.Semaphore(QUERY_CONCURRENCY)
ingest_semaphore = asyncio.Semaphore(INGEST_CONCURRENCY)
heavy_ingest_semaphore = asyncio.Semaphore(1)

# user_id -> number of runs currently executing, used for the per-tenant cap.
inflight_by_user: Dict[int, int] = {}

# Track running tasks to ensure graceful shutdown. A set, because the loop adds
# and removes entries as jobs start and finish rather than in batches.
running_tasks: set = set()
shutdown_event = asyncio.Event()

# How often to sweep for runs whose lease expired.
RECLAIM_INTERVAL = int(os.getenv("RECLAIM_INTERVAL", "60"))

# Liveness heartbeat. The worker serves no HTTP, so the image's HEALTHCHECK
# (which curls port 3000) could never pass for it and left the container
# permanently unhealthy, hiding real stalls. The loop touches this file on every
# iteration and the container healthcheck asserts it is recent.
HEARTBEAT_PATH = os.getenv("WORKER_HEARTBEAT_PATH", "/tmp/worker_heartbeat")


def _touch_heartbeat() -> None:
    try:
        Path(HEARTBEAT_PATH).write_text(str(int(time.time())))
    except Exception:
        logger.debug("Could not write heartbeat", exc_info=True)


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


@asynccontextmanager
async def _acquire_slot(run_type: str, payload: Dict[str, Any]):
    """Take the right concurrency slot for this run type.

    Queries and ingests draw on separate budgets so neither can starve the
    other, and a large file additionally takes the exclusive heavy slot.
    """
    if run_type == "answer_query":
        async with query_semaphore:
            yield
        return

    file_bytes = payload.get("file_size_bytes") or 0
    is_heavy = file_bytes >= HEAVY_FILE_BYTES

    async with ingest_semaphore:
        if is_heavy:
            async with heavy_ingest_semaphore:
                logger.info(
                    f"Ingest holding exclusive heavy slot ({file_bytes} bytes >= {HEAVY_FILE_BYTES})"
                )
                yield
        else:
            yield


async def process_agent_run(run_id: uuid.UUID) -> None:
    store = get_repository_manager()

    async with store.agent_run_repo.get_async_session() as session:
        run = await session.get(AgentRun, run_id)
        if not run:
            return
        payload = run.payload or {}
        run_type = run.run_type
        run_user_id = run.user_id
        queued_at = run.created_at
        started_at = run.started_at

    # Queue wait is the headline latency number: how long the user sat there
    # after uploading or asking before any work began.
    if queued_at and started_at:
        emit(
            "queue_wait",
            ms=(started_at - queued_at).total_seconds() * 1000,
            run_type=run_type,
            user_id=run_user_id,
        )

    async with _acquire_slot(run_type, payload):
        if shutdown_event.is_set():
            return

        if run_type == "ingest_file":
            file_id = payload.get("file_id")
            user_id = payload.get("user_id")
            filename = payload.get("filename")
            file_url = payload.get("file_url")
            workspace_id = payload.get("workspace_id")
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


            try:
                result = await process_file_data(
                    file_id=int(file_id),
                    user_id=int(user_id),
                    filename=str(filename),
                    file_url=str(file_url),
                    workspace_id=int(workspace_id) if workspace_id is not None else None,
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


async def reclaim_expired_runs() -> int:
    """Return abandoned runs to the queue.

    `lease_expires_at` was previously written but never read, so a worker killed
    mid-job left its runs stuck in "running" forever: never retried, never
    surfaced, invisible to the user whose upload silently stopped.
    """
    try:
        from sqlalchemy import select

        store = get_repository_manager()
        async with store.agent_run_repo.get_async_session() as session:
            now = datetime.utcnow()
            stmt = (
                select(AgentRun)
                .where(
                    AgentRun.status == "running",
                    AgentRun.lease_expires_at.is_not(None),
                    AgentRun.lease_expires_at < now,
                )
                .with_for_update(skip_locked=True)
                .limit(20)
            )
            runs = (await session.execute(stmt)).scalars().all()
            for run in runs:
                attempts = (run.attempts or 0) + 1
                run.attempts = attempts
                if attempts >= (run.max_attempts or 3):
                    run.status = "failed"
                    run.last_error = "Lease expired; worker presumed dead. Out of attempts."
                    run.finished_at = now
                    if run.file_id:
                        logger.warning(f"Run {run.id} abandoned permanently; marking file {run.file_id} failed")
                else:
                    run.status = "queued"
                    run.locked_by = None
                    run.locked_at = None
                    run.lease_expires_at = None
                    run.last_error = "Lease expired; worker presumed dead. Requeued."
                run.updated_at = now

            if runs:
                logger.warning(f"Reclaimed {len(runs)} run(s) with expired leases")
                emit("lease_reclaimed", count=len(runs))
            await session.commit()

            # Files whose run was permanently abandoned should not sit in a
            # perpetual "extracting" state in the UI.
            for run in runs:
                if run.status == "failed" and run.file_id:
                    try:
                        await update_file_status(int(run.file_id), "failed", error="Processing was interrupted")
                    except Exception:
                        logger.debug(f"Could not mark file {run.file_id} failed", exc_info=True)

            return len(runs)
    except Exception as e:
        logger.error(f"Error reclaiming expired runs: {e}")
        return 0


async def fetch_pending_runs(limit: int = 10) -> List[Tuple[uuid.UUID, Optional[int]]]:
    """Claim up to `limit` queued runs, respecting the per-tenant cap.

    Candidates are read in priority order, then filtered against the per-user
    in-flight count. Rows we decline are simply left untouched, so they stay
    queued and remain available to this worker's next pass or another worker.
    """
    if limit <= 0:
        return []
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
                # Over-fetch so that skipping a user at their cap does not cost
                # us the whole batch.
                .limit(max(limit * 4, 20))
            )

            res = await session.execute(stmt)
            runs = res.scalars().all()

            # Provisional per-user tally, seeded from what is already running.
            tally = dict(inflight_by_user)
            claimed: List[Tuple[uuid.UUID, Optional[int]]] = []
            deferred = 0
            for run in runs:
                if len(claimed) >= limit:
                    break
                uid = run.user_id
                if uid is not None:
                    if tally.get(uid, 0) >= MAX_RUNS_PER_USER:
                        deferred += 1
                        continue
                    tally[uid] = tally.get(uid, 0) + 1

                run.status = "running"
                run.locked_by = str(worker_id)
                run.locked_at = now
                run.started_at = now
                run.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
                run.updated_at = now
                claimed.append((run.id, uid))

            await session.commit()
            if deferred:
                logger.info(f"Deferred {deferred} run(s) whose owner is at the per-user cap of {MAX_RUNS_PER_USER}")
            return claimed
    except Exception as e:
        logger.error(f"Error fetching pending agent runs: {str(e)}")
        return []


async def _run_tracked(run_id: uuid.UUID, user_id: Optional[int]) -> None:
    """Run one job while maintaining the per-user in-flight count."""
    if user_id is not None:
        inflight_by_user[user_id] = inflight_by_user.get(user_id, 0) + 1
    try:
        await process_agent_run(run_id)
    except Exception:
        logger.exception(f"Agent run {run_id} raised")
    finally:
        if user_id is not None:
            remaining = inflight_by_user.get(user_id, 1) - 1
            if remaining > 0:
                inflight_by_user[user_id] = remaining
            else:
                inflight_by_user.pop(user_id, None)


async def worker_loop() -> None:
    """Claim work continuously, topping up to capacity as jobs finish.

    The previous version fetched a batch and then waited on ALL_COMPLETED before
    polling again, which made the whole batch move at the speed of its slowest
    member. A chat question claimed alongside a 500-page PDF could not even be
    looked at until that PDF finished, and no new work was picked up in the
    meantime, so priority ordering never got a chance to matter. Now the loop
    wakes the moment any job finishes and immediately refills the freed slot.
    """
    last_reclaim = 0.0
    while not shutdown_event.is_set():
        try:
            _touch_heartbeat()

            # Return abandoned runs to the queue periodically.
            if time.monotonic() - last_reclaim > RECLAIM_INTERVAL:
                await reclaim_expired_runs()
                last_reclaim = time.monotonic()

            capacity = MAX_INFLIGHT - len(running_tasks)
            if capacity > 0:
                claimed = await fetch_pending_runs(limit=capacity)
                if claimed:
                    logger.info(
                        f"Claimed {len(claimed)} run(s); in-flight {len(running_tasks)}/{MAX_INFLIGHT}"
                    )
                for run_id, uid in claimed:
                    task = asyncio.create_task(_run_tracked(run_id, uid))
                    running_tasks.add(task)
                    task.add_done_callback(running_tasks.discard)

            if running_tasks:
                # Wake as soon as any job completes so its slot is refilled
                # immediately, rather than waiting out the poll interval.
                await asyncio.wait(
                    set(running_tasks),
                    timeout=POLL_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Error in worker loop: {str(e)}")
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
    logger.info(
        "Starting SynText AI Worker "
        f"(queries={QUERY_CONCURRENCY}, ingests={INGEST_CONCURRENCY}, "
        f"heavy>={HEAVY_FILE_BYTES // (1024 * 1024)}MB, per-user cap={MAX_RUNS_PER_USER}, "
        f"max in-flight={MAX_INFLIGHT})"
    )
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    # Preload models to avoid OOM crashes during first file processing
    logger.info("Preloading models...")
    
    # Note: Embedding model preload removed - using HTTP API (Voyage AI)
    # No local model to preload
    logger.info("✅ Using HTTP-based embeddings (Voyage AI) - no model preload needed")

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
