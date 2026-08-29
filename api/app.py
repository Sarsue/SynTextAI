from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .core.websocket_manager import websocket_manager
from .core.rate_limit import limiter
from .repositories.repository_manager import RepositoryManager
from dotenv import load_dotenv
from .core.firebase_setup import initialize_firebase
import asyncio
import os
import logging

from api.core.log_safety import safe_text
from .core.utils import decode_firebase_token
# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(max_request_body_size= 2 * 1024 * 1024 * 1024)

# Rate limiting (see core/rate_limit.py for the shared Limiter instance and
# budget rationale). Endpoints opt in individually via @limiter.limit(...).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware to add COOP and COEP headers
@app.middleware("http")
async def add_coop_coep_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    # response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none" # or "require-corp"
    return response

# Content-Security-Policy, built from the actual third-party origins this app
# loads (frontend/index.html, frontend/src/services/analytics.ts, Firebase
# Auth's fixed API domains, GCS-hosted uploaded files).
#
# ENFORCING as of 2026-08-15. It ran in Report-Only mode before that, which
# sounds like an observation period and was not one: the policy carried no
# report-uri, so every violation went to an individual user's devtools console
# and nowhere else. Nobody was ever going to read those.
#
# Checked before switching it on: every external origin referenced anywhere in
# frontend/src and index.html was matched against this policy. The only three
# outside it are a bit.ly link href, schema.org inside a JSON-LD block, and
# syntextai.com itself, none of which is a resource load.
#
# What is NOT verified: the signed-in paths. Stripe checkout, the Drive picker
# and the citation iframes could not be exercised from here without account
# credentials, so report-uri is what covers them. A violation now means a
# feature broke for a real person, and it arrives in the logs rather than in
# their console.
#
# The Google entries are for the Drive picker, added 2026-08-11 with the
# import feature rather than after it. This policy is still Report-Only, so a
# missing origin costs nothing today and breaks the feature silently on the day
# somebody flips the header name. Adding it later would mean debugging a
# picker that opens in development and refuses in production.
#
#   accounts.google.com   the token client that asks for drive.file
#   apis.google.com       the picker itself
#   www.googleapis.com    the Drive API the picker talks to
#   docs.google.com       the picker renders in an iframe from here
_CSP_POLICY = "; ".join([
    "default-src 'self'",
    # PostHog is listed here as well as in connect-src: posthog-js is bundled,
    # but it lazily fetches config.js, surveys.js and the session recorder from
    # the assets host.
    #
    # THE REGIONAL HOSTS ARE NOT OPTIONAL, and app.posthog.com is not one of
    # them. It is the dashboard origin. analytics.ts used to name it as the
    # api_host, and posthog-js quietly resolved that to us-assets.i.posthog.com
    # for its config and us.i.posthog.com for events. Only app.posthog.com was
    # allowed here, so from the day this policy stopped being Report-Only every
    # event was blocked by the browser: homepage_get_started_click,
    # homepage_pricing_click, all of it, silently, with the product looking
    # perfectly healthy and the dashboard simply empty.
    #
    # analytics.ts now names us.i.posthog.com directly and app.posthog.com is
    # only its ui_host, which builds links and loads nothing, so it is gone from
    # both directives. Verified by loading the homepage and firing an event: the
    # only origins contacted are the two below, both 200.
    "script-src 'self' https://js.stripe.com https://accounts.google.com "
    "https://apis.google.com https://us-assets.i.posthog.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://storage.googleapis.com https://*.googleusercontent.com "
    "https://ssl.gstatic.com https://www.gstatic.com",
    "connect-src 'self' https://us.i.posthog.com "
    "https://us-assets.i.posthog.com https://identitytoolkit.googleapis.com "
    "https://securetoken.googleapis.com https://storage.googleapis.com wss: https://js.stripe.com "
    "https://accounts.google.com https://www.googleapis.com",
    # 'self' IS NOT OPTIONAL AND ITS ABSENCE BROKE SIGN-IN FOR 13 DAYS.
    #
    # An explicit frame-src replaces default-src entirely, so listing origins
    # here without 'self' blocks our own. Firebase's popup sign-in loads an
    # iframe at https://syntextai.com/__/auth/iframe to receive the result of
    # the popup, because authDomain is syntextai.com rather than the default
    # *.firebaseapp.com. Blocked, the popup opens, the person signs in at
    # Google, the popup closes, and the app is never told. Nothing happens, no
    # error, no toast, nothing in the console except a CSP violation nobody was
    # looking at.
    #
    # Broken from 2026-08-15, the day this policy stopped being Report-Only.
    # Invites 1 to 4 were accepted on 3 and 4 August and every one after that
    # failed. It stayed invisible because an existing session keeps working: the
    # iframe is only needed to complete a NEW sign-in, so the only people who
    # could see it were the ones who could not get in to report it.
    #
    # Confirmed by appending that iframe to the live page and catching the
    # securitypolicyviolation event: blockedURI the auth iframe, violatedDirective
    # frame-src.
    #
    # Cited documents open in an iframe pointed at their GCS URL, so leaving
    # storage.googleapis.com out here would break every citation. docs.google.com
    # is the Drive picker, which is also an iframe.
    "frame-src 'self' https://js.stripe.com https://storage.googleapis.com "
    "https://docs.google.com https://accounts.google.com",
    "object-src 'none'",
    "base-uri 'self'",
    # Without this, Report-Only reported to nobody. A violation appeared in one
    # customer's devtools console, which nobody opens, so the observation period
    # this policy was supposed to be having produced no observations at all. It
    # stays on after enforcement, because that is when a violation means a
    # feature just broke for somebody.
    "report-uri /api/csp-report",
])

