import eventlet
eventlet.monkey_patch()

from fastapi import FastAPI, WebSocket, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi import Request
from websocket_manager import websocket_manager
from docsynth_store import DocSynthStore
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
from redis import Redis, ConnectionPool, StrictRedis
from celery import Celery
from urllib.parse import quote
import os
import logging
import time
from utils import get_user_id, decode_firebase_token
from dotenv import load_dotenv
from kombu.utils.url import safequote
import ssl
# Load environment variables
load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Initialize FastAPI
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Track connections
user_connections = {}  # Maps user_id -> {sid: timestamp}

# Initialize Firebase and Redis
initialize_firebase()

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

store = DocSynthStore(database_url=DATABASE_URL)
app.state.store = store

redis_username = os.getenv('REDIS_USERNAME')
redis_pwd = os.getenv('REDIS_PASSWORD')
redis_host = os.getenv('REDIS_HOST')
redis_port = os.getenv('REDIS_PORT')


ssl_cert_path = os.path.join(BASE_DIR, "config", "ca-certificate.crt")

# Redis connection pool for Celery with SSL configuration
redis_url = (
    f"rediss://:{safequote(redis_pwd)}@{redis_host}:{redis_port}/0"
)


# Initialize Celery
celery = Celery(
    "__name__",
    broker=redis_url,
    backend=redis_url,
    redbeat_redis_url=redis_url,
    broker_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
    redis_backend_use_ssl={"ssl_cert_reqs": ssl.CERT_NONE},
)

# Celery configuration
celery.conf.update({
        'broker_url': redis_url,
        'result_backend': redis_url,
        'task_time_limit': 900,
        'task_soft_time_limit': 600,
        'worker_prefetch_multiplier': 1,
        'broker_connection_retry_on_startup': True,
        'broker_connection_max_retries': None,
})


build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../build"))
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")

# Import routers after app is set up
from routes.files import files_router
from routes.histories import histories_router
from routes.messages import messages_router
from routes.subscriptions import subscriptions_router
from routes.users import users_router

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)

@app.get("/")
async def serve_react_app():
    index_path = os.path.join(build_path, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="App not found. Build the App first.")
    return FileResponse(index_path)

# Serve other static files (CSS, JS, images, etc.)
@app.get("/{path:path}")
async def serve_static_file(path: str):
    file_path = os.path.join(build_path, path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    else:
        # If the file doesn't exist, serve the index.html (for React Router)
        index_path = os.path.join(build_path, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        else:
            raise HTTPException(status_code=404, detail="File not found.")

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    try:
        # Authenticate the user
        data = await websocket.receive_json()
        if data.get("type") != "auth":
            await websocket.close(code=1008)
            return

        token = data.get("token")
        success, user_info = decode_firebase_token(token)
        if not success:
            await websocket.close(code=1008)
            return

        # Add the WebSocket connection to the manager
        await websocket_manager.connect(user_id, websocket)

        # Keep the connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(user_id)
        logger.info(f"User {user_id} disconnected")

@app.get("/health")
async def health_check():
    try:
        redis_client.ping()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)