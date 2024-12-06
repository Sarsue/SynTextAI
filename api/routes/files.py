import os
from flask import Blueprint, request, jsonify, current_app
from redis.exceptions import RedisError
from utils import get_user_id, upload_to_gcs,delete_from_gcs
from tasks import process_file_data
from sqlite_store import DocSynthStore
import logging

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')


files_bp = Blueprint("files", __name__, url_prefix="/api/v1/files")
store = DocSynthStore(os.getenv("DATABASE_PATH"))

# Helper function to authenticate user and retrieve user ID
def authenticate_user():
    try:
        token = request.headers.get('Authorization')
        if not token:
            logging.error("Missing Authorization token")
            return None, None

        success, user_info = get_user_id(token)
        if not success:
            logging.error("Failed to authenticate user with token")
            return None, None

        user_id = store.get_user_id_from_email(user_info['email'])
        if not user_id:
            logging.error(f"No user ID found for email: {user_info['email']}")
            return None, user_info['user_id']

        logging.info(
            f"Authenticated user_id: {user_id}, user_gc_id: {user_info['user_id']}")
        return user_id, user_info['user_id']

    except Exception as e:
        logging.exception("Error during user authentication")
        return None, None

# Route to save file
@files_bp.route('', methods=['POST'])
def save_file():
    try:
        language = request.args.get('language', 'English')
        comprehension_level = request.args.get('comprehensionLevel', 'dropout')

        user_id, user_gc_id = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        if not request.files:
            logging.warning('No files provided')
            return jsonify({'error': 'No files provided'}), 400

        for _, file in request.files.items():
            file_url = upload_to_gcs(file, user_gc_id, file.filename)
            store.add_file(user_id,file.filename,file_url)
            if not file_url:
                logging.error(f"Failed to upload {file.filename} to GCS")
                return jsonify({'error': 'File upload failed'}), 500

            task = process_file_data.delay(user_id, user_gc_id, file.filename, language, comprehension_level)

            logging.info(f"Enqueued Task {task.id}  for processing {file.filename}")


        return jsonify({'message': 'File processing queued.'}), 202
    except RedisError as e:
        logging.error("Redis error")
        return jsonify({'error': 'Failed to enqueue job'}), 500
    except Exception as e:
        logging.error("Exception occurred")
        return jsonify({'error': str(e)}), 500

# Route to retrieve files
@files_bp.route('', methods=['GET'])
def retrieve_files():
    try:
        user_id, _ = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        files = store.get_files_for_user(user_id)
        return jsonify(files)
    except Exception as e:
        logging.error(f"Error retrieving files: {e}")
        return jsonify({'error': str(e)}), 500

# Route to delete a file
@files_bp.route('/<int:fileId>', methods=['DELETE'])
def delete_file(fileId):
    try:
        user_id, user_gc_id = authenticate_user()
        if user_id is None:
            return jsonify({'error': 'Unauthorized'}), 401

        file_info = store.delete_file_entry(user_id, fileId)
        delete_from_gcs(user_gc_id, file_info['file_name'])
        return '', 204
    except Exception as e:
        logging.error(f"Error deleting file: {e}")
        return jsonify({'error': str(e)}), 500
