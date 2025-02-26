from flask_socketio import SocketIO, emit, disconnect
from functools import wraps
import firebase_admin.auth as auth
from flask import request

socketio = SocketIO(cors_allowed_origins="*")
user_sid_map = {}  # Maps user IDs to their Socket IDs
guest_sids = set()  # Track guest session IDs

@socketio.on('connect')
def handle_connect():
    auth_token = None
    if 'Authorization' in request.headers:
        auth_token = request.headers['Authorization'].split(' ')[1]
        try:
            decoded_token = auth.verify_id_token(auth_token)
            user_id = decoded_token['uid']
            user_sid_map[user_id] = request.sid
            emit('connected', {'status': 'connected', 'authenticated': True})
        except:
            # Invalid token, treat as guest
            guest_sids.add(request.sid)
            emit('connected', {'status': 'connected', 'authenticated': False})
    else:
        # No token, treat as guest
        guest_sids.add(request.sid)
        emit('connected', {'status': 'connected', 'authenticated': False})

@socketio.on('disconnect')
def handle_disconnect():
    # Remove from user mapping if authenticated user
    for user_id, sid in user_sid_map.items():
        if sid == request.sid:
            del user_sid_map[user_id]
            break
    
    # Remove from guest set if guest
    guest_sids.discard(request.sid)

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