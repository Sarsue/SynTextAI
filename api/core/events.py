"""The tap on the shoulder.

WHY THIS EXISTS

Every chat question and every upload becomes a row in `agent_runs`, and the
worker found out about it by asking the database every POLL_INTERVAL seconds
(10 in production, 30 by default in the code). When the worker is idle, which
for a small customer base is most of the time, that was up to ten seconds of
nothing happening before an answer even began to be computed. Nothing was
running during it. It is not the queue being slow; it is the worker not having
looked yet.

This module is how the API tells the worker "there is work now", and how the
worker tells the API "tell this person their file is ready", without either one
waiting on the other.

WHAT IS DELIBERATE HERE

**Redis is the notification. Postgres remains the record.** A published message
reaches only whoever is listening at that instant. Publish while the worker is
restarting and that message is gone. That is acceptable, and it is exactly why
POLL_INTERVAL stays: the poll is the safety net underneath, so a missed tap
costs latency and never costs a job. Do not be tempted to make Redis the queue
instead. `agent_runs` already handles leases, retries and per-tenant fairness,
and every one of those behaviours was paid for in a real bug. Redis Streams
would be durable enough to tempt you; taking it would mean owning a second
implementation of all three.

**Publish after the commit, never inside it.** The run has to be visible to the
worker's SELECT by the time the worker goes looking. Announce it while the
transaction is still open and the worker can wake, find nothing, and go back to
sleep, and the work then waits out the full poll interval anyway, which is the
delay this module exists to remove. `enqueue_run` commits first for this reason.

**Nothing here may raise into its caller.** If Redis is down, an upload must
still be accepted and a question must still be answered. Every entry point
swallows its own errors and says so in the log. Redis being unreachable
degrades this system to precisely what it was before this module existed, which
is a working product with a slower start.

**An unset REDIS_URL means off, not broken.** A local checkout with no Redis
running behaves exactly as it did before, and says so once rather than warning
on every publish.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import requests

from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Empty means disabled. Set in docker-compose to redis://redis:6379/0.
REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Namespaced because this Redis will not stay single-purpose: roadmap item 21
# is a short-lived query cache and a shared rate limiter is a likely third
# tenant of the same instance.
WORK_CHANNEL = "syntext:work"
CLIENT_CHANNEL = "syntext:client"

# Short timeouts on purpose. Every call here sits in front of something a
# customer is waiting on, so failing fast and falling back to the old behaviour
# beats blocking an upload while a dead Redis is dialled.
_CONNECT_TIMEOUT = 2
_SOCKET_TIMEOUT = 2

_client = None
_client_lock = asyncio.Lock()


def is_enabled() -> bool:
    """Whether a Redis has been configured at all."""
    return bool(REDIS_URL)


async def _get_client():
    """The process-wide client, built on first use."""
    global _client
    if not REDIS_URL:
        return None
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            import redis.asyncio as aioredis

            _client = aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=_CONNECT_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
                health_check_interval=30,
            )
            logger.info("Connected to Redis for notifications")
    return _client


async def _reset_client() -> None:
    """Drop a client that has gone bad so the next call rebuilds it."""
    global _client
    client, _client = _client, None
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        logger.debug("Could not close the old Redis client", exc_info=True)


async def _publish(channel: str, message: Dict[str, Any]) -> bool:
    """Send one message. Returns whether it went out, never raises."""
    client = await _get_client()
    if client is None:
        return False
    try:
        await client.publish(channel, json.dumps(message))
        return True
    except Exception as e:
        # Warning rather than error: the poll and the HTTP fallback both cover
        # this, so it is a slower system rather than a broken one.
        logger.warning("Could not announce on %s: %s", channel, e)
        await _reset_client()
        return False


async def announce_work(run_id: str) -> bool:
    """Tell the worker a run is queued and waiting.

    Call this *after* the enqueueing transaction has committed. The payload is
    only the id: the worker re-reads the row under its own locking rules, so
    nothing here is trusted as the source of truth about the job.
    """
    return await _publish(WORK_CHANNEL, {"run_id": str(run_id)})


async def announce_client_event(
    user_id: int, event_type: str, data: Dict[str, Any]
) -> bool:
    """Tell the API to relay an event to one person's browser.

    Routed by database user id, matching what the WebSocket manager registers
    connections under.
    """
    return await _publish(
        CLIENT_CHANNEL,
        {"user_id": str(int(user_id)), "event_type": event_type, "data": data},
    )


async def notify_client(user_id: int, event_type: str, data: Dict[str, Any]) -> bool:
    """Get one event to one person's browser: Redis, then HTTP, then say so.

    ONE COPY, BECAUSE TWO DRIFTED

    This existed twice. The worker's copy published to Redis first and sent the
    shared secret on the HTTP fallback. The copy inside update_file_status did
    neither: no publish, and a POST with no headers to an endpoint that requires
    them. requests.post does not raise on a 403 and the result was discarded, so
    every file status change the worker produced was refused by the API in
    silence, and a customer watching an upload saw nothing until they reloaded.

    Both callers now come here. The header cannot be forgotten in one place and
    remembered in the other, because there is only one place.

    Returns True when the event was handed off. False means the browser will not
    hear about this one, which the caller may or may not care about.
    """
    if not user_id:
        return False

    if await announce_client_event(int(user_id), event_type, data):
        return True

    base = (os.getenv("API_BASE_URL") or "").rstrip("/")
    if not base:
        logger.warning(
            "API_BASE_URL is not set, so %s for user %s cannot fall back to "
            "HTTP and will not reach the browser",
            event_type, user_id,
        )
        return False

    secret = os.getenv("INTERNAL_API_SECRET", "")
    if not secret:
        # Named, never printed. The receiving end fails closed on a missing
        # secret, so without this the symptom is silence.
        logger.warning(
            "INTERNAL_API_SECRET is not set, so %s for user %s will be refused "
            "by the API. Set it in the environment of both the API and the "
            "worker.",
            event_type, user_id,
        )

    payload = {
        "user_id": str(int(user_id)),
        "event_type": event_type,
        "data": data,
    }
    headers = {"X-Internal-Secret": secret} if secret else {}

    def _post() -> bool:
        try:
            response = requests.post(
                f"{base}/api/v1/internal/notify-client",
                json=payload, headers=headers, timeout=5,
            )
        except Exception as e:
            logger.warning(
                "Could not notify the browser of %s for user %s: %s",
                event_type, user_id, type(e).__name__,
            )
            return False
        if response.status_code >= 400:
            # The status code and nothing else. A refusal here is about the
            # secret, and a log line is not the place for it: not the value,
            # not its length, not a prefix.
            logger.warning(
                "The API refused a %s notification for user %s with HTTP %s. "
                "A 403 means the shared secret does not match between worker "
                "and API; a 503 means it is not configured.",
                event_type, user_id, response.status_code,
            )
            return False
        return True

    return await asyncio.to_thread(_post)


async def listen(
    channel: str,
    handler: Callable[[Dict[str, Any]], Awaitable[None]],
) -> None:
    """Run `handler` for every message on `channel`, forever.

    Reconnects with backoff, and never lets a bad message or a failing handler
    end the loop. A listener that dies takes the fast path down with it, which
    is survivable because of the poll; a listener that kills its process is not.

    Exits only on cancellation. `redis.asyncio`'s listen() blocks waiting for
    the next message, so shutdown means cancelling this task rather than
    setting a flag it would never get around to reading.
    """
    if not REDIS_URL:
        logger.info("REDIS_URL is not set, so nothing is listening on %s", channel)
        return

    backoff = 1
    while True:
        pubsub = None
        try:
            client = await _get_client()
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            logger.info("Listening on %s", channel)
            backoff = 1

            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                try:
                    payload = json.loads(raw["data"])
                except Exception:
                    logger.warning("Ignoring an unreadable message on %s", channel)
                    continue
                try:
                    await handler(payload)
                except Exception:
                    logger.exception("The handler for %s failed", channel)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Lost the connection to %s (%s), retrying in %ss", channel, e, backoff
            )
            await _reset_client()
            await asyncio.sleep(backoff)
            # Capped, so a long outage settles into a steady retry rather than
            # drifting out to an interval that never recovers.
            backoff = min(backoff * 2, 30)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.debug("Could not close the subscription", exc_info=True)


async def aclose() -> None:
    """Release the connection at shutdown."""
    await _reset_client()
