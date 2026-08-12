"""Who is allowed to make this app speak.

The Slack endpoint is a public URL that takes an instruction and answers with
the contents of private documents. The signature is the only thing between
those two facts, so it is tested harder than anything else here.

Each case was checked by removing the guard it exists for.
"""
import hashlib
import hmac
import time

import pytest

from api.services import slack

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    base = f"v0:{timestamp}:".encode("utf-8") + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)


async def test_a_genuine_request_is_accepted():
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))

    assert slack.verify_signature(body=body, timestamp=ts, signature=sign(body, ts))


async def test_a_forged_signature_is_refused():
    """Somebody who knows the URL but not the secret."""
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))

    assert not slack.verify_signature(
        body=body, timestamp=ts, signature="v0=" + "0" * 64
    )


async def test_a_signature_from_a_different_secret_is_refused():
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    wrong = sign(body, ts, secret="a-different-workspaces-secret")

    assert not slack.verify_signature(body=body, timestamp=ts, signature=wrong)


async def test_a_body_that_changed_after_signing_is_refused():
    """The signature covers the body, which is the point of signing it.

    Without this, a captured request could be edited to name another team or
    another user and still verify.
    """
    ts = str(int(time.time()))
    signature = sign(b'{"team_id":"T_MINE"}', ts)

    assert not slack.verify_signature(
        body=b'{"team_id":"T_SOMEBODY_ELSES"}', timestamp=ts, signature=signature
    )


async def test_an_old_request_is_refused_even_though_it_is_genuine():
    """A replay. The signature is valid forever; the timestamp is what expires."""
    body = b'{"type":"event_callback"}'
    old = str(int(time.time()) - slack.MAX_REQUEST_AGE_SECONDS - 60)

    assert not slack.verify_signature(body=body, timestamp=old, signature=sign(body, old))


async def test_a_request_from_the_future_is_refused():
    """Clock skew in one direction is a mistake; in the other it is a way to
    make a captured request valid for longer than it should be."""
    body = b'{"type":"event_callback"}'
    ahead = str(int(time.time()) + slack.MAX_REQUEST_AGE_SECONDS + 60)

    assert not slack.verify_signature(body=body, timestamp=ahead, signature=sign(body, ahead))


async def test_nothing_is_accepted_when_the_secret_is_not_configured(monkeypatch):
    """Fails closed.

    A deploy that forgets SLACK_SIGNING_SECRET must refuse every request rather
    than answer all of them. One missing environment variable should not be the
    difference between private and public.
    """
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))

    assert not slack.verify_signature(body=body, timestamp=ts, signature=sign(body, ts))
    assert slack.is_configured() is False


async def test_missing_headers_are_refused():
    body = b'{}'
    ts = str(int(time.time()))

    assert not slack.verify_signature(body=body, timestamp=None, signature=sign(body, ts))
    assert not slack.verify_signature(body=body, timestamp=ts, signature=None)
    assert not slack.verify_signature(body=body, timestamp="not-a-number", signature="v0=x")


async def test_the_mention_is_stripped_from_the_question():
    """Left in, retrieval spends part of its budget matching an id that appears
    in no document."""
    assert slack.strip_mention("<@U012AB3CD> what is the refund policy?") == (
        "what is the refund policy?"
    )
    assert slack.strip_mention("no mention here") == "no mention here"


async def test_an_unresolvable_slack_user_yields_no_email(monkeypatch):
    """The bridge from "somebody in Slack" to "a person with access here".

    A guest with no email cannot be matched, and that must read as "no", never
    as "carry on".
    """
    class _Response:
        @staticmethod
        def json():
            return {"ok": False, "error": "users_not_found"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(slack.httpx, "AsyncClient", lambda **kw: _Client())

    assert await slack.email_for_slack_user("U123", "xoxb-token") is None


async def test_no_bot_token_means_no_lookup_and_no_post():
    """Both calls refuse rather than reaching out unauthenticated."""
    assert await slack.email_for_slack_user("U123", "") is None
    assert await slack.post_message(channel="C1", text="hi", bot_token="") is False
