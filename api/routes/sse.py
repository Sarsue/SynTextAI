from flask import Blueprint
from flask_sse import sse

sse_bp = Blueprint('sse', __name__)

@sse_bp.route('/v1/stream')
def stream():
    # Clients can connect to this endpoint for SSE updates
    return sse.stream()
