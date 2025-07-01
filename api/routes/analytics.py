from fastapi import APIRouter, Depends, HTTPException, Request
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import os
import time
import posthog
from sqlalchemy.orm import Session
from api.models.db import get_db
from api.repositories.repository_manager import RepositoryManager
from pydantic import BaseModel, Field

# Initialize PostHog client
posthog.api_key = os.environ.get('POST_HOG_API_KEY', '')
# If you're self-hosting PostHog, uncomment and set this
# posthog.host = 'https://your-instance.posthog.com'

router = APIRouter()

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

@router.post("/analytics")
async def receive_analytics(
    payload: AnalyticsPayload,
    request: Request,
    db: Session = Depends(get_db)
):
    # Extract client info
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "")
    
    # Process analytics events
    for event in payload.events:
        # Add metadata
        event["_meta"] = {
            "ip": client_ip,
            "userAgent": user_agent,
            "receivedAt": datetime.now().isoformat(),
            "sessionId": payload.sessionId
        }
        
        if payload.userId:
            event["_meta"]["userId"] = payload.userId
            # Optionally validate user exists
            # store = RepositoryManager()
            # user_id = store.get_user_id_from_email(payload.userId)  # Assuming userId is an email
            # if not user_id:
            #     raise HTTPException(status_code=404, detail="User not found")
        
        # Send event to PostHog
        try:
            event_type = event.get('type', 'unknown')
            # Convert to PostHog-friendly format
            properties = {
                **event,  # Include all original event properties
                'sessionId': payload.sessionId,
                'ip': client_ip,
                'userAgent': user_agent
            }
            
            # Remove 'type' from properties as it's used as the event name
            if 'type' in properties:
                del properties['type']
                
            # Send to PostHog - use userId as distinct_id if available
            distinct_id = payload.userId or payload.sessionId
            posthog.capture(
                distinct_id=distinct_id,
                event=f"{event_type}_event",
                properties=properties
            )
        except Exception as e:
            print(f"Error sending event to PostHog: {str(e)}")
    
    # In a production system, you would likely:
    # 1. Store events in a database (consider ClickHouse, TimescaleDB, or MongoDB)
    # 2. Queue them in a message broker like Kafka, RabbitMQ, or Redis
    # 3. Process them asynchronously
    
    # For this implementation, we'll log the events and save them to a file
    # (In production, replace this with proper database storage)
    try:
        # Log event types for debugging
        event_types = [event["type"] for event in payload.events]
        print(f"Received {len(payload.events)} analytics events: {event_types}")
        
        # Still save to file as a backup
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"analytics-{timestamp}-{payload.sessionId[:8]}.json"
        
        try:
            with open(f"/tmp/{filename}", "w") as f:
                json.dump(payload.dict(), f, indent=2)
        except Exception as e:
            print(f"Error saving analytics to file: {str(e)}")
        
        return {"status": "success", "eventsProcessed": len(payload.events)}
    
    except Exception as e:
        print(f"Error processing analytics: {str(e)}")
        return {"status": "error", "message": str(e)}


@router.get("/analytics/dashboard")
async def analytics_dashboard(
    db: Session = Depends(get_db)
):
    """
    In a real implementation, this would return analytics dashboard data
    aggregated from your analytics storage system.
    """
    # Placeholder for actual dashboard data
    return {
        "status": "success",
        "data": {
            "message": "Analytics dashboard API endpoint",
            "metrics": {
                "totalSessions": 0,
                "activeUsers": 0,
                "averageSessionDuration": 0,
                "bounceRate": 0,
                "clickEvents": 0,
                "rageClicks": 0,
                "ghostedSessions": 0
            },
            "note": "This is a placeholder. Connect to your analytics storage to get real metrics."
        }
    }
