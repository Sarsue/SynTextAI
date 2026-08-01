"""The endpoint that tells us why mail did not arrive.

Public and unauthenticated, because SendGrid posts to it and holds no credential
of ours. That makes two things worth pinning: it must not be knocked over by
anything posted at it, and it must refuse forged events once a verification key
is configured.
"""
import json
import os

import httpx
import pytest

from api.app import app

pytestmark = pytest.mark.asyncio(loop_scope="session")

URL = "/api/sendgrid/events"

BOUNCE = [{
    "email": "nobody@example.com",
    "event": "bounce",
    "reason": "550 5.1.1 The email account that you tried to reach does not exist",
    "status": "5.1.1",
    "type": "bounce",
    "sg_message_id": "msg-1",
}]


async def _post(content, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.post(URL, content=content, headers=headers or {})


async def test_a_batch_of_events_is_accepted():
    res = await _post(json.dumps(BOUNCE))
    assert res.status_code == 204


async def test_rubbish_does_not_break_it():
    """A non-2xx would have SendGrid redeliver the whole batch, repeatedly.

    A batch holds up to a thousand events for unrelated messages, so one
    malformed entry must not cost the rest of them.
    """
    for body in (b"not json", b"", b"null", json.dumps({"event": "bounce"}).encode(),
                 json.dumps(["a string", 42, None]).encode()):
        res = await _post(body)
        assert res.status_code == 204, body


async def test_forged_events_are_refused_once_a_key_is_configured(monkeypatch):
    """Without a key anyone who finds the URL can post plausible events.

    That only pollutes logs, which is why an unset key is permissive. With one
    set, an unsigned post is a forgery and is refused.
    """
    monkeypatch.setenv("SENDGRID_WEBHOOK_PUBLIC_KEY", "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE" + "A" * 52)

    unsigned = await _post(json.dumps(BOUNCE))
    assert unsigned.status_code == 403

    bad_signature = await _post(json.dumps(BOUNCE), headers={
        "X-Twilio-Email-Event-Webhook-Signature": "not-a-signature",
        "X-Twilio-Email-Event-Webhook-Timestamp": "1785600000",
    })
    assert bad_signature.status_code == 403


async def test_without_a_key_configured_events_are_accepted(monkeypatch):
    monkeypatch.delenv("SENDGRID_WEBHOOK_PUBLIC_KEY", raising=False)
    res = await _post(json.dumps(BOUNCE))
    assert res.status_code == 204
