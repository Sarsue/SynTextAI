"""Making and reading an API key token. Nothing here decides permission.

FORMAT

    stx_live_<12 hex>_<64 hex>
    ^^^^^^^^^ ^^^^^^^ ^^^^^^^^
    tag       prefix  secret

Three tags share it: `stx_live_` for an API key, `stx_at_` for an OAuth access
token, `stx_rt_` for a refresh token. The tag decides which resolver and which
table, before any query runs, so a token can never be checked against the wrong
one.

The tag is what tells `authenticate_api_caller` this is not a Firebase token,
before any database work happens. The prefix identifies one row and is stored in
the clear. The secret is 256 bits of randomness and is stored only as a hash.

Hex, not base64url. `secrets.token_urlsafe` emits '-' and '_', and '_' is the
separator here, so a urlsafe secret would make the string ambiguous to split at
exactly the moment it must not be. Hex costs a few characters and cannot.

WHY SHA-256 AND NOT BCRYPT

A password is short and guessable, so it is hashed slowly to make guessing
expensive. This is 256 bits of randomness with no dictionary behind it, and the
hash runs on every single request. A slow hash here would buy nothing and cost
latency on the hot path.

The comparison is still constant time. The stored hash is not a secret, but
timing a comparison against it is a free oracle we have no reason to hand out.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional, Tuple

# "live" left room for a second kind of credential without reusing this one's
# namespace, and OAuth is what arrived. Three tags, one format: the tag is what
# tells the resolvers apart before any database work happens, so a token can
# never be looked up in the wrong table.
TOKEN_TAG = "stx_live_"  # an API key, issued in Settings
ACCESS_TOKEN_TAG = "stx_at_"  # an OAuth access token, short-lived
REFRESH_TOKEN_TAG = "stx_rt_"  # an OAuth refresh token, presented only to /token

# Ordered longest-first. "stx_live_" and "stx_at_" share no prefix today, but a
# tag added later that extends another would otherwise be claimed by the shorter
# one, and the failure would be a token resolved against the wrong table.
ALL_TAGS = tuple(
    sorted((TOKEN_TAG, ACCESS_TOKEN_TAG, REFRESH_TOKEN_TAG), key=len, reverse=True)
)

_PREFIX_BYTES = 6  # 12 hex characters
_SECRET_BYTES = 32  # 64 hex characters, 256 bits


def looks_like(credential: str, tag: str) -> bool:
    """Whether to try resolving this as a credential of that kind.

    Deliberately a cheap string test and not a validity check. A malformed
    credential still reaches its resolver and is refused there, so there is one
    place that says no rather than two that could disagree.
    """
    return bool(credential) and credential.startswith(tag)


def looks_like_api_key(credential: str) -> bool:
    return looks_like(credential, TOKEN_TAG)


def looks_like_access_token(credential: str) -> bool:
    return looks_like(credential, ACCESS_TOKEN_TAG)


def generate_token(tag: str = TOKEN_TAG) -> Tuple[str, str, str]:
    """A new token. Returns (full_token, prefix, token_hash).

    The full token is returned to the caller once, here, and never
    reconstructed: only its hash is stored, so nothing downstream can show it
    again.
    """
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    full = f"{tag}{prefix}_{secret}"
    return full, prefix, hash_token(full)


def hash_token(full_token: str) -> str:
    return hashlib.sha256(full_token.encode("utf-8")).hexdigest()


def parse_prefix(full_token: str, tag: str = TOKEN_TAG) -> Optional[str]:
    """The prefix to look up, or None when the string is not shaped like one.

    Returning None rather than raising: an unparseable credential is a 401 like
    any other bad one, and the caller should not have to tell two kinds of
    refusal apart.
    """
    if not looks_like(full_token, tag):
        return None
    body = full_token[len(tag):]
    prefix, sep, secret = body.partition("_")
    if not sep or not prefix or not secret:
        return None
    return prefix


def any_prefix(full_token: str) -> Optional[str]:
    """The prefix of whichever kind of credential this is.

    For the rate limiter, which needs one budget per credential and must decide
    that from the header alone, before anything is looked up. It does not care
    which table the credential lives in.
    """
    for tag in ALL_TAGS:
        prefix = parse_prefix(full_token, tag)
        if prefix:
            return prefix
    return None


def matches(full_token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(full_token), stored_hash or "")
