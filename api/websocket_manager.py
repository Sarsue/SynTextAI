import json
import logging
from typing import Dict, List, Set, Optional, Any
from fastapi import WebSocket, status
from fastapi.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        # user_id -> set of active connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # connection_id -> user_id mapping
        self.connection_map: Dict[str, str] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        """Register a new WebSocket connection for a user"""
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        
        self.active_connections[user_id].add(websocket)
        self.connection_map[id(websocket)] = user_id
        logger.info(f"New WebSocket connection for user {user_id}")

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection"""
        conn_id = id(websocket)
        if conn_id in self.connection_map:
            user_id = self.connection_map[conn_id]
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]
            del self.connection_map[conn_id]
            logger.info(f"WebSocket disconnected for user {user_id}")

    def active_connections_count(self) -> int:
        """Return the total number of active WebSocket connections across all users"""
        return sum(len(connections) for connections in self.active_connections.values())
        
    async def send_message(self, user_id: str, event_type: str, data: dict):
        """Send a message to all connections for a specific user"""
        if user_id not in self.active_connections:
            return
            
        message = {
            "event": event_type,
            "data": data
        }
        
        for websocket in self.active_connections[user_id]:
            try:
                await websocket.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(websocket)
            except Exception as e:
                logger.error(f"Error sending WebSocket message: {e}")
                self.disconnect(websocket)
    
    async def broadcast(self, event_type: str, data: dict, user_ids: Optional[List[str]] = None):
        """Broadcast a message to multiple users or all connected users"""
        targets = user_ids if user_ids is not None else self.active_connections.keys()
        for user_id in targets:
            await self.send_message(user_id, event_type, data)
    
    async def update_file_status(self, user_id: str, file_id: int, status: str, error: Optional[str] = None):
        """Helper method to send file status updates"""
        data = {
            "file_id": file_id,
            "status": status
        }
        if error:
            data["error_message"] = error
        
        await self.send_message(user_id, "file_status_update", data)
    
    async def notify_file_processed(self, user_id: str, file_id: int, filename: str, success: bool = True, error: Optional[str] = None):
        """Notify that file processing has completed"""
        data = {
            "file_id": file_id,
            "filename": filename,
            "status": "completed" if success else "failed",
            "success": success
        }
        if error:
            data["error"] = error
            
        await self.send_message(user_id, "file_processed", data)

# Global WebSocket manager instance
websocket_manager = WebSocketManager()