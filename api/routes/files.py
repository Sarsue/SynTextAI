import os
import logging
from flask import Blueprint, request, jsonify, current_app
from google.cloud import storage
from redis.exceptions import RedisError
from utils import get_user_id
from celery_worker import process_file_data
from sqlite_store import DocSynthStore


logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')

bucket_name = 'docsynth-fbb02.appspot.com'
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

# Helper functions for GCS operations
def upload_to_gcs(file_data, user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        blob.upload_from_file(file_data, content_type=file_data.mimetype)
        blob.make_public()
        logging.info(f"Uploaded {filename} to GCS: {blob.public_url}")
        return blob.public_url
    except Exception as e:
        logging.error("Error uploading to GCS")
        return None

def download_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        if not blob.exists():
            logging.warning(f"File {filename} not found in GCS")
            return None
        return blob.download_as_bytes()
    except Exception as e:
        logging.error("Error downloading from GCS")
        return None

def delete_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        blob.delete()
        logging.info(f"Deleted {filename} from GCS.")
    except Exception as e:
        logging.error("Error deleting from GCS")

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
            if not file_url:
                logging.error(f"Failed to upload {file.filename} to GCS")
                return jsonify({'error': 'File upload failed'}), 500

            task = process_file_data.apply_async(args=[
                user_id, user_gc_id, file, language, comprehension_level
            ])
            logging.info(f"Enqueued processing for {file.filename}")
            return jsonify({'task_id': task.id, 'status': 'Processing'}), 202


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

@files_bp.route('/task_status/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """
    Endpoint to fetch task status by task ID.
    """
    from celery.result import AsyncResult

    task_result = AsyncResult(task_id)
    if task_result.state == 'PENDING':
        response = {
            'state': task_result.state,
            'status': 'Task is pending...',
        }
    elif task_result.state == 'FAILURE':
        response = {
            'state': task_result.state,
            'status': str(task_result.info),  # Include error message if task failed
        }
    else:
        response = {
            'state': task_result.state,
            'result': task_result.info,  # Task return value
        }

    return jsonify(response)

@files_bp.route('/task_result/<task_id>', methods=['POST'])
def save_task_result(task_id):
    """
    Endpoint to save task result by task ID.
    """
    from celery.result import AsyncResult

    task_result = AsyncResult(task_id)
    if task_result.state == 'SUCCESS':
        result = task_result.result
        user_id = result['user_id']
        filename = result['filename']
        transcription = result['transcription']
        interpretations = result['interpretations']

        store.update_file_with_extract(user_id, filename, transcription)
        result_message = "\n\n".join(interpretations)
        store.add_message(content=result_message, sender='bot', user_id=user_id)

        return jsonify({'status': 'Result saved successfully'}), 200
    else:
        return jsonify({'error': 'Task result not available'}), 404
