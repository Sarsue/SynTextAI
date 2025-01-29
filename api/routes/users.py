from flask import Blueprint, request, jsonify, current_app
from utils import decode_firebase_token, get_user_id

users_bp = Blueprint("users", __name__, url_prefix="api/v1/users")


def get_id_helper(store, success, user_info):
    if not success:
        return jsonify(user_info), 401

    email = user_info['email']
    id = store.get_user_id_from_email(email)
    return id


@users_bp.route("", methods=["POST"])
def create_user():
    store = current_app.store
    token = request.headers.get('Authorization')

    if not token:
        return jsonify({'error': 'Authorization token is missing'}), 401

    # Extract the actual token from the "Authorization" header
    token = token.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)

    if not success:
        return jsonify(user_info), 401

    # Now you can use the user_info dictionary to allow or restrict actions
    name = user_info['name']
    email = user_info['email']

    print(f"{email} and {name}")

    User = store.add_user(email, name)
    print(User)
    return jsonify(User)


@users_bp.route("", methods=["DELETE"])
def delete_user():
    store = current_app.store
    token = request.headers.get('Authorization')

    if not token:
        return jsonify({'error': 'Authorization token is missing'}), 401

    # Extract the actual token from the "Authorization" header
    token = token.split("Bearer ")[1]
    success, user_info = decode_firebase_token(token)


    if not success:
        return jsonify(user_info), 401
    
    id = get_id_helper(store, success, user_info)

    # Now you can use the user_info dictionary to allow or restrict actions
    name = user_info['name']
    email = user_info['email']

    store.delete_user_account(id)
    return jsonify({"name" : name, "email" : email}), 200



    # delete file, messages, history, subscription, card details etc 


# @users_bp.route("/personas", methods=["GET"])
# def get_user_personas():
#     store = current_app.store
#     token = request.headers.get('Authorization')
#     success, user_info = get_user_id(token)
#     user_id = get_id_helper(store, success, user_info)

#     user_personas = store.get_user_personas(user_id)
#     return jsonify(user_personas)


# @users_bp.route("/personas", methods=["PUT"])
# def update_user_personas():
#     store = current_app.store
#     token = request.headers.get('Authorization')
#     success, user_info = get_user_id(token)
#     user_id = get_id_helper(store, success, user_info)

#     data = request.json
#     # Default to an empty list if None
#     selected_personas = data.get("selected_personas", [])

#     if not isinstance(selected_personas, list):
#         return jsonify({"error": "Invalid data format for selected_personas"}), 400

#     store.update_user_personas(user_id, selected_personas)
#     return jsonify({"success": True})
