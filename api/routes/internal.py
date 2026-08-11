import hmac
import logging
import os

from fastapi import APIRouter, HTTPException, Request, status, Header
from pydantic import BaseModel
from typing import Dict, Any, Optional, List

from api.core.utils import get_user_id
from api.repositories.repository_manager import RepositoryManager
from api.workflows.tasks import run_query_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared with the worker. Read at call time rather than at import, so a test
# can set it without reimporting the module.
INTERNAL_SECRET_ENV = "INTERNAL_API_SECRET"


def _assert_from_worker(supplied: Optional[str]) -> None:
    """Refuse anyone who cannot prove they are the worker.

    Fails closed when the secret is not configured. The alternative, treating
    "no secret set" as "allow everyone", would mean one missing environment
    variable silently reopens the hole, and nothing would say so. A deploy
    without INTERNAL_API_SECRET loses the fallback notifications instead, which
    only matter while Redis is down, and says so in the log.
    """
    expected = os.getenv(INTERNAL_SECRET_ENV, "")
    if not expected:
        logger.error(
            "%s is not set, so worker notifications are being refused. "
            "Set it in the environment of both the API and the worker.",
            INTERNAL_SECRET_ENV,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal notifications are not configured.",
        )
    # compare_digest rather than ==, so the comparison does not finish early on
    # the first wrong character and leak the secret one byte at a time.
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not permitted.",
        )

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
    notification: WorkerNotification,
    x_internal_secret: Optional[str] = Header(None),
):
    """
    Internal endpoint for the worker to send status updates or other messages
    to be relayed to the appropriate frontend client via WebSocket using the standard event/data structure.

    Only the fallback now. The worker announces over Redis first and comes here
    when that publish did not go out; see core/events.py.

    It used to take anyone's word for it. "Internal" described the intent and
    nothing enforced it: the container publishes port 3000, the router is
    mounted on the same public prefix as everything else, and this endpoint
    takes a user id and arbitrary event data. Anybody who could reach the API
    could push whatever they liked into any signed-in customer's browser.
    """
    _assert_from_worker(x_internal_secret)
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
    except HTTPException:
        # A 403 or 404 is this endpoint's answer, not a failure.
        # The broad handler below would turn a refusal into an
        # internal error, which reads as the app being broken and
        # hides the denial from logs and status codes alike.
        raise
    except Exception as e:
        # Log the error appropriately in a real application
        print(f"Error in /notify-client: {e}") # Basic logging for now
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to relay notification to client"
        )
