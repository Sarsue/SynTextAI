from flask import Flask, request, Blueprint, Response,current_app
from utils import decode_firebase_token
# Blueprint for SSE
sse_bp = Blueprint("stream", __name__, url_prefix="/api/v1/stream")

# Function to handle the event stream for a specific user
def event_stream(user_id, redis_client):
    pubsub = redis_client.pubsub()
    channel_name = f"user_{user_id}"
    pubsub.subscribe(channel_name)
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            data = message['data'].decode('utf-8')
            yield f"data: {data}\n\n"

# SSE route
@sse_bp.route('', methods=['GET'])
def stream():
    token = request.args.get('token')
    if token:
        success, user_info = decode_firebase_token(token)
        redis_client = current_app.redis_client  # Access within the Flask app context
        return Response(event_stream(user_info['user_id'], redis_client), content_type='text/event-stream')
    return "User ID is required", 400
