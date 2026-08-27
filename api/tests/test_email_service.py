"""The invite email has to survive contact with SendGrid's link rewriter.

WHAT BROKE, 2026-07-29 TO 2026-08-27

Nobody could accept an invite for four weeks. The link we generate was correct
the whole time, and pasting it into a browser worked. The chain:

    email_service builds  https://syntextai.com/#/invite/<token>
    SendGrid rewrites to  http://url639.syntextai.com/ls/click?upn=...
    that host is          a CNAME to sendgrid.net, no cert for this name
    api/app.py sends      Strict-Transport-Security ... includeSubDomains
    so the browser        upgrades http to https, refuses the cert, and HSTS
                          removes the "proceed anyway" escape

Chrome shows NET::ERR_CERT_COMMON_NAME_INVALID and the invite is unreachable.
The http hop itself is fine: it 302s to the right place and even preserves the
#/invite/<token> fragment. It never gets used, because the browser will not
speak http to a subdomain of a domain that sent includeSubDomains.

It looked intermittent, which is why it survived a first misdiagnosis on
2026-08-03 that blamed the anchor's button styling. HSTS is trust-on-first-use
and we do not set preload, so it only bites once that browser has seen
syntextai.com. An invitee who looked the product up first is broken. A stranger
who clicked straight from the email is not.

WHAT IS ASSERTED HERE

That click tracking is off in the payload SendGrid actually receives, not that
an attribute was set on an object on the way there. Both halves of it: `enable`
and `enable_text` are separate settings at SendGrid and the plain-text part of
this message carries the same link.

This lives in the repo rather than in the SendGrid dashboard for the same
reason. A dashboard toggle is one click from undoing it, and nothing would fail
until an invite went unanswered.
"""
import pytest

from api.services import email_service


class _FakeResponse:
    status_code = 202
    body = b""


class _FakeClient:
    """Stands in for SendGridAPIClient and keeps what it was asked to send."""

    sent = None

    def __init__(self, api_key):
        self.api_key = api_key

    def send(self, message):
        _FakeClient.sent = message
        return _FakeResponse()


@pytest.fixture
def sent_payload(monkeypatch):
    """Send one invite and hand back the JSON body SendGrid would receive."""
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "invites@syntextai.com")
    monkeypatch.setenv("APP_URL", "https://syntextai.com")
    monkeypatch.setattr(email_service, "SendGridAPIClient", _FakeClient)
    _FakeClient.sent = None

    email_service.send_workspace_invite(
        to_email="someone@example.com",
        workspace_name="Operations",
        token="846253ed-6986-491d-a58f-56bf2ce1faed",
        inviter_name="Osas",
    )

    assert _FakeClient.sent is not None, "nothing was sent"
    return _FakeClient.sent.get()


def test_click_tracking_is_off_in_what_sendgrid_receives(sent_payload):
    """With this on, SendGrid rewrites the link to a host our own HSTS header
    makes unreachable. See the module docstring."""
    click_tracking = (
        sent_payload.get("tracking_settings", {}).get("click_tracking")
    )

    assert click_tracking is not None, (
        "no click_tracking in the payload, so SendGrid applies the account "
        "default, and the account default is what broke invites for a month"
    )
    assert click_tracking.get("enable") is False, (
        "SendGrid will rewrite the href to url639.syntextai.com, which our "
        "includeSubDomains HSTS forces to https and which has no certificate"
    )
    assert click_tracking.get("enable_text") is False, (
        "the plain-text part carries the same link and is tracked separately"
    )


def test_the_invite_link_reaches_the_recipient_intact(sent_payload):
    """The link is the entire payload of this email. If it is not in both parts
    exactly as built, there is nothing for the recipient to do."""
    expected = "https://syntextai.com/#/invite/846253ed-6986-491d-a58f-56bf2ce1faed"

    bodies = {c["type"]: c["value"] for c in sent_payload["content"]}
    assert set(bodies) == {"text/plain", "text/html"}, (
        f"expected both parts, got {sorted(bodies)}"
    )
    for content_type, body in bodies.items():
        assert expected in body, f"the invite link is missing from {content_type}"


def test_the_invite_link_is_https(sent_payload):
    """An http link would be upgraded by the same HSTS header that broke the
    tracking hop, so it has to be https at the point we write it."""
    assert email_service.app_url().startswith("https://")

    bodies = [c["value"] for c in sent_payload["content"]]
    for body in bodies:
        assert "http://syntextai.com" not in body
