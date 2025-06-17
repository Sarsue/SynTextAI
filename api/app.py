from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from websocket_manager import websocket_manager
from repositories.repository_manager import RepositoryManager
from dotenv import load_dotenv
from firebase_setup import initialize_firebase
import os
import logging
from utils import decode_firebase_token
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

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

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

store = RepositoryManager(database_url=DATABASE_URL)
app.state.store = store

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
            await websocket.close(code=1008)
            return

        token = data.get("token")
        success, user_info = decode_firebase_token(token)
        if not success:
            await websocket.close(code=1008)
            websocket_manager.disconnect(user_id)
            return

        # Add the WebSocket connection to the manager
        await websocket_manager.connect(user_id, websocket)

        # Keep the connection alive
        while True:
            message = await websocket.receive_text()
            # Handle the message (e.g., broadcast to other clients)
    except WebSocketDisconnect:
        websocket_manager.disconnect(user_id)
        logger.info(f"User {user_id} disconnected")

# Import routers after app is set up
from routes.files import files_router
from routes.histories import histories_router
from routes.messages import messages_router
from routes.subscriptions import subscriptions_router
from routes.users import users_router
from routes.analytics import router as analytics_router

# Include routers
app.include_router(files_router)
app.include_router(histories_router)
app.include_router(messages_router)
app.include_router(subscriptions_router)
app.include_router(users_router)
app.include_router(analytics_router)

# Define the build path for React app
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))

# Define a route to serve the index.html for SPA routing
@app.get("/", response_class=HTMLResponse)
@app.get("/app/{rest_of_path:path}", response_class=HTMLResponse)
async def serve_spa_routes(rest_of_path: str = ""):
    index_path = os.path.join(build_path, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="App not found. Build the App first.")
    with open(index_path, "r") as f:
        content = f.read()
    return HTMLResponse(content=content)

# Mount static files LAST - this ensures the above routes take precedence
# Note: StaticFiles will only handle requests for files that exist
app.mount("/", StaticFiles(directory=build_path, html=True), name="static")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)