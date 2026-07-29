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
import os
import logging
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
# Auth's fixed API domains, GCS-hosted uploaded files). Shipped in
# Report-Only mode: it's observable (violations show in the browser devtools
# console) without risking breaking Stripe checkout, Firebase auth, or
# PostHog analytics in production on a policy that's never been run against
# live traffic. Check the console for violations, then switch the header
# name below from Content-Security-Policy-Report-Only to
# Content-Security-Policy once it's confirmed clean.
_CSP_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://js.stripe.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://storage.googleapis.com",
    "connect-src 'self' https://app.posthog.com https://identitytoolkit.googleapis.com "
    "https://securetoken.googleapis.com https://storage.googleapis.com wss: https://js.stripe.com",
    "frame-src https://js.stripe.com",
    "object-src 'none'",
    "base-uri 'self'",
])

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy-Report-Only"] = _CSP_POLICY
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

@app.on_event("shutdown")
async def shutdown_event():
    """
    Cleanup shared database resources on application shutdown.
    """
    logger.info("Executing shutdown event: Cleaning up database resources...")
    from .repositories.async_base_repository import cleanup_shared_db_resources
    await cleanup_shared_db_resources()
    logger.info("Database resources cleaned up successfully.")

from .models.async_db import get_database_url

# Get centralized async database URL
DATABASE_URL = get_database_url()

store = RepositoryManager(database_url=DATABASE_URL)
app.state.store = store
app.state.websocket_manager = websocket_manager  # ⬅️ Make websocket_manager available in app state

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

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
                logger.debug(f"Received message from user {user_id}: {message}")
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
from .routes.workspaces import workspaces_router

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)
app.include_router(internal_router, prefix="/api/v1/internal")  # ⬅️ Include internal router
app.include_router(workspaces_router)

# Define the build path for React app
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))



# Mount static files LAST - this ensures the above routes take precedence
# Note: StaticFiles will only handle requests for files that exist
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)