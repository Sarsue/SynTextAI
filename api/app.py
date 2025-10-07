import asyncio
import json
import logging
import os
import time
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    Request,
    status,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

from .firebase_setup import initialize_firebase
from .websocket_manager import websocket_manager
from .repositories import get_repository_manager as _get_repository_manager, RepositoryManager
from .models import init_db, close_db, get_engine, get_session_factory
from .agents import register_agents
from .core.config import settings
from .middleware.auth import AuthMiddleware
from .routes.analytics import router as analytics_router, posthog_middleware
from .utils import utils

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- Environment ---
load_dotenv()

# --- Globals ---
limiter = Limiter(key_func=get_remote_address)
_repo_manager: RepositoryManager | None = None


# ------------------------------
# Lifespan Manager (startup/shutdown)
# ------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown for the app."""
    global _repo_manager
    logger.info("🚀 Starting SynTextAI...")

    # --- Firebase ---
    try:
        initialize_firebase()
        logger.info("🔥 Firebase initialized")
    except Exception:
        logger.exception("Firebase initialization failed")
        raise

    # --- Database & Repository Manager ---
    try:
        await init_db()
        engine = get_engine()
        session_factory = get_session_factory()
        _repo_manager = await _get_repository_manager(engine=engine, session_factory=session_factory)
        app.state.repo_manager = _repo_manager
        logger.info("✅ RepositoryManager initialized successfully")
    except Exception:
        logger.exception("RepositoryManager initialization failed")
        raise

    # --- Attach Middleware (now that repo is ready) ---
    app.add_middleware(AuthMiddleware, repo_manager=_repo_manager)
    logger.info("🔐 AuthMiddleware initialized with RepositoryManager")

    # --- Register Agents ---
    try:
        register_agents()
        logger.info("🧠 Agents registered successfully")
    except Exception:
        logger.exception("Agent registration failed")
        raise

    yield  # 🟢 App runs here

    # ------------------------------
    # Shutdown
    # ------------------------------
    logger.info("🧹 Shutting down SynTextAI...")

    if _repo_manager:
        try:
            await _repo_manager.close()
            logger.info("RepositoryManager closed")
        except Exception:
            logger.exception("Error closing RepositoryManager")

    try:
        await close_db()
        logger.info("Database closed successfully")
    except Exception:
        logger.exception("Error closing database")

    try:
        await websocket_manager.disconnect_all()
        logger.info("All WebSocket connections closed")
    except Exception:
        logger.exception("Error closing WebSocket connections")


# ------------------------------
# FastAPI App Setup
# ------------------------------
app = FastAPI(
    title="SynTextAI API",
    description="AI-powered learning assistant API",
    version="1.0.0",
    max_request_body_size=2 * 1024 * 1024 * 1024,
    lifespan=lifespan,
)

# --- Base Middleware ---
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------
# Middleware Stack
# ------------------------------
@app.middleware("http")
async def add_coop_coep_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response


@app.middleware("http")
async def apply_posthog_middleware(request: Request, call_next):
    return await posthog_middleware(request, call_next)


# ------------------------------
# Dependencies
# ------------------------------
async def get_repository_manager_dep() -> RepositoryManager:
    if _repo_manager is None:
        raise HTTPException(status_code=500, detail="Repository manager not initialized")
    return _repo_manager


# ------------------------------
# WebSocket Handler
# ------------------------------
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    logger.info(f"WebSocket connection attempt for user {user_id}")
    CONNECTION_TIMEOUT = 10.0
    HEARTBEAT_INTERVAL = 30.0
    HEARTBEAT_TIMEOUT = 90.0

    client_info = {
        "user_agent": websocket.headers.get("user-agent", "unknown"),
        "ip": websocket.client.host if websocket.client else "unknown",
        "connected_at": datetime.utcnow().isoformat(),
    }

    try:
        await asyncio.wait_for(websocket.accept(), timeout=CONNECTION_TIMEOUT)
        logger.info(f"WebSocket connection accepted for user {user_id}")

        try:
            auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=CONNECTION_TIMEOUT)
        except asyncio.TimeoutError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        token = auth_data.get("token")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        success, user_info = utils.decode_firebase_token(token)
        if not success or str(user_info.get("user_id")) != user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        logger.info(f"WebSocket authenticated for user {user_id} ({user_info.get('email')})")
        await websocket_manager.connect(user_id, websocket, client_info)

        last_heartbeat = time.time()
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_INTERVAL)
                if message.get("type") == "pong":
                    last_heartbeat = time.time()
                    continue
                if message.get("type") == "ping":
                    await websocket.send_json({"event": "pong", "timestamp": datetime.utcnow().isoformat()})
                    continue
            except asyncio.TimeoutError:
                if time.time() - last_heartbeat > HEARTBEAT_TIMEOUT:
                    raise WebSocketDisconnect()
                await websocket.send_json({"type": "ping", "timestamp": int(time.time())})
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user_id}")
    finally:
        await websocket_manager.disconnect(websocket)
        logger.info(f"WebSocket connection closed for user {user_id}")


# ------------------------------
# Routers
# ------------------------------
from .routes.files import files_router
from .routes.histories import histories_router
from .routes.messages import messages_router
from .routes.subscriptions import subscriptions_router
from .routes.users import users_router

app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)

# ------------------------------
# Static Frontend
# ------------------------------
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")


# ------------------------------
# Run Server
# ------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=3000, reload=False)
