"""Where this app actually lives, decided once.

Three things need the public origin now: the links in invite mail, the OAuth
discovery documents and consent redirect, and the WWW-Authenticate header that
tells a client where to go for a token. Three copies of "read APP_URL, fall back
to something" would drift, and the fallback is the part that has already gone
wrong once.

That was invite mail. The default was https://app.syntext.ai, which is not this
product's domain, so an invite that did send led nowhere.

The order below is not arbitrary. APP_URL first, because it is the explicit
answer. Then the first CORS origin, because that is by definition a real
frontend origin for this deployment and it is already set in production, so a
deploy where nobody remembered APP_URL still gets this right. localhost only
when neither is set, which is a developer's machine and nowhere else.

Read at call time, never at import: these come from /app/.env via load_dotenv,
which has not run yet when modules are first imported.
"""
import logging
import os

logger = logging.getLogger(__name__)


def public_app_url() -> str:
    """The origin a customer's browser and a customer's client both reach."""
    explicit = (os.getenv("APP_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        # Worth a line in the log: it is correct, and it is also a sign that
        # APP_URL was never set on this host.
        logger.info("APP_URL is unset; using the first CORS origin as the public URL")
        return origins[0].rstrip("/")

    return "http://localhost:3000"
