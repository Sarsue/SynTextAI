from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect
from dotenv import load_dotenv
import os
import logging

from websocket_manager import websocket_manager
from docsynth_store import DocSynthStore
from firebase_setup import initialize_firebase
from utils import decode_firebase_token

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Firebase
initialize_firebase()

# Database Config
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

# Mount static files
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend/build"))
app.mount("/", StaticFiles(directory=build_path, html=True), name="frontend")

# WebSocket route
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        if not isinstance(data, dict) or data.get("type") != "auth":
            await websocket.close(code=1008)
            return

        token = data.get("token")
        success, user_info = decode_firebase_token(token)
        if not success:
            await websocket.close(code=1008)
            return

        await websocket_manager.connect(user_id, websocket)

        while True:
            message = await websocket.receive_text()
            logger.info(f"Received from {user_id}: {message}")

    except WebSocketDisconnect:
        websocket_manager.disconnect(user_id)
        logger.info(f"User {user_id} disconnected")

# Health check route
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Run the application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
