from flask import Flask, request, Blueprint
from flask_sse import sse


sse_bp = Blueprint("stream", __name__, url_prefix="api/v1/stream")

# This route will handle the incoming SSE connections from clients.
@sse_bp.route('', methods=['GET'])  # This will be /api/v1/stream (without the need to repeat the prefix)
def stream():
    user_id = request.args.get('user_id')  # Get the user_id from the query parameters
    if user_id:
        # Log user connection (optional for debugging)
        print(f"User {user_id} connected to SSE stream.")
        
        # Flask-SSE automatically keeps the connection open for SSE
        # You can use Flask-SSE's `sse.stream()` to allow clients to listen for events
        return sse.stream()  # Keeps the connection open and listens for messages
    
    # If no user_id is provided, return a bad request response
    return "User ID is required", 400
