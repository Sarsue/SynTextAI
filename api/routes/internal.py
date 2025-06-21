from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from typing import Dict, Any

router = APIRouter()

class WorkerNotification(BaseModel):
    user_id: str
    event_type: str  # e.g., "file_status_update"
    data: Dict[str, Any] # This will be the 'data' object for the frontend, e.g., {"file_id": 1, "status": "processed"}

@router.post(
    "/notify-client",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive notification from worker and relay to client via WebSocket",
    tags=["Internal"]
)
async def notify_client_endpoint(
    request: Request,
    notification: WorkerNotification
):
    """
    Internal endpoint for the worker to send status updates or other messages
    to be relayed to the appropriate frontend client via WebSocket using the standard event/data structure.
    """
    try:
        # Access WebSocketManager from application state
        # This assumes WebSocketManager is attached to app.state in your main.py
        websocket_manager = request.app.state.websocket_manager
        
        await websocket_manager.send_message(
            user_id=notification.user_id,
            event_type=notification.event_type,
            data=notification.data
        )
        return {"message": "Notification relayed"}
    except Exception as e:
        # Log the error appropriately in a real application
        print(f"Error in /notify-client: {e}") # Basic logging for now
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to relay notification to client"
        )

# You might want to add authentication to this internal endpoint later,
# e.g., using a shared secret or an internal API key, to ensure only the worker can call it.
