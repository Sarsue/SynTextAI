"""
Rate limiting shared instance. A separate module so both app.py (registers
the middleware/exception handler) and individual route files (apply the
@limiter.limit(...) decorator) can import the same Limiter without a
circular import through app.py.

Keyed by IP address for a person in a browser. Per-user would be more precise
(an IP behind a shared corporate NAT could hit the same limit across multiple
real users), but requires re-deriving the user from the Authorization header
before the route's own auth dependency runs. IP-based catches the obvious
abuse case (a script hammering the endpoint) with far less complexity,
lean-simple default.

An API key is the case that made IP too coarse, which this module said to watch
for. An integration calls from one server address, so every human behind that
address would share one budget with it, and one busy integration would exhaust
the allowance for real people who have nothing to do with it. So a request
carrying an API key is keyed by that key's prefix instead.

The prefix, deliberately, and not the key's row id: the prefix is readable
straight off the header, so this stays a string operation on the hot path with
no database lookup before the limiter can decide. It identifies exactly one
credential, which is all a budget needs.

Budgets below are a first-pass default, not a confirmed number from Osas,
"Rate limiting approach/budget" was an open question in
docs/ENGINEERING_OVERVIEW.md before this. Tune once there's real usage data.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from .api_keys import parse_prefix


def credential_or_address(request: Request) -> str:
    """One budget per API key, otherwise one per address.

    Never raises. A malformed header falls through to the address, because the
    limiter runs before authentication and must not be the thing that decides a
    bad credential is fatal: that refusal belongs to the auth dependency, which
    gives a 401 rather than a 429.
    """
    header = request.headers.get("authorization") or ""
    token = header.split("Bearer ", 1)[-1].strip()
    prefix = parse_prefix(token)
    if prefix:
        return f"key:{prefix}"
    return get_remote_address(request)


limiter = Limiter(key_func=credential_or_address)

# Applied to endpoints that trigger paid LLM/embedding calls.
CHAT_RATE_LIMIT = "30/minute"
UPLOAD_RATE_LIMIT = "10/minute"
