import eventlet
eventlet.monkey_patch()

from flask_socketio import SocketIO, emit
import logging
import time
from utils import get_user_id

# Initialize SocketIO (independent of Flask app)
socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet', logger=True)

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Track connections
user_connections = {}  # Maps user_id -> {sid: timestamp}
guest_sids = set()

def cleanup_stale_connections():
    """Periodically remove stale connections"""
    current_time = time.time()
    stale_sids = []

    for user_id, connections in list(user_connections.items()):
        for sid, timestamp in list(connections.items()):
            if current_time - timestamp > 70:  # 70-second timeout
                stale_sids.append((user_id, sid))

    for user_id, sid in stale_sids:
        del user_connections[user_id][sid]
        if not user_connections[user_id]:
            del user_connections[user_id]
        logger.info(f"Cleaned up stale connection: user={user_id}, sid={sid}")

def background_cleanup():
    """Runs periodic cleanup in the background"""
    while True:
        eventlet.sleep(30)  # Run cleanup every 30 seconds
        cleanup_stale_connections()

eventlet.spawn(background_cleanup)  # Start cleanup task

@socketio.on('connect')
def handle_connect():
    try:
        token = request.headers.get('Authorization')
        if not token:
            guest_sids.add(request.sid)
            emit('connected', {'status': 'connected', 'authenticated': False})
            return
        
        token = token.split(' ')[1]
        status, decoded_token = get_user_id(token)
        user_id = decoded_token['user_id']

        if user_id not in user_connections:
            user_connections[user_id] = {}
        user_connections[user_id][request.sid] = time.time()

        logger.info(f"User {user_id} connected (sid={request.sid})")
        emit('connected', {'status': 'connected', 'authenticated': True})
    
    except Exception as e:
        logger.error(f"Connection error: {e}")
        guest_sids.add(request.sid)
        emit('connected', {'status': 'connected', 'authenticated': False})

@socketio.on('disconnect')
def handle_disconnect():
    """Handles client disconnection"""
    for user_id, connections in list(user_connections.items()):
        if request.sid in connections:
            del connections[request.sid]
            if not connections:
                del user_connections[user_id]
            logger.info(f"User {user_id} disconnected (sid={request.sid})")
            break

    guest_sids.discard(request.sid)
    logger.info(f"Client {request.sid} disconnected")

def notify_user(user_id, event_type, data):
    """Send real-time notification to a specific user"""
    if user_id in user_connections:
        for sid in user_connections[user_id].keys():
            try:
                emit(event_type, data, room=sid)
                logger.debug(f"Sent {event_type} to user {user_id} (sid={sid})")
            except Exception as e:
                logger.error(f"Notification error: {e}")


@socketio.on('ping')
def handle_ping():
    cleanup_stale_connections()
    emit('pong')

@socketio.on_error()
def error_handler(e):
    logger.error(f"SocketIO error: {e}")
