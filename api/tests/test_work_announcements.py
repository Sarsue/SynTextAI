"""The tap on the shoulder, and what happens when it does not arrive.

The behaviour worth protecting is not "a message was published". It is that
none of this can cost a job or break an upload. So the cases below are mostly
about failure: Redis missing, Redis broken, an announcement nobody heard.

The event loop is shared across the session (see pytest.ini), so anything here
that waits does so with a timeout. A test that hangs on a never-set event would
take the whole run down with it.
"""
import asyncio
import os
import uuid

import pytest

from api.core import events
from api.workers import worker

# pytest.ini is not copied into the image, so asyncio_mode=auto does not apply
# when these run in the container. Every other async test module in this suite
# declares it the same way.
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def _no_redis_client():
    """Every test starts with no connection and leaves none behind."""
    events._client = None
    yield
    events._client = None


class _FakeRedis:
    """Records what was published. Optionally fails, like a Redis that is down."""

    def __init__(self, fail: bool = False):
        self.published = []
        self.fail = fail
        self.closed = False

    async def publish(self, channel, message):
        if self.fail:
            raise ConnectionError("Connection refused")
        self.published.append((channel, message))

    async def aclose(self):
        self.closed = True


async def test_no_redis_configured_is_silence_not_an_error(monkeypatch):
    """A checkout with no Redis behaves exactly as it did before this existed."""
    monkeypatch.setattr(events, "REDIS_URL", "")

    assert events.is_enabled() is False
    assert await events.announce_work("some-run-id") is False
    assert await events.announce_client_event(1, "file_status_update", {}) is False


async def test_a_dead_redis_never_raises_into_the_caller(monkeypatch):
    """An upload must still be accepted while Redis is refusing connections.

    This is the case that decides whether the feature is safe to ship. If this
    raises, a Redis outage takes the product down instead of slowing it.
    """
    monkeypatch.setattr(events, "REDIS_URL", "redis://unused")
    events._client = _FakeRedis(fail=True)

    assert await events.announce_work("some-run-id") is False
    # And the broken client was dropped, so the next call rebuilds rather than
    # reusing a connection that is known bad.
    assert events._client is None


async def test_an_announcement_carries_only_the_run_id(monkeypatch):
    """The worker re-reads the row, so the message is a nudge, not a payload."""
    monkeypatch.setattr(events, "REDIS_URL", "redis://unused")
    fake = _FakeRedis()
    events._client = fake

    assert await events.announce_work("abc-123") is True

    channel, message = fake.published[0]
    assert channel == events.WORK_CHANNEL
    assert message == '{"run_id": "abc-123"}'


async def test_a_client_event_is_addressed_by_database_user_id(monkeypatch):
    """The WebSocket manager registers connections under this id."""
    monkeypatch.setattr(events, "REDIS_URL", "redis://unused")
    fake = _FakeRedis()
    events._client = fake

    assert await events.announce_client_event(
        42, "message_received", {"status": "success"}
    ) is True

    channel, message = fake.published[0]
    assert channel == events.CLIENT_CHANNEL
    assert '"user_id": "42"' in message
    assert '"event_type": "message_received"' in message


async def test_the_worker_wakes_when_work_is_announced():
    """The whole point: the loop stops waiting the moment it is told."""
    worker.work_available.clear()

    waiter = asyncio.create_task(worker.work_available.wait())
    await worker._work_announced({"run_id": "anything"})

    await asyncio.wait_for(waiter, timeout=1)
    assert worker.work_available.is_set()


async def test_an_announcement_makes_the_loop_come_back_around(monkeypatch):
    """The feature itself, driven through the real loop.

    An announcement must make the loop stop waiting and fetch again. The poll
    interval is raised to thirty seconds, so a loop that does not react to the
    announcement cannot reach the second fetch inside the timeout and the test
    fails rather than quietly passing on the next tick.

    Confirmed to have teeth by replacing the wake-up in the loop's wait with a
    plain sleep: this test fails, the other seven still pass.

    What this does *not* cover, despite an earlier version of its name: whether
    the flag is cleared before the fetch or after the wait. Both orderings were
    run against this suite and both are green, because every wake is followed
    by a fetch either way. See the comment at the clear in worker_loop.
    """
    fetches = []
    second_fetch = asyncio.Event()

    async def fake_fetch(limit):
        fetches.append(limit)
        if len(fetches) == 1:
            # An announcement arrives while this first fetch is still running,
            # which is the race being tested.
            await worker._work_announced({"run_id": "arrived-mid-fetch"})
        else:
            second_fetch.set()
        return []

    async def no_reclaim():
        return 0

    monkeypatch.setattr(worker, "fetch_pending_runs", fake_fetch)
    monkeypatch.setattr(worker, "reclaim_expired_runs", no_reclaim)
    monkeypatch.setattr(worker, "POLL_INTERVAL", 30)

    worker.shutdown_event.clear()
    worker.work_available.clear()
    loop_task = asyncio.create_task(worker.worker_loop())
    try:
        # Two seconds against a thirty second poll interval: this can only pass
        # if the loop came back around because of the announcement.
        await asyncio.wait_for(second_fetch.wait(), timeout=2)
    finally:
        worker.shutdown_event.set()
        worker.work_available.set()
        try:
            await asyncio.wait_for(loop_task, timeout=2)
        except asyncio.TimeoutError:
            loop_task.cancel()
        worker.shutdown_event.clear()
        worker.work_available.clear()

    assert len(fetches) >= 2


