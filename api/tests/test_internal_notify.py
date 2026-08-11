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
