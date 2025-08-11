import asyncio
import json
import logging
import os

from dotenv import load_dotenv
from fastapi import (
    FastAPI, 
    WebSocket, 
    WebSocketDisconnect, 
    Request, 
    status
)
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .websocket_manager import websocket_manager
from .repositories.repository_manager import RepositoryManager
from .firebase_setup import initialize_firebase
from .utils import decode_firebase_token
from .models.db import SessionLocal, engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(
    title="SynTextAI API",
    description="API for SynTextAI - AI-powered learning assistant",
    version="1.0.0",
    max_request_body_size=2 * 1024 * 1024 * 1024
)

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

database_config = {
    'dbname': os.getenv("DATABASE_NAME"),
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': os.getenv("DATABASE_PORT"),
}

DATABASE_URL = (
    f"postgresql://{database_config['user']}:{database_config['password']}"
    f"@{database_config['host']}:{database_config['port']}/{database_config['dbname']}"
)

store = RepositoryManager(database_url=DATABASE_URL)
app.state.store = store

# Dependency to get the store
def get_store(request: Request):
    return request.app.state.store

# WebSocket endpoint
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket endpoint for real-time communication with clients.
    
    Handles:
    - Authentication
    - Connection management
    - Message routing
    - Heartbeats
    """
    logger.info(f"WebSocket connection attempt for user {user_id}")
    
    try:
        # Accept the WebSocket connection
        await websocket.accept()
        
        # Wait for authentication message
        try:
            data = await websocket.receive_json()
            if data.get("type") != "auth":
                logger.warning(f"WebSocket connection rejected: missing or invalid auth message")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
                
            token = data.get("token")
            success, user_info = decode_firebase_token(token)
            
            if not success or str(user_info.get('user_id')) != user_id:
                logger.warning(f"WebSocket authentication failed for user {user_id}")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
                
            logger.info(f"WebSocket authenticated for user {user_id} ({user_info.get('email')})")
            
            # Register the connection
            await websocket_manager.connect(user_id, websocket)
            
            # Send connection confirmation
            await websocket.send_json({
                "event": "connection_established",
                "data": {
                    "message": "Connected to WebSocket server",
                    "user_id": user_id
                }
            })
            
            # Main message loop
            while True:
                try:
                    # Wait for a message with timeout
                    try:
                        message = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Send a ping to keep the connection alive
                        await websocket.send_json({"event": "ping"})
                        continue
                        
                    # Handle different message types
                    message_type = message.get("type")
                    
                    if message_type == "ping":
                        # Respond to pings
                        await websocket.send_json({"event": "pong"})
                        
                    elif message_type == "subscribe":
                        # Handle subscription requests (e.g., to specific channels)
                        channel = message.get("channel")
                        # TODO: Implement channel subscription logic
                        await websocket.send_json({
                            "event": "subscribed",
                            "data": {"channel": channel}
                        })
                        
                    # Add more message handlers as needed
                    
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received from user {user_id}")
                    await websocket.send_json({
                        "event": "error",
                        "error": "Invalid JSON format"
                    })
                    
        except WebSocketDisconnect as e:
            logger.info(f"WebSocket disconnected for user {user_id}: {e}")
            
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {str(e)}", exc_info=True)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except:
            pass
    finally:
        # Ensure the connection is properly cleaned up
        websocket_manager.disconnect(websocket)
        logger.info(f"User {user_id} disconnected")

# Import routers after app is set up
from .routes.files import files_router
from .routes.histories import histories_router
from .routes.messages import messages_router

# Register shutdown event handler
@app.on_event("shutdown")
async def shutdown_event():
    """Handle application shutdown."""
    logger.info("Shutting down application...")
    # Clean up WebSocket connections
    await websocket_manager.disconnect_all()
    # Clean up database connections
    SessionLocal.remove()
    engine.dispose()
    logger.info("Application shutdown complete.")

# Health check endpoint
@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint for monitoring and container health checks."""
    try:
        # Test database connection
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected",
            "websockets": {
                "active_connections": websocket_manager.active_connections_count()
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "error": str(e)}
        )
from .routes.subscriptions import subscriptions_router
from .routes.users import users_router
from .routes.analytics import router as analytics_router, posthog_middleware

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)

# Define the build path for React app
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))



# Mount static files LAST - this ensures the above routes take precedence
# Note: StaticFiles will only handle requests for files that exist
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)