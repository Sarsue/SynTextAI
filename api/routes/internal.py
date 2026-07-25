from fastapi import APIRouter, HTTPException, Request, status, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional, List

from api.core.utils import get_user_id
from api.repositories.repository_manager import RepositoryManager
from api.workflows.tasks import run_query_pipeline

router = APIRouter()

class WorkerNotification(BaseModel):
    user_id: str
    event_type: str  # e.g., "file_status_update"
    data: Dict[str, Any] # This will be the 'data' object for the frontend, e.g., {"file_id": 1, "status": "processed"}


class EvalQueryRequest(BaseModel):
    message: str
    workspace_id: Optional[int] = None
    file_id: Optional[int] = None
    language: str = "English"
    comprehension_level: str = "beginner"
    formatted_history: str = ""


@router.post(
    "/eval/query",
    status_code=status.HTTP_200_OK,
    summary="Run retrieval+generation for evaluation (no DB writes)",
    tags=["Internal"],
)
async def eval_query_endpoint(
    request: Request,
    payload: EvalQueryRequest,
    authorization: str = Header(None),
):
    """Internal eval endpoint to run a single query against a workspace.

    Notes:
    - Authenticated like other endpoints (Firebase bearer token via Authorization header)
    - Does not create chat history or store messages
    - Returns model response + retrieval context for scoring
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized")

    success, user_info = get_user_id(authorization)
    if not success:
        raise HTTPException(status_code=401, detail="Unauthorized")

    store: RepositoryManager = request.app.state.store
    user_id = await store.user_repo.get_user_id_from_email(user_info["email"])
    if not user_id:
        raise HTTPException(status_code=404, detail="User not found")

    result = await run_query_pipeline(
        user_id=user_id,
        message=payload.message,
        language=payload.language,
        comprehension_level=payload.comprehension_level,
        formatted_history=payload.formatted_history or "",
        workspace_id=payload.workspace_id,
        file_id=payload.file_id,
    )

    return {
        "response": result.get("response"),
        "context_chunks": result.get("context_chunks", []),
        "mode": result.get("mode"),
        "rewritten_query": result.get("rewritten_query"),
        "expanded_terms": result.get("expanded_terms", []),
    }

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
