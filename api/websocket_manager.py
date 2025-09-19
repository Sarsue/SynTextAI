import asyncio
import json
import logging
import time
from typing import Dict, List, Set, Optional, Any, Union, Awaitable
from fastapi import WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class ConnectionMetadata:
    """Metadata for a WebSocket connection"""
    def __init__(self, user_id: str, websocket: WebSocket):
        self.user_id = user_id
        self.websocket = websocket
        self.connected_at = time.time()
        self.last_activity = time.time()
        self.subscriptions: Set[str] = set()
        self.client_info: Dict[str, Any] = {}

    def update_activity(self):
        """Update the last activity timestamp"""
        self.last_activity = time.time()

    def __str__(self):
        return f"Connection(user_id={self.user_id}, active_since={self.connected_at})"

class WebSocketManager:
    def __init__(self):
        # user_id -> set of ConnectionMetadata
        self.active_connections: Dict[str, Dict[str, ConnectionMetadata]] = {}
        # connection_id -> ConnectionMetadata mapping
        self.connection_map: Dict[str, ConnectionMetadata] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: WebSocket, client_info: Optional[Dict] = None) -> bool:
        """Register a new WebSocket connection for a user"""
        connection_id = str(id(websocket))
        metadata = ConnectionMetadata(user_id, websocket)
        metadata.client_info = client_info or {}
        
        async with self._lock:
            # Initialize user's connection set if it doesn't exist
            if user_id not in self.active_connections:
                self.active_connections[user_id] = {}
            
            # Add connection to user's connections
            self.active_connections[user_id][connection_id] = metadata
            self.connection_map[connection_id] = metadata
            
        logger.info(f"New WebSocket connection for user {user_id} (connection_id={connection_id})")
        return True

    async def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection"""
        connection_id = str(id(websocket))
        
        async with self._lock:
            if connection_id not in self.connection_map:
                return
                
            metadata = self.connection_map[connection_id]
            user_id = metadata.user_id
            
            # Remove from user's connections
            if user_id in self.active_connections:
                if connection_id in self.active_connections[user_id]:
                    del self.active_connections[user_id][connection_id]
                    if not self.active_connections[user_id]:
                        del self.active_connections[user_id]
            
            # Remove from connection map
            if connection_id in self.connection_map:
                del self.connection_map[connection_id]
            
            logger.info(f"WebSocket disconnected for user {user_id} (connection_id={connection_id})")
            
            # Clean up any other resources
            await self._cleanup_connection(metadata)

    def active_connections_count(self) -> int:
        """Return the total number of active WebSocket connections across all users"""
        return len(self.connection_map)
        
    def get_connection_metadata(self, websocket: WebSocket) -> Optional[ConnectionMetadata]:
        """Get metadata for a connection"""
        connection_id = str(id(websocket))
        return self.connection_map.get(connection_id)
        
    async def _cleanup_connection(self, metadata: ConnectionMetadata):
        """Clean up resources for a connection"""
        # Close the WebSocket if it's still open
        try:
            await metadata.websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        except Exception as e:
            logger.warning(f"Error closing WebSocket: {e}")
            
        # Clean up any subscriptions or other resources
        metadata.subscriptions.clear()
        
    async def send_message(self, user_id: str, message: Union[dict, str, bytes]):
        """Send a message to all connections for a specific user"""
        if not user_id or not message:
            return
            
        if not isinstance(message, (str, bytes)):
            try:
                message = json.dumps(message)
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to serialize message: {e}")
                return
                
        async with self._lock:
            if user_id not in self.active_connections:
                return
                
            dead_connections = []
            
            for connection_id, metadata in list(self.active_connections[user_id].items()):
                try:
                    if isinstance(message, str):
                        await metadata.websocket.send_text(message)
                    else:
                        await metadata.websocket.send_bytes(message)
                    metadata.update_activity()
                except Exception as e:
                    logger.warning(f"Error sending message to {user_id}: {e}")
                    dead_connections.append(metadata.websocket)
            
            # Clean up dead connections
            for websocket in dead_connections:
                await self.disconnect(websocket)
    
    async def broadcast(self, message: Union[dict, str, bytes], user_ids: Optional[List[str]] = None):
        """Broadcast a message to multiple users or all connected users"""
        if not message:
            return
            
        if not isinstance(message, (str, bytes)):
            try:
                message = json.dumps(message)
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to serialize broadcast message: {e}")
                return
                
        target_users = user_ids if user_ids is not None else list(self.active_connections.keys())
        
        for user_id in target_users:
            await self.send_message(user_id, message)
    
    async def subscribe(self, websocket: WebSocket, channel: str) -> bool:
        """Subscribe a connection to a channel"""
        metadata = self.get_connection_metadata(websocket)
        if not metadata:
            return False
            
        metadata.subscriptions.add(channel)
        logger.debug(f"User {metadata.user_id} subscribed to channel {channel}")
        return True
        
    async def unsubscribe(self, websocket: WebSocket, channel: str) -> bool:
        """Unsubscribe a connection from a channel"""
        metadata = self.get_connection_metadata(websocket)
        if not metadata:
            return False
            
        metadata.subscriptions.discard(channel)
        logger.debug(f"User {metadata.user_id} unsubscribed from channel {channel}")
        return True
        
    async def publish(self, channel: str, message: Union[dict, str, bytes]):
        """Publish a message to all subscribers of a channel"""
        if not channel or not message:
            return
            
        if not isinstance(message, (str, bytes)):
            try:
                message = json.dumps(message)
            except (TypeError, ValueError) as e:
                logger.error(f"Failed to serialize publish message: {e}")
                return
                
        async with self._lock:
            dead_connections = []
            
            for user_id, connections in list(self.active_connections.items()):
                for connection_id, metadata in list(connections.items()):
                    if channel in metadata.subscriptions:
                        try:
                            if isinstance(message, str):
                                await metadata.websocket.send_text(message)
                            else:
                                await metadata.websocket.send_bytes(message)
                            metadata.update_activity()
                        except Exception as e:
                            logger.warning(f"Error publishing to {user_id}: {e}")
                            dead_connections.append(metadata.websocket)
            
            # Clean up dead connections
            for websocket in dead_connections:
                await self.disconnect(websocket)
    
    async def disconnect_all(self):
        """Close all active WebSocket connections"""
        logger.info(f"Closing all WebSocket connections ({self.active_connections_count()} total)")
        # Create a list of all connections to avoid modifying the dict during iteration
        all_connections = []
        for connections in self.active_connections.values():
            all_connections.extend(connections.values())
            
        # Close all connections
        for websocket in all_connections:
            try:
                await websocket.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
        
        # Clear all connection tracking
        self.active_connections.clear()
        self.connection_map.clear()
        logger.info("All WebSocket connections closed")
        
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