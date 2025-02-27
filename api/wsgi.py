import eventlet
eventlet.monkey_patch()  # Ensure eventlet patches everything before imports

from app import app , redis_url # Import the Flask app directly from app.py
from websocket_server import socketio

# Initialize SocketIO with Redis message queue
socketio.init_app(app, message_queue=redis_url)  # Make sure the Redis URL is correct

# Expose the Flask app for Gunicorn
if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=3000)
