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
from api.core.events import (
    WORK_CHANNEL,
    aclose as aclose_events,
    announce_client_event,
    is_enabled as is_events_enabled,
    listen,
)
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

# A running job pushes its own lease forward this often. Without it the lease
# means "this job started less than LEASE_MINUTES ago", not "a worker is alive
# and holding it", so anything slower than the lease gets reclaimed while it is
# still working. Measured 2026-08-12: vision extraction on the 69-page Carrier
# manual takes about an hour, so the sweeper requeued it at 15, 30 and 45
# minutes and then marked it "worker presumed dead. Out of attempts." The worker
# was alive the whole time, and each requeue started a SECOND live extraction of
# the same document against a metered vision endpoint. Renewing at a third of
# the lease leaves two missed renewals of slack before a genuinely dead worker
# is reclaimed.
LEASE_RENEW_SECONDS = max(30, (LEASE_MINUTES * 60) // 3)

# API base URL for internal notifications (docker-compose sets this to http://syntextaiapp:3000)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3000").rstrip("/")

# Proves to /internal/notify-client that a notification came from the worker.
# Only used on the fallback path, when a Redis publish did not go out. The
# endpoint refuses without it, so an unset value here means notifications are
# lost during a Redis outage rather than being accepted from anybody.
INTERNAL_API_SECRET = os.getenv("INTERNAL_API_SECRET", "")


query_semaphore = asyncio.Semaphore(QUERY_CONCURRENCY)
ingest_semaphore = asyncio.Semaphore(INGEST_CONCURRENCY)
heavy_ingest_semaphore = asyncio.Semaphore(1)

# user_id -> number of runs currently executing, used for the per-tenant cap.
inflight_by_user: Dict[int, int] = {}

# Track running tasks to ensure graceful shutdown. A set, because the loop adds
# and removes entries as jobs start and finish rather than in batches.
running_tasks: set = set()
shutdown_event = asyncio.Event()

# Set by the Redis listener when the API announces a queued run, so the loop
# stops waiting and goes to look. Cleared by the loop before each fetch.
work_available = asyncio.Event()

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


def _run_record(result: Dict[str, Any]) -> Dict[str, Any]:
    """What is worth keeping about a query run.

    Not the whole result. context_chunks carries the full text of every
    retrieved page, so storing the result verbatim wrote something like fifty
    kilobytes of duplicated document text into agent_runs.result for every
    question asked, none of which anything ever read back.

    What is worth keeping is the trace: which queries the model issued, what
    came back, and how much of it was new. That is the difference between "the
    query was bad", "retrieval was fine and the answer ignored it", "it stopped
    too early" and "it asked the same thing three times", which are four
    different fixes that look identical from the answer alone.
    """
    keep = {
        k: result.get(k)
        for k in ("mode", "turns", "searches", "reads", "outlines",
                  "rewritten_query", "expanded_terms", "information_needs",
                  "covered_needs", "retrievals", "error")
        if result.get(k) is not None
    }
    chunks = result.get("context_chunks") or []
    keep["context_chunks"] = len(chunks)
    # Which documents actually answered, as ids rather than text. The count
    # above says how much context there was; this says where it came from, and
    # it is the only way to answer "which of my documents has never once been
    # used", which is usually a document that failed to extract properly.
    # Cheap: a handful of integers against a count that was already stored.
    keep["cited_file_ids"] = sorted(
        {c.get("file_id") for c in chunks if isinstance(c, dict) and c.get("file_id")}
    )
    return keep


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
                accessible_ids = None
                if not workspace_id:
                    accessible_ids = await store.workspace_repo.accessible_workspace_ids(int(user_id))
                formatted_history = await store.chat_repo.format_user_chat_history(
                    int(history_id), int(user_id), accessible_workspace_ids=accessible_ids
                )
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
                    answer_message_id = await store.chat_repo.add_message(
                        content=str(response),
                        sender="bot",
                        user_id=int(user_id),
                        chat_history_id=int(history_id),
                    )
                    # Tie this run to the answer it produced, so a thumbs-down
                    # on that message can be read next to what was retrieved,
                    # whether coverage was satisfied, and how much context the
                    # model was given. Without it a rating can only be matched
                    # to a run by timestamp within a conversation, which is a
                    # guess. Never raises: the answer is already saved and the
                    # customer is waiting, so a missing link must not fail it.
                    if answer_message_id:
                        await store.chat_repo.link_run_to_message(
                            run_id, int(answer_message_id)
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
                    result=_run_record(result),
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

    Redis first, HTTP second, and the fallback is deliberate. Publishing is
    non-blocking and costs no round trip, but a published message reaches only
    a listener that is connected at that instant. This is the one notification
    path where losing a message is visible to a customer: they would sit
    watching a spinner for an answer that is already saved in the database.
    So when the publish does not go out, we make the old direct call rather
    than shrug.
    """
    if not user_id:
        return

    if await announce_client_event(int(user_id), event_type, data):
        return

    url = f"{API_BASE_URL}/api/v1/internal/notify-client"
    payload = {
        "user_id": str(int(user_id)),
        "event_type": event_type,
        "data": data,
    }
    headers = {"X-Internal-Secret": INTERNAL_API_SECRET} if INTERNAL_API_SECRET else {}

    def _post():
        try:
            requests.post(url, json=payload, headers=headers, timeout=5)
        except Exception as e:
            logger.warning(f"Failed to notify client for user {user_id}: {e}")

    await asyncio.to_thread(_post)


async def renew_lease(run_id: uuid.UUID) -> bool:
    """Push one running run's lease forward. False means we no longer hold it.

    Deliberately conditional on the row still being ours and still running: if
    the sweeper already reclaimed this run, renewing would drag a row somebody
    else now owns, and two workers would believe they hold the same job.
    """
    try:
        from sqlalchemy import update as sa_update

        store = get_repository_manager()
        worker_id = os.getenv("WORKER_ID") or str(os.getpid())
        now = datetime.utcnow()
        async with store.agent_run_repo.get_async_session() as session:
            res = await session.execute(
                sa_update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.locked_by == str(worker_id),
                )
                .values(lease_expires_at=now + timedelta(minutes=LEASE_MINUTES), updated_at=now)
            )
            await session.commit()
            return res.rowcount > 0
    except Exception as e:
        logger.warning(f"Could not renew lease for run {run_id}: {e}")
        # Not fatal on its own. A single failed renewal still leaves most of the
        # lease in hand, and the next tick will try again.
        return True


async def _hold_lease(run_id: uuid.UUID) -> None:
    """Renew a run's lease for as long as its job is running."""
    while True:
        await asyncio.sleep(LEASE_RENEW_SECONDS)
        still_ours = await renew_lease(run_id)
        if not still_ours:
            # The job is still executing here, so this is the moment the run got
            # taken away from us. Say so loudly: the alternative is two workers
            # doing the same paid work and neither of them mentioning it.
            logger.error(
                f"Run {run_id} was reclaimed while this worker is still executing it. "
                "Lease renewal is losing to the sweeper."
            )
            emit("lease_lost_while_running", run_id=str(run_id))
            return


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
    keepalive = asyncio.create_task(_hold_lease(run_id))
    try:
        await process_agent_run(run_id)
    except Exception:
        logger.exception(f"Agent run {run_id} raised")
    finally:
        keepalive.cancel()
        if user_id is not None:
            remaining = inflight_by_user.get(user_id, 1) - 1
            if remaining > 0:
                inflight_by_user[user_id] = remaining
            else:
                inflight_by_user.pop(user_id, None)


async def _work_announced(payload: Dict[str, Any]) -> None:
    """Somebody queued a run. Wake the loop.

    The run id in the payload is deliberately ignored. The loop claims work
    under `SELECT ... FOR UPDATE SKIP LOCKED` in priority order, which is what
    keeps two workers off the same row and keeps one tenant from starving
    another. Chasing the announced id directly would sidestep all of it. The
    message means "look now", nothing more.
    """
    work_available.set()


async def worker_loop() -> None:
    """Claim work continuously, topping up to capacity as jobs finish.

    The previous version fetched a batch and then waited on ALL_COMPLETED before
    polling again, which made the whole batch move at the speed of its slowest
    member. A chat question claimed alongside a 500-page PDF could not even be
    looked at until that PDF finished, and no new work was picked up in the
    meantime, so priority ordering never got a chance to matter. Now the loop
    wakes the moment any job finishes and immediately refills the freed slot.

    It also wakes the moment work is announced over Redis, which is what
    removes the idle wait: previously a question asked while the worker had
    nothing to do sat for up to POLL_INTERVAL seconds before anything started.
    POLL_INTERVAL stays as the safety net for an announcement nobody was
    listening for, so the worst case is what the whole system used to do.
    """
    last_reclaim = 0.0
    while not shutdown_event.is_set():
        try:
            _touch_heartbeat()

            # Return abandoned runs to the queue periodically.
            if time.monotonic() - last_reclaim > RECLAIM_INTERVAL:
                await reclaim_expired_runs()
                last_reclaim = time.monotonic()

            # Cleared here so the flag only ever means "announcements this
            # iteration has not acted on yet". One that lands during the fetch
            # below leaves it set, and the wait then returns at once.
            #
            # This comment used to claim that clearing after the wait instead
            # would swallow an announcement and cost a full poll interval. That
            # is wrong, and it was checked by making the change and watching
            # the tests stay green: every wake is followed by a fetch at the
            # top of the next iteration, so a swallowed announcement costs one
            # wasted fetch and no latency. Kept here for adjacency to the fetch
            # it describes, not because it is guarding a race.
            work_available.clear()

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

            # One wait covers both reasons to wake: a job finished and freed a
            # slot, or new work was announced. At capacity the announcement is
            # not interesting yet, but waking on it is harmless, because the
            # next fetch simply claims nothing.
            waiters = set(running_tasks)
            announcement = asyncio.create_task(work_available.wait())
            waiters.add(announcement)
            try:
                await asyncio.wait(
                    waiters,
                    timeout=POLL_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                # Nothing else awaits this task, so an uncancelled one left
                # behind on every iteration is a slow leak.
                if not announcement.done():
                    announcement.cancel()

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

    # Listen for announcements alongside the loop. This is the fast path only:
    # if it never connects, or dies and stays dead, the loop still polls and
    # the product behaves exactly as it did before this existed.
    listener = asyncio.create_task(listen(WORK_CHANNEL, _work_announced))
    if is_events_enabled():
        logger.info("Waking on announcements as well as polling")
    else:
        logger.info(
            "REDIS_URL is not set, so work is found by polling every %ss", POLL_INTERVAL
        )

    # Start the worker loop
    try:
        await worker_loop()

    except Exception as e:
        logger.exception(f"Fatal error in worker: {str(e)}")

    finally:
        # listen() blocks on the next message and exits only on cancellation.
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass

        # Wait for remaining tasks to complete on shutdown
        if running_tasks:
            logger.info(f"Waiting for {len(running_tasks)} tasks to complete...")
            await asyncio.wait(running_tasks)

        # Clean up shared database resources
        logger.info("Cleaning up shared database resources...")
        from api.repositories.async_base_repository import cleanup_shared_db_resources
        await cleanup_shared_db_resources()
        from api.services.llm_service import aclose_client
        await aclose_client()
        await aclose_events()

        logger.info("SynText AI Worker shutdown complete")


if __name__ == "__main__":
    # Run the main async function
    asyncio.run(main())
