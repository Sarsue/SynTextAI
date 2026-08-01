"""What SendGrid did with the mail we handed it.

A 202 from the send API means SendGrid accepted the message, not that anybody
received it. Everything that decides whether an invite actually lands happens
afterwards and silently: the recipient's server rejects it, the domain is
unauthenticated so it is filed as spam, the address is on a suppression list
from a bounce months ago. None of that comes back on the send call, which is why
invites could read as sent and never arrive.

This is the other half of the conversation. SendGrid posts every delivery event
here, and the failures carry the reason with them.

Deliberately not stored in the database. The question being answered is "why did
this not arrive", which is a question about a moment, and the logs already have
timestamps and retention. A table would need a migration, a cleanup policy and a
screen to be worth anything, and none of that helps until there is a pattern
worth querying.
"""
import json
import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Request, Response, status

from ..core.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sendgrid", tags=["sendgrid"])

# Events that mean the mail did not arrive, each with the field that says why.
_FAILURES = {"bounce", "dropped", "deferred", "blocked", "spamreport"}
# Events that mean it did. Logged quietly: useful to confirm a specific address
# worked, useless in bulk.
_DELIVERIES = {"delivered", "processed"}


def _verify(request_body: bytes, headers) -> bool:
    """Check the ECDSA signature, when a public key is configured.

    Returns True when the payload is trustworthy or when verification is not
    configured at all. The endpoint is public and unauthenticated, so without a
    key anyone who finds the URL can post plausible-looking events. That only
    pollutes logs, which is why an unconfigured key is a warning rather than a
    refusal, but set SENDGRID_WEBHOOK_PUBLIC_KEY and this becomes real.
    """
    public_key = os.getenv("SENDGRID_WEBHOOK_PUBLIC_KEY")
    if not public_key:
        return True

    signature = headers.get("X-Twilio-Email-Event-Webhook-Signature")
    timestamp = headers.get("X-Twilio-Email-Event-Webhook-Timestamp")
    if not signature or not timestamp:
        logger.warning("SendGrid event post missing signature headers")
        return False

    try:
        from sendgrid.helpers.eventwebhook import EventWebhook

        verifier = EventWebhook()
        return bool(
            verifier.verify_signature(
                request_body.decode("utf-8"),
                signature,
                timestamp,
                verifier.convert_public_key_to_ecdsa(public_key),
            )
        )
    except Exception as e:
        logger.error("Could not verify SendGrid signature: %s", e, exc_info=True)
        return False


def _describe(event: Dict[str, Any]) -> str:
    """One line carrying the fields that actually explain a failure."""
    parts = [
        f"email={event.get('email')}",
        f"event={event.get('event')}",
    ]
    # 'reason' is the human-readable rejection from the receiving server, and is
    # the single most useful field here: "550 5.1.1 user unknown",
    # "unauthenticated senders not accepted by this domain".
    for field in ("reason", "status", "type", "response", "attempt", "sg_message_id"):
        value = event.get(field)
        if value not in (None, ""):
            parts.append(f"{field}={value}")
    return " ".join(str(p) for p in parts)


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("120/minute")
async def sendgrid_events(request: Request):
    """Receive SendGrid's event webhook.

    Always answers 2xx unless the signature is wrong. SendGrid retries any
    non-2xx with backoff, so an error raised over one malformed event would have
    the whole batch redelivered repeatedly, and a batch is up to a thousand
    events belonging to unrelated messages.
    """
    body = await request.body()

    if not _verify(body, request.headers):
        # The one case worth refusing: a configured key that did not match.
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        events: List[Dict[str, Any]] = json.loads(body or b"[]")
    except json.JSONDecodeError:
        logger.warning("SendGrid posted a body that is not JSON: %r", body[:200])
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if not isinstance(events, list):
        events = [events]

    for event in events:
        if not isinstance(event, dict):
            continue
        name = (event.get("event") or "").lower()
        if name in _FAILURES:
            # Warning, not error: mail failing to reach one address is a normal
            # condition of sending mail, and paging on it would be noise. It
            # still needs to be findable, which is the whole point of this
            # endpoint.
            logger.warning("SendGrid failure: %s", _describe(event))
        elif name in _DELIVERIES:
            logger.info("SendGrid %s: %s", name, _describe(event))
        else:
            logger.debug("SendGrid %s: %s", name or "event", _describe(event))

    return Response(status_code=status.HTTP_204_NO_CONTENT)
