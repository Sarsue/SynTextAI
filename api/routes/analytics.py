from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import os
import time
import logging
import posthog
from fastapi import APIRouter, Depends, Request, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import get_repository_manager
from ..models.user import UserInDB
from ..middleware.auth import get_current_user

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize PostHog client
posthog.api_key = os.environ.get('POST_HOG_API_KEY', '')
# If you're self-hosting PostHog, uncomment and set this
# posthog.host = 'https://your-instance.posthog.com'

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

# PostHog middleware to track API requests
# This function should be registered in app.py as middleware, not on the router
async def posthog_middleware(request: Request, call_next):
    # Capture request start time
    start_time = time.time()
    path = request.url.path
    method = request.method
    
    # Process the request
    response = await call_next(request)
    
    # Calculate processing time
    process_time = time.time() - start_time
    status_code = response.status_code
    
    # Don't track analytics endpoints to avoid recursion
    if not path.startswith("/analytics"):
        try:
            # Get user ID from headers or session if available
            # This is a placeholder - replace with your auth logic
            user_id = request.headers.get("X-User-Id", "anonymous")
            
            # Track API request in PostHog
            posthog.capture(
                distinct_id=user_id,
                event="api_request",
                properties={
                    "path": path,
                    "method": method,
                    "status_code": status_code,
                    "duration_ms": round(process_time * 1000),
                    "user_agent": request.headers.get("user-agent", "")
                }
            )
        except Exception as e:
            print(f"Error in PostHog middleware: {str(e)}")
    
    return response

class AnalyticsEvent(BaseModel):
    type: str
    timestamp: int
    path: str
    # Other fields will be accepted as arbitrary JSON

class AnalyticsPayload(BaseModel):
    events: List[Dict[str, Any]]
    sessionId: str
    userId: Optional[str] = None
    timestamp: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))

@router.post("/events")
async def receive_analytics(
    payload: AnalyticsPayload,
    request: Request,
    _user: UserInDB = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Receive and process analytics events from the client.
    
    This endpoint accepts analytics events in batches and processes them asynchronously.
    Events are forwarded to PostHog for analysis and may be stored in the database.
    
    Args:
        payload: The analytics payload containing events to process
        request: The incoming HTTP request
        user: The authenticated user (optional, as some events may be from unauthenticated users)
        
    Returns:
        Dict with status and count of processed events
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        # Extract client info
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")
        
        # Process events in a transaction
        async with repo_manager.session_scope() as session:
            # Get repositories if needed
            # user_repo = await repo_manager.user_repo
            
            processed_count = 0
            
            for event in payload.events:
                try:
                    # Add metadata
                    event_meta = {
                        "ip": client_ip,
                        "user_agent": user_agent,
                        "received_at": datetime.utcnow().isoformat(),
                        "session_id": payload.sessionId,
                        "user_id": str(user.id) if user else None
                    }
                    
                    # Add metadata to event
                    if "_meta" not in event:
                        event["_meta"] = {}
                    event["_meta"].update(event_meta)
                    
                    # Get event type and properties for PostHog
                    event_type = event.get('type', 'unknown')
                    properties = {
                        **{k: v for k, v in event.items() if k != 'type'},
                        'session_id': payload.sessionId,
                        'user_id': str(user.id) if user else None,
                        'ip': client_ip,
                        'user_agent': user_agent
                    }
                    
                    # Send to PostHog
                    distinct_id = str(user.id) if user else payload.sessionId
                    posthog.capture(
                        distinct_id=distinct_id,
                        event=f"{event_type}_event",
                        properties=properties
                    )
                    
                    # Here you could also store events in your database if needed
                    # Example:
                    # await analytics_repo.store_event(
                    #     event_type=event_type,
                    #     properties=event,
                    #     user_id=user.id if user else None,
                    #     session_id=payload.sessionId,
                    #     session=session
                    # )
                    
                    processed_count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing event: {str(e)}", exc_info=True)
                    # Continue with next event even if one fails
            
            # Log successful processing
            logger.info(
                f"Processed {processed_count}/{len(payload.events)} analytics events",
                extra={
                    "user_id": str(user.id) if user else None,
                    "session_id": payload.sessionId,
                    "event_count": len(payload.events),
                    "processed_count": processed_count
                }
            )
            
            # Commit the transaction
            await session.commit()
            
            return {
                "status": "success",
                "message": f"Processed {processed_count} events",
                "processed_count": processed_count,
                "total_events": len(payload.events)
            }
            
    except Exception as e:
        logger.error(f"Error in receive_analytics: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing analytics events"
        )

@router.get("/dashboard")
async def analytics_dashboard(
    request: Request,
    _user: UserInDB = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get analytics dashboard data for the authenticated user.
    
    This endpoint returns aggregated analytics data that can be displayed
    on an admin or user dashboard. The data is fetched from the analytics
    storage system (PostHog) and may be combined with data from the database.
    
    Args:
        request: The incoming HTTP request
        user: The authenticated user
        
    Returns:
        Dict containing aggregated analytics data for the dashboard
    """
    try:
        # Get repository manager
        repo_manager = await get_repository_manager()
        
        # In a real implementation, you would fetch data from your analytics storage
        # For example, using PostHog's API or querying your analytics database
        
        # Example of fetching data from PostHog
        # This is a placeholder - replace with actual PostHog API calls
        dashboard_data = {
            "active_users": {
                "today": 0,  # Replace with actual data
                "this_week": 0,  # Replace with actual data
                "this_month": 0,  # Replace with actual data
                "ghostedSessions": 0
            },
            "note": "This is a placeholder. Connect to your analytics storage to get real metrics."
        }
        
        return dashboard_data
        
    except Exception as e:
        logger.error(f"Error fetching analytics dashboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while fetching analytics data"
        )

# Export the router
analytics_router = router
