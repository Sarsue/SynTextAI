import asyncio
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from fastapi import (
    FastAPI, 
    WebSocket, 
    WebSocketDisconnect, 
    Request, 
    status
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .websocket_manager import websocket_manager
from .repositories.repository_manager import RepositoryManager
from .firebase_setup import initialize_firebase
from .utils import utils
from .models.async_db import engine, startup as db_startup, shutdown as db_shutdown
from .agents import register_agents

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize FastAPI
app = FastAPI(
    title="SynTextAI API",
    description="API for SynTextAI - AI-powered learning assistant",
    version="1.0.0",
    max_request_body_size=2 * 1024 * 1024 * 1024
)

# Configure rate limiting
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

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

# Initialize repository manager as None, will be set during startup
app.state.store = None

# Import settings
from api.core.config import settings

async def startup_event():
    """Initialize application services."""
    try:
        logger.info("Starting application initialization...")
        
        # Initialize Firebase
        initialize_firebase()
        logger.info("Firebase initialized")
        
        # Initialize database connection
        await db_startup()
        logger.info("Database connection established")
        
        # Initialize repository manager
        from .repositories.repository_manager import get_repository_manager
        repo_manager = get_repository_manager()
        await repo_manager.initialize()
        app.state.store = repo_manager
        logger.info("Repository manager initialized")
        
        # Register agents
        register_agents()
        logger.info("Agents registered")
        
        logger.info("Application startup complete")
        
    except ImportError as e:
        logger.error(f"Import error during application startup: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during application startup: {e}", exc_info=True)
        raise

# Register startup and shutdown event handlers
@app.on_event("startup")
async def on_startup():
    await startup_event()

@app.on_event("shutdown")
async def shutdown_event():
    """Handle application shutdown."""
    logger.info("Shutting down application...")
    
    # Clean up repository manager if it was initialized
    if hasattr(app.state, 'store') and app.state.store is not None:
        try:
            await app.state.store.close()
            logger.info("Repository manager closed")
        except Exception as e:
            logger.error(f"Error closing repository manager: {e}", exc_info=True)
    
    # Clean up database connections
    await db_shutdown()
    logger.info("Database connections closed")
    
    logger.info("Application shutdown complete")

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
    - Subscriptions
    """
    logger.info(f"WebSocket connection attempt for user {user_id}")
    
    # Timeout constants
    CONNECTION_TIMEOUT = 10.0  # seconds to wait for initial setup
    HEARTBEAT_INTERVAL = 30.0  # seconds between heartbeats
    HEARTBEAT_TIMEOUT = 90.0   # seconds before considering connection dead
    
    # Client information
    client_info = {
        'user_agent': websocket.headers.get('user-agent', 'unknown'),
        'ip': websocket.client.host if websocket.client else 'unknown',
        'connected_at': datetime.utcnow().isoformat()
    }
    
    try:
        # Accept the WebSocket connection with timeout
        await asyncio.wait_for(websocket.accept(), timeout=CONNECTION_TIMEOUT)
        logger.info(f"WebSocket connection accepted for user {user_id} from {client_info['ip']}")
        
        # Wait for authentication message with timeout
        try:
            auth_data = await asyncio.wait_for(websocket.receive_json(), timeout=CONNECTION_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"WebSocket authentication timeout for user {user_id}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        except json.JSONDecodeError as e:
            logger.error(f"Invalid auth message format from {user_id}: {e}")
            await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
            return
        except Exception as e:
            logger.error(f"Error receiving auth message from {user_id}: {e}")
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return
        
        # Validate auth message
        if not isinstance(auth_data, dict) or auth_data.get("type") != "auth" or not auth_data.get("token"):
            logger.warning(f"WebSocket auth failed: invalid auth message format from {user_id}")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
            
        token = auth_data["token"]
        logger.debug(f"Validating token for user {user_id}")
        
        # Verify token
        try:
            success, user_info = utils.decode_firebase_token(token)
            if not success or str(user_info.get('user_id')) != user_id:
                logger.warning(f"WebSocket auth failed: invalid token for user {user_id}")
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        except Exception as e:
            logger.error(f"Error validating token for user {user_id}: {e}")
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return
                
        logger.info(f"WebSocket authenticated for user {user_id} ({user_info.get('email')})")
        
        # Register the connection with client info
        client_info.update({
            'email': user_info.get('email'),
            'user_id': user_id
        })
        
        await websocket_manager.connect(user_id, websocket, client_info)
        
        # Send connection confirmation
        await websocket.send_json({
            "event": "connection_established",
            "data": {
                "message": "Connected to WebSocket server",
                "user_id": user_id,
                "timestamp": datetime.utcnow().isoformat(),
                "heartbeat_interval": HEARTBEAT_INTERVAL
            }
        })
        
        # Heartbeat tracking
        last_heartbeat = time.time()
        
        # Main message loop
        while True:
            try:
                # Wait for a message with timeout
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=HEARTBEAT_INTERVAL)
                    
                    # Handle heartbeat pong
                    if message.get("type") == "pong":
                        last_heartbeat = time.time()
                        logger.debug(f"Received pong from {user_id}")
                        continue
                        
                    # Handle subscription messages
                    if message.get("type") == "subscribe" and isinstance(message.get("channels"), list):
                        for channel in message["channels"]:
                            await websocket_manager.subscribe(websocket, channel)
                        await websocket.send_json({
                            "event": "subscription_updated",
                            "data": {
                                "channels": list(websocket_manager.get_connection_metadata(websocket).subscriptions),
                                "timestamp": datetime.utcnow().isoformat()
                            }
                        })
                        continue
                        
                    # Handle unsubscription messages
                    if message.get("type") == "unsubscribe" and isinstance(message.get("channels"), list):
                        for channel in message["channels"]:
                            await websocket_manager.unsubscribe(websocket, channel)
                        await websocket.send_json({
                            "event": "subscription_updated",
                            "data": {
                                "channels": list(websocket_manager.get_connection_metadata(websocket).subscriptions),
                                "timestamp": datetime.utcnow().isoformat()
                            }
                        })
                        continue
                        
                    # Handle ping messages
                    if message.get("type") == "ping":
                        await websocket.send_json({
                            "event": "pong",
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        continue
                        
                    # Echo the message back for testing
                    if message.get("echo", False):
                        await websocket.send_json({
                            "event": "echo",
                            "data": message,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                        continue
                    
                    # Log other messages
                    logger.debug(f"Received message from {user_id}: {json.dumps(message, indent=2)}")
                    
                except asyncio.TimeoutError:
                    # Check if we've missed too many heartbeats
                    time_since_heartbeat = time.time() - last_heartbeat
                    if time_since_heartbeat > HEARTBEAT_TIMEOUT:
                        logger.warning(f"No heartbeat from {user_id} for {time_since_heartbeat:.1f}s, closing connection")
                        raise WebSocketDisconnect()
                        
                    # Send ping to check connection
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": int(time.time())
                    })
                    
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received from {user_id}: {e}")
                await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
                break
            except Exception as e:
                logger.error(f"WebSocket error for user {user_id}: {str(e)}", exc_info=True)
                try:
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                except Exception as close_error:
                    logger.error(f"Error closing WebSocket: {close_error}")
                break
            finally:
                # Ensure any resources are cleaned up
                pass
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user_id}")
    except asyncio.CancelledError:
        logger.info(f"WebSocket connection cancelled for user {user_id}")
        raise
    finally:
        # Clean up the connection
        try:
            await websocket_manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"Error during WebSocket cleanup for {user_id}: {e}", exc_info=True)
            
        logger.info(f"WebSocket connection closed for user {user_id}")

# Import routers after app is set up
from .routes.files import files_router
from .routes.histories import histories_router
from .routes.messages import messages_router

# Health check endpoint removed
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)