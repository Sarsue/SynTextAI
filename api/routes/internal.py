import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from ..dependencies import get_repository_manager
from ..repositories.domain_models import UserInDB
from ..core.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize router with internal API prefix
router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[],
    responses={status.HTTP_404_NOT_FOUND: {"description": "Not found"}},
)

# API Key authentication for internal endpoints
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)) -> bool:
    """Verify the API key for internal endpoints."""
    if not api_key or api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API Key"
        )
    return True

class WorkerNotification(BaseModel):
    """Schema for worker notifications."""
    user_id: str = Field(..., description="ID of the user to notify")
    event_type: str = Field(..., description="Type of event, e.g., 'file_status_update'")
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Data payload for the notification"
    )

@router.post(
    "/notify-client",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive notification from worker and relay to client via WebSocket",
    response_model=Dict[str, str],
    dependencies=[Depends(verify_api_key)]
)
async def notify_client_endpoint(
    request: Request,
    notification: WorkerNotification
) -> Dict[str, str]:
    """
    Internal endpoint for the worker to send status updates or other messages
    to be relayed to the appropriate frontend client via WebSocket.
    
    This endpoint is protected by an API key that must be included in the request headers.
    """
    try:
        # Access WebSocketManager from application state
        websocket_manager = request.app.state.websocket_manager
        
        if not websocket_manager:
            logger.error("WebSocket manager not available in application state")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WebSocket service not available"
            )
        
        # Send the message to the appropriate client
        await websocket_manager.send_message(
            user_id=notification.user_id,
            event_type=notification.event_type,
            data=notification.data
        )
        
        logger.info(
            f"Relayed {notification.event_type} notification to user {notification.user_id}"
        )
        
        return {"status": "success", "message": "Notification relayed to client"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error relaying notification: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to relay notification to client"
        )

class SystemHealthCheck(BaseModel):
    """Schema for system health check response."""
    status: str
    version: str
    database: bool
    cache: bool
    workers: int

@router.get(
    "/health",
    response_model=SystemHealthCheck,
    summary="Check system health status",
    dependencies=[Depends(verify_api_key)]
)
async def health_check(request: Request) -> SystemHealthCheck:
    """
    Check the health status of the system and its dependencies.
    
    Returns a comprehensive health status including database connectivity,
    cache status, and worker availability.
    """
    try:
        # Check database connectivity
        db_ok = False
        try:
            repo_manager = await get_repository_manager()
            async with repo_manager.session_scope() as session:
                # Simple query to check database connectivity
                await session.execute("SELECT 1")
                db_ok = True
        except Exception as e:
            logger.error(f"Database health check failed: {str(e)}", exc_info=True)
        
        # Check cache status (if applicable)
        cache_ok = False
        try:
            # This is a placeholder - implement actual cache check if you have a cache
            cache_ok = True
        except Exception as e:
            logger.error(f"Cache health check failed: {str(e)}", exc_info=True)
        
        # Get worker status (this is a simplified example)
        worker_count = len(getattr(request.app.state, 'workers', {}))
        
        overall_status = "healthy" if all([db_ok, cache_ok, worker_count > 0]) else "degraded"
        
        return SystemHealthCheck(
            status=overall_status,
            version=getattr(settings, "API_VERSION", "1.0.0"),
            database=db_ok,
            cache=cache_ok,
            workers=worker_count
        )
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        return SystemHealthCheck(
            status="unhealthy",
            version=getattr(settings, "API_VERSION", "1.0.0"),
            database=False,
            cache=False,
            workers=0
        )
