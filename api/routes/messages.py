from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id

from llm_service import get_text_embedding
from tasks import process_query_data
import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')


messages_bp = Blueprint("messages", __name__, url_prefix="api/v1/messages")


def get_id_helper(store, success, user_info):
    if not success:
        return jsonify(user_info), 401

    # Now you can use the user_info dictionary to allow or restrict actions
    name = user_info['name']
    email = user_info['email']
    id = store.get_user_id_from_email(email)
    return id

@messages_bp.route('', methods=['POST'])
def create_message():
    store = current_app.store
    message_list = []
    message = request.args.get('message')
    language = request.args.get('language', 'English')  # Default to 'English' if not provided
    comprehension_level = request.args.get('comprehensionLevel', 'beginner')  # Set a default value if desired
    token = request.headers.get('Authorization')
    
    success, user_info = get_user_id(token)
    id = get_id_helper(store, success, user_info)
    history_id = int(request.args.get('history-id'))

    # Save the user message to the history
    user_request = store.add_message(
        content=message, sender='user', user_id=id, chat_history_id=history_id)
    message_list.append(user_request)
    task = process_query_data.delay(id, history_id, message, language,comprehension_level)
    logging.info(f"Enqueued Task {task.id}  for processing {message}")
    return message_list
   
  
