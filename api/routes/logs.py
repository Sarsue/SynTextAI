from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id
import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')
logs_bp = Blueprint("logs", __name__, url_prefix="api/v1/logs")



@logs_bp.route('', methods=['POST'])
def capture_logs():
    data = request.json
    log_level = data.get('level', 'info').upper()
    message = data.get('message', '')
    timestamp = data.get('timestamp', '')
    logging.info(f" {log_level} [{timestamp}] {message}")
    return '', 204
