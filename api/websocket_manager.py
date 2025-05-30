from fastapi import WebSocket
from typing import Dict, List

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            del self.active_connections[user_id]

    async def send_message(self, user_id: str, event_type: str, data: dict):
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            await websocket.send_json({"event": event_type, "data": data})

websocket_manager = WebSocketManager()