async def test_the_run_is_already_committed_when_it_is_announced(store, tenant, monkeypatch):
    """The ordering the whole module rests on, against a real database.

    Announce inside the transaction and the worker can wake, run its SELECT
    against a row that is not visible yet, find nothing, and go back to
    waiting. The announcement is spent at that point, so the run then sits
    until the poll interval elapses: the exact delay this feature removes,
    reintroduced by publishing one line too early.

    Mocks cannot show this, because the thing being tested is what a *second*
    connection can see. So the fake announcer here opens its own session and
    looks for the row, which is precisely what the worker would do.
    """
    from sqlalchemy import select
    from api.models.orm_models import AgentRun

    visible_at_announce = {}

    async def announce_and_look(run_id: str) -> bool:
        async with store.agent_run_repo.get_async_session() as session:
            found = (
                await session.execute(
                    select(AgentRun).where(AgentRun.id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            visible_at_announce[run_id] = found is not None
        return True

    monkeypatch.setattr(
        "api.repositories.async_agent_run_repository.announce_work", announce_and_look
    )

    run_id = await store.agent_run_repo.enqueue_run(
        run_type="answer_query",
        agent_name="QueryAgent",
        agent_version=None,
        payload={"message": "does the worker see this yet?"},
        user_id=tenant.owner,
    )

    assert run_id is not None
    assert visible_at_announce[run_id] is True, (
        "the run was announced before its transaction committed, so a worker "
        "waking on that announcement would find nothing"
    )


async def test_an_unreadable_message_does_not_kill_the_listener(monkeypatch):
    """A malformed message is skipped, not fatal.

    Redis is shared infrastructure. Something else publishing junk onto the
    channel must not stop this process finding work for the rest of its life.
    """
    monkeypatch.setattr(events, "REDIS_URL", "redis://unused")
    handled = []

    class _FakePubSub:
        async def subscribe(self, channel):
            return None

        async def aclose(self):
            return None

        async def listen(self):
            yield {"type": "subscribe", "data": "1"}
            yield {"type": "message", "data": "not json at all"}
            yield {"type": "message", "data": '{"run_id": "good"}'}
            # Ending the stream sends listen() into its reconnect path, which
            # is where the test stops it.
            raise asyncio.CancelledError()

    class _FakeClientWithPubSub(_FakeRedis):
        def pubsub(self):
            return _FakePubSub()

    events._client = _FakeClientWithPubSub()

    async def handler(payload):
        handled.append(payload)

    with pytest.raises(asyncio.CancelledError):
        await events.listen("syntext:test", handler)

    assert handled == [{"run_id": "good"}]


async def test_a_failing_handler_does_not_kill_the_listener(monkeypatch):
    """One bad message must not cost every message after it."""
    monkeypatch.setattr(events, "REDIS_URL", "redis://unused")
    seen = []

    class _FakePubSub:
        async def subscribe(self, channel):
            return None

        async def aclose(self):
            return None

        async def listen(self):
            yield {"type": "message", "data": '{"n": 1}'}
            yield {"type": "message", "data": '{"n": 2}'}
            raise asyncio.CancelledError()

    class _FakeClientWithPubSub(_FakeRedis):
        def pubsub(self):
            return _FakePubSub()

    events._client = _FakeClientWithPubSub()

    async def handler(payload):
        seen.append(payload["n"])
        if payload["n"] == 1:
            raise RuntimeError("handler blew up")

    with pytest.raises(asyncio.CancelledError):
        await events.listen("syntext:test", handler)

    assert seen == [1, 2]
