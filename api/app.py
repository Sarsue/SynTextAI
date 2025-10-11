from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .websocket_manager import websocket_manager
from .repositories.repository_manager import RepositoryManager
from dotenv import load_dotenv
from .firebase_setup import initialize_firebase
import os
import logging
from .utils import decode_firebase_token
# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(max_request_body_size= 2 * 1024 * 1024 * 1024)

# Middleware to add COOP and COEP headers
@app.middleware("http")
async def add_coop_coep_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    # response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none" # or "require-corp"
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

        # Add the WebSocket connection to the manager (accept is already called)
        websocket_manager.active_connections[user_id] = websocket
        logger.info(f"WebSocket authenticated and connected for user {user_id}")

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
        websocket_manager.disconnect(user_id)

# Import routers after app is set up
from .routes.files import files_router
from .routes.histories import histories_router
from .routes.messages import messages_router
from .routes.subscriptions import subscriptions_router
from .routes.users import users_router
from .routes.analytics import router as analytics_router, posthog_middleware
from .routes.internal import router as internal_router  # ⬅️ Add internal router

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)
app.include_router(internal_router, prefix="/api/v1/internal")  # ⬅️ Include internal router

# Define the build path for React app
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))



# Mount static files LAST - this ensures the above routes take precedence
# Note: StaticFiles will only handle requests for files that exist
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)