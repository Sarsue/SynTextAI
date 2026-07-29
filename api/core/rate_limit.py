"""
Rate limiting shared instance. A separate module so both app.py (registers
the middleware/exception handler) and individual route files (apply the
@limiter.limit(...) decorator) can import the same Limiter without a
circular import through app.py.

Keyed by IP address, not per-user. Per-user would be more precise (an IP
behind a shared corporate NAT could hit the same limit across multiple real
users), but requires re-deriving the user from the Authorization header
before the route's own auth dependency runs. IP-based catches the obvious
abuse case (a script hammering the endpoint) with far less complexity,
lean-simple default. Revisit if IP-based proves too coarse in practice.

Budgets below are a first-pass default, not a confirmed number from Osas,
"Rate limiting approach/budget" was an open question in
docs/ENGINEERING_OVERVIEW.md before this. Tune once there's real usage data.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Applied to endpoints that trigger paid LLM/embedding calls.
CHAT_RATE_LIMIT = "30/minute"
UPLOAD_RATE_LIMIT = "10/minute"
