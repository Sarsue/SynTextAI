import eventlet
eventlet.monkey_patch()  # Ensure eventlet patches everything before imports

from app import flask_app, redis_url
from websocket_server import socketio

# Initialize SocketIO with Redis message queue
socketio.init_app(flask_app, message_queue=redis_url)

# Expose the Flask app for Gunicorn
app = flask_app
