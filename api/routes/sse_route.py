from flask import Blueprint,current_app
from flask_sse import sse
from utils import decode_firebase_token
sse_bp = Blueprint('sse', __name__)



@sse_bp.route('/v1/stream/<token>')
def stream(token):
    store = current_app.store
    success, user_info = decode_firebase_token(token)
    user_id = store.get_user_id_from_email(user_info['email'])
    # Here you might want to implement logic to filter events by user ID
    return sse.stream(user_id=user_id)
