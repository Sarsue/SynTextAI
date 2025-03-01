from flask import Blueprint, request, jsonify, current_app
from utils import decode_firebase_token, get_user_id
from sqlalchemy.exc import IntegrityError


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
    User = store.add_user(email, name)
    print(User)
    return jsonify(User)
    
@users_bp.route("", methods=["DELETE"])
def delete_user():
    from tasks import delete_user_task
    store = current_app.store
    token = request.headers.get('Authorization')

    if not token or not token.startswith("Bearer "):
        return jsonify({'error': 'Invalid or missing Authorization token'}), 401

    try:
        token = token.split("Bearer ")[1]
        success, user_info = decode_firebase_token(token)
        if not success:
            return jsonify({'error': 'Invalid token'}), 401

        user_id = get_id_helper(store, success, user_info)
        user_gc_id = user_info['user_id']

        if not user_id:
            return jsonify({'error': 'User not found'}), 404

        # Trigger Celery task
        # delete google cloud files too
        delete_user_task.apply_async(args=[user_id,user_gc_id])

        return jsonify({"message": "User deletion in progress", "email": user_info['email']}), 200

    except IntegrityError:
        current_app.logger.error(f"Database error while deleting user {user_info['email']}")
        return jsonify({'error': 'Failed to delete user due to database constraints'}), 500

    except Exception as e:
        current_app.logger.error(f"Unexpected error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred'}), 500



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
