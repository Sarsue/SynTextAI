from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id
from web_link_handler import process_newsletter_link
from query_processor import process, summarize
from llm_service import process_content  
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
    token = request.headers.get('Authorization')
    success, user_info = get_user_id(token)
    id = get_id_helper(store, success, user_info)
    history_id = int(request.args.get('history-id'))

    # process context first
    # use history and current query
    # what is it looking for 
    formatted_history = store.format_user_chat_history(history_id, id)

    user_request = store.add_message(
        content=message, sender='user', user_id=id, chat_history_id=history_id)

    message_list.append(user_request)
   
    # context is information retrieval or summarize with language detected
    prompt = f"""
        This is a conversation between a user and an assistant. Use the context from the history and the latest message to provide a thoughtful response.

        ### User's Message:
        {message}

        ### Current Conversation History:
        {formatted_history}
        """

    response = process_content(prompt)
    bot_response = store.add_message(
            content=response, sender='bot', user_id=id, chat_history_id=history_id)

    message_list.append(bot_response)
    return message_list

