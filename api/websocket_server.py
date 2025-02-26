from flask_socketio import SocketIO, emit, disconnect
from functools import wraps
import firebase_admin.auth as auth
from flask import request
import logging

# Initialize with longer ping timeout and ping interval
socketio = SocketIO(
    cors_allowed_origins="*",
    ping_timeout=60,  # Increase ping timeout
    ping_interval=25  # Increase ping interval
)

user_sid_map = {}  # Maps user IDs to their Socket IDs
guest_sids = set()  # Track guest session IDs
logger = logging.getLogger(__name__)

def authenticated_only(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'Authorization' not in request.headers:
            disconnect()
            return False
        try:
            token = request.headers['Authorization'].split(' ')[1]
            auth.verify_id_token(token)
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            disconnect()
            return False
    return wrapped

@socketio.on('connect')
def handle_connect():
    try:
        auth_token = None
        if 'Authorization' in request.headers:
            auth_token = request.headers['Authorization'].split(' ')[1]
            try:
                decoded_token = auth.verify_id_token(auth_token)
                user_id = decoded_token['uid']
                
                # Clean up any existing connection for this user
                if user_id in user_sid_map:
                    old_sid = user_sid_map[user_id]
                    if old_sid != request.sid:
                        socketio.close_room(old_sid)
                
                user_sid_map[user_id] = request.sid
                logger.info(f"Authenticated user {user_id} connected with sid {request.sid}")
                emit('connected', {'status': 'connected', 'authenticated': True})
            except Exception as e:
                logger.error(f"Auth token verification failed: {str(e)}")
                guest_sids.add(request.sid)
                emit('connected', {'status': 'connected', 'authenticated': False})
        else:
            guest_sids.add(request.sid)
            emit('connected', {'status': 'connected', 'authenticated': False})
    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        disconnect()

@socketio.on('disconnect')
def handle_disconnect():
    try:
        # Remove from user mapping if authenticated user
        for user_id, sid in list(user_sid_map.items()):  # Create a copy of items
            if sid == request.sid:
                del user_sid_map[user_id]
                logger.info(f"User {user_id} disconnected")
                break
        
        # Remove from guest set if guest
        guest_sids.discard(request.sid)
        logger.info(f"Client {request.sid} disconnected")
    except Exception as e:
        logger.error(f"Disconnect error: {str(e)}")

@socketio.on_error()
def error_handler(e):
    logger.error(f"SocketIO error: {str(e)}")

def notify_user(user_id, event_type, data):
    """
    Send notification to specific user
    event_type: 'file_processed' or 'message_received'
    """
    if user_id in user_sid_map:
        emit(event_type, data, room=user_sid_map[user_id])

def notify_guest(sid, event_type, data):
    """
    Send notification to guest user
    """
    if sid in guest_sids:
        emit(event_type, data, room=sid) 