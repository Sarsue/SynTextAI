"""Answering from inside Slack.

WHAT THIS IS

A surface, not a source. Slack is not another place to read documents from; it
is another place to ask, so that somebody who lives in Slack never has to open
this app to get an answer out of their own documents. Teams is the same shape
with a different envelope, which is why the parts that are not Slack-specific
live in functions rather than in the route.

THE HARD PART IS NOT SLACK, IT IS WHO IS ASKING

Everything else in this product answers a question as a *person*, scoped to the
workspaces that person may see. A Slack message arrives with a Slack user id
and a Slack team id, and neither means anything here. If that gap is bridged
carelessly, a company's whole document set becomes readable by anybody who can
type in their Slack, including guests and, in a shared channel, people from
another company entirely.

So the bridge is deliberate and narrow:

  - An owner links a Slack workspace to one of ours, once, on purpose.
  - Every question resolves the *asker* to a real member by their verified
    email address, and answers with that person's own access.
  - An email we do not recognise gets a polite refusal, not a public answer.

That last rule is the whole security model. It means adding somebody to Slack
never adds them to the knowledge base, which is the property an administrator
needs to be able to state plainly.

WHY THE SIGNATURE CHECK IS NOT OPTIONAL

This endpoint is a public URL that takes an instruction and answers with the
contents of private documents. Without verifying that a request really came
from Slack, anybody who learns the URL can post a payload naming any team and
any user and read whatever that person can read. The signature is the only
thing standing between the two, so it is checked before the body is parsed,
and a request that fails is refused without explanation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Slack signs with this scheme version; a different one means the contract
# changed and refusing is the right answer.
SIGNATURE_VERSION = "v0"

# Slack's own guidance. A request older than this is a replay, whether it is an
# attacker resending a captured payload or a queue that stalled; neither should
# be answered.
MAX_REQUEST_AGE_SECONDS = 60 * 5

SLACK_POST_MESSAGE = "https://slack.com/api/chat.postMessage"
SLACK_USER_INFO = "https://slack.com/api/users.info"


def signing_secret() -> str:
    return os.getenv("SLACK_SIGNING_SECRET", "")


def is_configured() -> bool:
    """Whether Slack has been set up at all.

    Read at call time rather than at import, so a deploy that adds the secret
    does not need a code change to notice it.
    """
    return bool(signing_secret())


def verify_signature(
    *,
    body: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
    now: Optional[float] = None,
) -> bool:
    """Did this really come from Slack, recently.

    Both halves matter. The signature proves the sender holds the signing
    secret; the timestamp stops a captured request being replayed tomorrow.

    `compare_digest` rather than `==`, so the comparison does not return early
    on the first wrong byte and leak the expected value one character at a time.
    """
    secret = signing_secret()
    if not secret:
        # Unconfigured means refuse, never allow. A missing environment
        # variable must not be a way in.
        logger.error("SLACK_SIGNING_SECRET is not set; refusing a Slack request")
        return False

    if not timestamp or not signature:
        return False

    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError):
        return False

    if abs((now if now is not None else time.time()) - sent_at) > MAX_REQUEST_AGE_SECONDS:
        logger.warning("Refusing a Slack request that is too old to be genuine")
        return False

    basestring = f"{SIGNATURE_VERSION}:{timestamp}:".encode("utf-8") + body
    expected = (
        SIGNATURE_VERSION
        + "="
        + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


async def email_for_slack_user(user_id: str, bot_token: str) -> Optional[str]:
    """The verified email behind a Slack user id, or nothing.

    This is what turns "somebody in Slack" into "a person with access here", so
    a failure to resolve it must never fall through to answering anyway. Slack
    only returns the address when the app holds users:read.email, and only for
    a real member: a guest without an email simply cannot be matched, which is
    the correct outcome rather than a problem to work around.
    """
    if not bot_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                SLACK_USER_INFO,
                params={"user": user_id},
                headers={"Authorization": f"Bearer {bot_token}"},
            )
        payload = response.json()
        if not payload.get("ok"):
            logger.warning("Slack refused users.info: %s", payload.get("error"))
            return None
        email = (payload.get("user") or {}).get("profile", {}).get("email")
        return email.strip().lower() if email else None
    except Exception as e:
        logger.warning("Could not resolve a Slack user: %s", type(e).__name__)
        return None


async def post_message(
    *, channel: str, text: str, bot_token: str, thread_ts: Optional[str] = None
) -> bool:
    """Say something back, in the thread the question was asked in.

    Threaded on purpose: an answer with citations is long, and a channel where
    every reply lands at the bottom becomes unreadable for the people not
    asking.
    """
    if not bot_token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                SLACK_POST_MESSAGE,
                headers={"Authorization": f"Bearer {bot_token}"},
                json={
                    "channel": channel,
                    "text": text,
                    **({"thread_ts": thread_ts} if thread_ts else {}),
                },
            )
        payload = response.json()
        if not payload.get("ok"):
            logger.warning("Slack refused chat.postMessage: %s", payload.get("error"))
            return False
        return True
    except Exception as e:
        logger.warning("Could not post to Slack: %s", type(e).__name__)
        return False


def strip_mention(text: str) -> str:
    """The question, without the "@Syntext" that summoned it.

    Left in, the mention becomes part of the query and retrieval spends part of
    its budget matching an id that appears in no document.
    """
    import re

    return re.sub(r"<@[A-Z0-9]+>", " ", text or "").strip()
