# api/wsgi.py
import eventlet
eventlet.monkey_patch()

from app import app

# Gunicorn will use this 'application' variable
application = app

if __name__ == "__main__":
    from app import socketio
    socketio.run(app, debug=True, host="0.0.0.0", port=3000)  # For local testing only