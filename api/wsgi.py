# This file will be for Gunicorn only
import eventlet
eventlet.monkey_patch()

from app import app
from websocket_server import socketio

# Initialize SocketIO with the Flask app
socketio.init_app(app, async_mode='eventlet')

# For Gunicorn, we expose the app directly
application = app

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=3000)
