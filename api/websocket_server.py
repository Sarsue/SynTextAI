from flask_socketio import SocketIO, emit, disconnect
from functools import wraps
import firebase_admin.auth as auth
from flask import request, current_app
import logging
from threading import Lock
import time
from gevent import monkey
monkey.patch_all()

# Add thread synchronization
thread_lock = Lock()

# Initialize SocketIO with better configuration
socketio = SocketIO(
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    async_mode='gevent',
    logger=True,
    engineio_logger=True
)

# Improve connection tracking
user_connections = {}  # Maps user IDs to {sid: timestamp} dict
guest_sids = set()
logger = logging.getLogger(__name__)

def cleanup_stale_connections():
    """Clean up stale connections periodically"""
    with thread_lock:
        current_time = time.time()
        for user_id in list(user_connections.keys()):
            for sid, timestamp in list(user_connections[user_id].items()):
                if current_time - timestamp > 70:  # Connection considered stale after 70 seconds
                    del user_connections[user_id][sid]
                    if not user_connections[user_id]:
                        del user_connections[user_id]
                    logger.info(f"Cleaned up stale connection for user {user_id} sid {sid}")

@socketio.on('connect')
def handle_connect():
    try:
        if 'Authorization' not in request.headers:
            guest_sids.add(request.sid)
            emit('connected', {'status': 'connected', 'authenticated': False})
            return

        token = request.headers['Authorization'].split(' ')[1]
        try:
            decoded_token = auth.verify_id_token(token)
            user_id = decoded_token['uid']
            
            with thread_lock:
                if user_id not in user_connections:
                    user_connections[user_id] = {}
                user_connections[user_id][request.sid] = time.time()
            
            logger.info(f"Authenticated user {user_id} connected with sid {request.sid}")
            emit('connected', {'status': 'connected', 'authenticated': True})
            
        except Exception as e:
            logger.error(f"Auth token verification failed: {str(e)}")
            guest_sids.add(request.sid)
            emit('connected', {'status': 'connected', 'authenticated': False})
            
    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        disconnect()

@socketio.on('disconnect')
def handle_disconnect():
    try:
        with thread_lock:
            # Clean up user connections
            for user_id, connections in list(user_connections.items()):
                if request.sid in connections:
                    del connections[request.sid]
                    if not connections:
                        del user_connections[user_id]
                    logger.info(f"User {user_id} disconnected from sid {request.sid}")
                    break
            
            # Clean up guest connections
            guest_sids.discard(request.sid)
            
        logger.info(f"Client {request.sid} disconnected")
        
    except Exception as e:
        logger.error(f"Disconnect error: {str(e)}")

def notify_user(user_id, event_type, data):
    """Send notification to specific user"""
    try:
        with thread_lock:
            if user_id in user_connections:
                for sid in user_connections[user_id].keys():
                    try:
                        emit(event_type, data, room=sid)
                        logger.debug(f"Notification sent to user {user_id} sid {sid}")
                    except Exception as e:
                        logger.error(f"Error sending notification to {sid}: {str(e)}")
    except Exception as e:
        logger.error(f"Error in notify_user: {str(e)}")

# Add periodic cleanup
@socketio.on('ping')
def handle_ping():
    cleanup_stale_connections()
    emit('pong')

@socketio.on_error()
def error_handler(e):
    logger.error(f"SocketIO error: {str(e)}") 