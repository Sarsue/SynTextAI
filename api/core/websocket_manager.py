from fastapi import WebSocket
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        logger.info(f"WebSocket connected for user {user_id}")

    def disconnect(self, user_id: str):
        if user_id not in self.active_connections:
            return

        websocket = self.active_connections.get(user_id)
        # Remove all keys that reference the same websocket (supports alias keys like db user_id + firebase uid)
        keys_to_remove = [k for k, ws in self.active_connections.items() if ws is websocket]
        for k in keys_to_remove:
            try:
                del self.active_connections[k]
            except Exception:
                pass
        logger.info(f"WebSocket disconnected for user {user_id} (removed {len(keys_to_remove)} keys)")

    async def send_message(self, user_id: str, event_type: str, data: dict):
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                # Check if WebSocket is in a valid state
                if websocket.client_state and websocket.client_state.name == 'CONNECTED':
                    await websocket.send_json({"event": event_type, "data": data})
                    logger.debug(f"WebSocket message sent to user {user_id}: {event_type}")
                else:
                    logger.warning(f"WebSocket not connected for user {user_id}, removing from active connections")
                    self.disconnect(user_id)
            except Exception as e:
                logger.error(f"Error sending WebSocket message to user {user_id}: {str(e)}")
                # Remove broken connection
                self.disconnect(user_id)
        else:
            logger.debug(f"No active WebSocket connection for user {user_id}")

    def is_connected(self, user_id: str) -> bool:
        """Check if user has an active WebSocket connection"""
        if user_id in self.active_connections:
            websocket = self.active_connections[user_id]
            try:
                return websocket.client_state and websocket.client_state.name == 'CONNECTED'
            except:
                return False
        return False

websocket_manager = WebSocketManager()