@app.post("/api/csp-report", include_in_schema=False)
@limiter.limit("30/minute")
async def csp_report(request: Request):
    """Where the browser posts a Content-Security-Policy violation.

    Public by necessity: the browser sends these with no credentials, and a
    violation is most interesting precisely when it happens to somebody who is
    not signed in.

    Deliberately narrow about what it keeps. Only three fields are logged, and
    each is truncated. The body is attacker-controlled -- anyone can post here,
    and a blocked URI is a URL the page tried to load -- so storing it whole
    would put arbitrary text of arbitrary length into the logs on request.

    Never raises. A malformed report is a report, not an incident, and this
    endpoint failing must not show up as an error to the browser that sent it.
    """
    try:
        body = await request.json()
        report = body.get("csp-report") or body
        logger.warning(
            "CSP violation: directive=%s blocked=%s on=%s",
            str(report.get("violated-directive") or report.get("effectiveDirective"))[:80],
            str(report.get("blocked-uri") or report.get("blockedURL"))[:200],
            str(report.get("document-uri") or report.get("documentURL"))[:200],
        )
    except Exception:
        logger.debug("Unparseable CSP report", exc_info=True)
    # 204: the browser wants nothing back, and an empty body cannot be reflected.
    return Response(status_code=204)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP_POLICY
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # nginx already terminates TLS in front of this app (see deploy.sh) — HSTS
    # tells browsers to only ever use HTTPS for this domain going forward.
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
# allow_origins=["*"] combined with allow_credentials=True let any site on the
# internet make authenticated requests using a logged-in user's credentials.
# Lock this to the real frontend domain(s). CORS_ORIGINS (comma-separated) lets
# this be widened for staging/other domains via env without a code change;
# falls back to production + common local dev ports if unset.
cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    allowed_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
else:
    allowed_origins = [
        "https://syntextai.com",
        "https://www.syntextai.com",
        "http://localhost:3000",
        "http://localhost:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add PostHog middleware for analytics
@app.middleware("http")
async def apply_posthog_middleware(request: Request, call_next):
    return await posthog_middleware(request, call_next)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy"}
# Initialize Firebase on application startup
@app.on_event("startup")
async def startup_event():
    """
    Initializes Firebase Admin SDK. This must be run before the app starts
    accepting requests to ensure that authentication utilities are ready.
    """
    logger.info("Executing startup event: Initializing Firebase...")
    initialize_firebase()
    logger.info("Firebase initialized successfully.")

    # Relay the worker's announcements to the browser. Replaces the worker
    # making a blocking HTTP call back into this process for every file status
    # change and every finished answer. That endpoint still exists as the
    # fallback for when Redis is unreachable, and now requires a shared secret.
    from .core.events import CLIENT_CHANNEL, listen as listen_for_events

    async def _relay(payload: dict) -> None:
        user_id = payload.get("user_id")
        event_type = payload.get("event_type")
        if not user_id or not event_type:
            logger.warning("Ignoring a client event with no user or type")
            return
        await websocket_manager.send_message(
            user_id=str(user_id),
            event_type=str(event_type),
            data=payload.get("data") or {},
        )

    app.state.event_listener = asyncio.create_task(
        listen_for_events(CLIENT_CHANNEL, _relay)
    )

@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup shared database resources on application shutdown.
    """
    logger.info("Executing shutdown event: Cleaning up database resources...")

    listener = getattr(app.state, "event_listener", None)
    if listener is not None:
        # listen() blocks on the next message, so it ends by being cancelled.
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass
    from .core.events import aclose as aclose_events
    await aclose_events()

    from .repositories.async_base_repository import cleanup_shared_db_resources
    await cleanup_shared_db_resources()
    # The inference client pools connections for the life of the process, so it
    # holds sockets open until told otherwise.
    from .services.llm_service import aclose_client
    await aclose_client()
    logger.info("Database and inference resources cleaned up successfully.")

from .models.async_db import get_database_url

# Get centralized async database URL
DATABASE_URL = get_database_url()

store = RepositoryManager(database_url=DATABASE_URL)
app.state.store = store
app.state.websocket_manager = websocket_manager  # ⬅️ Make websocket_manager available in app state

# WebSocket endpoint
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    db_user_id: str | None = None
    try:
        # Authenticate the user
        data = await websocket.receive_json()
        if data.get("type") != "auth":
            await websocket.close(code=1008, reason="Authentication required")
            return

        token = data.get("token")
        if not token:
            await websocket.close(code=1008, reason="Token required")
            return

        success, user_info = decode_firebase_token(token)
        if not success:
            await websocket.close(code=1008, reason="Invalid token")
            return

        # Store the connection ONLY under DB user_id (derived from token) since worker notifications
        # route by DB user_id. The client can keep connecting to /ws/{firebase_uid}.
        try:
            email = (user_info or {}).get("email")
            if not email:
                await websocket.close(code=1008, reason="Token missing email")
                return

            resolved_db_user_id = await store.user_repo.get_user_id_from_email(email)
            if not resolved_db_user_id:
                await websocket.close(code=1008, reason="User not registered")
                return

            db_user_id = str(resolved_db_user_id)
            websocket_manager.active_connections[db_user_id] = websocket
            logger.info(f"WebSocket authenticated for firebase_uid={user_id} db_user_id={db_user_id}")
        except Exception as e:
            logger.warning(f"WebSocket connected for {user_id} but failed to register db user id: {e}")
            await websocket.close(code=1011, reason="WebSocket registration failed")
            return

        # Keep the connection alive and handle incoming messages
        while True:
            try:
                message = await websocket.receive_text()
                # Handle incoming messages if needed
                logger.debug(f"Received message from user {user_id}: {safe_text(message)}")
            except Exception as e:
                logger.warning(f"Error receiving WebSocket message from user {user_id}: {str(e)}")
                break

    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected via WebSocket")
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {str(e)}")
    finally:
        # Clean up the connection
        websocket_manager.disconnect(db_user_id or user_id)

# Import routers after app is set up
from .routes.files import files_router
from .routes.histories import histories_router
from .routes.messages import messages_router
from .routes.subscriptions import subscriptions_router
from .routes.users import users_router
from .routes.analytics import router as analytics_router, posthog_middleware
from .routes.internal import router as internal_router  # ⬅️ Add internal router
from .routes.search import search_router
from .routes.workspaces import workspaces_router
from .routes.organizations import organizations_router
from .routes.sendgrid_events import router as sendgrid_events_router
from .routes.drafts import drafts_router
from .routes.api_keys import api_keys_router

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)
app.include_router(internal_router, prefix="/api/v1/internal")  # ⬅️ Include internal router
# Finding a passage, as opposed to being told an answer. Carries its own
# prefix, like files/histories/messages, so nothing is added here.
app.include_router(search_router)
app.include_router(workspaces_router)
app.include_router(drafts_router)
app.include_router(organizations_router)
app.include_router(api_keys_router)
# Public and unauthenticated by necessity: SendGrid posts here, and it holds no
# credential of ours. Its own prefix rather than /api/v1, because it is not part
# of the product's API and is not versioned alongside it.
app.include_router(sendgrid_events_router)

# Define the build path for React app
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))



class SPAStaticFiles(StaticFiles):
    """Static files with cache headers that match how the build works.

    Vite fingerprints every asset, so index.html is the only file whose *name*
    stays the same while its contents change. Without a Cache-Control header the
    browser caches it heuristically off Last-Modified and keeps serving an HTML
    file pointing at the previous build's bundle — so a deploy lands and nobody
    sees it until they hard-refresh, which no ordinary user will think to do.

    index.html therefore always revalidates, and the fingerprinted assets it
    names are cached for a year: their names change whenever their contents do,
    so they can never be stale.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path in ("", ".", "index.html") or path.endswith("/index.html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif "/assets/" in f"/{path}":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Mount static files LAST - this ensures the above routes take precedence
# Note: StaticFiles will only handle requests for files that exist
app.mount("/", SPAStaticFiles(directory=build_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)