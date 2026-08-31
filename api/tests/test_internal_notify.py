"""Who is allowed to push a message into a customer's browser.

This endpoint takes a user id and arbitrary event data and relays it over the
WebSocket. It had no authentication at all: "internal" was the intent, and the
only thing enforcing it was that nobody had noticed. The container publishes
port 3000 and the router sits on the same public prefix as every other route.

It is now the fallback for when a Redis publish fails, and it requires a shared
secret. Each test here was checked by removing the guard and confirming it
turned red.
"""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.internal import router, INTERNAL_SECRET_ENV


class _RecordingWebSocketManager:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, event_type, data):
        self.sent.append((user_id, event_type, data))


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/internal")
    app.state.websocket_manager = _RecordingWebSocketManager()
    return TestClient(app)


@pytest.fixture
def secret(monkeypatch):
    value = "test-secret-value"
    monkeypatch.setenv(INTERNAL_SECRET_ENV, value)
    return value


def _notification():
    return {
        "user_id": "42",
        "event_type": "file_status_update",
        "data": {"file_id": 1, "status": "processed"},
    }


def test_a_stranger_with_no_secret_is_refused(client, secret):
    """The hole. Anybody who could reach the API could do this."""
    response = client.post("/api/v1/internal/notify-client", json=_notification())

    assert response.status_code == 403
    assert client.app.state.websocket_manager.sent == []


def test_a_wrong_secret_is_refused(client, secret):
    response = client.post(
        "/api/v1/internal/notify-client",
        json=_notification(),
        headers={"X-Internal-Secret": "not-the-secret"},
    )

    assert response.status_code == 403
    assert client.app.state.websocket_manager.sent == []


def test_the_worker_gets_through_and_the_message_is_relayed(client, secret):
    response = client.post(
        "/api/v1/internal/notify-client",
        json=_notification(),
        headers={"X-Internal-Secret": secret},
    )

    assert response.status_code == 202
    assert client.app.state.websocket_manager.sent == [
        ("42", "file_status_update", {"file_id": 1, "status": "processed"})
    ]


def test_an_unconfigured_secret_refuses_everyone(client, monkeypatch):
    """Fails closed.

    Treating "no secret configured" as "allow anybody" would mean one missing
    environment variable silently reopens the hole with nothing to say so. This
    way a misconfigured deploy loses the fallback notifications, which only
    matter while Redis is down, and logs why.
    """
    monkeypatch.delenv(INTERNAL_SECRET_ENV, raising=False)

    response = client.post(
        "/api/v1/internal/notify-client",
        json=_notification(),
        headers={"X-Internal-Secret": "anything"},
    )

    assert response.status_code == 503
    assert client.app.state.websocket_manager.sent == []


# ---------------------------------------------------------------------------
# The caller's side of the same handshake
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_fallback_post_carries_the_secret(monkeypatch):
    """The header that was missing for as long as this endpoint has been guarded.

    There were two copies of this call. The worker's sent the secret; the one
    inside update_file_status did not, and posted to an endpoint that answers
    403 without it. requests.post does not raise on a 403 and the response was
    discarded, so every file status change was refused in silence and an upload
    sat at "extracting" until the customer reloaded the page.

    Asserting on the header rather than on the outcome, because the outcome was
    exactly the same either way: nothing visible.
    """
    from api.core import events

    monkeypatch.setenv("API_BASE_URL", "http://api.invalid")
    monkeypatch.setenv(INTERNAL_SECRET_ENV, "s3cr3t")

    async def no_redis(*_a, **_k):
        return False

    monkeypatch.setattr(events, "announce_client_event", no_redis)

    seen = {}

    class _Response:
        status_code = 200

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        seen["json"] = json
        return _Response()

    monkeypatch.setattr(events.requests, "post", fake_post)

    ok = await events.notify_client(42, "file_status_update", {"file_id": 1, "status": "processed"})

    assert ok is True
    assert seen["headers"].get("X-Internal-Secret") == "s3cr3t"
    assert seen["url"].endswith("/api/v1/internal/notify-client")
    assert seen["json"]["user_id"] == "42"


@pytest.mark.asyncio
async def test_a_refused_notification_is_logged_not_swallowed(monkeypatch, caplog):
    """A 403 has to say something. Silence is what let this live.

    And it must say it without the secret in it: not the value, not its length,
    not a prefix. The log line carries the status code and what it means.
    """
    from api.core import events

    monkeypatch.setenv("API_BASE_URL", "http://api.invalid")
    monkeypatch.setenv(INTERNAL_SECRET_ENV, "s3cr3t-do-not-print")

    async def no_redis(*_a, **_k):
        return False

    monkeypatch.setattr(events, "announce_client_event", no_redis)

    class _Refused:
        status_code = 403

    monkeypatch.setattr(
        events.requests, "post",
        lambda url, json=None, headers=None, timeout=None: _Refused(),
    )

    with caplog.at_level("WARNING"):
        ok = await events.notify_client(42, "file_status_update", {"file_id": 1})

    assert ok is False
    logged = caplog.text
    assert "403" in logged, "a refusal must be visible in the log"
    assert "s3cr3t-do-not-print" not in logged, "the secret was written to the log"


@pytest.mark.asyncio
async def test_a_missing_secret_says_so_without_printing_it(monkeypatch, caplog):
    """The receiving end fails closed, so an unset secret is silence otherwise."""
    from api.core import events

    monkeypatch.setenv("API_BASE_URL", "http://api.invalid")
    monkeypatch.delenv(INTERNAL_SECRET_ENV, raising=False)

    async def no_redis(*_a, **_k):
        return False

    monkeypatch.setattr(events, "announce_client_event", no_redis)

    class _Unavailable:
        status_code = 503

    monkeypatch.setattr(
        events.requests, "post",
        lambda url, json=None, headers=None, timeout=None: _Unavailable(),
    )

    with caplog.at_level("WARNING"):
        await events.notify_client(42, "file_status_update", {"file_id": 1})

    assert "INTERNAL_API_SECRET is not set" in caplog.text
