import os
import logging
from flask import Blueprint, request, jsonify, current_app
from google.cloud import storage
from redis.exceptions import RedisError
from celery_worker import celery_app  # Ensure this import is correct
from postgresql_store import DocSynthStore
from llm_service import process_content  # API call logic
from utils import get_user_id
from doc_processor import process_file

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

bucket_name = 'docsynth-fbb02.appspot.com'
files_bp = Blueprint("files", __name__, url_prefix="/api/v1/files")

# Database configuration
database_config = {
    'dbname': os.getenv("DATABASE_NAME"),
    'user': os.getenv("DATABASE_USER"),
    'password': os.getenv("DATABASE_PASSWORD"),
    'host': os.getenv("DATABASE_HOST"),
    'port': os.getenv("DATABASE_PORT")
}
store = DocSynthStore(database_config)

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
        logging.error(f"Error uploading to GCS: {e}")
        return None

def download_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        return blob.download_as_bytes()
    except Exception as e:
        logging.error(f"Error downloading from GCS: {e}")
        return None

def delete_from_gcs(user_gc_id, filename):
    try:
        client = storage.Client()
        bucket = client.get_bucket(bucket_name)
        blob = bucket.blob(f"{user_gc_id}/{filename}")
        blob.delete()
        logging.info(f"Deleted {filename} from GCS.")
    except Exception as e:
        logging.error(f"Error deleting from GCS: {e}")

# Celery task for processing files
@celery_app.task
def process_and_store_file(user_id, user_gc_id, filename):
    try:
        file_data = download_from_gcs(user_gc_id, filename)
        if not file_data:
            raise FileNotFoundError(f"{filename} not found.")

        _, ext = os.path.splitext(filename)
        chunks = process_file(file_data, ext.lstrip('.'))  # Process in chunks

        for chunk in chunks:
            result = process_content(chunk)  # Call LLM service
            store.add_message(content=result, sender='bot', user_id=user_id)

        logging.info(f"Processed and stored '{filename}' successfully.")
    except Exception as e:
        logging.error(f"Error processing {filename}: {e}")

# Flask route to upload files
@files_bp.route('', methods=['POST'])
def save_file():
    try:
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        if not success:
            return jsonify({'error': 'Unauthorized'}), 401

        user_id = current_app.store.get_user_id_from_email(user_info['email'])

        if not request.files:
            return jsonify({'error': 'No files provided'}), 400

        for _, file in request.files.items():
            file_url = upload_to_gcs(file, user_info['user_id'], file.filename)
            if not file_url:
                return jsonify({'error': 'File upload failed'}), 500

            store.add_file(user_id, file.filename, file_url)

            # Enqueue the file processing task
            process_and_store_file.apply_async((user_id, user_info['user_id'], file.filename))
            logging.info(f"Enqueued processing for {file.filename}")

        return jsonify({'message': 'File processing queued.'}), 202
    except RedisError as e:
        logging.error(f"Redis error: {e}")
        return jsonify({'error': 'Failed to enqueue job'}), 500
    except Exception as e:
        logging.error(f"Exception occurred: {e}")
        return jsonify({'error': str(e)}), 500

# Route to retrieve files
@files_bp.route('', methods=['GET'])
def retrieve_files():
    try:
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        if not success:
            return jsonify({'error': 'Unauthorized'}), 401

        user_id = current_app.store.get_user_id_from_email(user_info['email'])
        files = store.get_files_for_user(user_id)
        return jsonify(files)
    except Exception as e:
        logging.error(f"Error retrieving files: {e}")
        return jsonify({'error': str(e)}), 500

# Route to delete a file
@files_bp.route('/<int:fileId>', methods=['DELETE'])
def delete_file(fileId):
    try:
        token = request.headers.get('Authorization')
        success, user_info = get_user_id(token)
        if not success:
            return jsonify({'error': 'Unauthorized'}), 401

        user_id = current_app.store.get_user_id_from_email(user_info['email'])
        file_info = store.delete_file_entry(user_id, fileId)
        delete_from_gcs(user_info['user_id'], file_info['file_name'])
        return '', 204
    except Exception as e:
        logging.error(f"Error deleting file: {e}")
        return jsonify({'error': str(e)}), 500
