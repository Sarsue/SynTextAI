from flask import Flask, request, Blueprint, current_app, Response
import redis
import json

# Initialize the Blueprint
sse_bp = Blueprint("stream", __name__, url_prefix="/api/v1/stream")

# Access the Redis client from Flask's app context
def get_redis_client():
    return current_app.redis_client

# Function to handle the event stream for a specific user
def event_stream(user_id):
    # Get the Redis client
    redis_client = get_redis_client()
    
    # Subscribe to the user's channel
    pubsub = redis_client.pubsub()
    channel_name = f"user_{user_id}"
    pubsub.subscribe(channel_name)
    
    # Listen for messages from the Redis channel
    for message in pubsub.listen():
        if message['type'] == 'message':
            # Decode and yield the message in SSE format
            data = message['data'].decode('utf-8')
            yield f"data: {data}\n\n"  # Proper SSE format (data: <message>\n\n)

# SSE Route to handle incoming connections
@sse_bp.route('', methods=['GET'])
def stream():
    # actuay google / external user id 
    user_id = request.args.get('user_id')  # Get the user_id from the query parameters
    if user_id:
        # Log user connection (optional)
        print(f"User {user_id} connected to SSE stream.")
        
        # Return the event stream with proper headers for SSE
        return Response(event_stream(user_id), content_type='text/event-stream')
    
    # If no user_id is provided, return a bad request response
    return "User ID is required", 400
