from flask import Blueprint, Response, current_app
import redis
import json
from utils import decode_firebase_token
sse_bp = Blueprint('sse', __name__)

# Connect to Redis
redis_client = redis.StrictRedis.from_url(current_app.config['REDIS_URL'])

@sse_bp.route('/v1/stream/<token>')
def stream(token):
    # Decode the token to get user info
    store = current_app.store
    success, user_info = decode_firebase_token(token)
    
    if not success:
        return Response(status=401)

    user_id = store.get_user_id_from_email(user_info['email'])

    def generate_events():
        pubsub = redis_client.pubsub()
        pubsub.subscribe(user_id)  # Subscribe to the user's specific channel

        try:
            for message in pubsub.listen():
                if message['type'] == 'message':
                    # SSE event format: "data: <message>\n\n"
                    yield f"data: {message['data'].decode()}\n\n"
        finally:
            pubsub.unsubscribe(user_id)

    return Response(generate_events(), content_type='text/event-stream')
