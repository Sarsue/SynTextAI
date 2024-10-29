from flask import Blueprint, request, jsonify, current_app
from utils import get_user_id

histories_bp = Blueprint("histories", __name__, url_prefix="api/v1/histories")


def get_id_helper(store, success, user_info):
    if not success:
        return jsonify(user_info), 401

    # Now you can use the user_info dictionary to allow or restrict actions
    name = user_info['name']
    email = user_info['email']
    id = store.get_user_id_from_email(email)
    return id


@histories_bp.route('', methods=['POST'])
def create_history():
    store = current_app.store
    title = request.args.get('title')
    token = request.headers.get('Authorization')
    success, user_info = get_user_id(token)
    id = get_id_helper(store, success, user_info)
    history = store.add_chat_history(title, id)
    return history


@histories_bp.route('', methods=['GET'])
def get_history_messages():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        id = get_id_helper(store, success, user_info)
        # message list is history and the messages in it
        message_list = store.get_all_user_chat_histories(id)
        return message_list
    except Exception as e:
        print(str(e))




@histories_bp.route('', methods=['GET'])
def get_specific_history_messages():
    store = current_app.store
    token = request.headers.get('Authorization')
    success, user_info = get_user_id(token)
    id = get_id_helper(store, success, user_info)
    history_id = int(request.args.get('history-id'))
    message_list = store.get_messages_for_chat_history(id, history_id)
    return message_list


@histories_bp.route('', methods=['DELETE'])
def delete_specific_history_messages():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        id = get_id_helper(store, success, user_info)
        history_id = int(request.args.get('history-id'))

        # Delete the chat history and messages
        store.delete_chat_history(id, history_id)

        # Return a success response with the deleted history ID
        return jsonify({'message': 'History deleted successfully', 'deletedHistoryId': history_id}), 200

    except Exception as e:
        # Handle exceptions and return an error response
        print(str(e))
        return jsonify({'error': str(e)}), 500


@histories_bp.route('/all', methods=['DELETE'])
def delete_all_user_histories():
    try:
        store = current_app.store
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        id = get_id_helper(store, success, user_info)
        store.delete_all_user_histories(id)

        # Return a simple success response
        return jsonify({'message': 'All histories deleted successfully'}), 200

    except Exception as e:
        # Handle exceptions and return an error response
        print(str(e))
        return jsonify({'error': str(e)}), 500
