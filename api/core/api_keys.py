"""Making and reading an API key token. Nothing here decides permission.

FORMAT

    stx_live_<12 hex>_<64 hex>
    ^^^^^^^^^ ^^^^^^^ ^^^^^^^^
    tag       prefix  secret

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

# "live" leaves room for a second kind of key later without reusing this one's
# namespace. Present from the first token so no issued credential has to change
# shape if that happens.
TOKEN_TAG = "stx_live_"

_PREFIX_BYTES = 6  # 12 hex characters
_SECRET_BYTES = 32  # 64 hex characters, 256 bits


def looks_like_api_key(credential: str) -> bool:
    """Whether to try resolving this as an API key rather than a Firebase token.

    Deliberately a cheap string test and not a validity check. A malformed key
    still reaches the resolver and is refused there, so there is one place that
    says no rather than two that could disagree.
    """
    return bool(credential) and credential.startswith(TOKEN_TAG)


def generate_token() -> Tuple[str, str, str]:
    """A new token. Returns (full_token, prefix, token_hash).

    The full token is returned to the caller once, here, and never reconstructed:
    only its hash is stored, so nothing downstream can show it again.
    """
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    full = f"{TOKEN_TAG}{prefix}_{secret}"
    return full, prefix, hash_token(full)


def hash_token(full_token: str) -> str:
    return hashlib.sha256(full_token.encode("utf-8")).hexdigest()


def parse_prefix(full_token: str) -> Optional[str]:
    """The prefix to look up, or None when the string is not shaped like a key.

    Returning None rather than raising: an unparseable credential is a 401 like
    any other bad one, and the caller should not have to tell two kinds of
    refusal apart.
    """
    if not looks_like_api_key(full_token):
        return None
    body = full_token[len(TOKEN_TAG):]
    prefix, sep, secret = body.partition("_")
    if not sep or not prefix or not secret:
        return None
    return prefix


def matches(full_token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(full_token), stored_hash or "")
